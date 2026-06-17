from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Any, cast

from agentos.channels.a2a import AllowAllA2AInboundAuthPolicy
from agentos.channels.a2a_server import A2AServerAdapter
from agentos.channels.asgi import AllowAllTeamUiAuthPolicy
from agentos.channels.auth import AllowAllChannelAuthPolicy, ChannelAuthError
from agentos.channels.session import InMemoryAgentSessionProvider
from agentos.multi import TaskRecord, TaskRequest, TaskResult, TaskTable
from agentos.multi.team import InMemoryTeamUiStreamStore
from agentos.runtime import Agent, AgentResult
from agentos.runtime.stream_events import AssistantContentDelta, TurnStreamCompleted
from tests.multi.helpers import build_agent_with_response


class StaticRunner:
    def run_task(self, request: TaskRequest) -> TaskResult:
        return TaskResult(
            task_id=request.task_id,
            status="completed",
            summary="a2a done",
        )


class FailingA2ATaskRunner:
    def run_task(self, request: TaskRequest) -> TaskResult:
        raise RuntimeError("database password=secret-token failed")


class HeaderRecordingA2AServer:
    def __init__(self) -> None:
        self.headers: dict[str, str] | None = None

    def handle_task(
        self,
        payload: dict[str, object],
        headers: dict[str, str] | None = None,
    ) -> dict[str, object]:
        self.headers = headers
        return {
            "task_id": str(payload["task_id"]),
            "status": "completed",
            "summary": "a2a done",
            "artifacts": {},
            "error": None,
            "elapsed_seconds": 0,
        }

    def handle_health(self) -> dict[str, object]:
        return {"status": "ok"}


class DenyAuth:
    def authorize(self, headers: Mapping[str, str]) -> None:
        raise ChannelAuthError("denied")


class RecordingLegacyAuth:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def authorize(self, headers: Mapping[str, str]) -> None:
        self.calls.append(dict(headers))


class RecordingResourceAwareAuth:
    def __init__(self) -> None:
        self.legacy_calls: list[dict[str, str]] = []
        self.calls: list[dict[str, object]] = []

    def authorize(self, headers: Mapping[str, str]) -> None:
        self.legacy_calls.append(dict(headers))

    def authorize_channel(self, headers: Mapping[str, str], *, context: object) -> None:
        self.calls.append(
            {
                "headers": dict(headers),
                "operation": getattr(context, "operation"),
                "method": getattr(context, "method"),
                "path": getattr(context, "path"),
                "session_id": getattr(context, "session_id"),
                "resource_type": getattr(context, "resource_type"),
                "resource_id": getattr(context, "resource_id"),
            },
        )


class DenyTeamUiAccess:
    def authorize_team_ui(
        self,
        headers: Mapping[str, str],
        *,
        team_id: str,
        stream: bool,
    ) -> None:
        raise ChannelAuthError(f"team denied: {team_id}")


class InterruptRecordingAgent:
    def __init__(self) -> None:
        self.interrupt_calls = 0

    def run(
        self,
        user_message: str,
        *,
        thinking: bool = False,
        show_thinking: bool = False,
    ) -> AgentResult:
        return AgentResult(content="unused")

    def stream(
        self,
        user_message: str,
        *,
        thinking: bool = False,
        show_thinking: bool = False,
    ):
        yield AssistantContentDelta(index=0, text="first")
        yield TurnStreamCompleted(content="first")

    def interrupt(self) -> None:
        self.interrupt_calls += 1


class FailingRunAgent(InterruptRecordingAgent):
    def run(
        self,
        user_message: str,
        *,
        thinking: bool = False,
        show_thinking: bool = False,
    ) -> AgentResult:
        raise RuntimeError("provider token=secret-token unavailable")


class BlockingAsyncStreamAgent:
    def __init__(self) -> None:
        self.interrupt_calls = 0
        self.started = asyncio.Event()

    async def async_stream(
        self,
        user_message: str,
        *,
        thinking: bool = False,
        show_thinking: bool = False,
    ):
        yield AssistantContentDelta(index=1, text="first")
        self.started.set()
        await asyncio.Event().wait()

    def interrupt(self) -> None:
        self.interrupt_calls += 1


class NonCachingProvider:
    def __init__(self, *agents: InterruptRecordingAgent) -> None:
        self._agents = list(agents)
        self.get_calls = 0
        self.released: list[InterruptRecordingAgent] = []

    def get_agent(self, session_id: str) -> Agent:
        self.get_calls += 1
        if self._agents:
            return cast(Agent, self._agents.pop(0))
        raise AssertionError("unexpected second get_agent call")

    def release_agent(self, session_id: str, agent: Agent) -> None:
        self.released.append(cast(InterruptRecordingAgent, agent))


class ResumableAsyncStreamAgent:
    def __init__(self) -> None:
        self.interrupt_calls = 0
        self.continue_stream = asyncio.Event()

    async def async_stream(
        self,
        user_message: str,
        *,
        thinking: bool = False,
        show_thinking: bool = False,
    ):
        yield AssistantContentDelta(index=0, text="first")
        await self.continue_stream.wait()
        yield AssistantContentDelta(index=1, text="second")
        yield TurnStreamCompleted(content="firstsecond")

    def interrupt(self) -> None:
        self.interrupt_calls += 1


async def call_asgi(
    app: object,
    *,
    method: str,
    path: str,
    body: bytes = b"",
    headers: list[tuple[bytes, bytes]] | None = None,
    query_string: bytes = b"",
    scope_type: str = "http",
    receive_after_body: list[dict[str, object]] | None = None,
) -> list[dict[str, Any]]:
    messages = (
        list(receive_after_body or [])
        if scope_type == "lifespan"
        else [
            {"type": "http.request", "body": body, "more_body": False},
            *(receive_after_body or []),
        ]
    )
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, object]:
        if messages:
            return messages.pop(0)
        await asyncio.Future()
        raise AssertionError("unreachable")

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await app(  # type: ignore[misc]
        {
            "type": scope_type,
            "method": method,
            "path": path,
            "query_string": query_string,
            "headers": headers or [],
        },
        receive,
        send,
    )
    return sent


def response_body(sent: list[dict[str, Any]]) -> bytes:
    return b"".join(
        message.get("body", b"")
        for message in sent
        if message["type"] == "http.response.body"
    )


def response_status(sent: list[dict[str, Any]]) -> int:
    for message in sent:
        if message["type"] == "http.response.start":
            return int(message["status"])
    raise AssertionError("missing response start")


def build_app(agent: Agent):
    from agentos.channels.asgi import AsgiAgentApp

    sessions = InMemoryAgentSessionProvider(lambda session_id: agent)
    return AsgiAgentApp(
        sessions=sessions,
        auth_policy=AllowAllChannelAuthPolicy(),
        a2a_server=A2AServerAdapter(StaticRunner()),
    )


def build_local_dev_a2a_app(agent: Agent):
    from agentos.channels.asgi import AsgiAgentApp

    sessions = InMemoryAgentSessionProvider(lambda session_id: agent)
    return AsgiAgentApp(
        sessions=sessions,
        auth_policy=AllowAllChannelAuthPolicy(),
        a2a_auth_policy=AllowAllA2AInboundAuthPolicy(),
        a2a_server=A2AServerAdapter(StaticRunner()),
    )


def test_asgi_app_rejects_oversized_body() -> None:
    from agentos.channels.asgi import AsgiAgentApp

    app = AsgiAgentApp(
        sessions=InMemoryAgentSessionProvider(
            lambda session_id: build_agent_with_response("unused"),
        ),
        auth_policy=AllowAllChannelAuthPolicy(),
        max_body_bytes=8,
    )

    sent = asyncio.run(
        call_asgi(
            app,
            method="POST",
            path="/v1/sessions/session_1/turns",
            body=b'{"message":"too large"}',
        ),
    )

    assert response_status(sent) == 413
    assert json.loads(response_body(sent)) == {
        "status": "failed",
        "error": "request body too large",
    }


def test_asgi_app_requires_explicit_channel_auth_policy_by_default() -> None:
    from agentos.channels.asgi import AsgiAgentApp

    app = AsgiAgentApp(
        sessions=InMemoryAgentSessionProvider(
            lambda session_id: build_agent_with_response("unused"),
        ),
    )

    sent = asyncio.run(
        call_asgi(
            app,
            method="POST",
            path="/v1/sessions/session_1/turns",
            body=b'{"message":"hello"}',
        ),
    )

    assert response_status(sent) == 401
    assert json.loads(response_body(sent)) == {
        "status": "failed",
        "error": "channel auth policy required",
    }


def test_asgi_app_passes_session_context_to_resource_aware_channel_auth() -> None:
    from agentos.channels.asgi import AsgiAgentApp

    auth = RecordingResourceAwareAuth()
    app = AsgiAgentApp(
        sessions=InMemoryAgentSessionProvider(
            lambda session_id: build_agent_with_response("ok"),
        ),
        auth_policy=auth,
    )

    sent = asyncio.run(
        call_asgi(
            app,
            method="POST",
            path="/v1/sessions/session_1/turns",
            body=b'{"message":"hello"}',
            headers=[(b"authorization", b"Bearer token")],
        ),
    )

    assert response_status(sent) == 200
    assert auth.legacy_calls == []
    assert auth.calls == [
        {
            "headers": {"authorization": "Bearer token"},
            "operation": "session_turn",
            "method": "POST",
            "path": "/v1/sessions/session_1/turns",
            "session_id": "session_1",
            "resource_type": "session",
            "resource_id": "session_1",
        },
    ]


