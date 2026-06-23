from __future__ import annotations

import asyncio
import inspect
import json
from typing import Any

from agentos.channels.auth import AllowAllChannelAuthPolicy
from agentos.channels.asgi import AsgiAgentApp
from agentos.channels.durable_session import SessionLeaseError
from agentos.persistence import BackendUnavailableError
from agentos.channels.session import InMemoryAgentSessionProvider
from agentos.channels.sse_turn_control import InMemorySseTurnControlStore
from agentos.runtime import AgentResult, AssistantContentDelta, TurnStreamCompleted
from agentos.channels.types import ChannelTurnRequest
from agentos.channels.sse_turns import SseTurnEntry
from tests.channels.test_asgi_app import (
    call_asgi as call_http_asgi,
    response_body,
    response_status,
)


class AsyncOnlyAgent:
    def __init__(self, content: str) -> None:
        self.content = content
        self.async_stream_calls = 0
        self.stream_calls = 0
        self.interrupt_calls = 0

    async def async_run(
        self,
        user_message: str,
        *,
        thinking: bool = False,
        show_thinking: bool = False,
    ) -> AgentResult:
        return AgentResult(content=self.content)

    async def async_stream(
        self,
        user_message: str,
        *,
        thinking: bool = False,
        show_thinking: bool = False,
    ):
        self.async_stream_calls += 1
        await asyncio.sleep(0)
        yield TurnStreamCompleted(content=self.content)

    def stream(self, *args: object, **kwargs: object):
        self.stream_calls += 1
        raise AssertionError("ASGI SSE must use async_stream")

    def interrupt(self) -> None:
        self.interrupt_calls += 1


class BlockingAsyncOnlyAgent(AsyncOnlyAgent):
    async def async_stream(
        self,
        user_message: str,
        *,
        thinking: bool = False,
        show_thinking: bool = False,
    ):
        self.async_stream_calls += 1
        yield TurnStreamCompleted(content=self.content)
        await asyncio.Event().wait()


class FailingAsyncRunAgent(AsyncOnlyAgent):
    async def async_run(
        self,
        user_message: str,
        *,
        thinking: bool = False,
        show_thinking: bool = False,
    ) -> AgentResult:
        raise RuntimeError("model failed")


class SlowAsyncOnlyAgent(AsyncOnlyAgent):
    def __init__(self, content: str) -> None:
        super().__init__(content)
        self.allow_json_response = asyncio.Event()

    async def async_run(
        self,
        user_message: str,
        *,
        thinking: bool = False,
        show_thinking: bool = False,
    ) -> AgentResult:
        await self.allow_json_response.wait()
        return AgentResult(content=self.content)

    async def async_stream(
        self,
        user_message: str,
        *,
        thinking: bool = False,
        show_thinking: bool = False,
    ):
        self.async_stream_calls += 1
        await asyncio.sleep(0.02)
        yield TurnStreamCompleted(content=self.content)


class LeaseFencedAsyncOnlyAgent(AsyncOnlyAgent):
    def __init__(self) -> None:
        super().__init__("unused")
        self.continue_after_refresh_failure = asyncio.Event()

    async def async_stream(
        self,
        user_message: str,
        *,
        thinking: bool = False,
        show_thinking: bool = False,
    ):
        self.async_stream_calls += 1
        yield AssistantContentDelta(index=0, text="first")
        await self.continue_after_refresh_failure.wait()
        yield AssistantContentDelta(index=1, text="second")
        yield TurnStreamCompleted(content="firstsecond")


class FailingAsyncGetSessionProvider:
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.async_get_calls = 0
        self.async_release_calls = 0

    async def async_get_agent(self, session_id: str) -> AsyncOnlyAgent:
        self.async_get_calls += 1
        raise self.error

    async def async_release_agent(
        self,
        session_id: str,
        agent: AsyncOnlyAgent,
    ) -> None:
        self.async_release_calls += 1

    def get_agent(self, session_id: str) -> AsyncOnlyAgent:
        raise AssertionError("sync get_agent should not be called")

    def release_agent(self, session_id: str, agent: AsyncOnlyAgent) -> None:
        raise AssertionError("sync release_agent should not be called")


