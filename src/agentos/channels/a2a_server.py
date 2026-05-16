from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Protocol

from agentos.multi.types import TaskRequest, TaskResult
from agentos.observability import use_incoming_trace_headers
from agentos.runtime import Agent


class A2ATaskRunner(Protocol):
    """Inbound A2A task 执行边界。"""

    def run_task(self, request: TaskRequest) -> TaskResult:
        """执行 A2A task request。"""


class AgentA2ATaskRunner:
    """用一个 Agent 执行 inbound A2A task。"""

    def __init__(self, agent: Agent) -> None:
        """创建 runner。"""

        self._agent = agent

    def run_task(self, request: TaskRequest) -> TaskResult:
        """调用 Agent.run() 并包装成 TaskResult。"""

        started_at = time.time()
        result = self._agent.run(request.instruction)
        return TaskResult(
            task_id=request.task_id,
            status="completed",
            summary=result.content,
            elapsed_seconds=time.time() - started_at,
        )


class A2AServerAdapter:
    """Inbound A2A JSON payload 到 TaskRunner 的适配器。"""

    def __init__(self, runner: A2ATaskRunner) -> None:
        """创建 A2A server adapter。"""

        self._runner = runner

    def handle_task(
        self,
        payload: dict[str, object],
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, object]:
        """处理 /a2a/tasks payload 并返回 JSON-safe TaskResult。"""

        with use_incoming_trace_headers(headers):
            try:
                request = self._parse_request(payload)
                result = self._runner.run_task(request)
            except Exception as error:
                result = TaskResult(
                    task_id=str(payload.get("task_id", "")),
                    status="failed",
                    summary="task failed",
                    error=str(error),
                )
        return self._result_to_dict(result)

    def handle_health(self) -> dict[str, object]:
        """返回 A2A health payload。"""

        return {"status": "ok"}

    def _parse_request(self, payload: dict[str, object]) -> TaskRequest:
        task_id = payload.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("task_id is required")
        instruction = payload.get("instruction")
        if not isinstance(instruction, str) or not instruction.strip():
            raise ValueError("instruction is required")
        allowed_tool_names = payload.get("allowed_tool_names", [])
        if not isinstance(allowed_tool_names, list):
            raise ValueError("allowed_tool_names must be a list")
        timeout_seconds = payload.get("timeout_seconds", 300)
        return TaskRequest(
            task_id=task_id,
            instruction=instruction,
            allowed_tool_names=tuple(str(name) for name in allowed_tool_names),
            timeout_seconds=float(timeout_seconds),
        )

    def _result_to_dict(self, result: TaskResult) -> dict[str, object]:
        return {
            "task_id": result.task_id,
            "status": result.status,
            "summary": result.summary,
            "artifacts": dict(result.artifacts),
            "error": result.error,
            "elapsed_seconds": result.elapsed_seconds,
        }