def test_asgi_app_keeps_legacy_channel_auth_policy_compatible() -> None:
    from agentos.channels.asgi import AsgiAgentApp

    auth = RecordingLegacyAuth()
    app = AsgiAgentApp(
        sessions=InMemoryAgentSessionProvider(
            lambda session_id: build_agent_with_response("ok"),
        ),
        auth_policy=auth,
    )

    sent = asyncio.run(
        call_asgi(
            app,
            method="POST",
            path="/v1/sessions/session_1/turns",
            body=b'{"message":"hello"}',
            headers=[(b"authorization", b"Bearer token")],
        ),
    )

    assert response_status(sent) == 200
    assert auth.calls == [{"authorization": "Bearer token"}]


def test_asgi_app_passes_stream_and_interrupt_context_to_channel_auth() -> None:
    from agentos.channels.asgi import AsgiAgentApp

    auth = RecordingResourceAwareAuth()
    app = AsgiAgentApp(
        sessions=InMemoryAgentSessionProvider(
            lambda session_id: InterruptRecordingAgent(),
        ),
        auth_policy=auth,
    )

    stream_sent = asyncio.run(
        call_asgi(
            app,
            method="POST",
            path="/v1/sessions/session_1/turns/stream",
            body=b'{"message":"hello"}',
        ),
    )
    interrupt_sent = asyncio.run(
        call_asgi(
            app,
            method="POST",
            path="/v1/sessions/session_1/interrupt",
        ),
    )

    assert response_status(stream_sent) == 200
    assert response_status(interrupt_sent) == 200
    assert auth.legacy_calls == []
    assert [
        {
            "operation": call["operation"],
            "session_id": call["session_id"],
            "resource_type": call["resource_type"],
            "resource_id": call["resource_id"],
        }
        for call in auth.calls
    ] == [
        {
            "operation": "session_stream_turn",
            "session_id": "session_1",
            "resource_type": "session",
            "resource_id": "session_1",
        },
        {
            "operation": "session_interrupt",
            "session_id": "session_1",
            "resource_type": "session",
            "resource_id": "session_1",
        },
    ]


def test_asgi_app_requires_explicit_team_ui_auth_policy_by_default() -> None:
    from agentos.channels.asgi import AsgiAgentApp

    ui_stream = InMemoryTeamUiStreamStore()
    ui_stream.append(
        team_id="team_1",
        kind="message_appended",
        payload={"message_id": "secret_message"},
        created_at=1.0,
    )
    app = AsgiAgentApp(
        sessions=InMemoryAgentSessionProvider(
            lambda session_id: build_agent_with_response("unused"),
        ),
        auth_policy=AllowAllChannelAuthPolicy(),
        team_ui_stream=ui_stream,
    )

    sent = asyncio.run(
        call_asgi(
            app,
            method="GET",
            path="/v1/teams/team_1/ui-events",
        ),
    )

    assert response_status(sent) == 403
    assert json.loads(response_body(sent)) == {
        "status": "failed",
        "error": "team ui authorization policy required",
    }
    assert b"secret_message" not in response_body(sent)


def test_asgi_app_health_route_returns_json() -> None:
    sent = asyncio.run(
        call_asgi(
            build_app(build_agent_with_response("unused")),
            method="GET",
            path="/v1/health",
        ),
    )

    assert response_status(sent) == 200
    assert json.loads(response_body(sent)) == {"status": "ok"}


def test_asgi_app_ready_accepts_a2a_push_worker_deployment_profile() -> None:
    from agentos.channels.a2a_operations import (
        A2APushNotificationConfig,
        A2APushNotificationDaemon,
        A2APushNotificationDeploymentProfile,
        A2APushNotificationDeliveryRecord,
        A2ATask,
        A2ATaskSubscriptionEvent,
    )
    from agentos.channels.asgi import AsgiAgentApp

    class StaticPushWorker:
        def run_pending(
            self,
            *,
            now: float | None = None,
            worker_id: str = "a2a-push-worker",
            limit: int = 10,
        ) -> tuple[A2APushNotificationDeliveryRecord, ...]:
            return (
                A2APushNotificationDeliveryRecord(
                    delivery_id="delivery_1",
                    task_id="task_1",
                    config=A2APushNotificationConfig(
                        config_id="cfg_1",
                        url="https://client.example/webhook",
                    ),
                    event=A2ATaskSubscriptionEvent(
                        event_id="7",
                        task=A2ATask(
                            task_id="task_1",
                            context_id="ctx_1",
                            state="completed",
                        ),
                    ),
                    created_at=1.0,
                    next_run_at=1.0,
                    status="delivered",
                    worker_id=worker_id,
                ),
            )

    daemon = A2APushNotificationDaemon(
        worker=StaticPushWorker(),
        worker_id="push-worker-a",
        clock=lambda: 100.0,
    )
    daemon.run_once()
    profile = A2APushNotificationDeploymentProfile(daemon=daemon)
    app = AsgiAgentApp(
        sessions=InMemoryAgentSessionProvider(
            lambda session_id: build_agent_with_response("unused"),
        ),
        auth_policy=AllowAllChannelAuthPolicy(),
        readiness_checks={profile.probe_name: profile.readiness_check},
    )

    sent = asyncio.run(call_asgi(app, method="GET", path="/v1/ready"))

    assert response_status(sent) == 200
    assert json.loads(response_body(sent)) == {
        "status": "ready",
        "checks": {"a2a_push_worker": "ok"},
    }


def test_asgi_app_routes_json_turn() -> None:
    sent = asyncio.run(
        call_asgi(
            build_app(build_agent_with_response("json done")),
            method="POST",
            path="/v1/sessions/session_1/turns",
            body=b'{"message":"hello"}',
        ),
    )

    assert response_status(sent) == 200
    assert json.loads(response_body(sent))["content"] == "json done"


def test_asgi_app_routes_sse_turn() -> None:
    sent = asyncio.run(
        call_asgi(
            build_app(build_agent_with_response("stream done")),
            method="POST",
            path="/v1/sessions/session_1/turns/stream",
            body=b'{"message":"hello"}',
        ),
    )

    assert response_status(sent) == 200
    assert b"event: content_delta" in response_body(sent)
    assert b"event: done" in response_body(sent)


def test_asgi_app_closes_sse_stream_on_parse_error() -> None:
    sent = asyncio.run(
        call_asgi(
            build_app(build_agent_with_response("unused")),
            method="POST",
            path="/v1/sessions/session_1/turns/stream",
            body=b'{"missing":"message"}',
        ),
    )

    assert response_status(sent) == 200
    assert b"event: error" in response_body(sent)
    assert sent[-1] == {
        "type": "http.response.body",
        "body": b"",
        "more_body": False,
    }


def test_asgi_app_routes_a2a_task() -> None:
    sent = asyncio.run(
        call_asgi(
            build_local_dev_a2a_app(build_agent_with_response("unused")),
            method="POST",
            path="/a2a/tasks",
            body=b'{"task_id":"task_1","instruction":"work"}',
        ),
    )

    assert response_status(sent) == 200
    assert json.loads(response_body(sent))["summary"] == "a2a done"


def test_asgi_app_a2a_task_does_not_disclose_runner_exception_text() -> None:
    from agentos.channels.asgi import AsgiAgentApp

    app = AsgiAgentApp(
        sessions=InMemoryAgentSessionProvider(
            lambda session_id: build_agent_with_response("unused"),
        ),
        a2a_server=A2AServerAdapter(FailingA2ATaskRunner()),
        a2a_auth_policy=AllowAllA2AInboundAuthPolicy(),
    )

    sent = asyncio.run(
        call_asgi(
            app,
            method="POST",
            path="/a2a/tasks",
            body=b'{"task_id":"task_1","instruction":"work"}',
        ),
    )
    body = response_body(sent)

    assert response_status(sent) == 200
    assert b"secret-token" not in body
    assert json.loads(body) == {
        "task_id": "task_1",
        "status": "failed",
        "summary": "task failed",
        "artifacts": {},
        "error": "internal error",
        "elapsed_seconds": 0,
    }


def test_asgi_app_a2a_task_bridge_defaults_to_rejecting_peers() -> None:
    sent = asyncio.run(
        call_asgi(
            build_app(build_agent_with_response("unused")),
            method="POST",
            path="/a2a/tasks",
            body=b'{"task_id":"task_1","instruction":"work"}',
        ),
    )

    assert response_status(sent) == 401
    assert json.loads(response_body(sent)) == {
        "status": "failed",
        "error": "unauthorized peer",
    }


def test_asgi_app_routes_a2a_message_send() -> None:
    from agentos.channels.a2a_operations import (
        A2AOperationServer,
        AgentA2AOperationRunner,
    )
    from agentos.channels.asgi import AsgiAgentApp

    app = AsgiAgentApp(
        sessions=InMemoryAgentSessionProvider(
            lambda session_id: build_agent_with_response("unused"),
        ),
        a2a_operations=A2AOperationServer(
            AgentA2AOperationRunner(build_agent_with_response("operation done")),
            inbound_auth_policy=AllowAllA2AInboundAuthPolicy(),
        ),
        a2a_auth_policy=AllowAllA2AInboundAuthPolicy(),
    )

    sent = asyncio.run(
        call_asgi(
            app,
            method="POST",
            path="/a2a/message:send",
            body=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": "req_1",
                    "method": "SendMessage",
                    "params": {
                        "message": {
                            "role": "user",
                            "parts": [{"kind": "text", "text": "hello"}],
                        },
                    },
                },
            ).encode("utf-8"),
        ),
    )

    body = json.loads(response_body(sent))
    assert response_status(sent) == 200
    assert body["id"] == "req_1"
    assert body["result"]["task"]["status"]["state"] == "completed"
    assert body["result"]["task"]["messages"][-1]["parts"][0]["text"] == (
        "operation done"
    )