async def call_asgi(
    app: AsgiAgentApp,
    *,
    body: bytes = b'{"message":"hello"}',
    receive_after_body: list[dict[str, object]] | None = None,
) -> list[dict[str, Any]]:
    messages = [
        {"type": "http.request", "body": body, "more_body": False},
        *(receive_after_body or []),
    ]
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, object]:
        if messages:
            return messages.pop(0)
        await asyncio.Future()
        raise AssertionError("unreachable")

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


def test_asgi_sse_uses_agent_async_stream() -> None:
    agent = AsyncOnlyAgent("async sse done")
    app = AsgiAgentApp(
        sessions=InMemoryAgentSessionProvider(lambda session_id: agent),  # type: ignore[arg-type]
        auth_policy=AllowAllChannelAuthPolicy(),
    )

    sent = asyncio.run(call_asgi(app))

    assert response_status(sent) == 200
    assert b"event: done" in response_body(sent)
    assert agent.async_stream_calls == 1
    assert agent.stream_calls == 0


def test_asgi_sse_interrupts_async_agent_after_disconnect_grace() -> None:
    agent = BlockingAsyncOnlyAgent("async sse done")
    app = AsgiAgentApp(
        sessions=InMemoryAgentSessionProvider(lambda session_id: agent),  # type: ignore[arg-type]
        auth_policy=AllowAllChannelAuthPolicy(),
        sse_resume_grace_seconds=0.01,
    )

    async def run() -> list[dict[str, Any]]:
        sent = await call_asgi(
            app,
            receive_after_body=[{"type": "http.disconnect"}],
        )
        assert agent.interrupt_calls == 0
        await asyncio.sleep(0.03)
        return sent

    sent = asyncio.run(run())

    assert response_status(sent) == 200
    assert agent.interrupt_calls == 1


def test_asgi_sse_handler_no_longer_contains_sleep_zero_bridge() -> None:
    source = inspect.getsource(AsgiAgentApp._handle_sse_turn)

    assert "asyncio.sleep(0)" not in source


def test_json_turn_uses_async_session_provider_when_available() -> None:
    class AsyncSessionProvider:
        def __init__(self) -> None:
            self.agent = AsyncOnlyAgent("json ok")
            self.async_get_calls = 0
            self.async_release_calls = 0

        async def async_get_agent(self, session_id: str) -> AsyncOnlyAgent:
            self.async_get_calls += 1
            return self.agent

        async def async_release_agent(
            self,
            session_id: str,
            agent: AsyncOnlyAgent,
        ) -> None:
            self.async_release_calls += 1

        def get_agent(self, session_id: str) -> AsyncOnlyAgent:
            raise AssertionError("sync get_agent should not be called")

        def release_agent(self, session_id: str, agent: AsyncOnlyAgent) -> None:
            raise AssertionError("sync release_agent should not be called")

    provider = AsyncSessionProvider()
    app = AsgiAgentApp(  # type: ignore[arg-type]
        sessions=provider,
        auth_policy=AllowAllChannelAuthPolicy(),
    )

    async def run() -> list[dict[str, Any]]:
        return await call_http_asgi(
            app,
            method="POST",
            path="/v1/sessions/session_1/turns",
            body=b'{"message":"hello"}',
        )

    sent = asyncio.run(run())

    assert response_status(sent) == 200
    assert json.loads(response_body(sent))["content"] == "json ok"
    assert provider.async_get_calls == 1
    assert provider.async_release_calls == 1


def test_json_turn_maps_async_session_lease_acquisition_failure_to_locked_response() -> None:
    provider = FailingAsyncGetSessionProvider(
        SessionLeaseError("session is locked: session_1"),
    )
    app = AsgiAgentApp(  # type: ignore[arg-type]
        sessions=provider,
        auth_policy=AllowAllChannelAuthPolicy(),
    )

    async def run() -> list[dict[str, Any]]:
        return await call_http_asgi(
            app,
            method="POST",
            path="/v1/sessions/session_1/turns",
            body=b'{"message":"hello"}',
        )

    sent = asyncio.run(run())
    payload = json.loads(response_body(sent))

    assert response_status(sent) == 423
    assert payload == {
        "session_id": "session_1",
        "status": "failed",
        "content": None,
        "error": "session unavailable",
    }
    assert provider.async_get_calls == 1
    assert provider.async_release_calls == 0


