from pathlib import Path

from agentos.capabilities import ToolCallRouter, ToolRegistry, read_file_tool
from agentos.context import ContextRenderer, ContextRuntime
from agentos.messages import MessageRuntime
from agentos.providers import FakeProvider, ProviderResponse, ProviderToolCall
from agentos.runtime import (
    ProviderRequestBuilder,
    QueryLoop,
    SessionState,
    ToolStreamCompleted,
    ToolStreamStarted,
    TurnStreamCompleted,
)


def test_streaming_query_loop_executes_tool_and_continues(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('name = "agent-os"', encoding="utf-8")
    context = ContextRuntime()
    messages = MessageRuntime()
    registry = ToolRegistry()
    registry.register(read_file_tool(root=tmp_path))
    router = ToolCallRouter(tool_registry=registry, context_runtime=context)
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
                stop_reason="tool_calls",
            ),
            ProviderResponse(content="项目名是 agent-os。", stop_reason="stop"),
        ],
    )
    loop = QueryLoop(
        context_runtime=context,
        message_runtime=messages,
        request_builder=ProviderRequestBuilder(
            context_renderer=ContextRenderer(),
            message_runtime=messages,
            tools=router.tool_specs(),
        ),
        provider=provider,
        tool_call_router=router,
        session_state=SessionState(id="session_stream"),
    )

    events = list(loop.run_turn_stream("读取项目名"))

    assert ToolStreamStarted(
        tool_name="read_file",
        tool_call_id="call_read",
    ) in events
    assert any(isinstance(event, ToolStreamCompleted) for event in events)
    assert events[-1] == TurnStreamCompleted(content="项目名是 agent-os。")
    assert provider.requests[1].messages[-1]["role"] == "tool"
    assert 'name = "agent-os"' in str(provider.requests[1].messages[-1]["content"])
