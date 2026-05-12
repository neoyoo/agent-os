from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
import json
from typing import Any

from agentos.channels.a2a_server import A2AServerAdapter
from agentos.channels.auth import (
    AllowAllChannelAuthPolicy,
    ChannelAuthError,
    ChannelAuthPolicy,
)
from agentos.channels.http import HttpAgentChannel
from agentos.channels.session import AgentSessionProvider
from agentos.channels.sse import SseAgentChannel
from agentos.runtime import Agent


AsgiReceive = Callable[[], Awaitable[dict[str, object]]]
AsgiSend = Callable[[dict[str, Any]], Awaitable[None]]


class AsgiAgentApp:
    """最小 ASGI channel app，不依赖具体 Web framework。

    当前版本在 ASGI 调用内同步消费 Agent stream，适合本地/dev 或由
    外层 server 做线程隔离的部署；生产级 async offloading 后续补齐。
    """

    def __init__(
        self,
        *,
        sessions: AgentSessionProvider,
        auth_policy: ChannelAuthPolicy | None = None,
        a2a_server: A2AServerAdapter | None = None,
    ) -> None:
        """创建 ASGI app。"""

        self._sessions = sessions
        self._auth_policy = auth_policy or AllowAllChannelAuthPolicy()
        self._http = HttpAgentChannel(sessions)
        self._sse = SseAgentChannel(sessions)
        self._a2a_server = a2a_server

    async def __call__(
        self,
        scope: Mapping[str, object],
        receive: AsgiReceive,
        send: AsgiSend,
    ) -> None:
        """处理一个 ASGI HTTP request。"""

        if scope.get("type") != "http":
            await self._send_json(send, 500, {"status": "failed", "error": "unsupported scope"})
            return

        try:
            self._auth_policy.authorize(self._headers(scope))
        except ChannelAuthError as error:
            await self._send_json(
                send,
                401,
                {"status": "failed", "error": str(error)},
            )
            return

        method = str(scope.get("method", "GET")).upper()
        path = str(scope.get("path", "/"))

        if method == "GET" and path == "/v1/health":
            await self._send_json(send, 200, {"status": "ok"})
            return
        if method == "GET" and path == "/a2a/health":
            if self._a2a_server is None:
                await self._send_json(send, 404, {"status": "failed", "error": "not found"})
                return
            await self._send_json(send, 200, self._a2a_server.handle_health())
            return
        if method == "POST" and path == "/a2a/tasks":
            await self._handle_a2a_task(receive, send)
            return

        session_id, is_stream = self._match_turn_path(method, path)
        if session_id is None:
            await self._send_json(send, 404, {"status": "failed", "error": "not found"})
            return

        body = await self._read_body(receive)
        if is_stream:
            await self._handle_sse_turn(session_id, body, receive, send)
            return
        result = self._http.handle_turn(session_id, body)
        await self._send_json(
            send,
            result.status_code,
            {
                "session_id": result.session_id,
                "status": result.status,
                "content": result.content,
                "error": result.error,
            },
        )

    async def _handle_a2a_task(
        self,
        receive: AsgiReceive,
        send: AsgiSend,
    ) -> None:
        if self._a2a_server is None:
            await self._send_json(send, 404, {"status": "failed", "error": "not found"})
            return
        body = await self._read_body(receive)
        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as error:
            payload = {"error": str(error)}
        if not isinstance(payload, dict):
            payload = {"error": "payload must be an object"}
        await self._send_json(send, 200, self._a2a_server.handle_task(payload))

    async def _handle_sse_turn(
        self,
        session_id: str,
        body: bytes,
        receive: AsgiReceive,
        send: AsgiSend,
    ) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/event-stream; charset=utf-8")],
            },
        )
        disconnect_task = asyncio.create_task(receive())
        stream_agent: Agent | None = None

        def record_stream_agent(agent: Agent) -> None:
            nonlocal stream_agent
            stream_agent = agent

        stream = self._sse.stream_turn(session_id, body, on_agent=record_stream_agent)
        try:
            for chunk in stream:
                await asyncio.sleep(0)
                if disconnect_task.done():
                    message = disconnect_task.result()
                    if message.get("type") == "http.disconnect":
                        if stream_agent is not None:
                            stream_agent.interrupt()
                        break
                await send(
                    {
                        "type": "http.response.body",
                        "body": chunk.encode("utf-8"),
                        "more_body": True,
                    },
                )
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()
            if not disconnect_task.done():
                disconnect_task.cancel()
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    async def _read_body(self, receive: AsgiReceive) -> bytes:
        body = b""
        while True:
            message = await receive()
            if message.get("type") == "http.disconnect":
                return body
            chunk = message.get("body", b"")
            if isinstance(chunk, bytes):
                body += chunk
            if not bool(message.get("more_body", False)):
                return body

    def _match_turn_path(self, method: str, path: str) -> tuple[str | None, bool]:
        if method != "POST":
            return None, False
        parts = path.strip("/").split("/")
        if len(parts) == 4 and parts[:2] == ["v1", "sessions"] and parts[3] == "turns":
            return parts[2], False
        if (
            len(parts) == 5
            and parts[:2] == ["v1", "sessions"]
            and parts[3:] == ["turns", "stream"]
        ):
            return parts[2], True
        return None, False

    def _headers(self, scope: Mapping[str, object]) -> dict[str, str]:
        raw_headers = scope.get("headers", [])
        headers: dict[str, str] = {}
        if not isinstance(raw_headers, list):
            return headers
        for item in raw_headers:
            if not isinstance(item, tuple) or len(item) != 2:
                continue
            key, value = item
            if isinstance(key, bytes) and isinstance(value, bytes):
                headers[key.decode("latin-1").lower()] = value.decode("latin-1")
        return headers

    async def _send_json(
        self,
        send: AsgiSend,
        status: int,
        payload: dict[str, object],
    ) -> None:
        body = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [(b"content-type", b"application/json; charset=utf-8")],
            },
        )
        await send({"type": "http.response.body", "body": body, "more_body": False})