def test_json_turn_maps_async_backend_acquisition_failure_to_unavailable_response() -> None:
    provider = FailingAsyncGetSessionProvider(
        BackendUnavailableError("Redis backend unavailable"),
    )
    app = AsgiAgentApp(  # type: ignore[arg-type]
        sessions=provider,
        auth_policy=AllowAllChannelAuthPolicy(),
    )

    async def run() -> list[dict[str, Any]]:
        return await call_http_asgi(
            app,
            method="POST",
            path="/v1/sessions/session_1/turns",
            body=b'{"message":"hello"}',
        )

    sent = asyncio.run(run())
    payload = json.loads(response_body(sent))

    assert response_status(sent) == 503
    assert payload == {
        "session_id": "session_1",
        "status": "failed",
        "content": None,
        "error": "backend unavailable",
    }
    assert provider.async_get_calls == 1
    assert provider.async_release_calls == 0


def test_interrupt_uses_async_session_provider_when_available() -> None:
    class AsyncSessionProvider:
        def __init__(self) -> None:
            self.agent = AsyncOnlyAgent("unused")
            self.async_get_calls = 0
            self.async_release_calls = 0

        async def async_get_agent(self, session_id: str) -> AsyncOnlyAgent:
            self.async_get_calls += 1
            return self.agent

        async def async_release_agent(
            self,
            session_id: str,
            agent: AsyncOnlyAgent,
        ) -> None:
            self.async_release_calls += 1

        def get_agent(self, session_id: str) -> AsyncOnlyAgent:
            raise AssertionError("sync get_agent should not be called")

        def release_agent(self, session_id: str, agent: AsyncOnlyAgent) -> None:
            raise AssertionError("sync release_agent should not be called")

    provider = AsyncSessionProvider()
    app = AsgiAgentApp(  # type: ignore[arg-type]
        sessions=provider,
        auth_policy=AllowAllChannelAuthPolicy(),
    )

    async def run() -> list[dict[str, Any]]:
        return await call_http_asgi(
            app,
            method="POST",
            path="/v1/sessions/session_1/interrupt",
        )

    sent = asyncio.run(run())

    assert response_status(sent) == 200
    assert json.loads(response_body(sent)) == {
        "session_id": "session_1",
        "status": "interrupted",
    }
    assert provider.agent.interrupt_calls == 1
    assert provider.async_get_calls == 1
    assert provider.async_release_calls == 1


def test_interrupt_maps_async_session_lease_acquisition_failure_to_locked_response() -> None:
    provider = FailingAsyncGetSessionProvider(
        SessionLeaseError("session is locked: session_1"),
    )
    app = AsgiAgentApp(  # type: ignore[arg-type]
        sessions=provider,
        auth_policy=AllowAllChannelAuthPolicy(),
    )

    async def run() -> list[dict[str, Any]]:
        return await call_http_asgi(
            app,
            method="POST",
            path="/v1/sessions/session_1/interrupt",
        )

    sent = asyncio.run(run())

    assert response_status(sent) == 423
    assert json.loads(response_body(sent)) == {
        "session_id": "session_1",
        "status": "failed",
        "content": None,
        "error": "session unavailable",
    }
    assert provider.async_get_calls == 1
    assert provider.async_release_calls == 0


def test_json_turn_refreshes_session_lease_during_long_run() -> None:
    class AsyncSessionProvider:
        def __init__(self) -> None:
            self.agent = SlowAsyncOnlyAgent("json ok")
            self.async_refresh_calls: list[str] = []

        async def async_get_agent(self, session_id: str) -> SlowAsyncOnlyAgent:
            return self.agent

        async def async_release_agent(
            self,
            session_id: str,
            agent: SlowAsyncOnlyAgent,
        ) -> None:
            return None

        async def async_refresh_agent(self, session_id: str) -> None:
            self.async_refresh_calls.append(session_id)
            self.agent.allow_json_response.set()

    provider = AsyncSessionProvider()
    app = AsgiAgentApp(  # type: ignore[arg-type]
        sessions=provider,
        auth_policy=AllowAllChannelAuthPolicy(),
        session_lease_heartbeat_interval_seconds=0.001,
    )

    async def run() -> list[dict[str, Any]]:
        return await asyncio.wait_for(
            call_http_asgi(
                app,
                method="POST",
                path="/v1/sessions/session_1/turns",
                body=b'{"message":"hello"}',
            ),
            timeout=0.1,
        )

    sent = asyncio.run(run())

    assert response_status(sent) == 200
    assert json.loads(response_body(sent))["content"] == "json ok"
    assert provider.async_refresh_calls
    assert set(provider.async_refresh_calls) == {"session_1"}


