from datetime import UTC, datetime

from agentos.artifacts import ArtifactRuntime, InMemoryArtifactStore
from agentos.artifacts.projection import ArtifactMountProjectionProvider
from agentos.artifacts.tool_adapter import ArtifactToolAdapter
from agentos.capabilities import (
    SideEffectPolicy,
    ToolCallRouter,
    ToolConcurrencyPolicy,
    ToolRegistry,
)
from agentos.capabilities.executor import ToolExecutionResult
from agentos.capabilities.tools import ToolExecutionContract
from agentos.context import ContextRuntime
from agentos.context import WorkingStateField
from agentos.messages import MessageRuntime, ToolCall
from agentos.providers import FakeProvider, ProviderToolCall
from agentos.runtime import ProviderRequestBuilder, QueryLoop
from agentos.runtime.completed_result_projection import CompletedResultProjector
from agentos.runtime.run_runtime import RunWriteGuard
from agentos.runtime.run import RunOptions
from agentos.runtime.execution import RunExecutionCursor
from agentos.runtime.payloads import PayloadProtectionContext
from agentos.runtime.session import SessionState
from agentos.runtime.side_effect_memory import InMemorySideEffectStore
from agentos.runtime.tool_invocations import build_tool_invocation_plan
from agentos.runtime.tool_side_effect_runtime import ToolSideEffectRuntime
from agentos.runtime.tool_payloads import ToolPayloadRuntime
from agentos.security import FernetPayloadProtector
from tests._context_protocol_fixtures import default_context_renderer
from tests.planning._async import async_test


def _entry(name: str, arguments: dict[str, object]):  # type: ignore[no-untyped-def]
    return build_tool_invocation_plan(
        tenant_id=None,
        session_id="session_1",
        run_id="run_1",
        turn_id="turn_1",
        provider_call_index=0,
        assistant_message_id="message_1",
        calls=(ProviderToolCall("call_1", name, arguments),),
    ).entries[0]


def _contract() -> ToolExecutionContract:
    return ToolExecutionContract(
        SideEffectPolicy.IDEMPOTENT,
        ToolConcurrencyPolicy.EXCLUSIVE,
    )


@async_test
async def test_completed_context_mutation_is_replayed_without_handler() -> None:
    store = InMemorySideEffectStore()
    guard = RunWriteGuard(0)
    context = ContextRuntime()
    entry = _entry(
        "declare_schema",
        {
            "fields": [
                {"name": "goal", "type": "string", "purpose": "task goal"},
            ],
        },
    )
    producer_calls = 0

    async def produce(_invocation):  # type: ignore[no-untyped-def]
        nonlocal producer_calls
        producer_calls += 1
        return ToolExecutionResult("call_1", "context tool declare_schema applied")

    await ToolSideEffectRuntime(store).execute(
        entry,
        _contract(),
        guard=guard,
        produce=produce,
    )
    result = await ToolSideEffectRuntime(
        store,
        CompletedResultProjector(context_runtime=context),
    ).execute(
        entry,
        _contract(),
        guard=guard,
        produce=produce,
    )

    assert result == ToolExecutionResult(
        "call_1",
        "context tool declare_schema applied",
    )
    assert tuple(field.name for field in context.snapshot().working_state_schema.fields) == (
        "goal",
    )
    assert producer_calls == 1


@async_test
async def test_completed_load_attachment_restores_mount_without_handler() -> None:
    store = InMemorySideEffectStore()
    guard = RunWriteGuard(0)
    artifacts = ArtifactRuntime(
        session_id="session_1",
        store=InMemoryArtifactStore(
            clock=lambda: datetime(2026, 7, 20, tzinfo=UTC),
            id_factory=lambda: "art_00000000-0000-4000-8000-000000000001",
        ),
    )
    artifact = await artifacts.upload(
        data=b"drawing",
        filename="drawing.png",
        media_type="image/png",
    )
    entry = _entry("load_attachment", {"handle": artifact.id})
    content = (
        f"附件已挂载：{artifact.id}。"
        "附件内容将在下一次模型请求中作为当前轮次的工具结果数据提供。"
    )
    producer_calls = 0

    async def produce(_invocation):  # type: ignore[no-untyped-def]
        nonlocal producer_calls
        producer_calls += 1
        return ToolExecutionResult("call_1", content)

    await ToolSideEffectRuntime(store).execute(
        entry,
        _contract(),
        guard=guard,
        produce=produce,
    )
    result = await ToolSideEffectRuntime(
        store,
        CompletedResultProjector(artifact_runtime=artifacts),
    ).execute(
        entry,
        _contract(),
        guard=guard,
        produce=produce,
    )

    assert result == ToolExecutionResult("call_1", content)
    assert artifacts.active_mounts()[0].artifact_id == artifact.id
    assert artifacts.active_mounts()[0].reason == "tool_result"
    assert producer_calls == 1


