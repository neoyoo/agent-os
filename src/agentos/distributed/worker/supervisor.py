from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol

from agentos.distributed.models import QueueDelivery, WorkerState, WorkerStatus
from agentos.distributed.errors import DistributedShutdownTimeoutError
from agentos.distributed.protocols import QueuePort


Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class DeliveryRunner(Protocol):
    """Supervisor 调度 delivery 所需的最小异步边界。"""

    heartbeat_interval: timedelta
    claim_ttl: timedelta

    async def run_delivery(self, delivery: QueueDelivery) -> bool: ...


class DistributedWorker:
    """管理 receive、执行并发以及 drain/close 生命周期。"""

    def __init__(
        self,
        *,
        runner: DeliveryRunner,
        queue: QueuePort,
        worker_id: str,
        topic: str,
        max_concurrency: int = 1,
        clock: Clock | None = None,
        shutdown_cleanup_timeout: timedelta = timedelta(seconds=5),
    ) -> None:
        if not worker_id.strip():
            raise ValueError("worker_id must not be empty")
        if not topic.strip():
            raise ValueError("topic must not be empty")
        if type(max_concurrency) is not int or max_concurrency <= 0:
            raise ValueError("max_concurrency must be a positive integer")
        if (
            type(shutdown_cleanup_timeout) is not timedelta
            or shutdown_cleanup_timeout <= timedelta(0)
        ):
            raise ValueError("shutdown_cleanup_timeout must be positive")
        self._runner = runner
        self._queue = queue
        self._worker_id = worker_id
        self._topic = topic
        self._max_concurrency = max_concurrency
        self._clock = _utc_now if clock is None else clock
        self._shutdown_cleanup_timeout = shutdown_cleanup_timeout.total_seconds()
        self._status: WorkerStatus = "created"
        self._last_heartbeat_at: datetime | None = None
        self._drain_started_at: datetime | None = None
        self._receiver: asyncio.Task[None] | None = None
        self._receiver_stopped = asyncio.Event()
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

    async def wait(self) -> None:
        """等待 receiver 停止，并传播其原始失败。"""

        if self._status == "created":
            raise RuntimeError("worker has not been started")
        await self._receiver_stopped.wait()
        if self._failure is not None:
            raise self._failure

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
            receiver = self._receiver
            if receiver is None:
                self._receiver_stopped.set()
        cleanup_error: BaseException | None = None
        receiver_error: BaseException | None = None
        try:
            receiver_error = await _cancel_receiver(
                receiver,
                self._shutdown_cleanup_timeout,
            )
        except BaseException as error:
            cleanup_error = error
        if receiver is not None and receiver.done():
            self._receiver = None
        cancellation = next(
            (
                error
                for error in (cleanup_error, receiver_error)
                if isinstance(error, asyncio.CancelledError)
            ),
            None,
        )
        try:
            await self._finish_active(
                0.0 if cancellation is not None else float(timeout),
            )
        except BaseException as error:
            if cancellation is not None:
                cancellation.__cause__ = error
            else:
                cleanup_error = cleanup_error or error
        failure = cancellation or cleanup_error or receiver_error or self._failure
        if failure is not None:
            raise failure

    async def close(self) -> None:
        """幂等关闭 Worker；未 drain 的活动执行立即进入 cleanup。"""

        drain_error: BaseException | None = None
        try:
            if self._status != "closed":
                await self.drain(timeout=0)
        except BaseException as error:
            drain_error = error
        queue_error: BaseException | None = None
        queue_closed = False
        async with self._lifecycle_lock:
            if self._status == "closed":
                return
            queue_error, queue_closed = await _run_cleanup(
                self._queue.close(),
                self._shutdown_cleanup_timeout,
            )
            drain_incomplete = isinstance(
                drain_error,
                (asyncio.CancelledError, DistributedShutdownTimeoutError),
            )
            if queue_closed and not drain_incomplete:
                self._status = "closed"
        if isinstance(drain_error, asyncio.CancelledError):
            failure = drain_error
        elif isinstance(
            queue_error,
            (asyncio.CancelledError, DistributedShutdownTimeoutError),
        ):
            failure = queue_error
        else:
            failure = drain_error or queue_error
        if failure is not None:
            raise failure

    async def _receive_loop(self) -> None:
        while self._status == "running":
            available = self._max_concurrency - len(self._active)
            if available <= 0:
                self._capacity.clear()
                try:
                    await asyncio.wait_for(
                        self._capacity.wait(),
                        timeout=self._runner.heartbeat_interval.total_seconds(),
                    )
                except TimeoutError:
                    self._last_heartbeat_at = self._clock()
                if not self._can_accept_claims():
                    return
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
            if self._status == "running" and self._failure is None:
                self._failure = RuntimeError(
                    "worker receive loop stopped unexpectedly"
                )
        else:
            error = task.exception()
            if error is not None:
                if self._failure is None:
                    self._failure = error
            elif self._status == "running" and self._failure is None:
                self._failure = RuntimeError(
                    "worker receive loop stopped unexpectedly"
                )
        self._receiver_stopped.set()

    async def _finish_active(self, timeout: float) -> None:
        active = set(self._active)
        if not active:
            return
        pending = active
        cancellation: asyncio.CancelledError | None = None
        if timeout > 0:
            try:
                _, pending = await asyncio.wait(active, timeout=timeout)
            except asyncio.CancelledError as error:
                cancellation = error
        for task in pending:
            task.cancel()
        deadline = (
            asyncio.get_running_loop().time() + self._shutdown_cleanup_timeout
        )
        while pending:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            try:
                _, pending = await asyncio.wait(pending, timeout=remaining)
            except asyncio.CancelledError as error:
                cancellation = cancellation or error
        if pending:
            timeout_error = DistributedShutdownTimeoutError()
            if cancellation is not None:
                cancellation.__cause__ = timeout_error
                raise cancellation
            raise timeout_error
        await asyncio.gather(*active, return_exceptions=True)
        if cancellation is not None:
            raise cancellation