def test_json_turn_stops_session_lease_heartbeat_when_request_is_cancelled() -> None:
    first_refresh = asyncio.Event()

    class BlockingJsonAgent(SlowAsyncOnlyAgent):
        def __init__(self) -> None:
            super().__init__("json ok")
            self.started = asyncio.Event()

        async def async_run(
            self,
            user_message: str,
            *,
            thinking: bool = False,
            show_thinking: bool = False,
        ) -> AgentResult:
            self.started.set()
            await self.allow_json_response.wait()
            return AgentResult(content=self.content)

    class AsyncSessionProvider:
        def __init__(self) -> None:
            self.agent = BlockingJsonAgent()
            self.async_refresh_calls: list[str] = []
            self.async_release_calls = 0

        async def async_get_agent(self, session_id: str) -> BlockingJsonAgent:
            return self.agent

        async def async_release_agent(
            self,
            session_id: str,
            agent: BlockingJsonAgent,
        ) -> None:
            self.async_release_calls += 1

        async def async_refresh_agent(self, session_id: str) -> None:
            self.async_refresh_calls.append(session_id)
            first_refresh.set()

    provider = AsyncSessionProvider()
    app = AsgiAgentApp(  # type: ignore[arg-type]
        sessions=provider,
        auth_policy=AllowAllChannelAuthPolicy(),
        session_lease_heartbeat_interval_seconds=0.001,
    )

    async def run() -> int:
        task = asyncio.create_task(
            call_http_asgi(
                app,
                method="POST",
                path="/v1/sessions/session_1/turns",
                body=b'{"message":"hello"}',
            ),
        )
        await asyncio.wait_for(provider.agent.started.wait(), timeout=0.1)
        await asyncio.wait_for(first_refresh.wait(), timeout=0.1)
        refreshes_before_cancel = len(provider.async_refresh_calls)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(0.01)
        return refreshes_before_cancel

    refreshes_before_cancel = asyncio.run(run())

    assert refreshes_before_cancel > 0
    assert len(provider.async_refresh_calls) == refreshes_before_cancel
    assert provider.async_release_calls == 1


def test_json_turn_abandons_session_without_save_when_request_is_cancelled() -> None:
    class BlockingJsonAgent(SlowAsyncOnlyAgent):
        def __init__(self) -> None:
            super().__init__("json ok")
            self.started = asyncio.Event()

        async def async_run(
            self,
            user_message: str,
            *,
            thinking: bool = False,
            show_thinking: bool = False,
        ) -> AgentResult:
            self.started.set()
            await self.allow_json_response.wait()
            return AgentResult(content=self.content)

    class AsyncSessionProvider:
        def __init__(self) -> None:
            self.agent = BlockingJsonAgent()
            self.async_release_calls = 0
            self.async_abandon_calls = 0

        async def async_get_agent(self, session_id: str) -> BlockingJsonAgent:
            return self.agent

        async def async_release_agent(
            self,
            session_id: str,
            agent: BlockingJsonAgent,
        ) -> None:
            self.async_release_calls += 1

        async def async_abandon_agent(
            self,
            session_id: str,
            agent: BlockingJsonAgent,
        ) -> None:
            self.async_abandon_calls += 1

    provider = AsyncSessionProvider()
    app = AsgiAgentApp(  # type: ignore[arg-type]
        sessions=provider,
        auth_policy=AllowAllChannelAuthPolicy(),
    )

    async def run() -> None:
        task = asyncio.create_task(
            call_http_asgi(
                app,
                method="POST",
                path="/v1/sessions/session_1/turns",
                body=b'{"message":"hello"}',
            ),
        )
        await asyncio.wait_for(provider.agent.started.wait(), timeout=0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run())

    assert provider.async_abandon_calls == 1
    assert provider.async_release_calls == 0