def test_asgi_app_returns_404_for_missing_a2a_operations() -> None:
    sent = asyncio.run(
        call_asgi(
            build_local_dev_a2a_app(build_agent_with_response("unused")),
            method="POST",
            path="/a2a/message:send",
            body=b'{"jsonrpc":"2.0","method":"SendMessage","params":{}}',
        ),
    )

    assert response_status(sent) == 404
    assert json.loads(response_body(sent)) == {
        "status": "failed",
        "error": "not found",
    }


def test_asgi_app_routes_a2a_task_get_and_cancel() -> None:
    from agentos.channels.a2a_operations import (
        A2AOperationServer,
        AgentA2AOperationRunner,
        TaskStoreA2ATaskLifecycleRunner,
    )
    from agentos.channels.asgi import AsgiAgentApp

    store = TaskTable()
    store.create(
        TaskRecord(
            task_id="task_1",
            mode="dispatch",
            parent_agent_id="parent",
            target_agent_id="worker",
            request=TaskRequest(task_id="task_1", instruction="do work"),
            status="queued",
            created_at=1.0,
            deadline_at=10.0,
        ),
    )
    app = AsgiAgentApp(
        sessions=InMemoryAgentSessionProvider(
            lambda session_id: build_agent_with_response("unused"),
        ),
        a2a_operations=A2AOperationServer(
            AgentA2AOperationRunner(build_agent_with_response("unused")),
            task_lifecycle=TaskStoreA2ATaskLifecycleRunner(store, clock=lambda: 3.0),
            inbound_auth_policy=AllowAllA2AInboundAuthPolicy(),
        ),
        a2a_auth_policy=AllowAllA2AInboundAuthPolicy(),
    )

    found_sent = asyncio.run(
        call_asgi(
            app,
            method="GET",
            path="/a2a/tasks/task_1",
        ),
    )
    cancelled_sent = asyncio.run(
        call_asgi(
            app,
            method="POST",
            path="/a2a/tasks/task_1:cancel",
            body=b"{}",
        ),
    )

    found = json.loads(response_body(found_sent))
    cancelled = json.loads(response_body(cancelled_sent))
    assert response_status(found_sent) == 200
    assert found["result"]["task"]["status"]["state"] == "submitted"
    assert response_status(cancelled_sent) == 200
    assert cancelled["result"]["task"]["status"]["state"] == "canceled"


def test_asgi_app_subscribes_to_a2a_task_updates() -> None:
    from agentos.channels.a2a_operations import (
        A2AOperationServer,
        AgentA2AOperationRunner,
        TaskStoreA2ATaskLifecycleRunner,
    )
    from agentos.channels.asgi import AsgiAgentApp

    store = TaskTable()
    store.create(
        TaskRecord(
            task_id="task_1",
            mode="dispatch",
            parent_agent_id="parent",
            target_agent_id="worker",
            request=TaskRequest(task_id="task_1", instruction="do work"),
            status="queued",
            created_at=1.0,
            deadline_at=10.0,
        ),
    )
    app = AsgiAgentApp(
        sessions=InMemoryAgentSessionProvider(
            lambda session_id: build_agent_with_response("unused"),
        ),
        a2a_operations=A2AOperationServer(
            AgentA2AOperationRunner(build_agent_with_response("unused")),
            task_lifecycle=TaskStoreA2ATaskLifecycleRunner(store),
            inbound_auth_policy=AllowAllA2AInboundAuthPolicy(),
        ),
        a2a_auth_policy=AllowAllA2AInboundAuthPolicy(),
        a2a_task_stream_idle_timeout_seconds=0.01,
        a2a_task_stream_poll_interval_seconds=0.001,
        sse_heartbeat_interval_seconds=None,
    )

    sent = asyncio.run(
        call_asgi(
            app,
            method="POST",
            path="/a2a/tasks/task_1:subscribe",
        ),
    )

    body = response_body(sent).decode("utf-8")
    assert response_status(sent) == 200
    assert "id: 0\n" in body
    assert "event: task_status_update\n" in body
    assert '"statusUpdate":' in body
    assert '"state":"submitted"' in body


def test_asgi_app_a2a_task_subscribe_resumes_after_last_event_id() -> None:
    from agentos.channels.a2a_operations import (
        A2AOperationServer,
        AgentA2AOperationRunner,
        TaskStoreA2ATaskLifecycleRunner,
    )
    from agentos.channels.asgi import AsgiAgentApp

    store = TaskTable()
    store.create(
        TaskRecord(
            task_id="task_1",
            mode="dispatch",
            parent_agent_id="parent",
            target_agent_id="worker",
            request=TaskRequest(task_id="task_1", instruction="do work"),
            status="queued",
            created_at=1.0,
            deadline_at=10.0,
        ),
    )
    app = AsgiAgentApp(
        sessions=InMemoryAgentSessionProvider(
            lambda session_id: build_agent_with_response("unused"),
        ),
        a2a_operations=A2AOperationServer(
            AgentA2AOperationRunner(build_agent_with_response("unused")),
            task_lifecycle=TaskStoreA2ATaskLifecycleRunner(store),
            inbound_auth_policy=AllowAllA2AInboundAuthPolicy(),
        ),
        a2a_auth_policy=AllowAllA2AInboundAuthPolicy(),
        a2a_task_stream_idle_timeout_seconds=0.05,
        a2a_task_stream_poll_interval_seconds=0.001,
        sse_heartbeat_interval_seconds=None,
    )

    async def run() -> list[dict[str, Any]]:
        async def mark_running_later() -> None:
            await asyncio.sleep(0.005)
            store.mark_running("task_1", now=2.0)

        updater = asyncio.create_task(mark_running_later())
        sent = await call_asgi(
            app,
            method="POST",
            path="/a2a/tasks/task_1:subscribe",
            headers=[(b"last-event-id", b"0")],
        )
        await updater
        return sent

    sent = asyncio.run(run())
    body = response_body(sent).decode("utf-8")

    assert response_status(sent) == 200
    assert '"state":"submitted"' not in body
    assert "id: 1\n" in body
    assert '"state":"working"' in body


def test_asgi_app_a2a_task_subscribe_returns_404_for_missing_operations() -> None:
    sent = asyncio.run(
        call_asgi(
            build_local_dev_a2a_app(build_agent_with_response("unused")),
            method="POST",
            path="/a2a/tasks/task_1:subscribe",
        ),
    )

    assert response_status(sent) == 404
    assert json.loads(response_body(sent)) == {
        "status": "failed",
        "error": "not found",
    }


def test_asgi_app_a2a_task_subscribe_rejects_invalid_last_event_id() -> None:
    from agentos.channels.a2a_operations import (
        A2AOperationServer,
        AgentA2AOperationRunner,
        TaskStoreA2ATaskLifecycleRunner,
    )
    from agentos.channels.asgi import AsgiAgentApp

    app = AsgiAgentApp(
        sessions=InMemoryAgentSessionProvider(
            lambda session_id: build_agent_with_response("unused"),
        ),
        a2a_operations=A2AOperationServer(
            AgentA2AOperationRunner(build_agent_with_response("unused")),
            task_lifecycle=TaskStoreA2ATaskLifecycleRunner(TaskTable()),
        ),
        a2a_auth_policy=AllowAllA2AInboundAuthPolicy(),
    )

    sent = asyncio.run(
        call_asgi(
            app,
            method="POST",
            path="/a2a/tasks/task_1:subscribe",
            headers=[(b"last-event-id", b"bad")],
        ),
    )

    assert response_status(sent) == 400
    assert json.loads(response_body(sent)) == {
        "status": "failed",
        "error": "invalid Last-Event-ID",
    }


def test_asgi_app_a2a_task_subscribe_uses_operation_inbound_auth_boundary() -> None:
    from agentos.channels.a2a import StaticBearerA2AInboundAuthPolicy
    from agentos.channels.a2a_operations import (
        A2AOperationServer,
        AgentA2AOperationRunner,
        TaskStoreA2ATaskLifecycleRunner,
    )
    from agentos.channels.asgi import AsgiAgentApp

    store = TaskTable()
    store.create(
        TaskRecord(
            task_id="task_1",
            mode="dispatch",
            parent_agent_id="parent",
            target_agent_id="worker",
            request=TaskRequest(task_id="task_1", instruction="do work"),
            status="queued",
            created_at=1.0,
            deadline_at=10.0,
        ),
    )
    app = AsgiAgentApp(
        sessions=InMemoryAgentSessionProvider(
            lambda session_id: build_agent_with_response("unused"),
        ),
        a2a_operations=A2AOperationServer(
            AgentA2AOperationRunner(build_agent_with_response("unused")),
            task_lifecycle=TaskStoreA2ATaskLifecycleRunner(store),
            inbound_auth_policy=StaticBearerA2AInboundAuthPolicy("peer-token"),
        ),
        a2a_auth_policy=AllowAllA2AInboundAuthPolicy(),
        a2a_task_stream_idle_timeout_seconds=0.01,
        a2a_task_stream_poll_interval_seconds=0.001,
        sse_heartbeat_interval_seconds=None,
    )

    sent = asyncio.run(
        call_asgi(
            app,
            method="POST",
            path="/a2a/tasks/task_1:subscribe",
        ),
    )

    assert response_status(sent) == 200
    assert json.loads(response_body(sent)) == {
        "jsonrpc": "2.0",
        "error": {
            "code": -32030,
            "message": "unauthorized peer",
        },
    }


