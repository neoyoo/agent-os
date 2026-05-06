from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor

from agentos.multi.types import TaskResult


class SpawnExecutor:
    """ephemeral subagent callable 的本地线程池执行器。"""

    def __init__(self, max_workers: int = 3) -> None:
        """创建 spawn executor。"""

        self._executor = ThreadPoolExecutor(max_workers=max_workers)

    def submit(
        self,
        task_id: str,
        run: Callable[[], TaskResult],
    ) -> Future[TaskResult]:
        """提交一个 subagent 任务。"""

        return self._executor.submit(run)

    def shutdown(self) -> None:
        """关闭底层线程池。"""

        self._executor.shutdown(wait=True)