def test_json_turn_fails_when_session_lease_refresh_fails() -> None:
    class AsyncSessionProvider:
        def __init__(self) -> None:
            self.agent = SlowAsyncOnlyAgent("json ok")
            self.async_refresh_calls = 0
            self.async_release_calls = 0

        async def async_get_agent(self, session_id: str) -> SlowAsyncOnlyAgent:
            return self.agent

        async def async_release_agent(
            self,
            session_id: str,
            agent: SlowAsyncOnlyAgent,
        ) -> None:
            self.async_release_calls += 1

        async def async_refresh_agent(self, session_id: str) -> None:
            self.async_refresh_calls += 1
            self.agent.allow_json_response.set()
            raise SessionLeaseError(f"session lease is not owned: {session_id}")

    provider = AsyncSessionProvider()
    app = AsgiAgentApp(  # type: ignore[arg-type]
        sessions=provider,
        auth_policy=AllowAllChannelAuthPolicy(),
        session_lease_heartbeat_interval_seconds=0.001,
    )

    async def run() -> list[dict[str, Any]]:
        return await asyncio.wait_for(
            call_http_asgi(
                app,
                method="POST",
                path="/v1/sessions/session_1/turns",
                body=b'{"message":"hello"}',
            ),
            timeout=0.1,
        )

    sent = asyncio.run(run())
    body = json.loads(response_body(sent))

    assert response_status(sent) == 500
    assert body == {
        "session_id": "session_1",
        "status": "failed",
        "content": None,
        "error": "session unavailable",
    }
    assert provider.async_refresh_calls == 1
    assert provider.async_release_calls == 1


def test_json_turn_abandons_session_without_save_when_lease_refresh_fails() -> None:
    class AsyncSessionProvider:
        def __init__(self) -> None:
            self.agent = SlowAsyncOnlyAgent("json ok")
            self.async_refresh_calls = 0
            self.async_release_calls = 0
            self.async_abandon_calls = 0

        async def async_get_agent(self, session_id: str) -> SlowAsyncOnlyAgent:
            return self.agent

        async def async_release_agent(
            self,
            session_id: str,
            agent: SlowAsyncOnlyAgent,
        ) -> None:
            self.async_release_calls += 1

        async def async_abandon_agent(
            self,
            session_id: str,
            agent: SlowAsyncOnlyAgent,
        ) -> None:
            self.async_abandon_calls += 1

        async def async_refresh_agent(self, session_id: str) -> None:
            self.async_refresh_calls += 1
            self.agent.allow_json_response.set()
            raise SessionLeaseError(f"session lease is not owned: {session_id}")

    provider = AsyncSessionProvider()
    app = AsgiAgentApp(  # type: ignore[arg-type]
        sessions=provider,
        auth_policy=AllowAllChannelAuthPolicy(),
        session_lease_heartbeat_interval_seconds=0.001,
    )

    async def run() -> list[dict[str, Any]]:
        return await asyncio.wait_for(
            call_http_asgi(
                app,
                method="POST",
                path="/v1/sessions/session_1/turns",
                body=b'{"message":"hello"}',
            ),
            timeout=0.1,
        )

    sent = asyncio.run(run())

    assert response_status(sent) == 500
    body = response_body(sent)
    assert b"session unavailable" in body
    assert b"session lease is not owned" not in body
    assert provider.async_refresh_calls == 1
    assert provider.async_abandon_calls == 1
    assert provider.async_release_calls == 0


def test_sse_turn_maps_async_backend_acquisition_failure_to_unavailable_response() -> None:
    provider = FailingAsyncGetSessionProvider(
        BackendUnavailableError("Redis backend unavailable"),
    )
    app = AsgiAgentApp(  # type: ignore[arg-type]
        sessions=provider,
        auth_policy=AllowAllChannelAuthPolicy(),
    )

    sent = asyncio.run(call_asgi(app))
    payload = json.loads(response_body(sent))

    assert response_status(sent) == 503
    assert payload == {
        "session_id": "session_1",
        "status": "failed",
        "content": None,
        "error": "backend unavailable",
    }
    assert provider.async_get_calls == 1
    assert provider.async_release_calls == 0


def test_json_turn_reports_failure_when_async_release_fails() -> None:
    class AsyncSessionProvider:
        def __init__(self) -> None:
            self.agent = AsyncOnlyAgent("json ok")
            self.async_get_calls = 0
            self.async_release_calls = 0

        async def async_get_agent(self, session_id: str) -> AsyncOnlyAgent:
            self.async_get_calls += 1
            return self.agent

        async def async_release_agent(
            self,
            session_id: str,
            agent: AsyncOnlyAgent,
        ) -> None:
            self.async_release_calls += 1
            raise RuntimeError("save failed")

    provider = AsyncSessionProvider()
    app = AsgiAgentApp(  # type: ignore[arg-type]
        sessions=provider,
        auth_policy=AllowAllChannelAuthPolicy(),
    )

    async def run() -> list[dict[str, Any]]:
        return await call_http_asgi(
            app,
            method="POST",
            path="/v1/sessions/session_1/turns",
            body=b'{"message":"hello"}',
        )

    sent = asyncio.run(run())
    body = json.loads(response_body(sent))

    assert response_status(sent) == 500
    assert body == {
        "session_id": "session_1",
        "status": "failed",
        "content": None,
        "error": "internal error",
    }
    assert provider.async_get_calls == 1
    assert provider.async_release_calls == 1


