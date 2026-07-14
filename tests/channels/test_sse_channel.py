from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import cast

from agentos.context import ContextRuntime
from agentos.messages import MessageRuntime
from agentos.providers import FakeProvider, ProviderResponse
from agentos.runtime import (
    Agent,
    ProviderRequestBuilder,
    RunInput,
    RunOptions,
    ToolStreamFailed,
    TurnStreamCancelled,
    TurnStreamCompleted,
    TurnStreamEvent,
    TurnStreamFailed,
    TurnStreamWaiting,
    WaitReason,
)
from agentos.runtime._execution_lease import ExecutionLease
from agentos.runtime.agent_stream import AgentStream
from tests._context_protocol_fixtures import default_context_renderer
from tests.multi.helpers import build_agent_with_response


def build_stream_agent(provider: FakeProvider) -> Agent:
    messages = MessageRuntime()
    return Agent(
        query_loop_kwargs={
            "context_runtime": ContextRuntime(),
            "message_runtime": messages,
            "request_builder": ProviderRequestBuilder(
                context_renderer=default_context_renderer(),
                message_runtime=messages,
                tools=[],
            ),
            "provider": provider,
        },
    )


class RecordingProvider:
    def __init__(self, agent: Agent) -> None:
        self.agent = agent
        self.released: list[tuple[str, Agent]] = []

    def get_agent(self, session_id: str) -> Agent:
        return self.agent

    def release_agent(self, session_id: str, agent: Agent) -> None:
        self.released.append((session_id, agent))


class StreamRecordingAgent:
    def __init__(self, agent: Agent) -> None:
        self._agent = agent
        self.stream: AgentStream | None = None

    async def run(
        self,
        input: RunInput,
        *,
        stream: bool = False,
        options: RunOptions | None = None,
    ) -> object:
        result = await self._agent.run(input, stream=stream, options=options)
        if stream:
            assert isinstance(result, AgentStream)
            self.stream = result
        return result

    def interrupt(self) -> bool:
        return self._agent.interrupt()


class OrderingAsyncProvider:
    def __init__(self, agent: StreamRecordingAgent, order: list[str]) -> None:
        self.agent = agent
        self.order = order

    async def async_get_agent(self, session_id: str) -> Agent:
        return cast(Agent, self.agent)

    async def async_release_agent(self, session_id: str, agent: Agent) -> None:
        assert self.agent.stream is not None
        assert self.agent.stream.closed
        self.order.append("stream_closed")
        replacement = await self.agent._agent.run("lease probe", stream=True)
        self.order.append("query_lease_released")
        await replacement.aclose()
        self.order.append("session_released")

    def get_agent(self, session_id: str) -> Agent:
        raise AssertionError("sync get_agent should not be called")

    def release_agent(self, session_id: str, agent: Agent) -> None:
        raise AssertionError("sync release_agent should not be called")


class EventStreamAgent:
    def __init__(self, *events: TurnStreamEvent) -> None:
        self._events = events

    async def run(
        self,
        input: RunInput,
        *,
        stream: bool = False,
        options: RunOptions | None = None,
    ) -> AgentStream:
        assert stream

        async def source() -> AsyncIterator[TurnStreamEvent]:
            for event in self._events:
                yield event

        return ExecutionLease().open_stream(source(), cleanup=lambda: None)

    def interrupt(self) -> bool:
        return False


class ReleaseFailingProvider(RecordingProvider):
    def release_agent(self, session_id: str, agent: Agent) -> None:
        super().release_agent(session_id, agent)
        raise RuntimeError("secret-release-detail")


def test_sse_channel_streams_existing_sse_chunks_and_releases_agent() -> None:
    from agentos.channels.sse import SseAgentChannel

    agent = build_agent_with_response("streamed")
    provider = RecordingProvider(agent)
    channel = SseAgentChannel(provider)

    async def run() -> list[str]:
        return [
            chunk
            async for chunk in channel.stream_turn(
                "session_1",
                b'{"message":"hello"}',
            )
        ]

    chunks = asyncio.run(run())

    assert any(chunk.startswith("event: content_delta") for chunk in chunks)
    assert chunks[-1].startswith("event: done")
    assert provider.released == [("session_1", agent)]