@async_test
async def test_after_tools_recovery_restores_only_ephemeral_attachment_mount() -> None:
    store = InMemorySideEffectStore()
    guard = RunWriteGuard(0)
    context = ContextRuntime()
    context.declare_schema([WorkingStateField("existing", "string", "keep")])
    artifacts = ArtifactRuntime(
        session_id="session_1",
        store=InMemoryArtifactStore(
            clock=lambda: datetime(2026, 7, 20, tzinfo=UTC),
            id_factory=lambda: "art_00000000-0000-4000-8000-000000000002",
        ),
    )
    artifact = await artifacts.upload(
        data=b"drawing",
        filename="drawing.png",
        media_type="image/png",
    )
    plan = build_tool_invocation_plan(
        tenant_id=None,
        session_id="session_1",
        run_id="run_1",
        turn_id="turn_1",
        provider_call_index=0,
        assistant_message_id="message_1",
        calls=(
            ProviderToolCall("call_1", "start_chapter", {}),
            ProviderToolCall("call_2", "load_attachment", {"handle": artifact.id}),
        ),
    )
    runtime = ToolSideEffectRuntime(store)
    for entry, content in zip(
        plan.entries,
        (
            "context tool start_chapter applied",
            (
                f"附件已挂载：{artifact.id}。"
                "附件内容将在下一次模型请求中作为当前轮次的工具结果数据提供。"
            ),
        ),
        strict=True,
    ):
        await runtime.execute(
            entry,
            _contract(),
            guard=guard,
            produce=lambda invocation, content=content: _result(invocation, content),
        )

    replay = ToolSideEffectRuntime(
        store,
        CompletedResultProjector(
            context_runtime=context,
            artifact_runtime=artifacts,
        ),
    )
    await replay.restore_after_tools(
        plan=plan,
        contracts=(_contract(), _contract()),
        guard=guard,
    )

    assert tuple(field.name for field in context.snapshot().working_state_schema.fields) == (
        "existing",
    )
    assert artifacts.active_mounts()[0].artifact_id == artifact.id


async def _result(invocation, content: str):  # type: ignore[no-untyped-def]
    return ToolExecutionResult(invocation.context.tool_call_id, content)


@async_test
async def test_after_tools_query_recovery_mounts_attachment_before_provider() -> None:
    context = ContextRuntime(session_id="session_1")
    messages = MessageRuntime()
    artifacts = ArtifactRuntime(
        session_id="session_1",
        store=InMemoryArtifactStore(
            clock=lambda: datetime(2026, 7, 20, tzinfo=UTC),
            id_factory=lambda: "art_00000000-0000-4000-8000-000000000003",
        ),
    )
    artifact = await artifacts.upload(
        data=b"drawing",
        filename="drawing.png",
        media_type="image/png",
    )
    registry = ToolRegistry()
    for tool in ArtifactToolAdapter(artifacts).registered_tools():
        registry.register(tool)
    router = ToolCallRouter(registry, context_runtime=context)
    provider = FakeProvider(["done"])
    payloads = ToolPayloadRuntime(
        FernetPayloadProtector(FernetPayloadProtector.generate_key()),
        PayloadProtectionContext(None, "session_1"),
    )
    side_effects = InMemorySideEffectStore()
    session = SessionState("session_1")
    turn = session.new_turn("continue", turn_id="turn_1")
    call = ProviderToolCall("call_1", "load_attachment", {"handle": artifact.id})
    assistant = messages.append_assistant(
        "",
        [ToolCall(call.id, call.name, call.arguments)],
    )
    plan = payloads.build_plan(
        run_id="run_1",
        turn_id=turn.id,
        provider_call_index=0,
        assistant_message_id=assistant.id,
        calls=(call,),
    )
    pending = payloads.pending_cursor(plan)
    content = (
        f"附件已挂载：{artifact.id}。"
        "附件内容将在下一次模型请求中作为当前轮次的工具结果数据提供。"
    )
    await ToolSideEffectRuntime(side_effects).execute(
        plan.entries[0],
        _contract(),
        guard=RunWriteGuard(0),
        produce=lambda invocation: _result(invocation, content),
    )
    messages.append_tool_result(call.id, content)
    cursor = RunExecutionCursor(
        turn_id=pending.turn_id,
        stage="after_tools",
        provider_call_index=pending.provider_call_index,
        assistant_message_id=pending.assistant_message_id,
    )
    loop = QueryLoop(
        context_runtime=context,
        message_runtime=messages,
        request_builder=ProviderRequestBuilder(
            context_renderer=default_context_renderer(),
            message_runtime=messages,
            tools=router.tool_specs(),
            input_projections=(ArtifactMountProjectionProvider(artifacts),),
        ),
        provider=provider,
        tool_call_router=router,
        session_state=session,
        artifact_runtime=artifacts,
        tool_payload_runtime=payloads,
        side_effect_store=side_effects,
    )

    _ = [
        event
        async for event in loop._run_provider_tool_events(
            "run_1",
            turn,
            RunOptions(),
            cursor,
        )
    ]

    assert any(item.kind == "context_mount" for item in provider.requests[0].messages)