def test_json_turn_releases_async_session_when_agent_run_fails() -> None:
    class AsyncSessionProvider:
        def __init__(self) -> None:
            self.agent = FailingAsyncRunAgent("unused")
            self.async_get_calls = 0
            self.async_release_calls = 0

        async def async_get_agent(self, session_id: str) -> FailingAsyncRunAgent:
            self.async_get_calls += 1
            return self.agent

        async def async_release_agent(
            self,
            session_id: str,
            agent: FailingAsyncRunAgent,
        ) -> None:
            self.async_release_calls += 1

    provider = AsyncSessionProvider()
    app = AsgiAgentApp(  # type: ignore[arg-type]
        sessions=provider,
        auth_policy=AllowAllChannelAuthPolicy(),
    )

    async def run() -> list[dict[str, Any]]:
        return await call_http_asgi(
            app,
            method="POST",
            path="/v1/sessions/session_1/turns",
            body=b'{"message":"hello"}',
        )

    sent = asyncio.run(run())
    body = json.loads(response_body(sent))

    assert response_status(sent) == 500
    assert body["status"] == "failed"
    assert body["content"] is None
    assert body["error"] == "internal error"
    assert provider.async_get_calls == 1
    assert provider.async_release_calls == 1


def test_json_turn_can_expose_internal_errors_for_local_debug() -> None:
    class AsyncSessionProvider:
        def __init__(self) -> None:
            self.agent = FailingAsyncRunAgent("unused")

        async def async_get_agent(self, session_id: str) -> FailingAsyncRunAgent:
            return self.agent

        async def async_release_agent(
            self,
            session_id: str,
            agent: FailingAsyncRunAgent,
        ) -> None:
            return None

    app = AsgiAgentApp(  # type: ignore[arg-type]
        sessions=AsyncSessionProvider(),
        auth_policy=AllowAllChannelAuthPolicy(),
        expose_internal_errors=True,
    )

    async def run() -> list[dict[str, Any]]:
        return await call_http_asgi(
            app,
            method="POST",
            path="/v1/sessions/session_1/turns",
            body=b'{"message":"hello"}',
        )

    sent = asyncio.run(run())
    body = json.loads(response_body(sent))

    assert response_status(sent) == 500
    assert body["error"] == "model failed"


def test_sse_turn_uses_async_session_provider_when_available() -> None:
    class AsyncSessionProvider:
        def __init__(self) -> None:
            self.agent = AsyncOnlyAgent("sse ok")
            self.async_get_calls = 0
            self.async_release_calls = 0

        async def async_get_agent(self, session_id: str) -> AsyncOnlyAgent:
            self.async_get_calls += 1
            return self.agent

        async def async_release_agent(
            self,
            session_id: str,
            agent: AsyncOnlyAgent,
        ) -> None:
            self.async_release_calls += 1

        def get_agent(self, session_id: str) -> AsyncOnlyAgent:
            raise AssertionError("sync get_agent should not be called")

        def release_agent(self, session_id: str, agent: AsyncOnlyAgent) -> None:
            raise AssertionError("sync release_agent should not be called")

    provider = AsyncSessionProvider()
    app = AsgiAgentApp(
        sessions=provider,  # type: ignore[arg-type]
        auth_policy=AllowAllChannelAuthPolicy(),
        sse_terminal_retention_seconds=0,
    )

    sent = asyncio.run(call_asgi(app))

    assert response_status(sent) == 200
    assert b"event: done" in response_body(sent)
    assert provider.async_get_calls == 1
    assert provider.async_release_calls == 1


