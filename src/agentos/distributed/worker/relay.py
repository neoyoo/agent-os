from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta

from agentos.distributed.errors import DeliveryUnavailableError
from agentos.distributed.models import OutboxClaim
from agentos.distributed.protocols import OutboxPort, QueuePort


@dataclass(slots=True)
class _RelayLifecycle:
    activity: asyncio.Lock = field(default_factory=asyncio.Lock)
    closing: bool = False
    closed: bool = False


@dataclass(frozen=True, slots=True)
class OutboxRelay:
    """把 PostgreSQL Outbox claim 发布到 at-least-once Queue。"""

    outbox: OutboxPort
    queue: QueuePort
    owner_id: str
    batch_size: int
    claim_ttl: timedelta
    batch_timeout: timedelta = timedelta(seconds=30)
    _lifecycle: _RelayLifecycle = field(
        default_factory=_RelayLifecycle,
        compare=False,
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        if not self.owner_id.strip():
            raise ValueError("owner_id must not be empty")
        if type(self.batch_size) is not int or self.batch_size <= 0:
            raise ValueError("batch_size must be a positive integer")
        if self.claim_ttl <= timedelta(0):
            raise ValueError("claim_ttl must be positive")
        if type(self.batch_timeout) is not timedelta or self.batch_timeout <= timedelta(0):
            raise ValueError("batch_timeout must be positive")

    async def relay_once(self) -> int:
        """Publish one bounded batch while the relay accepts work."""

        lifecycle = self._lifecycle
        if lifecycle.closing:
            raise RuntimeError("relay is closing or closed")
        async with lifecycle.activity:
            if lifecycle.closing:
                raise RuntimeError("relay is closing or closed")
            deadline = (
                asyncio.get_running_loop().time() + self.batch_timeout.total_seconds()
            )
            try:
                return await self._relay_batch(deadline=deadline)
            except TimeoutError:
                raise DeliveryUnavailableError() from None

    async def close(self) -> None:
        """Stop new batches and wait for the active batch to finish."""

        lifecycle = self._lifecycle
        if lifecycle.closed:
            return
        lifecycle.closing = True
        async with lifecycle.activity:
            lifecycle.closed = True

    async def _relay_batch(self, *, deadline: float) -> int:
        """发布一个有界批次；失败时释放尚未完成的 claim 尾段。"""

        async with asyncio.timeout_at(deadline):
            claims = await self.outbox.claim_batch(
                owner_id=self.owner_id,
                limit=self.batch_size,
                ttl=self.claim_ttl,
            )
        published = 0
        for index, claim in enumerate(claims):
            try:
                async with asyncio.timeout_at(deadline):
                    queue_entry_id = await self.queue.publish(record=claim.record)
                async with asyncio.timeout_at(deadline):
                    await self.outbox.mark_published(
                        claim=claim,
                        queue_entry_id=queue_entry_id,
                    )
            except BaseException as error:
                try:
                    await self._release_claims(
                        claims[index:],
                        deadline=deadline,
                    )
                except BaseException as release_error:
                    if isinstance(release_error, asyncio.CancelledError):
                        raise release_error from error
                    if isinstance(release_error, TimeoutError) and not isinstance(
                        error,
                        asyncio.CancelledError,
                    ):
                        raise release_error from error
                    raise error from release_error
                raise
            published += 1
        return published

    async def _release_claims(
        self,
        claims: tuple[OutboxClaim, ...],
        *,
        deadline: float,
    ) -> None:
        first_error: BaseException | None = None
        cancellation_error: asyncio.CancelledError | None = None
        timeout_error: TimeoutError | None = None
        for claim in claims:
            try:
                async with asyncio.timeout_at(deadline):
                    await self.outbox.release_claim(claim=claim)
            except BaseException as error:
                if (
                    isinstance(error, asyncio.CancelledError)
                    and cancellation_error is None
                ):
                    cancellation_error = error
                elif isinstance(error, TimeoutError) and timeout_error is None:
                    timeout_error = error
                elif first_error is None:
                    first_error = error
        if cancellation_error is not None:
            raise cancellation_error
        if timeout_error is not None:
            raise timeout_error
        if first_error is not None:
            raise first_error


__all__ = ["OutboxRelay"]
