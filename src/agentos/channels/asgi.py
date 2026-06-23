from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
import json
from typing import Any
from urllib.parse import parse_qs
from uuid import uuid4

from agentos.channels.a2a_server import A2AServerAdapter
from agentos.channels.a2a import (
    A2AAgentCard,
    A2AInboundAuthError,
    A2AInboundAuthPolicy,
    AllowAllA2AInboundAuthPolicy,
    RejectAllA2AInboundAuthPolicy,
    a2a_card_to_dict,
)
from agentos.channels.a2a_operations import (
    A2AOperationServer,
    a2a_operation_response_from_dict,
    a2a_push_notification_config_from_dict,
    a2a_task_to_dict,
    a2a_task_subscription_event_to_dict,
)
from agentos.channels.auth import (
    AllowAllChannelAuthPolicy,
    ChannelAuthContext,
    ChannelAuthError,
    ChannelAuthPolicy,
    RejectAllChannelAuthPolicy,
)
from agentos.channels.http import HttpAgentChannel
from agentos.channels.rate_limit import RateLimiter
from agentos.channels.session import AgentSessionProvider
from agentos.channels.sse_buffer import InMemorySseEventBuffer, SseEventBuffer
from agentos.channels.sse_turn_control import (
    SseTurnAlreadyActiveError,
    SseTurnControlStore,
)
from agentos.channels.sse_turns import SseTurnEntry
from agentos.channels.types import ChannelTurnRequest, parse_channel_turn_request
from agentos.multi.serializers import team_ui_event_to_dict
from agentos.multi.team import TeamUiStreamStore
from agentos.persistence import BackendUnavailableError
from agentos.runtime.stream_events import TurnStreamCompleted
from agentos.runtime.stream_serializers import event_to_sse
from agentos.channels.durable_session import SessionLeaseError


AsgiReceive = Callable[[], Awaitable[dict[str, object]]]
AsgiSend = Callable[[dict[str, Any]], Awaitable[None]]


class TeamUiAuthPolicy:
    """Resource authorization boundary for team UI replay/follow endpoints."""

    def authorize_team_ui(
        self,
        headers: Mapping[str, str],
        *,
        team_id: str,
        stream: bool,
    ) -> None:
        """Raise ChannelAuthError when the caller cannot read team UI events."""


class AllowAllTeamUiAuthPolicy:
    """Local/dev team UI authorization policy."""

    def authorize_team_ui(
        self,
        headers: Mapping[str, str],
        *,
        team_id: str,
        stream: bool,
    ) -> None:
        """Allow all team UI event reads."""


class RejectAllTeamUiAuthPolicy:
    """Fail-closed team UI authorization policy."""

    def __init__(
        self,
        reason: str = "team ui authorization policy required",
    ) -> None:
        self.reason = reason

    def authorize_team_ui(
        self,
        headers: Mapping[str, str],
        *,
        team_id: str,
        stream: bool,
    ) -> None:
        """Reject every team UI read until a resource policy is configured."""

        raise ChannelAuthError(self.reason)


class RequestBodyTooLarge(ValueError):
    """ASGI request body 超过本应用允许的上限。"""


