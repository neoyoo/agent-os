from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta

from agentos.distributed.protocols import OutboxPort, QueuePort


@dataclass(slots=True)
class OutboxRelay:
    """把 PostgreSQL Outbox claim 发布到 at-least-once Queue。"""

    outbox: OutboxPort
    queue: QueuePort
    owner_id: str
    batch_size: int
    claim_ttl: timedelta
    _activity: asyncio.Lock = field(
        default_factory=asyncio.Lock,
        compare=False,
        init=False,
        repr=False,
    )
    _closing: bool = field(default=False, compare=False, init=False, repr=False)
    _closed: bool = field(default=False, compare=False, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.owner_id.strip():
            raise ValueError("owner_id must not be empty")
        if type(self.batch_size) is not int or self.batch_size <= 0:
            raise ValueError("batch_size must be a positive integer")
        if self.claim_ttl <= timedelta(0):
            raise ValueError("claim_ttl must be positive")

    async def relay_once(self) -> int:
        """Publish one bounded batch while the relay accepts work."""

        if self._closing:
            raise RuntimeError("relay is closing or closed")
        async with self._activity:
            if self._closing:
                raise RuntimeError("relay is closing or closed")
            return await self._relay_batch()

    async def close(self) -> None:
        """Stop new batches and wait for the active batch to finish."""

        if self._closed:
            return
        self._closing = True
        async with self._activity:
            self._closed = True

    async def _relay_batch(self) -> int:
        """发布一个有界批次；mark 失败保留可重复发布窗口。"""

        claims = await self.outbox.claim_batch(
            owner_id=self.owner_id,
            limit=self.batch_size,
            ttl=self.claim_ttl,
        )
        published = 0
        for claim in claims:
            try:
                queue_entry_id = await self.queue.publish(record=claim.record)
            except BaseException:
                await self.outbox.release_claim(claim=claim)
                raise
            await self.outbox.mark_published(
                claim=claim,
                queue_entry_id=queue_entry_id,
            )
            published += 1
        return published


__all__ = ["OutboxRelay"]