def test_sse_channel_filters_thinking_when_hidden() -> None:
    from agentos.channels.sse import SseAgentChannel

    agent = build_stream_agent(
        FakeProvider(
            [
                ProviderResponse(
                    content="answer",
                    thinking_content="hidden thought",
                ),
            ],
        ),
    )
    channel = SseAgentChannel(RecordingProvider(agent))

    async def run() -> list[str]:
        return [
            chunk
            async for chunk in channel.stream_turn(
                "session_1",
                b'{"message":"hello","thinking":true,"show_thinking":false}',
            )
        ]

    chunks = asyncio.run(run())

    assert all("thinking_delta" not in chunk for chunk in chunks)
    assert any("content_delta" in chunk for chunk in chunks)


def test_sse_channel_preserves_actionable_request_parse_errors() -> None:
    from agentos.channels.sse import SseAgentChannel

    channel = SseAgentChannel(RecordingProvider(build_agent_with_response("unused")))

    async def run() -> list[str]:
        return [
            chunk
            async for chunk in channel.stream_turn(
                "session_1",
                b"{",
            )
        ]

    chunks = asyncio.run(run())

    assert len(chunks) == 1
    assert "invalid JSON" in chunks[0]


def test_sse_channel_preserves_actionable_input_validation_errors() -> None:
    from agentos.channels.sse import SseAgentChannel

    channel = SseAgentChannel(RecordingProvider(build_agent_with_response("unused")))

    async def run() -> list[str]:
        return [
            chunk
            async for chunk in channel.stream_turn(
                "session_1",
                b'{"thinking":true}',
            )
        ]

    chunks = asyncio.run(run())

    assert len(chunks) == 1
    assert "message" in chunks[0]


def test_sse_channel_masks_internal_errors_and_releases_agent() -> None:
    from agentos.channels.sse import SseAgentChannel

    agent = build_agent_with_response("unused")
    provider = RecordingProvider(agent)
    channel = SseAgentChannel(provider)

    def fail_callback(agent: Agent) -> None:
        raise RuntimeError("callback failed")

    async def run() -> list[str]:
        return [
            chunk
            async for chunk in channel.stream_turn(
                "session_1",
                b'{"message":"hello"}',
                on_agent=fail_callback,
            )
        ]

    chunks = asyncio.run(run())

    assert len(chunks) == 1
    assert '"error":"internal error"' in chunks[0]
    assert "callback failed" not in chunks[0]
    assert provider.released == [("session_1", agent)]


def test_sse_channel_can_expose_internal_errors_for_local_debug() -> None:
    from agentos.channels.sse import SseAgentChannel

    agent = build_agent_with_response("unused")
    provider = RecordingProvider(agent)
    channel = SseAgentChannel(provider, expose_internal_errors=True)

    def fail_callback(agent: Agent) -> None:
        raise RuntimeError("callback failed")

    async def run() -> list[str]:
        return [
            chunk
            async for chunk in channel.stream_turn(
                "session_1",
                b'{"message":"hello"}',
                on_agent=fail_callback,
            )
        ]

    chunks = asyncio.run(run())

    assert len(chunks) == 1
    assert '"error":"callback failed"' in chunks[0]
    assert provider.released == [("session_1", agent)]


def test_sse_channel_masks_failed_stream_event_details() -> None:
    from agentos.channels.sse import SseAgentChannel

    agent = cast(
        Agent,
        EventStreamAgent(
            ToolStreamFailed(
                tool_name="read_file",
                tool_call_id="call_1",
                error=RuntimeError("secret-tool-detail"),
            ),
            TurnStreamFailed(RuntimeError("secret-provider-detail")),
        ),
    )
    channel = SseAgentChannel(RecordingProvider(agent))

    async def run() -> list[str]:
        return [
            chunk
            async for chunk in channel.stream_turn(
                "session_1",
                b'{"message":"hello"}',
            )
        ]

    chunks = asyncio.run(run())

    assert [chunk.splitlines()[0] for chunk in chunks] == [
        "event: tool_failed",
        "event: error",
    ]
    assert all("secret-" not in chunk for chunk in chunks)
    assert all('"error":"internal error"' in chunk for chunk in chunks)


