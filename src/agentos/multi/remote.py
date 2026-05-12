from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Protocol

from agentos.channels.a2a import A2AAdapter
from agentos.multi.types import AgentCard, TaskRequest, TaskResult


class RemoteTaskAdapter(Protocol):
    """远程 task transport 边界。"""

    def send_task(self, card: AgentCard, request: TaskRequest) -> TaskResult:
        """向远程 agent 发送 task。"""


class RemoteTaskExecutor:
    """通过 A2AAdapter 执行 endpoint-backed remote task。"""

    def __init__(
        self,
        *,
        a2a_adapter: RemoteTaskAdapter | None = None,
        max_workers: int = 3,
    ) -> None:
        """创建 remote task executor。"""

        self._a2a_adapter = a2a_adapter or A2AAdapter()
        self._executor = ThreadPoolExecutor(max_workers=max_workers)

    def submit(
        self,
        card: AgentCard,
        request: TaskRequest,
        on_result: Callable[[TaskResult], None],
    ) -> Future[TaskResult]:
        """提交远程任务，并在后台回调 TaskResult。"""

        return self._executor.submit(self._run, card, request, on_result)

    def shutdown(self) -> None:
        """关闭底层线程池。"""

        self._executor.shutdown(wait=True)

    def _run(
        self,
        card: AgentCard,
        request: TaskRequest,
        on_result: Callable[[TaskResult], None],
    ) -> TaskResult:
        try:
            result = self._a2a_adapter.send_task(card, request)
        except Exception as error:
            result = TaskResult(
                task_id=request.task_id,
                status="failed",
                summary="remote task failed",
                error=str(error),
            )
        on_result(result)
        return result
