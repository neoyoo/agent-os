from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal, Protocol
from urllib import request as urllib_request

from agentos.multi.types import AgentCard, TaskRequest, TaskResult


AgentHealthStatus = Literal["ok", "unhealthy"]


@dataclass(frozen=True, slots=True)
class AgentHealth:
    """远程 agent health check 结果。"""

    status: AgentHealthStatus
    detail: str | None = None


class A2ATransport(Protocol):
    """A2A JSON transport 边界。"""

    def post_json(
        self,
        url: str,
        payload: dict[str, object],
        timeout_seconds: float,
        *,
        headers: dict[str, str] | None = None,
    ) -> dict[str, object]:
        """POST JSON 并返回 JSON 对象。"""

    def get_json(
        self,
        url: str,
        timeout_seconds: float,
    ) -> dict[str, object]:
        """GET JSON 并返回 JSON 对象。"""


class UrllibA2ATransport:
    """基于标准库 urllib 的最小 HTTP JSON transport。"""

    def post_json(
        self,
        url: str,
        payload: dict[str, object],
        timeout_seconds: float,
        *,
        headers: dict[str, str] | None = None,
    ) -> dict[str, object]:
        """POST JSON 并返回 JSON 对象。"""

        body = json.dumps(payload).encode("utf-8")
        request_headers = {"Content-Type": "application/json"}
        if headers:
            request_headers.update(headers)
        request = urllib_request.Request(
            url,
            data=body,
            headers=request_headers,
            method="POST",
        )
        with urllib_request.urlopen(request, timeout=timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))

    def get_json(
        self,
        url: str,
        timeout_seconds: float,
    ) -> dict[str, object]:
        """GET JSON 并返回 JSON 对象。"""

        with urllib_request.urlopen(url, timeout=timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))


class A2AAdapter:
    """把 AgentCard endpoint 映射为 A2A JSON 调用。"""

    def __init__(self, transport: A2ATransport | None = None) -> None:
        self._transport = transport or UrllibA2ATransport()

    def send_task(self, card: AgentCard, request: TaskRequest) -> TaskResult:
        """向远程 agent 发送任务请求并解析 TaskResult。"""

        payload = {
            "task_id": request.task_id,
            "instruction": request.instruction,
            "allowed_tool_names": list(request.allowed_tool_names),
            "timeout_seconds": request.timeout_seconds,
        }
        response = self._transport.post_json(
            self._url(card, "/a2a/tasks"),
            payload,
            request.timeout_seconds,
            headers=request.trace_context,
        )
        return TaskResult(
            task_id=str(response["task_id"]),
            status=response["status"],  # type: ignore[arg-type]
            summary=str(response.get("summary", "")),
            artifacts=dict(response.get("artifacts", {})),
            error=(
                None
                if response.get("error") is None
                else str(response.get("error"))
            ),
            elapsed_seconds=float(response.get("elapsed_seconds", 0)),
        )

    def check_health(
        self,
        card: AgentCard,
        *,
        timeout_seconds: float = 5,
    ) -> AgentHealth:
        """检查远程 agent health endpoint。"""

        try:
            response = self._transport.get_json(
                self._url(card, "/a2a/health"),
                timeout_seconds,
            )
        except Exception as error:
            return AgentHealth(
                status="unhealthy",
                detail=str(error),
            )
        status = response.get("status", "unhealthy")
        return AgentHealth(
            status="ok" if status == "ok" else "unhealthy",
            detail=(
                None
                if response.get("detail") is None
                else str(response.get("detail"))
            ),
        )

    def _url(self, card: AgentCard, path: str) -> str:
        if card.endpoint is None:
            raise ValueError(f"agent card has no endpoint: {card.agent_id}")
        return card.endpoint.rstrip("/") + path