def test_sse_turn_reports_error_when_async_release_fails_before_done() -> None:
    class AsyncSessionProvider:
        def __init__(self) -> None:
            self.agent = AsyncOnlyAgent("sse ok")
            self.async_get_calls = 0
            self.async_release_calls = 0

        async def async_get_agent(self, session_id: str) -> AsyncOnlyAgent:
            self.async_get_calls += 1
            return self.agent

        async def async_release_agent(
            self,
            session_id: str,
            agent: AsyncOnlyAgent,
        ) -> None:
            self.async_release_calls += 1
            raise RuntimeError("save failed")

    provider = AsyncSessionProvider()
    app = AsgiAgentApp(
        sessions=provider,  # type: ignore[arg-type]
        auth_policy=AllowAllChannelAuthPolicy(),
    )

    sent = asyncio.run(call_asgi(app))
    body = response_body(sent)

    assert response_status(sent) == 200
    assert b"event: done" not in body
    assert b"event: error" in body
    assert b"internal error" in body
    assert b"save failed" not in body
    assert provider.async_get_calls == 1
    assert provider.async_release_calls == 1


def test_sse_release_marker_is_set_only_after_release_succeeds() -> None:
    class AsyncSessionProvider:
        def __init__(self) -> None:
            self.agent = AsyncOnlyAgent("unused")
            self.release_attempts = 0

        async def async_release_agent(
            self,
            session_id: str,
            agent: AsyncOnlyAgent,
        ) -> None:
            self.release_attempts += 1
            if self.release_attempts == 1:
                raise RuntimeError("save failed")

    provider = AsyncSessionProvider()
    app = AsgiAgentApp(  # type: ignore[arg-type]
        sessions=provider,
        auth_policy=AllowAllChannelAuthPolicy(),
    )
    entry = SseTurnEntry(
        session_id="session_1",
        turn_stream_id="turn_1",
        stream_key="session_1:turn_1",
        agent=provider.agent,  # type: ignore[arg-type]
        request=ChannelTurnRequest(message="hello"),
    )

    async def run() -> None:
        try:
            await app._release_sse_entry(entry)  # type: ignore[attr-defined]
        except RuntimeError:
            pass
        assert entry.released is False
        await app._release_sse_entry(entry)  # type: ignore[attr-defined]

    asyncio.run(run())

    assert entry.released is True
    assert provider.release_attempts == 2


def test_sse_turn_refreshes_session_lease_during_long_stream() -> None:
    class AsyncSessionProvider:
        def __init__(self) -> None:
            self.agent = SlowAsyncOnlyAgent("sse ok")
            self.async_refresh_calls: list[str] = []

        async def async_get_agent(self, session_id: str) -> SlowAsyncOnlyAgent:
            return self.agent

        async def async_release_agent(
            self,
            session_id: str,
            agent: SlowAsyncOnlyAgent,
        ) -> None:
            return None

        async def async_refresh_agent(self, session_id: str) -> None:
            self.async_refresh_calls.append(session_id)

    provider = AsyncSessionProvider()
    app = AsgiAgentApp(
        sessions=provider,  # type: ignore[arg-type]
        auth_policy=AllowAllChannelAuthPolicy(),
        session_lease_heartbeat_interval_seconds=0.001,
        sse_terminal_retention_seconds=0,
    )

    sent = asyncio.run(call_asgi(app))

    assert response_status(sent) == 200
    assert b"event: done" in response_body(sent)
    assert provider.async_refresh_calls
    assert set(provider.async_refresh_calls) == {"session_1"}


def test_sse_turn_stops_when_session_lease_refresh_fails() -> None:
    class AsyncSessionProvider:
        def __init__(self) -> None:
            self.agent = LeaseFencedAsyncOnlyAgent()
            self.async_refresh_calls: list[str] = []
            self.async_release_calls = 0

        async def async_get_agent(self, session_id: str) -> LeaseFencedAsyncOnlyAgent:
            return self.agent

        async def async_release_agent(
            self,
            session_id: str,
            agent: LeaseFencedAsyncOnlyAgent,
        ) -> None:
            self.async_release_calls += 1

        async def async_refresh_agent(self, session_id: str) -> None:
            self.async_refresh_calls.append(session_id)
            self.agent.continue_after_refresh_failure.set()
            raise SessionLeaseError("session lease is not owned: session_1")

    provider = AsyncSessionProvider()
    app = AsgiAgentApp(
        sessions=provider,  # type: ignore[arg-type]
        auth_policy=AllowAllChannelAuthPolicy(),
        session_lease_heartbeat_interval_seconds=0.001,
        sse_heartbeat_interval_seconds=None,
        sse_terminal_retention_seconds=0,
    )

    sent = asyncio.run(call_asgi(app))

    body = response_body(sent)
    assert response_status(sent) == 200
    assert b'"text":"first"' in body
    assert b'"text":"second"' not in body
    assert b"event: done" not in body
    assert b"event: error" in body
    assert b"session unavailable" in body
    assert b"session lease is not owned" not in body
    assert provider.agent.interrupt_calls == 1
    assert provider.async_refresh_calls == ["session_1"]
    assert provider.async_release_calls == 1