def test_asgi_app_a2a_task_subscribe_uses_operation_rate_limit_boundary() -> None:
    from agentos.channels.a2a_operations import (
        A2AOperationServer,
        A2APeerIdResolver,
        AgentA2AOperationRunner,
        PeerKeyA2AOperationRateLimitPolicy,
        TaskStoreA2ATaskLifecycleRunner,
    )
    from agentos.channels.asgi import AsgiAgentApp
    from agentos.channels.rate_limit import RateLimitDecision

    class HeaderPeerResolver:
        def peer_id_for_headers(self, headers: dict[str, str]) -> str | None:
            return headers.get("x-a2a-peer")

    class DenyLimiter:
        def check(self, key: str) -> RateLimitDecision:
            return RateLimitDecision(False, 60)

    store = TaskTable()
    store.create(
        TaskRecord(
            task_id="task_1",
            mode="dispatch",
            parent_agent_id="parent",
            target_agent_id="worker",
            request=TaskRequest(task_id="task_1", instruction="do work"),
            status="queued",
            created_at=1.0,
            deadline_at=10.0,
        ),
    )
    resolver: A2APeerIdResolver = HeaderPeerResolver()
    app = AsgiAgentApp(
        sessions=InMemoryAgentSessionProvider(
            lambda session_id: build_agent_with_response("unused"),
        ),
        a2a_operations=A2AOperationServer(
            AgentA2AOperationRunner(build_agent_with_response("unused")),
            task_lifecycle=TaskStoreA2ATaskLifecycleRunner(store),
            inbound_auth_policy=AllowAllA2AInboundAuthPolicy(),
            rate_limit_policy=PeerKeyA2AOperationRateLimitPolicy(
                peer_id_resolver=resolver,
                rate_limiter=DenyLimiter(),
            ),
        ),
        a2a_auth_policy=AllowAllA2AInboundAuthPolicy(),
        a2a_task_stream_idle_timeout_seconds=0.01,
        a2a_task_stream_poll_interval_seconds=0.001,
        sse_heartbeat_interval_seconds=None,
    )

    sent = asyncio.run(
        call_asgi(
            app,
            method="POST",
            path="/a2a/tasks/task_1:subscribe",
            headers=[(b"x-a2a-peer", b"peer-a")],
        ),
    )

    assert response_status(sent) == 200
    payload = json.loads(response_body(sent))
    assert payload["error"]["code"] == -32029
    assert payload["error"]["message"] == "rate limit exceeded"
    assert payload["error"]["data"]["retryAfterSeconds"] == 60


def test_asgi_app_a2a_message_stream_uses_operation_inbound_auth_boundary() -> None:
    from agentos.channels.a2a import StaticBearerA2AInboundAuthPolicy
    from agentos.channels.a2a_operations import (
        A2AOperationServer,
        AgentA2AOperationRunner,
    )
    from agentos.channels.asgi import AsgiAgentApp

    app = AsgiAgentApp(
        sessions=InMemoryAgentSessionProvider(
            lambda session_id: build_agent_with_response("unused"),
        ),
        a2a_operations=A2AOperationServer(
            AgentA2AOperationRunner(build_agent_with_response("unused")),
            inbound_auth_policy=StaticBearerA2AInboundAuthPolicy("peer-token"),
        ),
        a2a_auth_policy=AllowAllA2AInboundAuthPolicy(),
        sse_heartbeat_interval_seconds=None,
    )

    sent = asyncio.run(
        call_asgi(
            app,
            method="POST",
            path="/a2a/message:stream",
            body=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": "req_stream",
                    "method": "SendStreamingMessage",
                    "params": {
                        "message": {
                            "role": "user",
                            "parts": [{"text": "hello"}],
                        },
                    },
                },
            ).encode("utf-8"),
        ),
    )

    assert response_status(sent) == 200
    assert json.loads(response_body(sent)) == {
        "jsonrpc": "2.0",
        "error": {
            "code": -32030,
            "message": "unauthorized peer",
        },
    }


def test_asgi_app_a2a_message_stream_rejects_send_method_on_stream_path() -> None:
    from agentos.channels.a2a_operations import (
        A2AOperationServer,
        AgentA2AOperationRunner,
    )
    from agentos.channels.asgi import AsgiAgentApp

    app = AsgiAgentApp(
        sessions=InMemoryAgentSessionProvider(
            lambda session_id: build_agent_with_response("unused"),
        ),
        a2a_operations=A2AOperationServer(
            AgentA2AOperationRunner(build_agent_with_response("unused")),
            inbound_auth_policy=AllowAllA2AInboundAuthPolicy(),
        ),
        a2a_auth_policy=AllowAllA2AInboundAuthPolicy(),
        sse_heartbeat_interval_seconds=None,
    )

    sent = asyncio.run(
        call_asgi(
            app,
            method="POST",
            path="/a2a/message:stream",
            body=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": "req_wrong_path",
                    "method": "SendMessage",
                    "params": {
                        "message": {
                            "role": "user",
                            "parts": [{"text": "hello"}],
                        },
                    },
                },
            ).encode("utf-8"),
        ),
    )

    assert response_status(sent) == 200
    payload = json.loads(response_body(sent))
    assert payload["id"] == "req_wrong_path"
    assert payload["error"]["code"] == -32601


def test_asgi_app_a2a_message_stream_emits_initial_task_event() -> None:
    from agentos.channels.a2a_operations import (
        A2AOperationServer,
        AgentA2AOperationRunner,
    )
    from agentos.channels.asgi import AsgiAgentApp

    app = AsgiAgentApp(
        sessions=InMemoryAgentSessionProvider(
            lambda session_id: build_agent_with_response("unused"),
        ),
        a2a_operations=A2AOperationServer(
            AgentA2AOperationRunner(build_agent_with_response("stream done")),
            inbound_auth_policy=AllowAllA2AInboundAuthPolicy(),
        ),
        a2a_auth_policy=AllowAllA2AInboundAuthPolicy(),
        a2a_task_stream_idle_timeout_seconds=0.01,
        a2a_task_stream_poll_interval_seconds=0.001,
        sse_heartbeat_interval_seconds=None,
    )

    sent = asyncio.run(
        call_asgi(
            app,
            method="POST",
            path="/a2a/message:stream",
            body=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": "req_stream",
                    "method": "SendStreamingMessage",
                    "params": {
                        "message": {
                            "role": "user",
                            "parts": [{"text": "hello"}],
                        },
                    },
                },
            ).encode("utf-8"),
        ),
    )

    body = response_body(sent).decode("utf-8")
    assert response_status(sent) == 200
    assert "event: task\n" in body
    assert '"state":"completed"' in body
    assert '"text":"stream done"' in body


def test_asgi_app_a2a_message_stream_follows_task_updates() -> None:
    from agentos.channels.a2a_operations import (
        A2AMessage,
        A2AMessagePart,
        A2AOperationRunner,
        A2AOperationServer,
        A2ATask,
        TaskStoreA2ATaskLifecycleRunner,
    )
    from agentos.channels.asgi import AsgiAgentApp

    class QueuedTaskRunner:
        def __init__(self, store: TaskTable) -> None:
            self.store = store

        def send_message(self, message: A2AMessage) -> A2ATask:
            task_id = message.task_id or "task_stream"
            self.store.create(
                TaskRecord(
                    task_id=task_id,
                    mode="dispatch",
                    parent_agent_id="parent",
                    target_agent_id="worker",
                    request=TaskRequest(task_id=task_id, instruction="do work"),
                    status="queued",
                    created_at=1.0,
                    deadline_at=10.0,
                ),
            )
            return A2ATask(
                task_id=task_id,
                context_id=message.context_id,
                state="submitted",
                messages=(
                    message,
                    A2AMessage(
                        role="agent",
                        parts=(A2AMessagePart.from_text("queued"),),
                        task_id=task_id,
                    ),
                ),
            )

    store = TaskTable()
    runner: A2AOperationRunner = QueuedTaskRunner(store)
    app = AsgiAgentApp(
        sessions=InMemoryAgentSessionProvider(
            lambda session_id: build_agent_with_response("unused"),
        ),
        a2a_operations=A2AOperationServer(
            runner,
            task_lifecycle=TaskStoreA2ATaskLifecycleRunner(store),
            inbound_auth_policy=AllowAllA2AInboundAuthPolicy(),
        ),
        a2a_auth_policy=AllowAllA2AInboundAuthPolicy(),
        a2a_task_stream_idle_timeout_seconds=0.05,
        a2a_task_stream_poll_interval_seconds=0.001,
        sse_heartbeat_interval_seconds=None,
    )

    async def run() -> list[dict[str, Any]]:
        async def complete_later() -> None:
            await asyncio.sleep(0.005)
            store.mark_running("task_stream", now=2.0)
            store.mark_completed(
                "task_stream",
                TaskResult(
                    task_id="task_stream",
                    status="completed",
                    summary="done",
                ),
                now=3.0,
            )

        updater = asyncio.create_task(complete_later())
        sent = await call_asgi(
            app,
            method="POST",
            path="/a2a/message:stream",
            body=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": "req_stream",
                    "method": "SendStreamingMessage",
                    "params": {
                        "message": {
                            "role": "user",
                            "parts": [{"text": "hello"}],
                        },
                    },
                },
            ).encode("utf-8"),
        )
        await updater
        return sent

    sent = asyncio.run(run())
    body = response_body(sent).decode("utf-8")

    assert response_status(sent) == 200
    assert "event: task\n" in body
    assert "event: task_status_update\n" in body
    assert '"state":"submitted"' in body
    assert '"state":"completed"' in body
    assert '"text":"done"' in body