def test_sse_channel_exposes_failed_stream_details_only_for_local_debug() -> None:
    from agentos.channels.sse import SseAgentChannel

    agent = cast(
        Agent,
        EventStreamAgent(
            ToolStreamFailed(
                tool_name="read_file",
                tool_call_id="call_1",
                error=RuntimeError("secret-tool-detail"),
            ),
            TurnStreamFailed(RuntimeError("secret-provider-detail")),
        ),
    )
    channel = SseAgentChannel(
        RecordingProvider(agent),
        expose_internal_errors=True,
    )

    async def run() -> list[str]:
        return [
            chunk
            async for chunk in channel.stream_turn(
                "session_1",
                b'{"message":"hello"}',
            )
        ]

    chunks = asyncio.run(run())

    assert [chunk.splitlines()[0] for chunk in chunks] == [
        "event: tool_failed",
        "event: error",
    ]
    assert "secret-tool-detail" in chunks[0]
    assert "secret-provider-detail" in chunks[1]


def test_sse_channel_replaces_terminal_with_error_if_session_release_fails() -> None:
    from agentos.channels.sse import SseAgentChannel

    terminal_events: tuple[TurnStreamEvent, ...] = (
        TurnStreamCompleted(content="done"),
        TurnStreamWaiting(
            run_id="run_1",
            reason=WaitReason(kind="human_input", handle="approval_1"),
        ),
        TurnStreamCancelled(reason="cancelled"),
    )

    async def run(event: TurnStreamEvent) -> tuple[list[str], ReleaseFailingProvider]:
        agent = cast(Agent, EventStreamAgent(event))
        provider = ReleaseFailingProvider(agent)
        channel = SseAgentChannel(provider)
        chunks = [
            chunk
            async for chunk in channel.stream_turn(
                "session_1",
                b'{"message":"hello"}',
            )
        ]
        return chunks, provider

    for event in terminal_events:
        chunks, provider = asyncio.run(run(event))
        agent = provider.agent
        assert [chunk.splitlines()[0] for chunk in chunks] == ["event: error"]
        assert '"error":"internal error"' in chunks[0]
        assert "secret-release-detail" not in chunks[0]
        assert provider.released == [("session_1", agent)]


def test_sse_channel_can_expose_session_release_failure_for_local_debug() -> None:
    from agentos.channels.sse import SseAgentChannel

    agent = cast(Agent, EventStreamAgent(TurnStreamCompleted(content="done")))
    provider = ReleaseFailingProvider(agent)
    channel = SseAgentChannel(provider, expose_internal_errors=True)

    async def run() -> list[str]:
        return [
            chunk
            async for chunk in channel.stream_turn(
                "session_1",
                b'{"message":"hello"}',
            )
        ]

    chunks = asyncio.run(run())

    assert [chunk.splitlines()[0] for chunk in chunks] == ["event: error"]
    assert '"error":"secret-release-detail"' in chunks[0]
    assert provider.released == [("session_1", agent)]


def test_sse_disconnect_closes_stream_before_session_release() -> None:
    from agentos.channels.sse import SseAgentChannel

    async def run() -> None:
        order: list[str] = []
        agent = StreamRecordingAgent(build_agent_with_response("streamed"))
        provider = OrderingAsyncProvider(agent, order)
        channel = SseAgentChannel(provider)

        response = channel.stream_turn("session_1", b'{"message":"hello"}')
        first_chunk = await anext(response)
        assert first_chunk
        await response.aclose()

        assert order == [
            "stream_closed",
            "query_lease_released",
            "session_released",
        ]

    asyncio.run(run())
