from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Any, cast

from agentos.channels.a2a_server import A2AServerAdapter
from agentos.channels.auth import ChannelAuthError
from agentos.channels.session import InMemoryAgentSessionProvider
from agentos.multi import TaskRequest, TaskResult
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
        a2a_server=A2AServerAdapter(StaticRunner()),
    )


def test_asgi_app_rejects_oversized_body() -> None:
    from agentos.channels.asgi import AsgiAgentApp

    app = AsgiAgentApp(
        sessions=InMemoryAgentSessionProvider(
            lambda session_id: build_agent_with_response("unused"),
        ),
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
            build_app(build_agent_with_response("unused")),
            method="POST",
            path="/a2a/tasks",
            body=b'{"task_id":"task_1","instruction":"work"}',
        ),
    )

    assert response_status(sent) == 200
    assert json.loads(response_body(sent))["summary"] == "a2a done"


def test_asgi_app_passes_headers_to_a2a_server() -> None:
    from agentos.channels.asgi import AsgiAgentApp

    a2a_server = HeaderRecordingA2AServer()
    app = AsgiAgentApp(
        sessions=InMemoryAgentSessionProvider(
            lambda session_id: build_agent_with_response("unused"),
        ),
        a2a_server=a2a_server,  # type: ignore[arg-type]
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
    assert json.loads(response_body(sent))["error"] == "denied"


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


def test_asgi_app_rejects_new_sse_turn_while_previous_turn_is_in_grace() -> None:
    from agentos.channels.asgi import AsgiAgentApp

    stream_agent = BlockingAsyncStreamAgent()
    provider = NonCachingProvider(stream_agent)  # type: ignore[arg-type]
    app = AsgiAgentApp(
        sessions=provider,
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
