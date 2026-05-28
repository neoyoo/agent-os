import asyncio

from agentos import AgentBuilder
from agentos.capabilities import RegisteredTool
from agentos.events import EventBus, ToolResultCappedEvent
from agentos.policies import ToolResultBudget
from agentos.providers import FakeProvider, ProviderResponse, ProviderToolCall
from agentos.providers import provider_message_to_dict
from agentos.tokens import HeuristicTokenCounter


def oversized_tool() -> RegisteredTool:
    return RegisteredTool(
        name="read_large_file",
        description="Read a large file.",
        parameters={"type": "object", "properties": {}},
        handler=lambda arguments: "x" * 100,
    )


def provider_for_tool_call() -> FakeProvider:
    return FakeProvider(
        [
            ProviderResponse(
                tool_calls=[
                    ProviderToolCall(
                        id="call_read",
                        name="read_large_file",
                        arguments={},
                    ),
                ],
            ),
            "handled capped result",
        ],
    )


def test_query_loop_caps_oversized_tool_result_before_appending_message() -> None:
    provider = provider_for_tool_call()
    bus = EventBus()
    agent = (
        AgentBuilder()
        .provider(provider)
        .tools([oversized_tool()])
        .event_bus(bus)
        .tool_result_budget(ToolResultBudget(default_max_tokens=5))
        .token_counter(HeuristicTokenCounter(char_per_token=1))
        .build()
    )

    result = agent.run("Use read_large_file.")

    assert result.content == "handled capped result"
    tool_message = provider_message_to_dict(provider.requests[1].messages[2])
    assert tool_message["role"] == "tool"
    assert tool_message["tool_call_id"] == "call_read"
    assert "tool result omitted" in str(tool_message["content"])
    assert "read_large_file" in str(tool_message["content"])
    assert "xxxxx" not in str(tool_message["content"])
    assert any(
        isinstance(event, ToolResultCappedEvent)
        and event.tool_name == "read_large_file"
        and event.actual_tokens == 100
        and event.cap == 5
        for event in bus.events
    )


def test_async_query_loop_caps_oversized_tool_result_before_appending_message() -> None:
    provider = provider_for_tool_call()
    agent = (
        AgentBuilder()
        .provider(provider)
        .tools([oversized_tool()])
        .tool_result_budget(ToolResultBudget(default_max_tokens=5))
        .token_counter(HeuristicTokenCounter(char_per_token=1))
        .build_async()
    )

    result = asyncio.run(agent.async_run("Use read_large_file."))

    assert result.content == "handled capped result"
    tool_message = provider_message_to_dict(provider.requests[1].messages[2])
    assert "tool result omitted" in str(tool_message["content"])
    assert "xxxxx" not in str(tool_message["content"])