class AsgiAgentApp:
    """最小 ASGI channel app，不依赖具体 Web framework。"""

    def __init__(
        self,
        *,
        sessions: AgentSessionProvider,
        auth_policy: ChannelAuthPolicy | None = None,
        a2a_auth_policy: A2AInboundAuthPolicy | None = None,
        a2a_server: A2AServerAdapter | None = None,
        a2a_operations: A2AOperationServer | None = None,
        max_body_bytes: int = 1_048_576,
        readiness_checks: Mapping[str, Callable[[], object]] | None = None,
        health_checks: Mapping[str, Callable[[], object]] | None = None,
        rate_limiter: RateLimiter | None = None,
        shutdown_handlers: list[Callable[[], object]] | None = None,
        a2a_agent_card: A2AAgentCard | None = None,
        sse_heartbeat_interval_seconds: float | None = 15.0,
        sse_event_buffer: SseEventBuffer | None = None,
        sse_resume_grace_seconds: float = 30.0,
        sse_terminal_retention_seconds: float = 60.0,
        sse_turn_control: SseTurnControlStore | None = None,
        sse_turn_control_owner_id: str | None = None,
        sse_turn_control_ttl_seconds: float = 60.0,
        sse_turn_control_poll_interval_seconds: float | None = None,
        session_lease_heartbeat_interval_seconds: float | None = None,
        team_ui_stream: TeamUiStreamStore | None = None,
        team_ui_auth_policy: TeamUiAuthPolicy | None = None,
        team_ui_event_default_limit: int = 256,
        team_ui_stream_poll_interval_seconds: float = 1.0,
        team_ui_stream_idle_timeout_seconds: float | None = 60.0,
        a2a_task_stream_poll_interval_seconds: float = 1.0,
        a2a_task_stream_idle_timeout_seconds: float | None = 60.0,
        expose_internal_errors: bool = False,
    ) -> None:
        """创建 ASGI app。"""

        self._sessions = sessions
        self._auth_policy = auth_policy or RejectAllChannelAuthPolicy()
        self._a2a_auth_policy = a2a_auth_policy or RejectAllA2AInboundAuthPolicy()
        self._expose_internal_errors = expose_internal_errors
        self._http = HttpAgentChannel(
            sessions,
            expose_internal_errors=expose_internal_errors,
        )
        self._a2a_server = a2a_server
        self._a2a_operations = a2a_operations
        self._a2a_agent_card = a2a_agent_card
        self._max_body_bytes = max_body_bytes
        self._readiness_checks = dict(readiness_checks or {})
        self._health_checks = dict(health_checks or {})
        self._rate_limiter = rate_limiter
        self._shutdown_handlers = list(shutdown_handlers or [])
        self._sse_heartbeat_interval_seconds = sse_heartbeat_interval_seconds
        self._sse_event_buffer = sse_event_buffer or InMemorySseEventBuffer()
        self._sse_resume_grace_seconds = sse_resume_grace_seconds
        self._sse_terminal_retention_seconds = sse_terminal_retention_seconds
        self._sse_turn_control = sse_turn_control
        self._sse_turn_control_owner_id = sse_turn_control_owner_id or f"node-{uuid4().hex}"
        self._sse_turn_control_ttl_seconds = sse_turn_control_ttl_seconds
        self._sse_turn_control_poll_interval_seconds = (
            sse_turn_control_poll_interval_seconds
        )
        self._session_lease_heartbeat_interval_seconds = (
            session_lease_heartbeat_interval_seconds
        )
        self._sse_turns_by_session: dict[str, SseTurnEntry] = {}
        self._sse_turns_by_id: dict[str, SseTurnEntry] = {}
        self._sse_turn_lock = asyncio.Lock()
        self._team_ui_stream = team_ui_stream
        self._team_ui_auth_policy = (
            team_ui_auth_policy or RejectAllTeamUiAuthPolicy()
        )
        self._team_ui_event_default_limit = team_ui_event_default_limit
        self._team_ui_stream_poll_interval_seconds = (
            team_ui_stream_poll_interval_seconds
        )
        self._team_ui_stream_idle_timeout_seconds = (
            team_ui_stream_idle_timeout_seconds
        )
        self._a2a_task_stream_poll_interval_seconds = (
            a2a_task_stream_poll_interval_seconds
        )
        self._a2a_task_stream_idle_timeout_seconds = (
            a2a_task_stream_idle_timeout_seconds
        )

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

        method = str(scope.get("method", "GET")).upper()
        path = str(scope.get("path", "/"))

        if method == "GET" and path in {"/health", "/v1/health"}:
            if not await self._authorize_channel(
                scope,
                send,
                operation="health",
            ):
                return
            await self._send_json(send, 200, {"status": "ok"})
            return
        if method == "GET" and path in {
            "/.well-known/agent-card.json",
            "/a2a/agent-card",
        }:
            if self._a2a_agent_card is None:
                await self._send_json(
                    send,
                    404,
                    {"status": "failed", "error": "not found"},
                )
                return
            await self._send_json(send, 200, a2a_card_to_dict(self._a2a_agent_card))
            return
        if method == "GET" and path in {"/ready", "/v1/ready"}:
            if not await self._authorize_channel(
                scope,
                send,
                operation="readiness",
            ):
                return
            await self._handle_ready(send)
            return
        if method == "GET" and path == "/a2a/health":
            if self._a2a_server is None:
                await self._send_json(send, 404, {"status": "failed", "error": "not found"})
                return
            await self._send_json(send, 200, self._a2a_server.handle_health())
            return
        if method == "POST" and path == "/a2a/tasks":
            if not await self._authorize_a2a(scope, send):
                return
            await self._handle_a2a_task(receive, send, headers=self._headers(scope))
            return
        if method == "POST" and path == "/a2a/message:send":
            if not await self._authorize_a2a(scope, send):
                return
            await self._handle_a2a_operation(receive, send, headers=self._headers(scope))
            return
        if method == "POST" and path == "/a2a/message:stream":
            if not await self._authorize_a2a(scope, send):
                return
            await self._handle_a2a_message_stream(
                scope,
                receive,
                send,
                headers=self._headers(scope),
            )
            return
        lifecycle_task_id, lifecycle_action = self._match_a2a_task_lifecycle(
            method,
            path,
        )
        if lifecycle_task_id is not None:
            if not await self._authorize_a2a(scope, send):
                return
            if lifecycle_action == "subscribe":
                await self._handle_a2a_task_subscribe(
                    lifecycle_task_id,
                    scope,
                    receive,
                    send,
                )
            else:
                await self._handle_a2a_task_lifecycle(
                    lifecycle_task_id,
                    lifecycle_action,
                    send,
                    headers=self._headers(scope),
                )
            return

        push_task_id, push_action, push_config_id = (
            self._match_a2a_push_notification_config(method, path)
        )
        if push_task_id is not None:
            if not await self._authorize_a2a(scope, send):
                return
            await self._handle_a2a_push_notification_config(
                push_task_id,
                push_action,
                push_config_id,
                receive,
                send,
                headers=self._headers(scope),
            )
            return

        team_ui_team_id = self._match_team_ui_events_stream_path(method, path)
        if team_ui_team_id is not None:
            if not await self._authorize_channel(
                scope,
                send,
                operation="team_ui_events_stream",
                resource_type="team",
                resource_id=team_ui_team_id,
            ):
                return
            await self._handle_team_ui_events_stream(
                team_ui_team_id,
                scope,
                receive,
                send,
            )
            return

        team_ui_team_id = self._match_team_ui_events_path(method, path)
        if team_ui_team_id is not None:
            if not await self._authorize_channel(
                scope,
                send,
                operation="team_ui_events",
                resource_type="team",
                resource_id=team_ui_team_id,
            ):
                return
            await self._handle_team_ui_events(team_ui_team_id, scope, send)
            return

        interrupt_session_id = self._match_interrupt_path(method, path)
        if interrupt_session_id is not None:
            if not await self._authorize_channel(
                scope,
                send,
                operation="session_interrupt",
                session_id=interrupt_session_id,
                resource_type="session",
                resource_id=interrupt_session_id,
            ):
                return
            await self._handle_interrupt(interrupt_session_id, send)
            return

        session_id, is_stream = self._match_turn_path(method, path)
        if session_id is None:
            await self._send_json(send, 404, {"status": "failed", "error": "not found"})
            return
        if not await self._authorize_channel(
            scope,
            send,
            operation="session_stream_turn" if is_stream else "session_turn",
            session_id=session_id,
            resource_type="session",
            resource_id=session_id,
        ):
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
        await self._handle_json_turn(session_id, body, send)

    async def _authorize_channel(
        self,
        scope: Mapping[str, object],
        send: AsgiSend,
        *,
        operation: str,
        session_id: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
    ) -> bool:
        method = str(scope.get("method", "GET")).upper()
        path = str(scope.get("path", "/"))
        context = ChannelAuthContext(
            operation=operation,
            method=method,
            path=path,
            session_id=session_id,
            resource_type=resource_type,
            resource_id=resource_id,
        )
        headers = self._headers(scope)
        try:
            authorize_channel = getattr(
                self._auth_policy,
                "authorize_channel",
                None,
            )
            if callable(authorize_channel):
                authorize_channel(headers, context=context)
            else:
                self._auth_policy.authorize(headers)
        except ChannelAuthError as error:
            await self._send_json(
                send,
                401,
                {
                    "status": "failed",
                    "error": self._authorization_error_message(error),
                },
            )
            return False
        return True

    async def _handle_json_turn(
        self,
        session_id: str,
        body: bytes,
        send: AsgiSend,
    ) -> None:
        async_get_agent = getattr(self._sessions, "async_get_agent", None)
        async_release_agent = getattr(self._sessions, "async_release_agent", None)
        if callable(async_get_agent) and callable(async_release_agent):
            try:
                request = parse_channel_turn_request(body)
            except ValueError as error:
                await self._send_json(
                    send,
                    400,
                    {
                        "session_id": session_id,
                        "status": "failed",
                        "error": str(error),
                    },
                )
                return
            try:
                agent = await async_get_agent(session_id)
            except (BackendUnavailableError, SessionLeaseError) as error:
                await self._send_json(
                    send,
                    self._session_acquisition_status_code(error),
                    self._session_acquisition_error_payload(session_id, error),
                )
                return
            try:
                heartbeat_task = self._start_json_session_lease_heartbeat(session_id)
                result = await agent.async_run(
                    request.message,
                    thinking=request.thinking,
                    show_thinking=request.show_thinking,
                )
            except asyncio.CancelledError:
                await self._stop_json_session_lease_heartbeat(heartbeat_task)
                with suppress(Exception):
                    await self._abandon_agent_for_session(session_id, agent)
                raise
            except Exception as error:
                heartbeat_error: BaseException | None = None
                try:
                    await self._stop_json_session_lease_heartbeat(heartbeat_task)
                except Exception as stop_error:
                    heartbeat_error = stop_error
                try:
                    if heartbeat_error is not None:
                        await self._abandon_agent_for_session(session_id, agent)
                        error = heartbeat_error
                    else:
                        await async_release_agent(session_id, agent)
                except Exception as release_error:
                    error = release_error
                await self._send_json(
                    send,
                    500,
                    {
                        "session_id": session_id,
                        "status": "failed",
                        "content": None,
                        "error": self._public_error_message(error),
                    },
                )
                return
            heartbeat_error: BaseException | None = None
            try:
                try:
                    await self._stop_json_session_lease_heartbeat(heartbeat_task)
                except Exception as error:
                    heartbeat_error = error
                if heartbeat_error is not None:
                    await self._abandon_agent_for_session(session_id, agent)
                else:
                    await async_release_agent(session_id, agent)
            except Exception as error:
                await self._send_json(
                    send,
                    500,
                    {
                        "session_id": session_id,
                        "status": "failed",
                        "content": None,
                        "error": self._public_error_message(error),
                    },
                )
                return
            if heartbeat_error is not None:
                await self._send_json(
                    send,
                    500,
                    {
                        "session_id": session_id,
                        "status": "failed",
                        "content": None,
                        "error": self._public_error_message(heartbeat_error),
                    },
                )
                return
            await self._send_json(
                send,
                200,
                {
                    "session_id": session_id,
                    "status": "completed",
                    "content": result.content,
                    "error": None,
                },
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

    async def _authorize_a2a(
        self,
        scope: Mapping[str, object],
        send: AsgiSend,
    ) -> bool:
        try:
            self._a2a_auth_policy.authorize(self._headers(scope))
        except A2AInboundAuthError:
            await self._send_json(
                send,
                401,
                {"status": "failed", "error": "unauthorized peer"},
            )
            return False
        return True

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

    async def _handle_a2a_operation(
        self,
        receive: AsgiReceive,
        send: AsgiSend,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        if self._a2a_operations is None:
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
            self._a2a_operations.handle_message_send(payload, headers=headers),
        )

    async def _handle_a2a_message_stream(
        self,
        scope: Mapping[str, object],
        receive: AsgiReceive,
        send: AsgiSend,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        if self._a2a_operations is None:
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
        response_payload = self._a2a_operations.handle_message_stream(
            payload,
            headers=headers,
        )
        response = a2a_operation_response_from_dict(response_payload)
        if response.error is not None or response.task is None:
            await self._send_json(send, 200, response_payload)
            return

        initial_chunk = self._a2a_task_sse_chunk(response.task)
        lifecycle = self._a2a_operations.task_lifecycle
        if lifecycle is None or response.task.state in {
            "completed",
            "canceled",
            "failed",
            "rejected",
        }:
            await self._start_sse_response(send)
            await self._send_sse_chunk(send, initial_chunk)
            await send({"type": "http.response.body", "body": b"", "more_body": False})
            return

        try:
            after_version = self._a2a_task_stream_cursor(scope)
        except ValueError as error:
            await self._send_json(
                send,
                400,
                {"status": "failed", "error": str(error)},
            )
            return
        await self._stream_a2a_task_updates(
            response.task.task_id,
            after_version=after_version,
            headers=headers,
            receive=receive,
            send=send,
            initial_chunks=(initial_chunk,),
        )

    async def _handle_a2a_task_lifecycle(
        self,
        task_id: str,
        action: str,
        send: AsgiSend,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        if self._a2a_operations is None:
            await self._send_json(send, 404, {"status": "failed", "error": "not found"})
            return
        if action == "get":
            payload = self._a2a_operations.handle_task_get(task_id, headers=headers)
        else:
            payload = self._a2a_operations.handle_task_cancel(task_id, headers=headers)
        await self._send_json(send, 200, payload)

    async def _handle_a2a_task_subscribe(
        self,
        task_id: str,
        scope: Mapping[str, object],
        receive: AsgiReceive,
        send: AsgiSend,
    ) -> None:
        if self._a2a_operations is None:
            await self._send_json(send, 404, {"status": "failed", "error": "not found"})
            return
        lifecycle = self._a2a_operations.task_lifecycle
        if lifecycle is None:
            await self._send_json(send, 404, {"status": "failed", "error": "not found"})
            return
        try:
            after_version = self._a2a_task_stream_cursor(scope)
        except ValueError as error:
            await self._send_json(
                send,
                400,
                {"status": "failed", "error": str(error)},
            )
            return
        headers = self._headers(scope)
        first_payload = self._a2a_operations.handle_task_resubscribe(
            task_id,
            after_event_id=after_version,
            headers=headers,
        )
        first_response = a2a_operation_response_from_dict(first_payload)
        if first_response.error is not None:
            await self._send_json(send, 200, first_payload)
            return

        await self._stream_a2a_task_updates(
            task_id,
            after_version=after_version,
            headers=headers,
            receive=receive,
            send=send,
            pending_event=first_response.task_event,
        )

    async def _stream_a2a_task_updates(
        self,
        task_id: str,
        *,
        after_version: int | None,
        headers: dict[str, str] | None,
        receive: AsgiReceive,
        send: AsgiSend,
        pending_event: object | None = None,
        initial_chunks: tuple[str, ...] = (),
    ) -> None:
        await self._start_sse_response(send)
        disconnect_task = asyncio.create_task(receive())
        send_lock = asyncio.Lock()
        heartbeat_task: asyncio.Task[None] | None = None
        if (
            self._sse_heartbeat_interval_seconds is not None
            and self._sse_heartbeat_interval_seconds > 0
        ):
            heartbeat_task = asyncio.create_task(
                self._send_sse_heartbeats(send, send_lock),
        )
        idle_started = None
        try:
            for chunk in initial_chunks:
                await self._send_sse_chunk(send, chunk, send_lock)
            while True:
                if pending_event is not None:
                    event = pending_event
                    pending_event = None
                else:
                    payload = self._a2a_operations.handle_task_resubscribe(
                        task_id,
                        after_event_id=after_version,
                        headers=headers,
                    )
                    response = a2a_operation_response_from_dict(payload)
                    if response.error is not None:
                        break
                    event = response.task_event
                if event is not None:
                    idle_started = None
                    await self._send_sse_chunk(
                        send,
                        self._a2a_task_update_sse_chunk(event),
                        send_lock,
                    )
                    after_version = int(event.event_id)
                    if event.final:
                        break
                else:
                    now = asyncio.get_running_loop().time()
                    if idle_started is None:
                        idle_started = now
                    timeout = self._a2a_task_stream_idle_timeout_seconds
                    if timeout is not None and now - idle_started >= timeout:
                        break
                if disconnect_task.done():
                    message = disconnect_task.result()
                    if message.get("type") == "http.disconnect":
                        break
                    disconnect_task = asyncio.create_task(receive())
                await asyncio.sleep(self._a2a_task_stream_poll_interval_seconds)
        finally:
            if heartbeat_task is not None and not heartbeat_task.done():
                heartbeat_task.cancel()
                with suppress(asyncio.CancelledError):
                    await heartbeat_task
            if not disconnect_task.done():
                disconnect_task.cancel()
            await send({"type": "http.response.body", "body": b"", "more_body": False})

    async def _handle_a2a_push_notification_config(
        self,
        task_id: str,
        action: str,
        config_id: str | None,
        receive: AsgiReceive,
        send: AsgiSend,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        if (
            self._a2a_operations is None
            or self._a2a_operations.push_notification_configs is None
        ):
            await self._send_json(send, 404, {"status": "failed", "error": "not found"})
            return
        try:
            if action == "create":
                body = await self._read_body(receive)
                payload = json.loads(body.decode("utf-8"))
                if not isinstance(payload, Mapping):
                    raise ValueError("payload must be an object")
                response = (
                    self._a2a_operations.handle_push_notification_config_create(
                        task_id,
                        a2a_push_notification_config_from_dict(payload),
                        headers=headers,
                    )
                )
            elif action == "get" and config_id is not None:
                response = self._a2a_operations.handle_push_notification_config_get(
                    task_id,
                    config_id,
                    headers=headers,
                )
            elif action == "list":
                response = self._a2a_operations.handle_push_notification_config_list(
                    task_id,
                    headers=headers,
                )
            elif action == "delete" and config_id is not None:
                response = (
                    self._a2a_operations.handle_push_notification_config_delete(
                        task_id,
                        config_id,
                        headers=headers,
                    )
                )
            else:
                await self._send_json(
                    send,
                    404,
                    {"status": "failed", "error": "not found"},
                )
                return
        except RequestBodyTooLarge as error:
            await self._send_json(
                send,
                413,
                {"status": "failed", "error": str(error)},
            )
            return
        except (json.JSONDecodeError, ValueError) as error:
            await self._send_json(
                send,
                400,
                {"status": "failed", "error": str(error)},
            )
            return
        await self._send_json(send, 200, response)

    async def _handle_interrupt(self, session_id: str, send: AsgiSend) -> None:
        async with self._sse_turn_lock:
            entry = self._sse_turns_by_session.get(session_id)
        if entry is not None and not entry.terminal and not entry.closed:
            await self._stop_sse_turn_after_lease_error(
                entry,
                RuntimeError("interrupted"),
            )
            await self._send_json(
                send,
                200,
                {"session_id": session_id, "status": "interrupt_requested"},
            )
            return
        if self._sse_turn_control is not None:
            if self._sse_turn_control.request_interrupt(session_id):
                await self._send_json(
                    send,
                    200,
                    {"session_id": session_id, "status": "interrupt_requested"},
                )
                return
        try:
            agent = await self._get_agent_for_session(session_id)
        except (BackendUnavailableError, SessionLeaseError) as error:
            await self._send_json(
                send,
                self._session_acquisition_status_code(error),
                self._session_acquisition_error_payload(session_id, error),
            )
            return
        try:
            agent.interrupt()
        finally:
            await self._release_agent_for_session(session_id, agent)
        await self._send_json(
            send,
            200,
            {"session_id": session_id, "status": "interrupted"},
        )

    async def _handle_team_ui_events(
        self,
        team_id: str,
        scope: Mapping[str, object],
        send: AsgiSend,
    ) -> None:
        if self._team_ui_stream is None:
            await self._send_json(send, 404, {"status": "failed", "error": "not found"})
            return
        if not await self._authorize_team_ui(team_id, scope, send, stream=False):
            return
        if self._rate_limiter is not None:
            decision = self._rate_limiter.check(f"team:{team_id}:ui-events")
            if not decision.allowed:
                await self._send_json(
                    send,
                    429,
                    {"status": "failed", "error": "rate limit exceeded"},
                    headers=[
                        (
                            b"retry-after",
                            str(decision.retry_after_seconds).encode("ascii"),
                        ),
                    ],
                )
                return
        query = self._query_params(scope)
        try:
            after_event_id = self._optional_non_negative_int(
                query,
                "after_event_id",
            )
        except ValueError:
            await self._send_json(
                send,
                400,
                {"status": "failed", "error": "invalid after_event_id"},
            )
            return
        try:
            limit = self._optional_bounded_int(
                query,
                "limit",
                default=self._team_ui_event_default_limit,
                minimum=1,
                maximum=1000,
            )
        except ValueError:
            await self._send_json(
                send,
                400,
                {"status": "failed", "error": "invalid limit"},
            )
            return
        events = self._team_ui_stream.list_events(
            team_id,
            after_event_id=after_event_id,
        )[:limit]
        await self._send_json(
            send,
            200,
            {
                "team_id": team_id,
                "events": [team_ui_event_to_dict(event) for event in events],
                "next_after_event_id": (
                    events[-1].event_id if events else after_event_id
                ),
            },
        )

    async def _handle_team_ui_events_stream(
        self,
        team_id: str,
        scope: Mapping[str, object],
        receive: AsgiReceive,
        send: AsgiSend,
    ) -> None:
        if self._team_ui_stream is None:
            await self._send_json(send, 404, {"status": "failed", "error": "not found"})
            return
        if not await self._authorize_team_ui(team_id, scope, send, stream=True):
            return
        if self._rate_limiter is not None:
            decision = self._rate_limiter.check(f"team:{team_id}:ui-events:stream")
            if not decision.allowed:
                await self._send_json(
                    send,
                    429,
                    {"status": "failed", "error": "rate limit exceeded"},
                    headers=[
                        (
                            b"retry-after",
                            str(decision.retry_after_seconds).encode("ascii"),
                        ),
                    ],
                )
                return
        try:
            last_event_id = self._team_ui_stream_cursor(scope)
        except ValueError as error:
            await self._send_json(
                send,
                400,
                {"status": "failed", "error": str(error)},
            )
            return

        await self._start_sse_response(send)
        disconnect_task = asyncio.create_task(receive())
        last_sent_event_id = last_event_id
        idle_started = None
        send_lock = asyncio.Lock()
        heartbeat_task: asyncio.Task[None] | None = None
        if (
            self._sse_heartbeat_interval_seconds is not None
            and self._sse_heartbeat_interval_seconds > 0
        ):
            heartbeat_task = asyncio.create_task(
                self._send_sse_heartbeats(send, send_lock),
            )
        try:
            while True:
                events = self._team_ui_stream.list_events(
                    team_id,
                    after_event_id=last_sent_event_id,
                )
                if events:
                    idle_started = None
                    for event in events:
                        await self._send_sse_chunk(
                            send,
                            self._team_ui_event_sse_chunk(event),
                            send_lock,
                        )
                        last_sent_event_id = event.event_id
                else:
                    now = asyncio.get_running_loop().time()
                    if idle_started is None:
                        idle_started = now
                    timeout = self._team_ui_stream_idle_timeout_seconds
                    if timeout is not None and now - idle_started >= timeout:
                        break
                if disconnect_task.done():
                    message = disconnect_task.result()
                    if message.get("type") == "http.disconnect":
                        break
                    disconnect_task = asyncio.create_task(receive())
                await asyncio.sleep(self._team_ui_stream_poll_interval_seconds)
        finally:
            if heartbeat_task is not None and not heartbeat_task.done():
                heartbeat_task.cancel()
                with suppress(asyncio.CancelledError):
                    await heartbeat_task
            if not disconnect_task.done():
                disconnect_task.cancel()
            await send({"type": "http.response.body", "body": b"", "more_body": False})

    async def _authorize_team_ui(
        self,
        team_id: str,
        scope: Mapping[str, object],
        send: AsgiSend,
        *,
        stream: bool,
    ) -> bool:
        try:
            self._team_ui_auth_policy.authorize_team_ui(
                self._headers(scope),
                team_id=team_id,
                stream=stream,
            )
        except ChannelAuthError as error:
            await self._send_json(
                send,
                403,
                {
                    "status": "failed",
                    "error": self._team_ui_authorization_error_message(error),
                },
            )
            return False
        return True

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

        try:
            entry = await self._create_sse_turn_entry(session_id, request)
        except (BackendUnavailableError, SessionLeaseError) as error:
            await self._send_json(
                send,
                self._session_acquisition_status_code(error),
                self._session_acquisition_error_payload(session_id, error),
            )
            return
        if entry is None:
            await self._send_json(
                send,
                409,
                {"status": "failed", "error": "session has active stream turn"},
            )
            return
        await self._read_sse_entry(entry, 0, receive, send)

    async def _get_agent_for_session(self, session_id: str) -> object:
        async_get_agent = getattr(self._sessions, "async_get_agent", None)
        if callable(async_get_agent):
            return await async_get_agent(session_id)
        return self._sessions.get_agent(session_id)

    async def _release_agent_for_session(self, session_id: str, agent: object) -> None:
        async_release_agent = getattr(self._sessions, "async_release_agent", None)
        if callable(async_release_agent):
            await async_release_agent(session_id, agent)
            return
        self._sessions.release_agent(session_id, agent)

    async def _abandon_agent_for_session(self, session_id: str, agent: object) -> None:
        async_abandon_agent = getattr(self._sessions, "async_abandon_agent", None)
        if callable(async_abandon_agent):
            await async_abandon_agent(session_id, agent)
            return
        abandon_agent = getattr(self._sessions, "abandon_agent", None)
        if callable(abandon_agent):
            await asyncio.to_thread(abandon_agent, session_id, agent)
            return
        await self._release_agent_for_session(session_id, agent)

    async def _refresh_agent_for_session(self, session_id: str) -> None:
        async_refresh_agent = getattr(self._sessions, "async_refresh_agent", None)
        if callable(async_refresh_agent):
            await async_refresh_agent(session_id)
            return
        refresh_agent = getattr(self._sessions, "refresh_agent", None)
        if callable(refresh_agent):
            await asyncio.to_thread(refresh_agent, session_id)

    def _session_acquisition_status_code(self, error: BaseException) -> int:
        if isinstance(error, SessionLeaseError):
            return 423
        if isinstance(error, BackendUnavailableError):
            return 503
        return 500

    def _session_acquisition_error_payload(
        self,
        session_id: str,
        error: BaseException,
    ) -> dict[str, object]:
        return {
            "session_id": session_id,
            "status": "failed",
            "content": None,
            "error": self._public_error_message(error),
        }

    def _public_error_message(self, error: BaseException) -> str:
        if self._expose_internal_errors:
            return str(error)
        if isinstance(error, SessionLeaseError):
            return "session unavailable"
        if isinstance(error, BackendUnavailableError):
            return "backend unavailable"
        if str(error) == "interrupted":
            return "interrupted"
        return "internal error"

    def _authorization_error_message(self, error: ChannelAuthError) -> str:
        if self._expose_internal_errors:
            return str(error)
        if str(error) == "channel auth policy required":
            return str(error)
        return "unauthorized"

    def _team_ui_authorization_error_message(self, error: ChannelAuthError) -> str:
        if self._expose_internal_errors:
            return str(error)
        if str(error) == "team ui authorization policy required":
            return str(error)
        return "forbidden"

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
        await self._read_shared_sse_stream(stream_key, last_sequence, receive, send)

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
            if self._sse_turn_control is not None:
                try:
                    self._sse_turn_control.claim_turn(
                        session_id,
                        turn_stream_id=turn_stream_id,
                        owner_id=self._sse_turn_control_owner_id,
                        ttl_seconds=self._sse_turn_control_ttl_seconds,
                    )
                except SseTurnAlreadyActiveError:
                    return None
            try:
                agent = await self._get_agent_for_session(session_id)
            except Exception:
                if self._sse_turn_control is not None:
                    self._sse_turn_control.release_turn(session_id, turn_stream_id)
                raise
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
            entry.lease_heartbeat_task = self._start_session_lease_heartbeat(entry)
            entry.turn_control_task = self._start_sse_turn_control_monitor(entry)
            return entry

    def _next_sse_turn_id(self) -> str:
        return f"turn_{uuid4().hex}"

    async def _run_sse_turn(self, entry: SseTurnEntry) -> None:
        completed_event: TurnStreamCompleted | None = None
        terminal_error: BaseException | None = None
        try:
            completed_event = await self._append_agent_sse_stream(entry)
        except asyncio.CancelledError:
            if entry.lease_error is None:
                raise
            terminal_error = entry.lease_error
        except Exception as error:
            lease_error = entry.lease_error
            if lease_error is not None:
                terminal_error = lease_error
            else:
                terminal_error = error
        try:
            await self._stop_sse_turn_control_monitor(entry)
            await self._stop_session_lease_heartbeat(entry)
            await self._release_sse_entry(entry, save=entry.lease_error is None)
        except Exception as error:
            if terminal_error is None:
                terminal_error = error
            else:
                terminal_error = RuntimeError(
                    f"{terminal_error}; release failed: {error}",
                )
        if terminal_error is not None:
            await self._append_sse_chunk(
                entry,
                self._error_sse_chunk(self._public_error_message(terminal_error)),
            )
        elif completed_event is not None:
            await self._append_sse_event(entry, completed_event)
        async with entry.lock:
            if entry.closed:
                return
            entry.terminal = True
        await self._sse_event_buffer.mark_terminal(entry.stream_key)
        self._schedule_sse_gc(entry)

    async def _stop_sse_turn_after_lease_error(
        self,
        entry: SseTurnEntry,
        error: BaseException,
    ) -> None:
        async with entry.lock:
            if entry.terminal or entry.closed:
                return
            entry.lease_error = error
        entry.agent.interrupt()
        task = entry.runner_task
        if task is not None and not task.done():
            task.cancel()

    def _start_session_lease_heartbeat(
        self,
        entry: SseTurnEntry,
    ) -> asyncio.Task[None] | None:
        interval = self._session_lease_heartbeat_interval_seconds
        if interval is None or interval <= 0:
            return None
        return asyncio.create_task(self._refresh_session_lease_until_terminal(entry))

    def _start_json_session_lease_heartbeat(
        self,
        session_id: str,
    ) -> asyncio.Task[None] | None:
        interval = self._session_lease_heartbeat_interval_seconds
        if interval is None or interval <= 0:
            return None
        return asyncio.create_task(
            self._refresh_json_session_lease_until_stopped(session_id),
        )

    async def _stop_json_session_lease_heartbeat(
        self,
        task: asyncio.Task[None] | None,
    ) -> None:
        if task is None:
            return
        if not task.done():
            task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        if task.cancelled():
            return
        exception = task.exception()
        if exception is not None:
            raise exception

    def _start_sse_turn_control_monitor(
        self,
        entry: SseTurnEntry,
    ) -> asyncio.Task[None] | None:
        if self._sse_turn_control is None:
            return None
        interval = self._sse_turn_control_poll_interval_seconds
        if interval is None or interval <= 0:
            return None
        return asyncio.create_task(self._poll_sse_turn_control_until_terminal(entry))

    async def _stop_sse_turn_control_monitor(self, entry: SseTurnEntry) -> None:
        task = entry.turn_control_task
        entry.turn_control_task = None
        if task is None or task.done():
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _stop_session_lease_heartbeat(self, entry: SseTurnEntry) -> None:
        task = entry.lease_heartbeat_task
        entry.lease_heartbeat_task = None
        if task is None or task.done():
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _refresh_session_lease_until_terminal(
        self,
        entry: SseTurnEntry,
    ) -> None:
        assert self._session_lease_heartbeat_interval_seconds is not None
        while True:
            await asyncio.sleep(self._session_lease_heartbeat_interval_seconds)
            async with entry.lock:
                if entry.terminal or entry.closed:
                    return
            try:
                await self._refresh_agent_for_session(entry.session_id)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                await self._stop_sse_turn_after_lease_error(entry, error)
                return

    async def _refresh_json_session_lease_until_stopped(
        self,
        session_id: str,
    ) -> None:
        assert self._session_lease_heartbeat_interval_seconds is not None
        while True:
            await asyncio.sleep(self._session_lease_heartbeat_interval_seconds)
            await self._refresh_agent_for_session(session_id)

    async def _poll_sse_turn_control_until_terminal(
        self,
        entry: SseTurnEntry,
    ) -> None:
        assert self._sse_turn_control is not None
        assert self._sse_turn_control_poll_interval_seconds is not None
        while True:
            await asyncio.sleep(self._sse_turn_control_poll_interval_seconds)
            async with entry.lock:
                if entry.terminal or entry.closed:
                    return
            try:
                state = self._sse_turn_control.get_turn(entry.session_id)
                if state is None or state.turn_stream_id != entry.turn_stream_id:
                    continue
                self._sse_turn_control.refresh_turn(
                    entry.session_id,
                    entry.turn_stream_id,
                    self._sse_turn_control_ttl_seconds,
                )
                if state.interrupt_requested:
                    await self._stop_sse_turn_after_lease_error(
                        entry,
                        RuntimeError("interrupted"),
                    )
                    return
            except asyncio.CancelledError:
                raise
            except Exception as error:
                await self._stop_sse_turn_after_lease_error(entry, error)
                return

    async def _append_agent_sse_stream(
        self,
        entry: SseTurnEntry,
    ) -> TurnStreamCompleted | None:
        """消费 Agent stream 并写入 SSE buffer。"""

        request = entry.request
        completed_event: TurnStreamCompleted | None = None
        async_stream = getattr(entry.agent, "async_stream", None)
        if callable(async_stream):
            async for event in async_stream(
                request.message,
                thinking=request.thinking,
                show_thinking=request.show_thinking,
            ):
                await self._raise_if_sse_turn_interrupted(entry)
                if isinstance(event, TurnStreamCompleted):
                    completed_event = event
                    continue
                await self._append_sse_event(entry, event)
            return completed_event

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
            await self._raise_if_sse_turn_interrupted(entry)
            if isinstance(event, TurnStreamCompleted):
                completed_event = event
                continue
            await self._append_sse_event(entry, event)
        return completed_event

    async def _raise_if_sse_turn_interrupted(self, entry: SseTurnEntry) -> None:
        if entry.lease_error is not None:
            raise entry.lease_error
        if self._sse_turn_control is None:
            return
        state = self._sse_turn_control.get_turn(entry.session_id)
        if (
            state is not None
            and state.turn_stream_id == entry.turn_stream_id
            and state.interrupt_requested
        ):
            error = RuntimeError("interrupted")
            await self._stop_sse_turn_after_lease_error(entry, error)
            raise error

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
        await self._send_sse_stream_events(
            entry.stream_key,
            last_sequence,
            send,
            send_lock,
        )

    async def _send_sse_stream_events(
        self,
        stream_key: str,
        last_sequence: int,
        send: AsgiSend,
        send_lock: asyncio.Lock,
    ) -> None:
        for sequence, chunk in await self._sse_event_buffer.replay_since(
            stream_key,
            last_sequence,
        ):
            last_sequence = sequence
            await self._send_sse_chunk(send, chunk, send_lock)
        async for sequence, chunk in self._sse_event_buffer.follow(
            stream_key,
            last_sequence,
        ):
            last_sequence = sequence
            await self._send_sse_chunk(send, chunk, send_lock)

    async def _read_shared_sse_stream(
        self,
        stream_key: str,
        last_sequence: int,
        receive: AsgiReceive,
        send: AsgiSend,
    ) -> None:
        window = await self._sse_event_buffer.replay_window(stream_key)
        if not window.exists:
            await self._start_sse_response(send)
            await self._send_sse_chunk(send, self._error_sse_chunk("stream turn not found"))
            await send({"type": "http.response.body", "body": b"", "more_body": False})
            return
        if window.has_gap_after(last_sequence):
            await self._start_sse_response(send)
            await self._send_sse_chunk(
                send,
                self._error_sse_chunk("SSE replay gap: requested events are no longer retained"),
            )
            await send({"type": "http.response.body", "body": b"", "more_body": False})
            return
        await self._start_sse_response(send)
        disconnect_task = asyncio.create_task(receive())
        send_lock = asyncio.Lock()
        reader_task = asyncio.create_task(
            self._send_sse_stream_events(
                stream_key,
                last_sequence,
                send,
                send_lock,
            ),
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
            if not reader_task.done():
                reader_task.cancel()
                with suppress(asyncio.CancelledError):
                    await reader_task
            if heartbeat_task is not None and not heartbeat_task.done():
                heartbeat_task.cancel()
                with suppress(asyncio.CancelledError):
                    await heartbeat_task
            if not disconnect_task.done():
                disconnect_task.cancel()
            await send({"type": "http.response.body", "body": b"", "more_body": False})

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
        await self._stop_sse_turn_control_monitor(entry)
        await self._release_sse_entry(entry)

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
        await self._release_sse_entry(entry)

    async def _forget_sse_entry(self, entry: SseTurnEntry) -> None:
        async with self._sse_turn_lock:
            if self._sse_turns_by_session.get(entry.session_id) is entry:
                del self._sse_turns_by_session[entry.session_id]
            if self._sse_turns_by_id.get(entry.turn_stream_id) is entry:
                del self._sse_turns_by_id[entry.turn_stream_id]

    async def _release_sse_entry(
        self,
        entry: SseTurnEntry,
        *,
        save: bool = True,
    ) -> None:
        if entry.released:
            return
        if self._sse_turn_control is not None:
            self._sse_turn_control.release_turn(entry.session_id, entry.turn_stream_id)
        if save:
            await self._release_agent_for_session(entry.session_id, entry.agent)
        else:
            await self._abandon_agent_for_session(entry.session_id, entry.agent)
        entry.released = True

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

    def _team_ui_event_sse_chunk(self, event: object) -> str:
        event_id = getattr(event, "event_id")
        payload = json.dumps(
            team_ui_event_to_dict(event),  # type: ignore[arg-type]
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return f"id: {event_id}\nevent: team_ui_event\ndata: {payload}\n\n"

    def _a2a_task_sse_chunk(self, task: object) -> str:
        task_id = getattr(task, "task_id")
        payload = json.dumps(
            {"task": a2a_task_to_dict(task)},  # type: ignore[arg-type]
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return f"id: {task_id}\nevent: task\ndata: {payload}\n\n"

    def _a2a_task_update_sse_chunk(self, event: object) -> str:
        event_id = getattr(event, "event_id")
        payload = json.dumps(
            a2a_task_subscription_event_to_dict(event),  # type: ignore[arg-type]
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return f"id: {event_id}\nevent: task_status_update\ndata: {payload}\n\n"

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

    def _match_a2a_task_lifecycle(
        self,
        method: str,
        path: str,
    ) -> tuple[str | None, str]:
        parts = path.strip("/").split("/")
        if len(parts) != 3 or parts[:2] != ["a2a", "tasks"]:
            return None, ""
        value = parts[2]
        if method == "GET" and value:
            return value, "get"
        if method == "POST" and value.endswith(":cancel"):
            task_id = value.removesuffix(":cancel")
            if task_id:
                return task_id, "cancel"
        if method == "POST" and value.endswith(":subscribe"):
            task_id = value.removesuffix(":subscribe")
            if task_id:
                return task_id, "subscribe"
        return None, ""

    def _match_a2a_push_notification_config(
        self,
        method: str,
        path: str,
    ) -> tuple[str | None, str, str | None]:
        parts = path.strip("/").split("/")
        if (
            len(parts) == 4
            and parts[:2] == ["a2a", "tasks"]
            and parts[3] == "pushNotificationConfigs"
            and parts[2]
        ):
            if method == "POST":
                return parts[2], "create", None
            if method == "GET":
                return parts[2], "list", None
        if (
            len(parts) == 5
            and parts[:2] == ["a2a", "tasks"]
            and parts[3] == "pushNotificationConfigs"
            and parts[2]
            and parts[4]
        ):
            if method == "GET":
                return parts[2], "get", parts[4]
            if method == "DELETE":
                return parts[2], "delete", parts[4]
        return None, "", None

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

    def _match_team_ui_events_path(self, method: str, path: str) -> str | None:
        if method != "GET":
            return None
        parts = path.strip("/").split("/")
        if (
            len(parts) == 4
            and parts[:2] == ["v1", "teams"]
            and parts[3] == "ui-events"
            and parts[2]
        ):
            return parts[2]
        return None

    def _match_team_ui_events_stream_path(
        self,
        method: str,
        path: str,
    ) -> str | None:
        if method != "GET":
            return None
        parts = path.strip("/").split("/")
        if (
            len(parts) == 5
            and parts[:2] == ["v1", "teams"]
            and parts[3:] == ["ui-events", "stream"]
            and parts[2]
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

    def _query_params(self, scope: Mapping[str, object]) -> dict[str, list[str]]:
        raw_query = scope.get("query_string", b"")
        if isinstance(raw_query, bytes):
            return parse_qs(raw_query.decode("utf-8"), keep_blank_values=True)
        if isinstance(raw_query, str):
            return parse_qs(raw_query, keep_blank_values=True)
        return {}

    def _optional_non_negative_int(
        self,
        query: Mapping[str, list[str]],
        name: str,
    ) -> int | None:
        values = query.get(name)
        if not values or values[0] == "":
            return None
        value = int(values[0])
        if value < 0:
            raise ValueError(name)
        return value

    def _optional_bounded_int(
        self,
        query: Mapping[str, list[str]],
        name: str,
        *,
        default: int,
        minimum: int,
        maximum: int,
    ) -> int:
        values = query.get(name)
        if not values or values[0] == "":
            value = default
        else:
            value = int(values[0])
        if value < minimum or value > maximum:
            raise ValueError(name)
        return value

    def _team_ui_stream_cursor(self, scope: Mapping[str, object]) -> int | None:
        headers = self._headers(scope)
        last_event_id = headers.get("last-event-id")
        if last_event_id is not None:
            try:
                parsed = int(last_event_id)
            except ValueError as exc:
                raise ValueError("invalid Last-Event-ID") from exc
            if parsed < 0:
                raise ValueError("invalid Last-Event-ID")
            return parsed
        try:
            return self._optional_non_negative_int(
                self._query_params(scope),
                "after_event_id",
            )
        except ValueError as exc:
            raise ValueError("invalid after_event_id") from exc

    def _a2a_task_stream_cursor(self, scope: Mapping[str, object]) -> int | None:
        headers = self._headers(scope)
        last_event_id = headers.get("last-event-id")
        if last_event_id is not None:
            try:
                parsed = int(last_event_id)
            except ValueError as exc:
                raise ValueError("invalid Last-Event-ID") from exc
            if parsed < 0:
                raise ValueError("invalid Last-Event-ID")
            return parsed
        try:
            return self._optional_non_negative_int(
                self._query_params(scope),
                "after_event_id",
            )
        except ValueError as exc:
            raise ValueError("invalid after_event_id") from exc

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
