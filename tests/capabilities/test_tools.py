import pytest

from agentos.capabilities import ToolCallRouter, RegisteredTool, ToolRegistry
from agentos.compression import CompressionRuntime
from agentos.context import ContextRuntime, WorkingStateField
from agentos.messages import MessageRuntime
from agentos.policies import SecurityPolicy, SecurityPolicyError
from agentos.policies import BudgetPolicy
from agentos.providers import ProviderToolCall
from agentos.recall import RecallRuntime


def test_tool_registry_exports_provider_tool_specs() -> None:
    registry = ToolRegistry()
    registry.register(
        RegisteredTool(
            name="echo",
            description="Echo text.",
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            handler=lambda arguments: str(arguments["text"]),
        ),
    )

    assert registry.provider_tool_specs() == [
        {
            "type": "function",
            "function": {
                "name": "echo",
                "description": "Echo text.",
                "parameters": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
            },
        },
    ]


def test_tool_registry_provider_specs_include_only_external_tools() -> None:
    registry = ToolRegistry()
    registry.register(
        RegisteredTool(
            name="echo",
            description="Echo text.",
            parameters={"type": "object"},
            handler=lambda arguments: str(arguments),
        ),
    )
    registry.register(
        RegisteredTool(
            name="internal_context_tool",
            description="Internal context tool.",
            parameters={"type": "object"},
            handler=lambda arguments: str(arguments),
            kind="context",
        ),
    )

    names = [
        spec["function"]["name"]
        for spec in registry.provider_tool_specs()
    ]

    assert names == ["echo"]


def test_tool_registry_exports_capability_plane_tool_group() -> None:
    registry = ToolRegistry()
    registry.register(
        RegisteredTool(
            name="echo",
            description="Echo text.",
            parameters={"type": "object"},
            handler=lambda arguments: str(arguments),
        ),
    )

    group = registry.capability_tool_group("Runtime tools")

    assert group.name == "Runtime tools"
    assert [(tool.name, tool.description) for tool in group.tools] == [
        ("echo", "Echo text."),
    ]


def test_tool_call_router_executes_external_tool_calls() -> None:
    registry = ToolRegistry()
    registry.register(
        RegisteredTool(
            name="echo",
            description="Echo text.",
            parameters={"type": "object"},
            handler=lambda arguments: f"echo:{arguments['text']}",
        ),
    )
    runtime = ToolCallRouter(tool_registry=registry)

    result = runtime.execute_tool_call(
        ProviderToolCall(
            id="call_1",
            name="echo",
            arguments={"text": "hello"},
        ),
    )

    assert result.tool_call_id == "call_1"
    assert result.content == "echo:hello"


def test_tool_call_router_exposes_context_protocol_tool_specs() -> None:
    runtime = ToolCallRouter(tool_registry=ToolRegistry())

    tool_names = [
        spec["function"]["name"]
        for spec in runtime.tool_specs()
    ]

    assert tool_names[:5] == [
        "declare_schema",
        "update_state",
        "extend_schema",
        "start_chapter",
        "recall_context",
    ]


def test_security_policy_denies_tool_before_handler_runs() -> None:
    called: list[bool] = []
    registry = ToolRegistry()
    registry.register(
        RegisteredTool(
            name="danger",
            description="Dangerous tool.",
            parameters={"type": "object"},
            handler=lambda arguments: called.append(True) or "done",
        ),
    )
    runtime = ToolCallRouter(
        tool_registry=registry,
        security_policy=SecurityPolicy(denied_tools={"danger"}),
    )

    with pytest.raises(SecurityPolicyError, match="denied"):
        runtime.execute_tool_call(ProviderToolCall(id="call_1", name="danger"))

    assert called == []


def test_security_policy_denies_context_tools_before_state_mutation() -> None:
    context = ContextRuntime()
    context.declare_schema(
        [
            WorkingStateField(
                name="task_goal",
                type="str",
                purpose="当前任务目标和完成标准",
            ),
        ],
    )
    runtime = ToolCallRouter(
        tool_registry=ToolRegistry(),
        context_runtime=context,
        security_policy=SecurityPolicy(denied_tools={"update_state"}),
    )

    with pytest.raises(SecurityPolicyError, match="denied"):
        runtime.execute_tool_call(
            ProviderToolCall(
                id="call_1",
                name="update_state",
                arguments={
                    "field_name": "task_goal",
                    "value": "mutated",
                },
            ),
        )

    assert context.state.working_state == {}


def test_tool_call_router_routes_context_tool_calls_to_context_runtime() -> None:
    context = ContextRuntime()
    context.declare_schema(
        [
            WorkingStateField(
                name="task_goal",
                type="str",
                purpose="当前任务目标和完成标准",
            ),
        ],
    )
    runtime = ToolCallRouter(
        tool_registry=ToolRegistry(),
        context_runtime=context,
    )

    result = runtime.execute_tool_call(
        ProviderToolCall(
            id="call_1",
            name="update_state",
            arguments={
                "field_name": "task_goal",
                "value": "Run a small agent.",
            },
        ),
    )

    assert result.content == "context tool update_state applied"
    assert context.state.working_state["task_goal"] == "Run a small agent."


def test_tool_call_router_routes_recall_context_to_recall_runtime() -> None:
    context = ContextRuntime()
    messages = MessageRuntime()
    messages.append_user("Original detail")
    messages.append_assistant("Original answer")
    messages.append_user("Current task")
    compression = CompressionRuntime(
        context_runtime=context,
        message_runtime=messages,
        budget_policy=BudgetPolicy(max_active_messages=2, retain_latest_messages=1),
    )
    compression.maybe_compress()
    runtime = ToolCallRouter(
        tool_registry=ToolRegistry(),
        context_runtime=context,
        recall_runtime=RecallRuntime(
            compression_index=compression.index,
            message_runtime=messages,
        ),
    )

    result = runtime.execute_tool_call(
        ProviderToolCall(
            id="call_recall",
            name="recall_context",
            arguments={"handle": "seg_1"},
        ),
    )

    assert result.tool_call_id == "call_recall"
    assert "recalled 2 message(s)" in result.content
    assert [message["content"] for message in messages.materialize_provider_messages()] == [
        "Original detail",
        "Original answer",
        "Current task",
    ]
