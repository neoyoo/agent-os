from __future__ import annotations

from agentos.context import ContextRenderer, ContextRuntime
from agentos.messages import MessageRuntime
from agentos.providers import FakeProvider, ProviderResponse
from agentos.runtime import Agent, ProviderRequestBuilder
from tests.multi.helpers import build_agent_with_response


def build_stream_agent(provider: FakeProvider) -> Agent:
    messages = MessageRuntime()
    return Agent(
        query_loop_kwargs={
            "context_runtime": ContextRuntime(),
            "message_runtime": messages,
            "request_builder": ProviderRequestBuilder(
                context_renderer=ContextRenderer(),
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


def test_sse_channel_streams_existing_sse_chunks_and_releases_agent() -> None:
    from agentos.channels.sse import SseAgentChannel

    agent = build_agent_with_response("streamed")
    provider = RecordingProvider(agent)
    channel = SseAgentChannel(provider)

    chunks = list(channel.stream_turn("session_1", b'{"message":"hello"}'))

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

    chunks = list(
        channel.stream_turn(
            "session_1",
            b'{"message":"hello","thinking":true,"show_thinking":false}',
        ),
    )

    assert all("thinking_delta" not in chunk for chunk in chunks)
    assert any("content_delta" in chunk for chunk in chunks)


def test_sse_channel_releases_agent_if_on_agent_callback_fails() -> None:
    from agentos.channels.sse import SseAgentChannel

    agent = build_agent_with_response("unused")
    provider = RecordingProvider(agent)
    channel = SseAgentChannel(provider)

    def fail_callback(agent: Agent) -> None:
        raise RuntimeError("callback failed")

    chunks = list(
        channel.stream_turn(
            "session_1",
            b'{"message":"hello"}',
            on_agent=fail_callback,
        )
    )

    assert any("callback failed" in chunk for chunk in chunks)
    assert provider.released == [("session_1", agent)]