async def _cancel_receiver(
    task: asyncio.Task[None] | None,
    timeout: float,
) -> BaseException | None:
    if task is None:
        return None
    if not task.done():
        task.cancel()
    deadline = asyncio.get_running_loop().time() + timeout
    cancellation: asyncio.CancelledError | None = None
    while not task.done():
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            break
        try:
            done, _ = await asyncio.wait((task,), timeout=remaining)
            if not done:
                break
        except asyncio.CancelledError as error:
            cancellation = cancellation or error
    if not task.done():
        timeout_error = DistributedShutdownTimeoutError()
        if cancellation is not None:
            cancellation.__cause__ = timeout_error
            raise cancellation
        raise timeout_error
    try:
        await task
    except asyncio.CancelledError:
        return cancellation
    except BaseException as error:
        if cancellation is not None:
            cancellation.__cause__ = error
            return cancellation
        return error
    return cancellation


async def _run_cleanup(
    cleanup: Awaitable[None],
    timeout: float,
) -> tuple[BaseException | None, bool]:
    task = asyncio.create_task(cleanup)
    deadline = asyncio.get_running_loop().time() + timeout
    cancellation: asyncio.CancelledError | None = None
    while not task.done():
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            break
        try:
            done, _ = await asyncio.wait((task,), timeout=remaining)
            if not done:
                break
        except asyncio.CancelledError as error:
            cancellation = cancellation or error
    if not task.done():
        task.cancel()
        task.add_done_callback(_consume_task_result)
        return cancellation or DistributedShutdownTimeoutError(), False
    try:
        await task
    except BaseException as error:
        if cancellation is not None:
            cancellation.__cause__ = error
            return cancellation, False
        return error, False
    return cancellation, True


def _consume_task_result(task: asyncio.Task[object]) -> None:
    if not task.cancelled():
        task.exception()


__all__ = ["DeliveryRunner", "DistributedWorker"]
