import inspect
from typing import get_type_hints

import pytest

import agentos.capabilities.router as router_module
from agentos.artifacts import ArtifactRuntime, InMemoryArtifactStore
from agentos.artifacts.tool_adapter import ArtifactToolAdapter
from agentos.capabilities import (
    RegisteredTool,
    SideEffectPolicy,
    ToolCallRouter,
    ToolInvocation,
    ToolRegistry,
)
from agentos.context_protocol import context_protocol_tool_specs
from agentos.compression import CompressionRuntime
from agentos.context import CompressedSegment, ContextRuntime, WorkingStateField
from agentos.context.state import working_state_value_to_json
from agentos.persistence import (
    InMemoryDurableSessionStore,
    InMemoryHotSessionStore,
)
from agentos.messages import MessageRef, MessageRuntime, StoredMessage
from agentos.policies import SecurityPolicy, SecurityPolicyError
from agentos.policies import BudgetPolicy
from agentos.recall import (
    CompressedSegmentPackage,
    InMemoryRecallIndex,
    RecallRuntime,
    SegmentRecallDocument,
    SegmentRepository,
)
from tests.planning._async import async_test
from tests.tool_invocation import make_tool_invocation


def test_tool_call_router_uses_stored_message_truth() -> None:
    assert "from agentos.messages import Message" not in inspect.getsource(router_module)
    hints = get_type_hints(ToolCallRouter._format_recalled_context)
    assert hints["messages"] == tuple[StoredMessage, ...]


@async_test
async def test_tool_call_router_result_persists_as_stored_message() -> None:
    registry = ToolRegistry()
    registry.register(
        RegisteredTool(
            name="echo",
            description="Echo text.",
            parameters={"type": "object"},
            handler=lambda invocation: str(invocation.arguments["text"]),
            side_effect_policy=SideEffectPolicy.PURE,
        ),
    )
    invocation = make_tool_invocation("echo", {"text": "hello"})
    result = await ToolCallRouter(tool_registry=registry).execute(invocation)
    messages = MessageRuntime()
    stored = messages.append_tool_result(result.tool_call_id, result.content)

    assert type(stored) is StoredMessage
    assert (stored.role, stored.tool_call_id, stored.content) == (
        "tool",
        invocation.context.tool_call_id,
        "hello",
    )


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
            handler=lambda invocation: str(invocation.arguments["text"]),
            side_effect_policy=SideEffectPolicy.PURE,
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
            handler=lambda invocation: str(invocation.arguments),
            side_effect_policy=SideEffectPolicy.PURE,
        ),
    )
    registry.register(
        RegisteredTool(
            name="internal_context_tool",
            description="Internal context tool.",
            parameters={"type": "object"},
            handler=lambda invocation: str(invocation.arguments),
            side_effect_policy=SideEffectPolicy.PURE,
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
            handler=lambda invocation: str(invocation.arguments),
            side_effect_policy=SideEffectPolicy.PURE,
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
                handler=lambda _invocation: "ok",
                side_effect_policy=SideEffectPolicy.PURE,
            ),
        )


@async_test
async def test_tool_call_router_executes_external_tool_calls() -> None:
    registry = ToolRegistry()
    registry.register(
        RegisteredTool(
            name="echo",
            description="Echo text.",
            parameters={"type": "object"},
            handler=lambda invocation: f"echo:{invocation.arguments['text']}",
            side_effect_policy=SideEffectPolicy.PURE,
        ),
    )
    runtime = ToolCallRouter(tool_registry=registry)

    invocation = make_tool_invocation("echo", {"text": "hello"})
    result = await runtime.execute(invocation)

    assert result.tool_call_id == invocation.context.tool_call_id
    assert result.content == "echo:hello"


@async_test
async def test_tool_executor_passes_nested_arguments_in_invocation() -> None:
    captured: list[ToolInvocation] = []
    registry = ToolRegistry()
    registry.register(
        RegisteredTool(
            name="batch",
            description="Process a batch.",
            parameters={
                "type": "object",
                "properties": {"items": {"type": "array"}},
                "required": ["items"],
            },
            handler=lambda invocation: captured.append(invocation) or "done",
            side_effect_policy=SideEffectPolicy.PURE,
        ),
    )

    result = await ToolCallRouter(tool_registry=registry).execute(
        make_tool_invocation("batch", {"items": [{"name": "first"}]}),
    )

    assert result.content == "done"
    assert [invocation.arguments for invocation in captured] == [
        {"items": [{"name": "first"}]},
    ]


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
    assert "load_image" not in tool_names


def test_start_chapter_schema_validates_optional_field_items() -> None:
    start_chapter = next(
        spec
        for spec in context_protocol_tool_specs()
        if spec["function"]["name"] == "start_chapter"
    )
    fields = start_chapter["function"]["parameters"]["properties"]["fields"]

    assert fields["items"]["required"] == ["name", "type", "purpose"]


@async_test
async def test_security_policy_denies_tool_before_handler_runs() -> None:
    called: list[bool] = []
    registry = ToolRegistry()
    registry.register(
        RegisteredTool(
            name="danger",
            description="Dangerous tool.",
            parameters={"type": "object"},
            handler=lambda _invocation: called.append(True) or "done",
            side_effect_policy=SideEffectPolicy.PURE,
        ),
    )
    runtime = ToolCallRouter(
        tool_registry=registry,
        security_policy=SecurityPolicy(denied_tools={"danger"}),
    )

    with pytest.raises(SecurityPolicyError, match="denied"):
        await runtime.execute(make_tool_invocation("danger", {}))

    assert called == []


