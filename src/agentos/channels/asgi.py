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
from agentos.channels.sse_buffer import InMemorySseEventBuffer, SseEventBuffer
from agentos.channels.sse_turns import SseTurnEntry
from agentos.channels.types import ChannelTurnRequest, parse_channel_turn_request
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
        sse_heartbeat_interval_seconds: float | None = 15.0,
        sse_event_buffer: SseEventBuffer | None = None,
        sse_resume_grace_seconds: float = 30.0,
        sse_terminal_retention_seconds: float = 60.0,
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
        self._sse_heartbeat_interval_seconds = sse_heartbeat_interval_seconds
        self._sse_event_buffer = sse_event_buffer or InMemorySseEventBuffer()
        self._sse_resume_grace_seconds = sse_resume_grace_seconds
        self._sse_terminal_retention_seconds = sse_terminal_retention_seconds
        self._sse_turns_by_session: dict[str, SseTurnEntry] = {}
        self._sse_turns_by_id: dict[str, SseTurnEntry] = {}
        self._sse_turn_lock = asyncio.Lock()
        self._next_sse_turn_number = 1

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

        interrupt_session_id = self._match_interrupt_path(method, path)
        if interrupt_session_id is not None:
            await self._handle_interrupt(interrupt_session_id, send)
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
            await self._handle_sse_turn(
                session_id,
                body,
                receive,
                send,
                headers=self._headers(scope),
            )
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

    async def _handle_interrupt(self, session_id: str, send: AsgiSend) -> None:
        agent = self._sessions.get_agent(session_id)
        try:
            agent.interrupt()
        finally:
            self._sessions.release_agent(session_id, agent)
        await self._send_json(
            send,
            200,
            {"session_id": session_id, "status": "interrupted"},
        )

    async def _handle_sse_turn(
        self,
        session_id: str,
        body: bytes,
        receive: AsgiReceive,
        send: AsgiSend,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        last_event_id = (headers or {}).get("last-event-id")
        if last_event_id:
            parsed = self._parse_sse_event_id(last_event_id)
            if parsed is None:
                await self._send_json(
                    send,
                    400,
                    {"status": "failed", "error": "invalid Last-Event-ID"},
                )
                return
            turn_stream_id, last_sequence = parsed
            await self._handle_sse_resume(
                session_id,
                turn_stream_id,
                last_sequence,
                receive,
                send,
            )
            return

        try:
            request = parse_channel_turn_request(body)
        except ValueError as error:
            await self._start_sse_response(send)
            await self._send_sse_chunk(send, self._error_sse_chunk(str(error)))
            await send({"type": "http.response.body", "body": b"", "more_body": False})
            return

        entry = await self._create_sse_turn_entry(session_id, request)
        if entry is None:
            await self._send_json(
                send,
                409,
                {"status": "failed", "error": "session has active stream turn"},
            )
            return
        await self._read_sse_entry(entry, 0, receive, send)

    async def _handle_sse_resume(
        self,
        session_id: str,
        turn_stream_id: str,
        last_sequence: int,
        receive: AsgiReceive,
        send: AsgiSend,
    ) -> None:
        async with self._sse_turn_lock:
            entry = self._sse_turns_by_id.get(turn_stream_id)
        if entry is not None:
            if entry.session_id != session_id:
                await self._send_json(
                    send,
                    404,
                    {"status": "failed", "error": "stream turn not found"},
                )
                return
            await self._read_sse_entry(entry, last_sequence, receive, send)
            return

        stream_key = f"{session_id}:{turn_stream_id}"
        await self._start_sse_response(send)
        for _sequence, chunk in await self._sse_event_buffer.replay_since(
            stream_key,
            last_sequence,
        ):
            await self._send_sse_chunk(send, chunk)
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    async def _create_sse_turn_entry(
        self,
        session_id: str,
        request: ChannelTurnRequest,
    ) -> SseTurnEntry | None:
        async with self._sse_turn_lock:
            existing = self._sse_turns_by_session.get(session_id)
            if existing is not None and not existing.terminal:
                return None
            turn_stream_id = self._next_sse_turn_id()
            agent = self._sessions.get_agent(session_id)
            entry = SseTurnEntry(
                session_id=session_id,
                turn_stream_id=turn_stream_id,
                stream_key=f"{session_id}:{turn_stream_id}",
                agent=agent,
                request=request,
            )
            self._sse_turns_by_session[session_id] = entry
            self._sse_turns_by_id[turn_stream_id] = entry
            entry.runner_task = asyncio.create_task(self._run_sse_turn(entry))
            return entry

    def _next_sse_turn_id(self) -> str:
        turn_stream_id = f"turn_{self._next_sse_turn_number}"
        self._next_sse_turn_number += 1
        return turn_stream_id

    async def _run_sse_turn(self, entry: SseTurnEntry) -> None:
        try:
            await self._append_agent_sse_stream(entry)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            await self._append_sse_chunk(entry, self._error_sse_chunk(str(error)))
        finally:
            async with entry.lock:
                if entry.closed:
                    return
                entry.terminal = True
            await self._sse_event_buffer.mark_terminal(entry.stream_key)
            self._schedule_sse_gc(entry)

    async def _append_agent_sse_stream(
        self,
        entry: SseTurnEntry,
    ) -> None:
        """消费 Agent stream 并写入 SSE buffer。"""

        request = entry.request
        async_stream = getattr(entry.agent, "async_stream", None)
        if callable(async_stream):
            async for event in async_stream(
                request.message,
                thinking=request.thinking,
                show_thinking=request.show_thinking,
            ):
                await self._append_sse_event(entry, event)
            return

        events = await asyncio.to_thread(
            lambda: list(
                entry.agent.stream(
                    request.message,
                    thinking=request.thinking,
                    show_thinking=request.show_thinking,
                ),
            ),
        )
        for event in events:
            await self._append_sse_event(entry, event)

    async def _append_sse_event(
        self,
        entry: SseTurnEntry,
        event: object,
    ) -> None:
        """把 typed stream event 写成 SSE chunk 并追加到 buffer。"""

        chunk = event_to_sse(
            event,
            show_thinking=entry.request.show_thinking,
        )
        if chunk is not None:
            await self._append_sse_chunk(entry, chunk)

    async def _append_sse_chunk(self, entry: SseTurnEntry, chunk: str) -> None:
        sequence = entry.next_sequence
        entry.next_sequence += 1
        await self._sse_event_buffer.append(
            entry.stream_key,
            sequence,
            f"id: {entry.turn_stream_id}:{sequence}\n{chunk}",
        )

    async def _read_sse_entry(
        self,
        entry: SseTurnEntry,
        last_sequence: int,
        receive: AsgiReceive,
        send: AsgiSend,
    ) -> None:
        await self._open_sse_reader(entry)
        await self._start_sse_response(send)
        disconnect_task = asyncio.create_task(receive())
        send_lock = asyncio.Lock()
        reader_task = asyncio.create_task(
            self._send_buffered_sse_events(entry, last_sequence, send, send_lock),
        )
        heartbeat_task: asyncio.Task[None] | None = None
        if (
            self._sse_heartbeat_interval_seconds is not None
            and self._sse_heartbeat_interval_seconds > 0
        ):
            heartbeat_task = asyncio.create_task(
                self._send_sse_heartbeats(send, send_lock),
            )
        try:
            done, _pending = await asyncio.wait(
                {reader_task, disconnect_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if disconnect_task in done:
                message = disconnect_task.result()
                if message.get("type") == "http.disconnect":
                    reader_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await reader_task
                else:
                    await reader_task
            else:
                if not disconnect_task.done():
                    disconnect_task.cancel()
                await reader_task
        finally:
            if reader_task is not None and not reader_task.done():
                reader_task.cancel()
                with suppress(asyncio.CancelledError):
                    await reader_task
            if heartbeat_task is not None and not heartbeat_task.done():
                heartbeat_task.cancel()
                with suppress(asyncio.CancelledError):
                    await heartbeat_task
            if not disconnect_task.done():
                disconnect_task.cancel()
            await self._close_sse_reader(entry)
            await send({"type": "http.response.body", "body": b"", "more_body": False})

    async def _send_buffered_sse_events(
        self,
        entry: SseTurnEntry,
        last_sequence: int,
        send: AsgiSend,
        send_lock: asyncio.Lock,
    ) -> None:
        for sequence, chunk in await self._sse_event_buffer.replay_since(
            entry.stream_key,
            last_sequence,
        ):
            last_sequence = sequence
            await self._send_sse_chunk(send, chunk, send_lock)
        async for sequence, chunk in self._sse_event_buffer.follow(
            entry.stream_key,
            last_sequence,
        ):
            last_sequence = sequence
            await self._send_sse_chunk(send, chunk, send_lock)

    async def _open_sse_reader(self, entry: SseTurnEntry) -> None:
        async with entry.lock:
            entry.active_readers += 1
            if entry.grace_task is not None and not entry.grace_task.done():
                entry.grace_task.cancel()
            entry.grace_task = None

    async def _close_sse_reader(self, entry: SseTurnEntry) -> None:
        should_start_grace = False
        async with entry.lock:
            entry.active_readers = max(0, entry.active_readers - 1)
            should_start_grace = (
                entry.active_readers == 0
                and not entry.terminal
                and not entry.closed
            )
        if should_start_grace:
            self._schedule_sse_grace(entry)

    def _schedule_sse_grace(self, entry: SseTurnEntry) -> None:
        if entry.grace_task is not None and not entry.grace_task.done():
            entry.grace_task.cancel()
        entry.grace_task = asyncio.create_task(self._expire_sse_grace(entry))

    async def _expire_sse_grace(self, entry: SseTurnEntry) -> None:
        await asyncio.sleep(self._sse_resume_grace_seconds)
        async with entry.lock:
            if entry.active_readers > 0 or entry.terminal or entry.closed:
                return
            entry.closed = True
        entry.agent.interrupt()
        if entry.runner_task is not None and not entry.runner_task.done():
            entry.runner_task.cancel()
        await self._sse_event_buffer.drop(entry.stream_key)
        await self._forget_sse_entry(entry)
        self._release_sse_entry(entry)

    def _schedule_sse_gc(self, entry: SseTurnEntry) -> None:
        if entry.gc_handle is not None and not entry.gc_handle.cancelled():
            entry.gc_handle.cancel()
        if entry.gc_task is not None and not entry.gc_task.done():
            entry.gc_task.cancel()
        if self._sse_terminal_retention_seconds <= 0:
            entry.gc_task = asyncio.create_task(self._gc_sse_entry(entry))
            return
        loop = asyncio.get_running_loop()
        entry.gc_handle = loop.call_later(
            self._sse_terminal_retention_seconds,
            self._start_sse_gc,
            entry,
        )

    def _start_sse_gc(self, entry: SseTurnEntry) -> None:
        if entry.closed:
            return
        entry.gc_task = asyncio.create_task(self._gc_sse_entry(entry))

    async def _gc_sse_entry(self, entry: SseTurnEntry) -> None:
        await self._sse_event_buffer.drop(entry.stream_key)
        await self._forget_sse_entry(entry)
        self._release_sse_entry(entry)

    async def _forget_sse_entry(self, entry: SseTurnEntry) -> None:
        async with self._sse_turn_lock:
            if self._sse_turns_by_session.get(entry.session_id) is entry:
                del self._sse_turns_by_session[entry.session_id]
            if self._sse_turns_by_id.get(entry.turn_stream_id) is entry:
                del self._sse_turns_by_id[entry.turn_stream_id]

    def _release_sse_entry(self, entry: SseTurnEntry) -> None:
        if entry.released:
            return
        entry.released = True
        self._sessions.release_agent(entry.session_id, entry.agent)

    async def _start_sse_response(self, send: AsgiSend) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/event-stream; charset=utf-8")],
            },
        )

    def _parse_sse_event_id(self, value: str) -> tuple[str, int] | None:
        turn_stream_id, separator, sequence_text = value.rpartition(":")
        if not separator or not turn_stream_id:
            return None
        try:
            sequence = int(sequence_text)
        except ValueError:
            return None
        if sequence < 0:
            return None
        return turn_stream_id, sequence

    async def _send_sse_heartbeats(
        self,
        send: AsgiSend,
        send_lock: asyncio.Lock,
    ) -> None:
        """周期性发送 SSE comment，避免代理空闲超时。"""

        assert self._sse_heartbeat_interval_seconds is not None
        while True:
            await asyncio.sleep(self._sse_heartbeat_interval_seconds)
            await self._send_sse_chunk(send, ": heartbeat\n\n", send_lock)

    async def _send_sse_chunk(
        self,
        send: AsgiSend,
        chunk: str,
        send_lock: asyncio.Lock | None = None,
    ) -> None:
        if send_lock is not None:
            async with send_lock:
                await self._send_sse_chunk(send, chunk)
            return
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

    def _match_interrupt_path(self, method: str, path: str) -> str | None:
        if method != "POST":
            return None
        parts = path.strip("/").split("/")
        if (
            len(parts) == 4
            and parts[:2] == ["v1", "sessions"]
            and parts[3] == "interrupt"
        ):
            return parts[2]
        return None

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