def test_asgi_app_routes_a2a_push_notification_configs() -> None:
    from agentos.channels.a2a_operations import (
        A2AOperationServer,
        AgentA2AOperationRunner,
        InMemoryA2APushNotificationConfigStore,
    )
    from agentos.channels.asgi import AsgiAgentApp

    app = AsgiAgentApp(
        sessions=InMemoryAgentSessionProvider(
            lambda session_id: build_agent_with_response("unused"),
        ),
        a2a_operations=A2AOperationServer(
            AgentA2AOperationRunner(build_agent_with_response("unused")),
            push_notification_configs=InMemoryA2APushNotificationConfigStore(),
            inbound_auth_policy=AllowAllA2AInboundAuthPolicy(),
        ),
        a2a_auth_policy=AllowAllA2AInboundAuthPolicy(),
    )

    created_sent = asyncio.run(
        call_asgi(
            app,
            method="POST",
            path="/a2a/tasks/task_1/pushNotificationConfigs",
            body=json.dumps(
                {
                    "id": "cfg_1",
                    "url": "https://client.example/webhook",
                    "authentication": {
                        "schemes": ["Bearer"],
                        "credentials": "token-1",
                    },
                },
            ).encode("utf-8"),
        ),
    )
    fetched_sent = asyncio.run(
        call_asgi(
            app,
            method="GET",
            path="/a2a/tasks/task_1/pushNotificationConfigs/cfg_1",
        ),
    )
    listed_sent = asyncio.run(
        call_asgi(
            app,
            method="GET",
            path="/a2a/tasks/task_1/pushNotificationConfigs",
        ),
    )
    deleted_sent = asyncio.run(
        call_asgi(
            app,
            method="DELETE",
            path="/a2a/tasks/task_1/pushNotificationConfigs/cfg_1",
        ),
    )

    created = json.loads(response_body(created_sent))
    fetched = json.loads(response_body(fetched_sent))
    listed = json.loads(response_body(listed_sent))
    deleted = json.loads(response_body(deleted_sent))

    assert response_status(created_sent) == 200
    assert created["result"]["pushNotificationConfig"]["id"] == "cfg_1"
    assert response_status(fetched_sent) == 200
    assert fetched["result"]["pushNotificationConfig"]["url"] == (
        "https://client.example/webhook"
    )
    assert response_status(listed_sent) == 200
    assert listed["result"]["pushNotificationConfigs"][0]["id"] == "cfg_1"
    assert response_status(deleted_sent) == 200
    assert deleted["result"] == {}


def test_asgi_app_returns_404_for_missing_a2a_push_notification_store() -> None:
    from agentos.channels.a2a_operations import (
        A2AOperationServer,
        AgentA2AOperationRunner,
    )
    from agentos.channels.asgi import AsgiAgentApp

    app = AsgiAgentApp(
        sessions=InMemoryAgentSessionProvider(
            lambda session_id: build_agent_with_response("unused"),
        ),
        a2a_operations=A2AOperationServer(
            AgentA2AOperationRunner(build_agent_with_response("unused")),
            inbound_auth_policy=AllowAllA2AInboundAuthPolicy(),
        ),
        a2a_auth_policy=AllowAllA2AInboundAuthPolicy(),
    )

    sent = asyncio.run(
        call_asgi(
            app,
            method="POST",
            path="/a2a/tasks/task_1/pushNotificationConfigs",
            body=b'{"url":"https://client.example/webhook"}',
        ),
    )

    assert response_status(sent) == 404
    assert json.loads(response_body(sent)) == {
        "status": "failed",
        "error": "not found",
    }


def test_asgi_app_enforces_a2a_peer_auth_without_blocking_web_turns() -> None:
    from agentos.channels.a2a import StaticBearerA2AInboundAuthPolicy
    from agentos.channels.a2a_operations import (
        A2AOperationServer,
        AgentA2AOperationRunner,
    )
    from agentos.channels.asgi import AsgiAgentApp

    peer_policy = StaticBearerA2AInboundAuthPolicy("peer-token")
    app = AsgiAgentApp(
        sessions=InMemoryAgentSessionProvider(
            lambda session_id: build_agent_with_response("web ok"),
        ),
        auth_policy=AllowAllChannelAuthPolicy(),
        a2a_operations=A2AOperationServer(
            AgentA2AOperationRunner(build_agent_with_response("a2a ok")),
            inbound_auth_policy=peer_policy,
        ),
        a2a_auth_policy=peer_policy,
    )
    a2a_payload = {
        "jsonrpc": "2.0",
        "id": "req_1",
        "method": "SendMessage",
        "params": {
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": "hello"}],
            },
        },
    }

    denied = asyncio.run(
        call_asgi(
            app,
            method="POST",
            path="/a2a/message:send",
            body=json.dumps(a2a_payload).encode("utf-8"),
        ),
    )
    allowed = asyncio.run(
        call_asgi(
            app,
            method="POST",
            path="/a2a/message:send",
            body=json.dumps(a2a_payload).encode("utf-8"),
            headers=[(b"authorization", b"Bearer peer-token")],
        ),
    )
    web_turn = asyncio.run(
        call_asgi(
            app,
            method="POST",
            path="/v1/sessions/session_1/turns",
            body=b'{"message":"hello"}',
        ),
    )

    assert response_status(denied) == 401
    assert json.loads(response_body(denied)) == {
        "status": "failed",
        "error": "unauthorized peer",
    }
    assert response_status(allowed) == 200
    assert json.loads(response_body(allowed))["result"]["task"]["messages"][-1][
        "parts"
    ][0]["text"] == "a2a ok"
    assert response_status(web_turn) == 200
    assert json.loads(response_body(web_turn))["content"] == "web ok"


def test_asgi_app_enforces_a2a_peer_allow_list() -> None:
    from agentos.channels.a2a import PeerAllowListA2AInboundAuthPolicy
    from agentos.channels.a2a_operations import (
        A2AOperationServer,
        AgentA2AOperationRunner,
    )
    from agentos.channels.asgi import AsgiAgentApp

    peer_policy = PeerAllowListA2AInboundAuthPolicy(
        peer_tokens={
            "researcher": "token-researcher",
            "blocked": "token-blocked",
        },
        allowed_peer_ids=("researcher",),
    )
    app = AsgiAgentApp(
        sessions=InMemoryAgentSessionProvider(
            lambda session_id: build_agent_with_response("web ok"),
        ),
        auth_policy=AllowAllChannelAuthPolicy(),
        a2a_operations=A2AOperationServer(
            AgentA2AOperationRunner(build_agent_with_response("a2a ok")),
            inbound_auth_policy=peer_policy,
        ),
        a2a_auth_policy=peer_policy,
    )
    a2a_payload = {
        "jsonrpc": "2.0",
        "id": "req_1",
        "method": "SendMessage",
        "params": {
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": "hello"}],
            },
        },
    }

    denied = asyncio.run(
        call_asgi(
            app,
            method="POST",
            path="/a2a/message:send",
            body=json.dumps(a2a_payload).encode("utf-8"),
            headers=[(b"authorization", b"Bearer token-blocked")],
        ),
    )
    allowed = asyncio.run(
        call_asgi(
            app,
            method="POST",
            path="/a2a/message:send",
            body=json.dumps(a2a_payload).encode("utf-8"),
            headers=[(b"authorization", b"Bearer token-researcher")],
        ),
    )
    web_turn = asyncio.run(
        call_asgi(
            app,
            method="POST",
            path="/v1/sessions/session_1/turns",
            body=b'{"message":"hello"}',
        ),
    )

    assert response_status(denied) == 401
    assert json.loads(response_body(denied)) == {
        "status": "failed",
        "error": "unauthorized peer",
    }
    assert response_status(allowed) == 200
    assert json.loads(response_body(allowed))["result"]["task"]["messages"][-1][
        "parts"
    ][0]["text"] == "a2a ok"
    assert response_status(web_turn) == 200
    assert json.loads(response_body(web_turn))["content"] == "web ok"


def test_asgi_app_replays_team_ui_events_after_cursor() -> None:
    from agentos.channels.asgi import AsgiAgentApp

    ui_stream = InMemoryTeamUiStreamStore()
    ui_stream.append(
        team_id="team_1",
        kind="team_created",
        payload={"team_id": "team_1"},
        created_at=1.0,
    )
    second = ui_stream.append(
        team_id="team_1",
        kind="message_appended",
        payload={"message_id": "message_1"},
        created_at=2.0,
    )
    app = AsgiAgentApp(
        sessions=InMemoryAgentSessionProvider(
            lambda session_id: build_agent_with_response("unused"),
        ),
        auth_policy=AllowAllChannelAuthPolicy(),
        team_ui_stream=ui_stream,
        team_ui_auth_policy=AllowAllTeamUiAuthPolicy(),
    )

    sent = asyncio.run(
        call_asgi(
            app,
            method="GET",
            path="/v1/teams/team_1/ui-events",
            query_string=b"after_event_id=1&limit=10",
        ),
    )

    body = json.loads(response_body(sent))
    assert response_status(sent) == 200
    assert body["team_id"] == "team_1"
    assert body["next_after_event_id"] == second.event_id
    assert [event["kind"] for event in body["events"]] == ["message_appended"]
    assert body["events"][0]["payload"] == {"message_id": "message_1"}


