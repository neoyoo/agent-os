from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
import json
from typing import Any

from agentos.channels.a2a_server import A2AServerAdapter
from agentos.channels.auth import (
    AllowAllChannelAuthPolicy,
    ChannelAuthError,
    ChannelAuthPolicy,
)
from agentos.channels.http import HttpAgentChannel
from agentos.channels.rate_limit import RateLimiter
from agentos.channels.session import AgentSessionProvider
from agentos.channels.types import ChannelTurnRequest, parse_channel_turn_request
from agentos.runtime import Agent
from agentos.runtime.stream_serializers import event_to_sse


AsgiReceive = Callable[[], Awaitable[dict[str, object]]]
AsgiSend = Callable[[dict[str, Any]], Awaitable[None]]


class RequestBodyTooLarge(ValueError):
    """ASGI request body 超过本应用允许的上限。"""


class AsgiAgentApp:
    """最小 ASGI channel app，不依赖具体 Web framework。"""

    def __init__(
        self,
        *,
        sessions: AgentSessionProvider,
        auth_policy: ChannelAuthPolicy | None = None,
        a2a_server: A2AServerAdapter | None = None,
        max_body_bytes: int = 1_048_576,
        readiness_checks: Mapping[str, Callable[[], object]] | None = None,
        health_checks: Mapping[str, Callable[[], object]] | None = None,
        rate_limiter: RateLimiter | None = None,
        shutdown_handlers: list[Callable[[], object]] | None = None,
    ) -> None:
        """创建 ASGI app。"""

        self._sessions = sessions
        self._auth_policy = auth_policy or AllowAllChannelAuthPolicy()
        self._http = HttpAgentChannel(sessions)
        self._a2a_server = a2a_server
        self._max_body_bytes = max_body_bytes
        self._readiness_checks = dict(readiness_checks or {})
        self._health_checks = dict(health_checks or {})
        self._rate_limiter = rate_limiter
        self._shutdown_handlers = list(shutdown_handlers or [])

    async def __call__(
        self,
        scope: Mapping[str, object],
        receive: AsgiReceive,
        send: AsgiSend,
    ) -> None:
        """处理一个 ASGI HTTP request。"""

        if scope.get("type") == "lifespan":
            await self._handle_lifespan(receive, send)
            return
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

        if method == "GET" and path in {"/health", "/v1/health"}:
            await self._send_json(send, 200, {"status": "ok"})
            return
        if method == "GET" and path in {"/ready", "/v1/ready"}:
            await self._handle_ready(send)
            return
        if method == "GET" and path == "/a2a/health":
            if self._a2a_server is None:
                await self._send_json(send, 404, {"status": "failed", "error": "not found"})
                return
            await self._send_json(send, 200, self._a2a_server.handle_health())
            return
        if method == "POST" and path == "/a2a/tasks":
            await self._handle_a2a_task(receive, send, headers=self._headers(scope))
            return

        session_id, is_stream = self._match_turn_path(method, path)
        if session_id is None:
            await self._send_json(send, 404, {"status": "failed", "error": "not found"})
            return
        if self._rate_limiter is not None:
            decision = self._rate_limiter.check(session_id)
            if not decision.allowed:
                await self._send_json(
                    send,
                    429,
                    {"status": "failed", "error": "rate limit exceeded"},
                    headers=[(b"retry-after", str(decision.retry_after_seconds).encode("ascii"))],
                )
                return

        try:
            body = await self._read_body(receive)
        except RequestBodyTooLarge as error:
            await self._send_json(
                send,
                413,
                {"status": "failed", "error": str(error)},
            )
            return
        if is_stream:
            await self._handle_sse_turn(session_id, body, receive, send)
            return
        result = await asyncio.to_thread(self._http.handle_turn, session_id, body)
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
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        if self._a2a_server is None:
            await self._send_json(send, 404, {"status": "failed", "error": "not found"})
            return
        try:
            body = await self._read_body(receive)
        except RequestBodyTooLarge as error:
            await self._send_json(
                send,
                413,
                {"status": "failed", "error": str(error)},
            )
            return
        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as error:
            payload = {"error": str(error)}
        if not isinstance(payload, dict):
            payload = {"error": "payload must be an object"}
        await self._send_json(
            send,
            200,
            self._a2a_server.handle_task(payload, headers=headers),
        )

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
        agent: Agent | None = None
        stream_task: asyncio.Task[None] | None = None
        try:
            try:
                request = parse_channel_turn_request(body)
            except ValueError as error:
                await self._send_sse_chunk(send, self._error_sse_chunk(str(error)))
                return

            agent = self._sessions.get_agent(session_id)
            stream_task = asyncio.create_task(
                self._send_agent_sse_stream(agent, request, send),
            )
            done, _pending = await asyncio.wait(
                {stream_task, disconnect_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if disconnect_task in done:
                message = disconnect_task.result()
                if message.get("type") == "http.disconnect":
                    agent.interrupt()
                    stream_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await stream_task
            else:
                if not disconnect_task.done():
                    disconnect_task.cancel()
                await stream_task
        finally:
            if stream_task is not None and not stream_task.done():
                stream_task.cancel()
                with suppress(asyncio.CancelledError):
                    await stream_task
            if not disconnect_task.done():
                disconnect_task.cancel()
            if agent is not None:
                self._sessions.release_agent(session_id, agent)
            await send({"type": "http.response.body", "body": b"", "more_body": False})

    async def _send_agent_sse_stream(
        self,
        agent: Agent,
        request: ChannelTurnRequest,
        send: AsgiSend,
    ) -> None:
        """消费 Agent.async_stream 并写出 SSE chunks。"""

        async_stream = getattr(agent, "async_stream", None)
        if callable(async_stream):
            async for event in async_stream(
                request.message,
                thinking=request.thinking,
                show_thinking=request.show_thinking,
            ):
                await self._send_sse_event(event, request, send)
            return

        events = await asyncio.to_thread(
            lambda: list(
                agent.stream(
                    request.message,
                    thinking=request.thinking,
                    show_thinking=request.show_thinking,
                ),
            ),
        )
        for event in events:
            await self._send_sse_event(event, request, send)

    async def _send_sse_event(
        self,
        event: object,
        request: ChannelTurnRequest,
        send: AsgiSend,
    ) -> None:
        """把 typed stream event 写成一个 SSE chunk。"""

        chunk = event_to_sse(
            event,
            show_thinking=request.show_thinking,
        )
        if chunk is not None:
            await self._send_sse_chunk(send, chunk)

    async def _send_sse_chunk(self, send: AsgiSend, chunk: str) -> None:
        await send(
            {
                "type": "http.response.body",
                "body": chunk.encode("utf-8"),
                "more_body": True,
            },
        )

    def _error_sse_chunk(self, error: str) -> str:
        payload = json.dumps(
            {"type": "error", "status": "failed", "error": error},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return f"event: error\ndata: {payload}\n\n"

    async def _read_body(self, receive: AsgiReceive) -> bytes:
        body = b""
        while True:
            message = await receive()
            if message.get("type") == "http.disconnect":
                return body
            chunk = message.get("body", b"")
            if isinstance(chunk, bytes):
                if len(body) + len(chunk) > self._max_body_bytes:
                    raise RequestBodyTooLarge("request body too large")
                body += chunk
            if not bool(message.get("more_body", False)):
                return body

    async def _handle_lifespan(self, receive: AsgiReceive, send: AsgiSend) -> None:
        """处理 ASGI lifespan startup/shutdown。"""

        while True:
            message = await receive()
            message_type = message.get("type")
            if message_type == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message_type == "lifespan.shutdown":
                try:
                    for handler in self._shutdown_handlers:
                        handler()
                    shutdown = getattr(self._sessions, "shutdown", None)
                    if callable(shutdown):
                        shutdown()
                except Exception as error:
                    await send(
                        {
                            "type": "lifespan.shutdown.failed",
                            "message": str(error),
                        },
                    )
                    return
                await send({"type": "lifespan.shutdown.complete"})
                return
            else:
                return

    async def _handle_ready(self, send: AsgiSend) -> None:
        checks: dict[str, str] = {}
        ready = True
        for name, check in {**self._health_checks, **self._readiness_checks}.items():
            try:
                result = check()
                if isinstance(result, dict):
                    status = result.get("status")
                    ok = status in {"ok", "ready", True}
                else:
                    ok = bool(result)
            except Exception:
                ok = False
            checks[name] = "ok" if ok else "failed"
            ready = ready and ok
        await self._send_json(
            send,
            200 if ready else 503,
            {"status": "ready" if ready else "not_ready", "checks": checks},
        )

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
        headers: list[tuple[bytes, bytes]] | None = None,
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
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    *(headers or []),
                ],
            },
        )
        await send({"type": "http.response.body", "body": body, "more_body": False})
