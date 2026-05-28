from __future__ import annotations

import asyncio
import inspect
from typing import Any

from agentos.channels.asgi import AsgiAgentApp
from agentos.channels.session import InMemoryAgentSessionProvider
from agentos.runtime import AgentResult, TurnStreamCompleted
from tests.channels.test_asgi_app import response_body, response_status


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