@async_test
async def test_security_policy_denies_context_tools_before_state_mutation() -> None:
    context = ContextRuntime()
    context.declare_schema(
        [
            WorkingStateField(
                name="task_goal",
                type="string",
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
        await runtime.execute(
            make_tool_invocation(
                "update_state",
                {
                    "field_name": "task_goal",
                    "value": "mutated",
                },
            ),
        )

    assert context.state.working_state == {}


@async_test
async def test_tool_call_router_routes_context_tool_calls_to_context_runtime() -> None:
    context = ContextRuntime()
    context.declare_schema(
        [
            WorkingStateField(
                name="task_goal",
                type="string",
                purpose="当前任务目标和完成标准",
            ),
        ],
    )
    runtime = ToolCallRouter(
        tool_registry=ToolRegistry(),
        context_runtime=context,
    )

    result = await runtime.execute(
        make_tool_invocation(
            "update_state",
            {
                "field_name": "task_goal",
                "value": "Run a small agent.",
            },
        ),
    )

    assert result.content == "context tool update_state applied"
    assert context.state.working_state["task_goal"] == "Run a small agent."


@async_test
async def test_tool_call_router_routes_recall_context_to_recall_runtime() -> None:
    context = ContextRuntime()
    messages = MessageRuntime()
    original_user = messages.append_user("Original detail")
    original_assistant = messages.append_assistant("Original answer")
    current_user = messages.append_user("Current task")
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
            message_runtime=messages,
            segment_repository=SegmentRepository.from_runtime(
                compression.index,
                messages,
                session_id="session_1",
            ),
            session_id="session_1",
        ),
    )

    invocation = make_tool_invocation("recall_context", {"handle": "seg_1"})
    result = await runtime.execute(invocation)

    assert result.tool_call_id == invocation.context.tool_call_id
    assert '<recalled-context source="compressed_history" handle="seg_1">' in result.content
    assert '<message role="user"' in result.content
    assert "Original detail" in result.content
    assert [message.content for message in messages.materialize_active()] == [
        "Original detail",
        "Original answer",
        "Current task",
    ]
    assert messages.active_window.snapshot_refs() == (
        MessageRef(original_user.id, temporary=True),
        MessageRef(original_assistant.id, temporary=True),
        MessageRef(current_user.id),
    )


@async_test
async def test_tool_call_router_routes_load_attachment_namespace() -> None:
    artifacts = ArtifactRuntime(
        session_id="session_1",
        store=InMemoryArtifactStore(),
    )
    artifact = await artifacts.upload(
        data=b"image-bytes",
        filename="diagram.png",
        media_type="image/png",
    )
    registry = ToolRegistry()
    for tool in ArtifactToolAdapter(artifacts).registered_tools():
        registry.register(tool)
    runtime = ToolCallRouter(tool_registry=registry)

    result = await runtime.execute(
        make_tool_invocation("load_attachment", {"handle": artifact.id}),
    )

    assert result.tool_call_id == "call_load_attachment"
    assert result.content == (
        f"附件已挂载：{artifact.id}。"
        "附件内容将在下一次模型请求中作为当前轮次的工具结果数据提供。"
    )


@async_test
async def test_tool_call_router_does_not_route_attachment_handles_through_recall_context() -> None:
    runtime = ToolCallRouter(tool_registry=ToolRegistry())

    with pytest.raises(RuntimeError, match="recall runtime is required"):
        await runtime.execute(
            make_tool_invocation(
                "recall_context",
                {
                    "handle": "art_12345678-1234-4234-9234-123456789abc"
                },
            ),
        )


@async_test
async def test_update_state_tool_accepts_nested_json_value() -> None:
    context = ContextRuntime()
    context.declare_schema(
        [
            WorkingStateField(
                name="config",
                type="object",
                purpose="嵌套配置对象",
            ),
        ],
    )
    runtime = ToolCallRouter(
        tool_registry=ToolRegistry(),
        context_runtime=context,
    )
    nested_value = {"theme": "dark", "limits": [1, 2, 3]}

    result = await runtime.execute(
        make_tool_invocation(
            "update_state",
            {
                "field_name": "config",
                "value": nested_value,
            },
        ),
    )

    assert result.content == "context tool update_state applied"
    restored = working_state_value_to_json(context.state.working_state["config"])
    assert restored == {"theme": "dark", "limits": [1, 2, 3]}


@async_test
async def test_tool_call_router_routes_query_recall_context_to_segment_repository() -> None:
    messages = MessageRuntime()
    durable_store = InMemoryDurableSessionStore()
    segment_repository = SegmentRepository(
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
    segment_repository.record_compressed_segment(package)
    durable_store.append_message(
        "session_1",
        StoredMessage(id="msg_1", role="user", content="读取 pyproject.toml"),
    )
    runtime = ToolCallRouter(
        tool_registry=ToolRegistry(),
        recall_runtime=RecallRuntime(
            message_runtime=messages,
            segment_repository=segment_repository,
            session_id="session_1",
        ),
    )

    invocation = make_tool_invocation(
        "recall_context",
        {"query": "pyproject 项目名", "limit": 1},
    )
    result = await runtime.execute(invocation)

    assert result.tool_call_id == invocation.context.tool_call_id
    assert '<recalled-context source="semantic_recall"' in result.content
    assert "pyproject.toml" in result.content
    assert [message.content for message in messages.materialize_active()] == [
        "读取 pyproject.toml",
    ]
    assert messages.active_window.snapshot_refs() == (
        MessageRef("msg_1", temporary=True),
    )

