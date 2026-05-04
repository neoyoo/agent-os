from pathlib import Path

from agentos.capabilities import ToolCallRouter, ToolRegistry
from agentos.capabilities.builtin import read_file_tool
from agentos.context import ContextRenderer, ContextRuntime
from agentos.messages import MessageRuntime
from agentos.providers import FakeProvider, ProviderResponse, ProviderToolCall
from agentos.runtime import (
    AssistantMessageAppendedEvent,
    EventBus,
    ProviderRequestBuilder,
    ProviderRequestBuiltEvent,
    ProviderResponseReceivedEvent,
    QueryLoop,
    ToolCallRequestedEvent,
    ToolExecutionCompletedEvent,
    ToolExecutionStartedEvent,
    ToolResultAppendedEvent,
    TurnCompletedEvent,
    TurnStartedEvent,
    UserMessageAppendedEvent,
    SessionState,
)


def test_small_agent_reads_project_file_with_tool_call_loop() -> None:
    context = ContextRuntime()
    messages = MessageRuntime()
    registry = ToolRegistry()
    registry.register(read_file_tool(root=Path.cwd()))
    capabilities = ToolCallRouter(
        tool_registry=registry,
        context_runtime=context,
    )
    provider = FakeProvider(
        [
            ProviderResponse(
                tool_calls=[
                    ProviderToolCall(
                        id="call_read",
                        name="read_file",
                        arguments={"path": "pyproject.toml"},
                    ),
                ],
            ),
            ProviderResponse(content="项目名是 agent-os。"),
        ],
    )
    event_bus = EventBus()
    loop = QueryLoop(
        context_runtime=context,
        message_runtime=messages,
        request_builder=ProviderRequestBuilder(
            context_renderer=ContextRenderer(),
            message_runtime=messages,
            tools=capabilities.tool_specs(),
        ),
        provider=provider,
        tool_call_router=capabilities,
        event_bus=event_bus,
        session_state=SessionState(id="session_small_agent"),
    )

    answer = loop.run_turn("读取 pyproject.toml 里的项目名")

    assert answer == "项目名是 agent-os。"
    assert provider.requests[0].tools == capabilities.tool_specs()
    assert provider.requests[0].messages == [
        {"role": "user", "content": "读取 pyproject.toml 里的项目名"},
    ]
    assert provider.requests[1].messages[0] == {
        "role": "user",
        "content": "读取 pyproject.toml 里的项目名",
    }
    assert provider.requests[1].messages[1]["tool_calls"] == [
        {
            "id": "call_read",
            "name": "read_file",
            "arguments": {"path": "pyproject.toml"},
        },
    ]
    assert provider.requests[1].messages[2]["role"] == "tool"
    assert provider.requests[1].messages[2]["tool_call_id"] == "call_read"
    assert 'name = "agent-os"' in str(provider.requests[1].messages[2]["content"])

    event_classes = [event.__class__ for event in event_bus.events]
    assert event_classes == [
        TurnStartedEvent,
        UserMessageAppendedEvent,
        ProviderRequestBuiltEvent,
        ProviderResponseReceivedEvent,
        AssistantMessageAppendedEvent,
        ToolCallRequestedEvent,
        ToolExecutionStartedEvent,
        ToolExecutionCompletedEvent,
        ToolResultAppendedEvent,
        ProviderRequestBuiltEvent,
        ProviderResponseReceivedEvent,
        AssistantMessageAppendedEvent,
        TurnCompletedEvent,
    ]