def test_sse_turn_abandons_session_without_save_when_lease_refresh_fails() -> None:
    class AsyncSessionProvider:
        def __init__(self) -> None:
            self.agent = LeaseFencedAsyncOnlyAgent()
            self.async_refresh_calls: list[str] = []
            self.async_release_calls = 0
            self.async_abandon_calls = 0

        async def async_get_agent(self, session_id: str) -> LeaseFencedAsyncOnlyAgent:
            return self.agent

        async def async_release_agent(
            self,
            session_id: str,
            agent: LeaseFencedAsyncOnlyAgent,
        ) -> None:
            self.async_release_calls += 1

        async def async_abandon_agent(
            self,
            session_id: str,
            agent: LeaseFencedAsyncOnlyAgent,
        ) -> None:
            self.async_abandon_calls += 1

        async def async_refresh_agent(self, session_id: str) -> None:
            self.async_refresh_calls.append(session_id)
            self.agent.continue_after_refresh_failure.set()
            raise SessionLeaseError("session lease is not owned: session_1")

    provider = AsyncSessionProvider()
    app = AsgiAgentApp(
        sessions=provider,  # type: ignore[arg-type]
        auth_policy=AllowAllChannelAuthPolicy(),
        session_lease_heartbeat_interval_seconds=0.001,
        sse_heartbeat_interval_seconds=None,
        sse_terminal_retention_seconds=0,
    )

    sent = asyncio.run(call_asgi(app))

    assert response_status(sent) == 200
    body = response_body(sent)
    assert b"session unavailable" in body
    assert b"session lease is not owned" not in body
    assert provider.async_refresh_calls == ["session_1"]
    assert provider.async_abandon_calls == 1
    assert provider.async_release_calls == 0


def test_sse_turn_stops_when_shared_interrupt_is_requested_on_another_node() -> None:
    class AsyncSessionProvider:
        def __init__(self) -> None:
            self.agent = LeaseFencedAsyncOnlyAgent()
            self.async_release_calls = 0

        async def async_get_agent(self, session_id: str) -> LeaseFencedAsyncOnlyAgent:
            return self.agent

        async def async_release_agent(
            self,
            session_id: str,
            agent: LeaseFencedAsyncOnlyAgent,
        ) -> None:
            self.async_release_calls += 1

    turn_control = InMemorySseTurnControlStore()
    provider = AsyncSessionProvider()
    producer_app = AsgiAgentApp(
        sessions=provider,  # type: ignore[arg-type]
        auth_policy=AllowAllChannelAuthPolicy(),
        sse_turn_control=turn_control,
        sse_turn_control_poll_interval_seconds=0.001,
        sse_heartbeat_interval_seconds=None,
        sse_terminal_retention_seconds=0,
    )
    follower_app = AsgiAgentApp(
        sessions=InMemoryAgentSessionProvider(lambda session_id: AsyncOnlyAgent("unused")),
        auth_policy=AllowAllChannelAuthPolicy(),
        sse_turn_control=turn_control,
        sse_heartbeat_interval_seconds=None,
    )

    async def run() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        producer_task = asyncio.create_task(call_asgi(producer_app))
        await asyncio.sleep(0.01)
        interrupt_sent = await call_http_asgi(
            follower_app,
            method="POST",
            path="/v1/sessions/session_1/interrupt",
        )
        provider.agent.continue_after_refresh_failure.set()
        return await producer_task, interrupt_sent

    sent, interrupt_sent = asyncio.run(run())
    body = response_body(sent)

    assert response_status(interrupt_sent) == 200
    assert json.loads(response_body(interrupt_sent)) == {
        "session_id": "session_1",
        "status": "interrupt_requested",
    }
    assert response_status(sent) == 200
    assert b'"text":"first"' in body
    assert b'"text":"second"' not in body
    assert b"event: done" not in body
    assert b"event: error" in body
    assert b"interrupted" in body
    assert provider.agent.interrupt_calls == 1
    assert provider.async_release_calls == 1