def test_asgi_app_team_ui_replay_uses_resource_authorization_policy() -> None:
    from agentos.channels.asgi import AsgiAgentApp

    ui_stream = InMemoryTeamUiStreamStore()
    ui_stream.append(
        team_id="team_1",
        kind="message_appended",
        payload={"message_id": "secret_message"},
        created_at=1.0,
    )
    app = AsgiAgentApp(
        sessions=InMemoryAgentSessionProvider(
            lambda session_id: build_agent_with_response("unused"),
        ),
        auth_policy=AllowAllChannelAuthPolicy(),
        team_ui_stream=ui_stream,
        team_ui_auth_policy=DenyTeamUiAccess(),
    )

    sent = asyncio.run(
        call_asgi(
            app,
            method="GET",
            path="/v1/teams/team_1/ui-events",
        ),
    )

    assert response_status(sent) == 403
    assert json.loads(response_body(sent)) == {
        "status": "failed",
        "error": "forbidden",
    }
    assert b"secret_message" not in response_body(sent)


def test_asgi_app_returns_404_for_missing_team_ui_stream() -> None:
    sent = asyncio.run(
        call_asgi(
            build_app(build_agent_with_response("unused")),
            method="GET",
            path="/v1/teams/team_1/ui-events",
        ),
    )

    assert response_status(sent) == 404
    assert json.loads(response_body(sent)) == {
        "status": "failed",
        "error": "not found",
    }


def test_asgi_app_rejects_invalid_team_ui_event_cursor_and_limit() -> None:
    from agentos.channels.asgi import AsgiAgentApp

    app = AsgiAgentApp(
        sessions=InMemoryAgentSessionProvider(
            lambda session_id: build_agent_with_response("unused"),
        ),
        auth_policy=AllowAllChannelAuthPolicy(),
        team_ui_stream=InMemoryTeamUiStreamStore(),
        team_ui_auth_policy=AllowAllTeamUiAuthPolicy(),
    )

    bad_cursor = asyncio.run(
        call_asgi(
            app,
            method="GET",
            path="/v1/teams/team_1/ui-events",
            query_string=b"after_event_id=-1",
        ),
    )
    bad_limit = asyncio.run(
        call_asgi(
            app,
            method="GET",
            path="/v1/teams/team_1/ui-events",
            query_string=b"limit=0",
        ),
    )

    assert response_status(bad_cursor) == 400
    assert json.loads(response_body(bad_cursor))["error"] == "invalid after_event_id"
    assert response_status(bad_limit) == 400
    assert json.loads(response_body(bad_limit))["error"] == "invalid limit"


def test_asgi_app_streams_team_ui_events_after_cursor() -> None:
    from agentos.channels.asgi import AsgiAgentApp

    ui_stream = InMemoryTeamUiStreamStore()
    ui_stream.append(
        team_id="team_1",
        kind="team_created",
        payload={"team_id": "team_1"},
        created_at=1.0,
    )
    ui_stream.append(
        team_id="team_1",
        kind="message_appended",
        payload={"message_id": "message_1"},
        created_at=2.0,
    )
    app = AsgiAgentApp(
        sessions=InMemoryAgentSessionProvider(
            lambda session_id: build_agent_with_response("unused"),
        ),
        auth_policy=AllowAllChannelAuthPolicy(),
        team_ui_stream=ui_stream,
        team_ui_auth_policy=AllowAllTeamUiAuthPolicy(),
        team_ui_stream_idle_timeout_seconds=0.01,
        team_ui_stream_poll_interval_seconds=0.001,
        sse_heartbeat_interval_seconds=None,
    )

    sent = asyncio.run(
        call_asgi(
            app,
            method="GET",
            path="/v1/teams/team_1/ui-events/stream",
            query_string=b"after_event_id=1",
        ),
    )

    body = response_body(sent).decode("utf-8")
    assert response_status(sent) == 200
    assert "content-type" in {
        key.decode("latin-1")
        for key, _value in sent[0]["headers"]
    }
    assert "id: 2\n" in body
    assert "event: team_ui_event\n" in body
    assert '"kind":"message_appended"' in body
    assert '"message_id":"message_1"' in body


def test_asgi_app_team_ui_stream_uses_resource_authorization_policy() -> None:
    from agentos.channels.asgi import AsgiAgentApp

    ui_stream = InMemoryTeamUiStreamStore()
    ui_stream.append(
        team_id="team_1",
        kind="message_appended",
        payload={"message_id": "secret_message"},
        created_at=1.0,
    )
    app = AsgiAgentApp(
        sessions=InMemoryAgentSessionProvider(
            lambda session_id: build_agent_with_response("unused"),
        ),
        auth_policy=AllowAllChannelAuthPolicy(),
        team_ui_stream=ui_stream,
        team_ui_auth_policy=DenyTeamUiAccess(),
        sse_heartbeat_interval_seconds=None,
    )

    sent = asyncio.run(
        call_asgi(
            app,
            method="GET",
            path="/v1/teams/team_1/ui-events/stream",
        ),
    )

    assert response_status(sent) == 403
    assert json.loads(response_body(sent)) == {
        "status": "failed",
        "error": "forbidden",
    }
    assert b"secret_message" not in response_body(sent)


def test_asgi_app_team_ui_stream_follows_new_events() -> None:
    from agentos.channels.asgi import AsgiAgentApp

    ui_stream = InMemoryTeamUiStreamStore()
    app = AsgiAgentApp(
        sessions=InMemoryAgentSessionProvider(
            lambda session_id: build_agent_with_response("unused"),
        ),
        auth_policy=AllowAllChannelAuthPolicy(),
        team_ui_stream=ui_stream,
        team_ui_auth_policy=AllowAllTeamUiAuthPolicy(),
        team_ui_stream_idle_timeout_seconds=0.05,
        team_ui_stream_poll_interval_seconds=0.001,
        sse_heartbeat_interval_seconds=None,
    )

    async def run() -> list[dict[str, Any]]:
        async def append_later() -> None:
            await asyncio.sleep(0.005)
            ui_stream.append(
                team_id="team_1",
                kind="message_appended",
                payload={"message_id": "message_live"},
                created_at=3.0,
            )

        append_task = asyncio.create_task(append_later())
        sent = await call_asgi(
            app,
            method="GET",
            path="/v1/teams/team_1/ui-events/stream",
        )
        await append_task
        return sent

    sent = asyncio.run(run())
    body = response_body(sent).decode("utf-8")

    assert response_status(sent) == 200
    assert "id: 1\n" in body
    assert '"message_id":"message_live"' in body


def test_asgi_app_team_ui_stream_resumes_from_last_event_id() -> None:
    from agentos.channels.asgi import AsgiAgentApp

    ui_stream = InMemoryTeamUiStreamStore()
    ui_stream.append(
        team_id="team_1",
        kind="team_created",
        payload={"team_id": "team_1"},
        created_at=1.0,
    )
    ui_stream.append(
        team_id="team_1",
        kind="message_appended",
        payload={"message_id": "message_2"},
        created_at=2.0,
    )
    app = AsgiAgentApp(
        sessions=InMemoryAgentSessionProvider(
            lambda session_id: build_agent_with_response("unused"),
        ),
        auth_policy=AllowAllChannelAuthPolicy(),
        team_ui_stream=ui_stream,
        team_ui_auth_policy=AllowAllTeamUiAuthPolicy(),
        team_ui_stream_idle_timeout_seconds=0.01,
        team_ui_stream_poll_interval_seconds=0.001,
        sse_heartbeat_interval_seconds=None,
    )

    sent = asyncio.run(
        call_asgi(
            app,
            method="GET",
            path="/v1/teams/team_1/ui-events/stream",
            headers=[(b"last-event-id", b"1")],
        ),
    )

    body = response_body(sent).decode("utf-8")
    assert response_status(sent) == 200
    assert '"team_created"' not in body
    assert '"message_2"' in body


def test_asgi_app_team_ui_stream_returns_404_for_missing_store() -> None:
    sent = asyncio.run(
        call_asgi(
            build_app(build_agent_with_response("unused")),
            method="GET",
            path="/v1/teams/team_1/ui-events/stream",
        ),
    )

    assert response_status(sent) == 404
    assert json.loads(response_body(sent)) == {
        "status": "failed",
        "error": "not found",
    }


def test_asgi_app_team_ui_stream_rejects_invalid_last_event_id() -> None:
    from agentos.channels.asgi import AsgiAgentApp

    app = AsgiAgentApp(
        sessions=InMemoryAgentSessionProvider(
            lambda session_id: build_agent_with_response("unused"),
        ),
        auth_policy=AllowAllChannelAuthPolicy(),
        team_ui_stream=InMemoryTeamUiStreamStore(),
        team_ui_auth_policy=AllowAllTeamUiAuthPolicy(),
    )

    sent = asyncio.run(
        call_asgi(
            app,
            method="GET",
            path="/v1/teams/team_1/ui-events/stream",
            headers=[(b"last-event-id", b"bad")],
        ),
    )

    assert response_status(sent) == 400
    assert json.loads(response_body(sent)) == {
        "status": "failed",
        "error": "invalid Last-Event-ID",
    }


