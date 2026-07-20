from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime

from agentos.distributed.models import WorkerState, WorkerStatus
from agentos.distributed.protocols import QueuePort
from agentos.distributed.worker.runner import WorkerRunner


Clock = Callable[[], datetime]


class DistributedWorker:
    """管理 receive、执行并发以及 drain/close 生命周期。"""

    def __init__(
        self,
        *,
        runner: WorkerRunner,
        queue: QueuePort,
        worker_id: str,
        topic: str,
        max_concurrency: int = 1,
        clock: Clock = lambda: datetime.now(UTC),
    ) -> None:
        if not worker_id.strip():
            raise ValueError("worker_id must not be empty")
        if not topic.strip():
            raise ValueError("topic must not be empty")
        if type(max_concurrency) is not int or max_concurrency <= 0:
            raise ValueError("max_concurrency must be a positive integer")
        self._runner = runner
        self._queue = queue
        self._worker_id = worker_id
        self._topic = topic
        self._max_concurrency = max_concurrency
        self._clock = clock
        self._status: WorkerStatus = "created"
        self._last_heartbeat_at: datetime | None = None
        self._drain_started_at: datetime | None = None
        self._receiver: asyncio.Task[None] | None = None
        self._failure: BaseException | None = None
        self._active: set[asyncio.Task[bool]] = set()
        self._capacity = asyncio.Event()
        self._capacity.set()
        self._lifecycle_lock = asyncio.Lock()

    @property
    def state(self) -> WorkerState:
        """返回不暴露内部 Task 的 Readiness 快照。"""

        return WorkerState(
            worker_id=self._worker_id,
            status=self._status,
            accepting_claims=(
                self._status == "running" and self._failure is None
            ),
            active_claim_count=len(self._active),
            last_heartbeat_at=self._last_heartbeat_at,
            drain_started_at=self._drain_started_at,
        )

    async def start(self) -> None:
        """开始接收 delivery；调用立即返回。"""

        async with self._lifecycle_lock:
            if self._status == "running":
                return
            if self._status != "created":
                raise RuntimeError("worker cannot be restarted")
            self._status = "running"
            self._last_heartbeat_at = self._clock()
            self._receiver = asyncio.create_task(self._receive_loop())
            self._receiver.add_done_callback(self._receiver_done)

    async def drain(self, timeout: float) -> None:
        """停止新 claim，等待活动执行，超时后关闭其 AgentStream。"""

        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise TypeError("timeout must be a number")
        if timeout < 0:
            raise ValueError("timeout must not be negative")
        async with self._lifecycle_lock:
            if self._status == "closed":
                return
            if self._status == "created":
                self._status = "draining"
                self._drain_started_at = self._clock()
            elif self._status == "running":
                self._status = "draining"
                self._drain_started_at = self._clock()
            receiver, self._receiver = self._receiver, None
        receiver_error = await _cancel_receiver(receiver)
        await self._finish_active(float(timeout))
        failure = receiver_error or self._failure
        if failure is not None:
            raise failure

    async def close(self) -> None:
        """幂等关闭 Worker；未 drain 的活动执行立即进入 cleanup。"""

        drain_error: BaseException | None = None
        try:
            if self._status in {"created", "running"}:
                await self.drain(timeout=0)
            elif self._status == "draining" and self._active:
                await self._finish_active(0)
        except BaseException as error:
            drain_error = error
        async with self._lifecycle_lock:
            if self._status == "closed":
                return
            await self._queue.close()
            self._status = "closed"
        if drain_error is not None:
            raise drain_error

    async def _receive_loop(self) -> None:
        while self._status == "running":
            available = self._max_concurrency - len(self._active)
            if available <= 0:
                self._capacity.clear()
                await self._capacity.wait()
                continue
            deliveries = await self._queue.reclaim(
                topic=self._topic,
                consumer_id=self._worker_id,
                min_idle=self._runner.claim_ttl,
                limit=available,
            )
            if not deliveries:
                deliveries = await self._queue.receive(
                    topic=self._topic,
                    consumer_id=self._worker_id,
                    limit=available,
                )
            self._last_heartbeat_at = self._clock()
            if not self._can_accept_claims():
                return
            for delivery in deliveries:
                if not self._can_accept_claims():
                    return
                task = asyncio.create_task(self._runner.run_delivery(delivery))
                self._active.add(task)
                task.add_done_callback(self._delivery_done)
            if not deliveries:
                await asyncio.sleep(0)

    def _delivery_done(self, task: asyncio.Task[bool]) -> None:
        self._active.discard(task)
        self._last_heartbeat_at = self._clock()
        self._capacity.set()
        if task.cancelled():
            return
        error = task.exception()
        if error is None:
            return
        self._remember_failure(error)
        receiver = self._receiver
        if receiver is not None and not receiver.done():
            receiver.cancel()

    def _can_accept_claims(self) -> bool:
        for task in tuple(self._active):
            if task.done() and not task.cancelled():
                self._remember_failure(task.exception())
        return self._status == "running" and self._failure is None

    def _remember_failure(self, error: BaseException | None) -> None:
        if error is not None and self._failure is None:
            self._failure = error

    def _receiver_done(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            if self._failure is None:
                self._failure = error
        elif self._status == "running" and self._failure is None:
            self._failure = RuntimeError("worker receive loop stopped")

    async def _finish_active(self, timeout: float) -> None:
        active = tuple(self._active)
        if not active:
            return
        _, pending = await asyncio.wait(active, timeout=timeout)
        if not pending:
            return
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)


async def _cancel_receiver(
    task: asyncio.Task[None] | None,
) -> BaseException | None:
    if task is None:
        return None
    if not task.done():
        task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        return None
    except BaseException as error:
        return error
    return None


__all__ = ["DistributedWorker"]
