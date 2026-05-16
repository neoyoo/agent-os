from __future__ import annotations

import contextvars
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from threading import RLock

from agentos.multi.types import TaskResult


class SpawnExecutor:
    """ephemeral subagent callable 的本地线程池执行器。"""

    def __init__(self, max_workers: int = 3) -> None:
        """创建 spawn executor。"""

        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._futures: set[Future[TaskResult]] = set()
        self._lock = RLock()

    def submit(
        self,
        task_id: str,
        run: Callable[[], TaskResult],
    ) -> Future[TaskResult]:
        """提交一个 subagent 任务。"""

        context = contextvars.copy_context()
        future = self._executor.submit(context.run, run)
        with self._lock:
            self._futures.add(future)
        future.add_done_callback(self._discard_future)
        return future

    def shutdown(self, timeout_seconds: float | None = None) -> None:
        """关闭底层线程池。"""

        with self._lock:
            futures = set(self._futures)
        if timeout_seconds is None:
            self._executor.shutdown(wait=True)
            return
        done, pending = wait(futures, timeout=timeout_seconds)
        for future in pending:
            future.cancel()
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _discard_future(self, future: Future[TaskResult]) -> None:
        with self._lock:
            self._futures.discard(future)