def test_asgi_app_returns_404_for_missing_a2a_task_lifecycle() -> None:
    get_sent = asyncio.run(
        call_asgi(
            build_local_dev_a2a_app(build_agent_with_response("unused")),
            method="GET",
            path="/a2a/tasks/task_1",
        ),
    )
    cancel_sent = asyncio.run(
        call_asgi(
            build_local_dev_a2a_app(build_agent_with_response("unused")),
            method="POST",
            path="/a2a/tasks/task_1:cancel",
            body=b"{}",
        ),
    )

    assert response_status(get_sent) == 404
    assert response_status(cancel_sent) == 404


def test_asgi_app_serves_a2a_agent_card() -> None:
    from agentos.channels.a2a import A2AAgentCard, A2AAgentSkill
    from agentos.channels.asgi import AsgiAgentApp

    card = A2AAgentCard(
        name="Research",
        description="Research agent.",
        url="https://agents.example/a2a",
        version="1.0.0",
        skills=(
            A2AAgentSkill(
                id="research",
                name="Research",
                description="Research.",
            ),
        ),
    )
    app = AsgiAgentApp(
        sessions=InMemoryAgentSessionProvider(
            lambda session_id: build_agent_with_response("unused"),
        ),
        a2a_agent_card=card,
    )

    sent = asyncio.run(
        call_asgi(
            app,
            method="GET",
            path="/.well-known/agent-card.json",
        ),
    )

    assert response_status(sent) == 200
    assert json.loads(response_body(sent))["name"] == "Research"


def test_asgi_app_returns_404_for_missing_a2a_agent_card() -> None:
    sent = asyncio.run(
        call_asgi(
            build_app(build_agent_with_response("unused")),
            method="GET",
            path="/.well-known/agent-card.json",
        ),
    )

    assert response_status(sent) == 404


def test_asgi_app_passes_headers_to_a2a_server() -> None:
    from agentos.channels.asgi import AsgiAgentApp

    a2a_server = HeaderRecordingA2AServer()
    app = AsgiAgentApp(
        sessions=InMemoryAgentSessionProvider(
            lambda session_id: build_agent_with_response("unused"),
        ),
        a2a_server=a2a_server,  # type: ignore[arg-type]
        a2a_auth_policy=AllowAllA2AInboundAuthPolicy(),
    )

    sent = asyncio.run(
        call_asgi(
            app,
            method="POST",
            path="/a2a/tasks",
            body=b'{"task_id":"task_1","instruction":"work"}',
            headers=[(b"traceparent", b"00-" + b"1" * 32 + b"-" + b"2" * 16 + b"-01")],
        ),
    )

    assert response_status(sent) == 200
    assert a2a_server.headers is not None
    assert a2a_server.headers["traceparent"].startswith("00-")


def test_asgi_app_returns_404_for_unknown_route() -> None:
    sent = asyncio.run(
        call_asgi(
            build_app(build_agent_with_response("unused")),
            method="GET",
            path="/missing",
        ),
    )

    assert response_status(sent) == 404


def test_asgi_app_returns_401_for_auth_failure() -> None:
    from agentos.channels.asgi import AsgiAgentApp

    app = AsgiAgentApp(
        sessions=InMemoryAgentSessionProvider(
            lambda session_id: build_agent_with_response("unused"),
        ),
        auth_policy=DenyAuth(),
        a2a_server=A2AServerAdapter(StaticRunner()),
    )

    sent = asyncio.run(call_asgi(app, method="GET", path="/v1/health"))

    assert response_status(sent) == 401
    assert json.loads(response_body(sent))["error"] == "unauthorized"


def test_asgi_app_can_expose_auth_error_for_local_debug() -> None:
    from agentos.channels.asgi import AsgiAgentApp

    app = AsgiAgentApp(
        sessions=InMemoryAgentSessionProvider(
            lambda session_id: build_agent_with_response("unused"),
        ),
        auth_policy=DenyAuth(),
        a2a_server=A2AServerAdapter(StaticRunner()),
        expose_internal_errors=True,
    )

    sent = asyncio.run(call_asgi(app, method="GET", path="/v1/health"))

    assert response_status(sent) == 401
    assert json.loads(response_body(sent))["error"] == "denied"


def test_asgi_app_redacts_sync_json_turn_internal_errors_by_default() -> None:
    from agentos.channels.asgi import AsgiAgentApp

    app = AsgiAgentApp(
        sessions=InMemoryAgentSessionProvider(lambda session_id: FailingRunAgent()),
        auth_policy=AllowAllChannelAuthPolicy(),
    )

    sent = asyncio.run(
        call_asgi(
            app,
            method="POST",
            path="/v1/sessions/session_1/turns",
            body=b'{"message":"hello"}',
        ),
    )
    body = response_body(sent)

    assert response_status(sent) == 500
    assert json.loads(body)["error"] == "internal error"
    assert b"secret-token" not in body


def last_sse_id(sent: list[dict[str, Any]]) -> str:
    for line in response_body(sent).decode("utf-8").splitlines():
        if line.startswith("id: "):
            return line.removeprefix("id: ")
    raise AssertionError("missing SSE id")


def test_asgi_app_keeps_sse_turn_alive_during_disconnect_grace() -> None:
    from agentos.channels.asgi import AsgiAgentApp

    stream_agent = BlockingAsyncStreamAgent()
    provider = NonCachingProvider(stream_agent)  # type: ignore[arg-type]
    app = AsgiAgentApp(
        sessions=provider,
        auth_policy=AllowAllChannelAuthPolicy(),
        a2a_server=A2AServerAdapter(StaticRunner()),
        sse_resume_grace_seconds=0.01,
    )

    async def run() -> list[dict[str, Any]]:
        sent = await call_asgi(
            app,
            method="POST",
            path="/v1/sessions/session_1/turns/stream",
            body=b'{"message":"hello"}',
            receive_after_body=[{"type": "http.disconnect"}],
        )
        assert stream_agent.interrupt_calls == 0
        assert provider.released == []
        await asyncio.sleep(0.03)
        return sent

    sent = asyncio.run(run())

    assert response_status(sent) == 200
    assert provider.get_calls == 1
    assert stream_agent.interrupt_calls == 1
    assert provider.released == [stream_agent]


def test_asgi_app_routes_explicit_interrupt_request() -> None:
    from agentos.channels.asgi import AsgiAgentApp

    stream_agent = InterruptRecordingAgent()
    provider = NonCachingProvider(stream_agent)
    app = AsgiAgentApp(
        sessions=provider,
        auth_policy=AllowAllChannelAuthPolicy(),
        a2a_server=A2AServerAdapter(StaticRunner()),
    )

    sent = asyncio.run(
        call_asgi(
            app,
            method="POST",
            path="/v1/sessions/session_1/interrupt",
        ),
    )

    assert response_status(sent) == 200
    assert json.loads(response_body(sent)) == {
        "session_id": "session_1",
        "status": "interrupted",
    }
    assert provider.get_calls == 1
    assert stream_agent.interrupt_calls == 1
    assert provider.released == [stream_agent]


def test_asgi_app_sends_sse_heartbeat_for_long_running_turn() -> None:
    from agentos.channels.asgi import AsgiAgentApp

    stream_agent = BlockingAsyncStreamAgent()
    provider = NonCachingProvider(stream_agent)  # type: ignore[arg-type]
    app = AsgiAgentApp(
        sessions=provider,
        auth_policy=AllowAllChannelAuthPolicy(),
        a2a_server=A2AServerAdapter(StaticRunner()),
        sse_heartbeat_interval_seconds=0.01,
    )

    async def call_with_delayed_disconnect() -> list[dict[str, Any]]:
        sent: list[dict[str, Any]] = []
        messages = [
            {
                "type": "http.request",
                "body": b'{"message":"hello"}',
                "more_body": False,
            },
        ]

        async def receive() -> dict[str, object]:
            if messages:
                return messages.pop(0)
            await asyncio.sleep(0.035)
            return {"type": "http.disconnect"}

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)

        await app(
            {
                "type": "http",
                "method": "POST",
                "path": "/v1/sessions/session_1/turns/stream",
                "headers": [],
            },
            receive,
            send,
        )
        return sent

    sent = asyncio.run(call_with_delayed_disconnect())

    assert response_status(sent) == 200
    body = response_body(sent)
    assert b"event: content_delta" in body
    assert b": heartbeat\n\n" in body
    assert b"id: " in body
    assert b"id: turn_1:2\n: heartbeat" not in body
    assert stream_agent.interrupt_calls == 0


