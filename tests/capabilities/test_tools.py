import pytest

from agentos.capabilities import ToolCallRouter, RegisteredTool, ToolRegistry
from agentos.context_protocol import context_protocol_tool_specs
from agentos.compression import CompressionRuntime
from agentos.context import CompressedSegment, ContextRuntime, WorkingStateField
from agentos.compression import CompressionIndex
from agentos.memory import CompressedSegmentPackage, MemoryRuntime, SegmentRecallDocument
from agentos.memory.in_memory import (
    InMemoryDurableSessionStore,
    InMemoryHotSessionStore,
    InMemoryRecallIndex,
)
from agentos.messages import Message, MessageRuntime
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


def test_tool_registry_rejects_external_tools_with_mcp_prefix() -> None:
    registry = ToolRegistry()

    with pytest.raises(ValueError, match="reserved MCP prefix"):
        registry.register(
            RegisteredTool(
                name="mcp__github__create_issue",
                description="Collides with MCP routing.",
                parameters={"type": "object"},
                handler=lambda arguments: "ok",
            ),
        )


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


def test_start_chapter_schema_validates_optional_field_items() -> None:
    start_chapter = next(
        spec
        for spec in context_protocol_tool_specs()
        if spec["function"]["name"] == "start_chapter"
    )
    fields = start_chapter["function"]["parameters"]["properties"]["fields"]

    assert fields["items"]["required"] == ["name", "type", "purpose"]


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


def test_tool_call_router_routes_query_recall_context_to_memory_runtime() -> None:
    messages = MessageRuntime()
    durable_store = InMemoryDurableSessionStore()
    memory_runtime = MemoryRuntime(
        hot_store=InMemoryHotSessionStore(),
        durable_store=durable_store,
        recall_index=InMemoryRecallIndex(),
    )
    package = CompressedSegmentPackage(
        segment=CompressedSegment(
            id="seg_1",
            topic="读取 pyproject.toml",
            summary="项目名是 agent-os。",
        ),
        source_refs=("msg_1",),
        recall_document=SegmentRecallDocument(
            session_id="session_1",
            segment_id="seg_1",
            topic="读取 pyproject.toml",
            summary="项目名是 agent-os。",
            keywords=("pyproject.toml", "agent-os"),
        ),
    )
    memory_runtime.record_compressed_segment(package)
    durable_store.append_message(
        "session_1",
        Message(id="msg_1", role="user", content="读取 pyproject.toml"),
    )
    runtime = ToolCallRouter(
        tool_registry=ToolRegistry(),
        recall_runtime=RecallRuntime(
            compression_index=CompressionIndex(),
            message_runtime=messages,
            memory_runtime=memory_runtime,
            session_id="session_1",
        ),
    )

    result = runtime.execute_tool_call(
        ProviderToolCall(
            id="call_recall",
            name="recall_context",
            arguments={"query": "pyproject 项目名", "limit": 1},
        ),
    )

    assert result.tool_call_id == "call_recall"
    assert "recalled 1 message(s)" in result.content
    assert [message["content"] for message in messages.materialize_provider_messages()] == [
        "读取 pyproject.toml",
    ]
