from agentos import Agent
from agentos.context import ContextRenderer, ContextRuntime
from agentos.messages import MessageRuntime
from agentos.providers import FakeProvider, ProviderResponse
from agentos.runtime import ProviderRequestBuilder


def build_agent(provider: FakeProvider) -> Agent:
    context = ContextRuntime()
    messages = MessageRuntime()
    return Agent(
        query_loop_kwargs={
            "context_runtime": context,
            "message_runtime": messages,
            "request_builder": ProviderRequestBuilder(
                context_renderer=ContextRenderer(),
                message_runtime=messages,
                tools=[],
            ),
            "provider": provider,
        },
    )


def test_agent_run_returns_result_without_extra_objects() -> None:
    agent = build_agent(FakeProvider([ProviderResponse(content="ok")]))

    result = agent.run("hello")

    assert result.content == "ok"


def test_agent_stream_accepts_per_turn_thinking_options() -> None:
    agent = build_agent(
        FakeProvider(
            [
                ProviderResponse(
                    content="answer",
                    thinking_content="think",
                ),
            ],
        ),
    )

    events = list(agent.stream("hello", thinking=True, show_thinking=True))

    assert "AssistantThinkingDelta" in [type(event).__name__ for event in events]


def test_agent_stream_sse_returns_sse_strings() -> None:
    agent = build_agent(FakeProvider([ProviderResponse(content="ok")]))

    chunks = list(agent.stream_sse("hello"))

    assert any(chunk.startswith("event: content_delta") for chunk in chunks)
    assert chunks[-1].startswith("event: done")


def test_agent_stream_jsonl_returns_json_lines() -> None:
    agent = build_agent(FakeProvider([ProviderResponse(content="ok")]))

    chunks = list(agent.stream_jsonl("hello"))

    assert any('"type":"content_delta"' in chunk for chunk in chunks)
    assert all(chunk.endswith("\n") for chunk in chunks)


def test_agent_callbacks_receive_specific_delta_events() -> None:
    agent = build_agent(FakeProvider([ProviderResponse(content="ok")]))
    deltas: list[str] = []

    result = agent.run_with_callbacks(
        "hello",
        on_content_delta=lambda text: deltas.append(text),
    )

    assert result.content == "ok"
    assert deltas == ["ok"]