def test_asgi_app_replays_missing_sse_events_after_disconnect() -> None:
    from agentos.channels.asgi import AsgiAgentApp

    stream_agent = ResumableAsyncStreamAgent()
    provider = NonCachingProvider(stream_agent)  # type: ignore[arg-type]
    app = AsgiAgentApp(
        sessions=provider,
        auth_policy=AllowAllChannelAuthPolicy(),
        a2a_server=A2AServerAdapter(StaticRunner()),
        sse_resume_grace_seconds=0.5,
        sse_heartbeat_interval_seconds=None,
    )

    async def run() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        first_sent = await call_asgi(
            app,
            method="POST",
            path="/v1/sessions/session_1/turns/stream",
            body=b'{"message":"hello"}',
            receive_after_body=[{"type": "http.disconnect"}],
        )
        first_id = last_sse_id(first_sent)
        assert stream_agent.interrupt_calls == 0
        stream_agent.continue_stream.set()
        resumed_sent = await call_asgi(
            app,
            method="POST",
            path="/v1/sessions/session_1/turns/stream",
            body=b'{"message":"hello"}',
            headers=[(b"last-event-id", first_id.encode("ascii"))],
        )
        return first_sent, resumed_sent

    first_sent, resumed_sent = asyncio.run(run())
    first_id = last_sse_id(first_sent)

    assert first_id.startswith("turn_")
    assert first_id.endswith(":1")
    resumed_body = response_body(resumed_sent)
    assert b'"text":"first"' not in resumed_body
    assert b'"text":"second"' in resumed_body
    assert b"event: done" in resumed_body
    assert provider.get_calls == 1


def test_asgi_app_follows_shared_sse_buffer_when_resuming_on_another_node() -> None:
    from agentos.channels import InMemorySseEventBuffer
    from agentos.channels.asgi import AsgiAgentApp

    stream_agent = ResumableAsyncStreamAgent()
    provider = NonCachingProvider(stream_agent)  # type: ignore[arg-type]
    shared_buffer = InMemorySseEventBuffer()
    producer_app = AsgiAgentApp(
        sessions=provider,
        auth_policy=AllowAllChannelAuthPolicy(),
        a2a_server=A2AServerAdapter(StaticRunner()),
        sse_event_buffer=shared_buffer,
        sse_resume_grace_seconds=0.5,
        sse_heartbeat_interval_seconds=None,
    )
    follower_app = AsgiAgentApp(
        sessions=InMemoryAgentSessionProvider(
            lambda session_id: build_agent_with_response("unused"),
        ),
        auth_policy=AllowAllChannelAuthPolicy(),
        a2a_server=A2AServerAdapter(StaticRunner()),
        sse_event_buffer=shared_buffer,
        sse_heartbeat_interval_seconds=None,
    )

    async def run() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        first_sent = await call_asgi(
            producer_app,
            method="POST",
            path="/v1/sessions/session_1/turns/stream",
            body=b'{"message":"hello"}',
            receive_after_body=[{"type": "http.disconnect"}],
        )
        first_id = last_sse_id(first_sent)

        async def resume_on_other_node() -> list[dict[str, Any]]:
            return await call_asgi(
                follower_app,
                method="POST",
                path="/v1/sessions/session_1/turns/stream",
                body=b'{"message":"hello"}',
                headers=[(b"last-event-id", first_id.encode("ascii"))],
            )

        follower_task = asyncio.create_task(resume_on_other_node())
        await asyncio.sleep(0.01)
        stream_agent.continue_stream.set()
        return first_sent, await follower_task

    first_sent, resumed_sent = asyncio.run(run())
    first_id = last_sse_id(first_sent)
    resumed_body = response_body(resumed_sent)

    assert first_id.endswith(":1")
    assert response_status(resumed_sent) == 200
    assert b'"text":"first"' not in resumed_body
    assert b'"text":"second"' in resumed_body
    assert b"event: done" in resumed_body


def test_asgi_app_reports_missing_shared_sse_stream_on_cross_node_resume() -> None:
    from agentos.channels import InMemorySseEventBuffer
    from agentos.channels.asgi import AsgiAgentApp

    app = AsgiAgentApp(
        sessions=InMemoryAgentSessionProvider(
            lambda session_id: build_agent_with_response("unused"),
        ),
        auth_policy=AllowAllChannelAuthPolicy(),
        a2a_server=A2AServerAdapter(StaticRunner()),
        sse_event_buffer=InMemorySseEventBuffer(),
        sse_heartbeat_interval_seconds=None,
    )

    sent = asyncio.run(
        call_asgi(
            app,
            method="POST",
            path="/v1/sessions/session_1/turns/stream",
            body=b'{"message":"hello"}',
            headers=[(b"last-event-id", b"turn_missing:1")],
        ),
    )
    body = response_body(sent)

    assert response_status(sent) == 200
    assert b"event: error" in body
    assert b"stream turn not found" in body


def test_asgi_app_reports_shared_sse_replay_gap_on_cross_node_resume() -> None:
    from agentos.channels import InMemorySseEventBuffer
    from agentos.channels.asgi import AsgiAgentApp

    async def build_buffer() -> InMemorySseEventBuffer:
        buffer = InMemorySseEventBuffer(max_events_per_stream=1)
        await buffer.append(
            "session_1:turn_1",
            1,
            'id: turn_1:1\nevent: content_delta\ndata: {"text":"one"}\n\n',
        )
        await buffer.append(
            "session_1:turn_1",
            2,
            'id: turn_1:2\nevent: content_delta\ndata: {"text":"two"}\n\n',
        )
        await buffer.mark_terminal("session_1:turn_1")
        return buffer

    app = AsgiAgentApp(
        sessions=InMemoryAgentSessionProvider(
            lambda session_id: build_agent_with_response("unused"),
        ),
        auth_policy=AllowAllChannelAuthPolicy(),
        a2a_server=A2AServerAdapter(StaticRunner()),
        sse_event_buffer=asyncio.run(build_buffer()),
        sse_heartbeat_interval_seconds=None,
    )

    sent = asyncio.run(
        call_asgi(
            app,
            method="POST",
            path="/v1/sessions/session_1/turns/stream",
            body=b'{"message":"hello"}',
            headers=[(b"last-event-id", b"turn_1:0")],
        ),
    )
    body = response_body(sent)

    assert response_status(sent) == 200
    assert b'"text":"two"' not in body
    assert b"event: error" in body
    assert b"replay gap" in body


def test_asgi_app_rejects_new_sse_turn_while_previous_turn_is_in_grace() -> None:
    from agentos.channels.asgi import AsgiAgentApp

    stream_agent = BlockingAsyncStreamAgent()
    provider = NonCachingProvider(stream_agent)  # type: ignore[arg-type]
    app = AsgiAgentApp(
        sessions=provider,
        auth_policy=AllowAllChannelAuthPolicy(),
        a2a_server=A2AServerAdapter(StaticRunner()),
        sse_resume_grace_seconds=0.5,
        sse_heartbeat_interval_seconds=None,
    )

    async def run() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        first_sent = await call_asgi(
            app,
            method="POST",
            path="/v1/sessions/session_1/turns/stream",
            body=b'{"message":"hello"}',
            receive_after_body=[{"type": "http.disconnect"}],
        )
        second_sent = await call_asgi(
            app,
            method="POST",
            path="/v1/sessions/session_1/turns/stream",
            body=b'{"message":"new"}',
        )
        return first_sent, second_sent

    first_sent, second_sent = asyncio.run(run())

    assert response_status(first_sent) == 200
    assert response_status(second_sent) == 409
    assert json.loads(response_body(second_sent))["error"] == "session has active stream turn"


def test_asgi_app_terminal_retention_does_not_block_next_sse_turn() -> None:
    from agentos.channels.asgi import AsgiAgentApp

    first_agent = InterruptRecordingAgent()
    second_agent = InterruptRecordingAgent()
    provider = NonCachingProvider(first_agent, second_agent)
    app = AsgiAgentApp(
        sessions=provider,
        auth_policy=AllowAllChannelAuthPolicy(),
        a2a_server=A2AServerAdapter(StaticRunner()),
        sse_terminal_retention_seconds=0.05,
        sse_heartbeat_interval_seconds=None,
    )

    async def run() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        first_sent = await call_asgi(
            app,
            method="POST",
            path="/v1/sessions/session_1/turns/stream",
            body=b'{"message":"first"}',
        )
        second_sent = await call_asgi(
            app,
            method="POST",
            path="/v1/sessions/session_1/turns/stream",
            body=b'{"message":"second"}',
        )
        await asyncio.sleep(0.06)
        return first_sent, second_sent

    first_sent, second_sent = asyncio.run(run())
    first_id = last_sse_id(first_sent)
    second_id = last_sse_id(second_sent)

    assert response_status(second_sent) == 200
    assert first_id.split(":", 1)[0] != second_id.split(":", 1)[0]


def test_asgi_app_sse_turn_ids_are_unique_across_app_instances() -> None:
    from agentos.channels.asgi import AsgiAgentApp

    first_app = AsgiAgentApp(
        sessions=NonCachingProvider(InterruptRecordingAgent()),
        auth_policy=AllowAllChannelAuthPolicy(),
        a2a_server=A2AServerAdapter(StaticRunner()),
        sse_heartbeat_interval_seconds=None,
    )
    second_app = AsgiAgentApp(
        sessions=NonCachingProvider(InterruptRecordingAgent()),
        auth_policy=AllowAllChannelAuthPolicy(),
        a2a_server=A2AServerAdapter(StaticRunner()),
        sse_heartbeat_interval_seconds=None,
    )

    first_sent = asyncio.run(
        call_asgi(
            first_app,
            method="POST",
            path="/v1/sessions/session_1/turns/stream",
            body=b'{"message":"first"}',
        ),
    )
    second_sent = asyncio.run(
        call_asgi(
            second_app,
            method="POST",
            path="/v1/sessions/session_1/turns/stream",
            body=b'{"message":"second"}',
        ),
    )

    assert response_status(first_sent) == 200
    assert response_status(second_sent) == 200
    assert last_sse_id(first_sent).split(":", 1)[0] != (
        last_sse_id(second_sent).split(":", 1)[0]
    )
