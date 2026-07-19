from __future__ import annotations

import asyncio
import gc
import weakref
from datetime import UTC, datetime
from threading import Event, Thread

import pytest

from agentos import AgentBuilder
from agentos.capabilities import RegisteredTool, WaitRequest
from agentos.capabilities.skill_activation import SkillActivationStoreClosedError
from agentos.capabilities.skills import (
    BuiltinSkillSource,
    SkillDefinition,
    SkillRegistry,
    SkillRuntime,
    SkillTrustDecision,
)
from agentos.memory.records import MemoryRecord, MemorySelectionContext
from agentos.memory.runtime import BoundMemoryProjectionProvider, MemoryRuntime
from agentos.memory.sqlite_errors import SQLiteMemoryStoreClosedError
from agentos.planning.models import PlanState, PlanStep
from agentos.planning.projection import (
    AuthorizedPlanSource,
    BoundPlanProjectionProvider,
)
from agentos.planning.sqlite_errors import SQLitePlanStoreClosedError
from agentos.providers import (
    FakeProvider,
    ProviderResponse,
    ProviderToolCall,
    TextPart,
)
from agentos.runtime import AgentWaiting, TurnStreamWaiting, WaitReason
from agentos.runtime.errors import AgentBusyError, DurableStoreClosedError
from agentos.durable import DurableRuntimeProfile
from agentos.security import FernetPayloadProtector


NOW = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)
_PAYLOAD_PROTECTOR = FernetPayloadProtector(
    FernetPayloadProtector.generate_key(),
)


class _AllowMemory:
    def allows(self, record, context):  # type: ignore[no-untyped-def]
        return True


class _TrustSkills:
    def verify(self, metadata, subject):  # type: ignore[no-untyped-def]
        return SkillTrustDecision(
            verified=metadata.trust == "trusted",
            policy_id="phase5-tests",
            subject=subject,
        )


def _profile(tmp_path, builder: AgentBuilder) -> DurableRuntimeProfile:
    return DurableRuntimeProfile(
        agent_builder=builder,
        database_path=tmp_path / "state.db",
        artifact_root=tmp_path / "artifacts",
        clock=lambda: NOW,
        payload_protector=_PAYLOAD_PROTECTOR,
    )


def _plan() -> PlanState:
    return PlanState(
        plan_id="plan_1",
        objective="完成恢复后的报价。",
        owner_agent_id="agent_1",
        status="running",
        steps=(PlanStep("step_1", "读取持久状态。", status="pending"),),
        created_at=1.0,
        updated_at=1.0,
    )


def _memory() -> MemoryRecord:
    return MemoryRecord(
        handle="mem_1",
        session_id="session_1",
        kind="semantic",
        category="preference",
        content="用户要求使用人民币报价。",
    )


def _selection() -> MemorySelectionContext:
    return MemorySelectionContext(
        session_id="session_1",
        principal_id="user_1",
        permissions={"memory:read"},
        query="用户要求使用人民币报价",
        now=NOW,
    )


def _skill(revision: str) -> SkillDefinition:
    return SkillDefinition(
        name="review",
        description="Review a quotation.",
        when_to_use="Before returning a quotation.",
        content="# Review\nCheck every quotation field.",
        source="builtin",
        trust="trusted",
        source_revision=revision,
    )


def test_profile_plan_and_memory_stores_restart_and_project_current_truth(
    tmp_path,
) -> None:
    with _profile(tmp_path, AgentBuilder().provider(FakeProvider([]))) as first:
        first.plan_store.create_plan(_plan())
        first.memory_store.put(_memory())

    provider = FakeProvider(["restored"])
    builder = AgentBuilder().provider(provider)
    with _profile(tmp_path, builder) as restarted:
        memory = MemoryRuntime(
            restarted.memory_store,
            _AllowMemory(),  # type: ignore[arg-type]
            top_k=3,
            candidate_limit=10,
        )
        builder.context_projections(
            (
                BoundPlanProjectionProvider(
                    AuthorizedPlanSource(restarted.plan_store),
                    "plan_1",
                    "agent_1",
                ),
                BoundMemoryProjectionProvider(memory, _selection()),
            )
        )
        agent = asyncio.run(restarted.build_agent("session_1"))
        result = asyncio.run(agent.run("继续"))

    assert result.content == "restored"
    snapshot = provider.requests[0].messages[0]
    assert isinstance(snapshot.content[0], TextPart)
    assert "<active-plan" in snapshot.content[0].text
    assert "完成恢复后的报价" in snapshot.content[0].text
    assert "<memory-context>" in snapshot.content[0].text
    assert "用户要求使用人民币报价" in snapshot.content[0].text


def test_profile_skill_activation_reloads_and_reverifies_current_source(
    tmp_path,
) -> None:
    async def scenario() -> None:
        policy = _TrustSkills()
        with _profile(tmp_path, AgentBuilder().provider(FakeProvider([]))) as first:
            runtime = SkillRuntime(
                await SkillRegistry.aload(BuiltinSkillSource((_skill("1"),))),
                policy,
                activation_store=first.skill_activation_store,
            )
            await runtime.load("session_1", "review")

        with _profile(tmp_path, AgentBuilder().provider(FakeProvider([]))) as second:
            restored = SkillRuntime(
                await SkillRegistry.aload(BuiltinSkillSource((_skill("1"),))),
                policy,
                activation_store=second.skill_activation_store,
            )
            assert await restored.restore("session_1") == ("review",)
            assert restored.items("session_1")[0].text.startswith("# Review")

        with _profile(tmp_path, AgentBuilder().provider(FakeProvider([]))) as changed:
            invalidated = SkillRuntime(
                await SkillRegistry.aload(BuiltinSkillSource((_skill("2"),))),
                policy,
                activation_store=changed.skill_activation_store,
            )
            assert await invalidated.restore("session_1") == ()
            assert changed.skill_activation_store.list("session_1") == ()

    asyncio.run(scenario())


def test_profile_extension_store_access_is_closed_with_profile(tmp_path) -> None:
    profile = _profile(tmp_path, AgentBuilder().provider(FakeProvider([])))
    plan_store = profile.plan_store
    memory_store = profile.memory_store
    activation_store = profile.skill_activation_store

    profile.close()
    profile.close()

    with pytest.raises(DurableStoreClosedError, match="durable profile is closed"):
        _ = profile.plan_store
    with pytest.raises(SQLitePlanStoreClosedError):
        plan_store.list_plans()
    with pytest.raises(SQLiteMemoryStoreClosedError):
        memory_store.get("mem_1")
    with pytest.raises(SkillActivationStoreClosedError):
        activation_store.list("session_1")


def test_profile_close_invalidates_old_agent_artifact_access(tmp_path) -> None:
    profile = _profile(tmp_path, AgentBuilder().provider(FakeProvider([])))
    agent = asyncio.run(profile.build_agent("session_1"))
    record = agent.artifacts.upload(
        data=b"drawing",
        filename="drawing.png",
        media_type="image/png",
    )

    profile.close()
    profile.close()

    operations = (
        lambda: agent.artifacts.upload(
            data=b"new",
            filename="new.png",
            media_type="image/png",
        ),
        lambda: agent.artifacts.read(record.id),
        lambda: agent.artifacts.list(),
    )
    for operation in operations:
        with pytest.raises(
            DurableStoreClosedError,
            match="^durable artifact store is closed$",
        ) as error:
            operation()
        assert str(tmp_path) not in str(error.value)


def test_profile_uses_one_live_agent_and_checkpoint_source_per_session(
    tmp_path,
) -> None:
    reason = WaitReason("human_input", "approval_1")
    wait_tool = RegisteredTool(
        name="wait_here",
        description="Pause this run.",
        parameters={"type": "object", "properties": {}},
        handler=lambda _arguments: WaitRequest(reason),
    )
    provider = FakeProvider(
        [
            ProviderResponse(
                tool_calls=(ProviderToolCall("call_1", "wait_here", {}),)
            )
        ]
    )
    builder = AgentBuilder().provider(provider).tools([wait_tool])

    with _profile(tmp_path, builder) as profile:
        first = asyncio.run(profile.build_agent("session_1"))
        second = asyncio.run(profile.build_agent("session_1"))
        assert second.query_loop is first.query_loop
        waiting = asyncio.run(first.run("checkpoint owner"))
        assert isinstance(waiting, AgentWaiting)

    with _profile(tmp_path, builder) as restarted:
        hydrated = asyncio.run(restarted.build_agent("session_1"))
        assert hydrated.query_loop.message_runtime.store.all()[0].content == (
            "checkpoint owner"
        )


def test_profile_does_not_retain_idle_session_query_loop(tmp_path) -> None:
    with _profile(tmp_path, AgentBuilder().provider(FakeProvider([]))) as profile:
        agent = asyncio.run(profile.build_agent("session_1"))
        loop_ref = weakref.ref(agent.query_loop)

        del agent
        gc.collect()

        assert loop_ref() is None
        replacement = asyncio.run(profile.build_agent("session_1"))
        assert replacement.query_loop is not loop_ref()


def test_profile_reuses_stream_owned_query_loop_without_agent_facade(
    tmp_path,
) -> None:
    async def scenario() -> None:
        profile = _profile(tmp_path, AgentBuilder().provider(FakeProvider(["done"])))
        agent = await profile.build_agent("session_1")
        stream = await agent.run("start", stream=True)
        query_loop = agent.query_loop

        del agent
        gc.collect()

        replacement = await profile.build_agent("session_1")
        assert replacement.query_loop is query_loop
        await stream.aclose()
        profile.close()

    asyncio.run(scenario())


def test_same_session_facades_share_execution_lease(tmp_path) -> None:
    async def scenario() -> None:
        profile = _profile(tmp_path, AgentBuilder().provider(FakeProvider(["done"])))
        first = await profile.build_agent("session_1")
        second = await profile.build_agent("session_1")
        stream = await first.run("start", stream=True)

        with pytest.raises(AgentBusyError, match="active execution"):
            await second.run("conflict", stream=True)

        await stream.aclose()
        profile.close()

    asyncio.run(scenario())


def test_stream_keeps_checkpoint_source_alive_until_wait_is_committed(
    tmp_path,
) -> None:
    async def scenario() -> None:
        reason = WaitReason("human_input", "approval_1")
        wait_tool = RegisteredTool(
            name="wait_here",
            description="Pause this run.",
            parameters={"type": "object", "properties": {}},
            handler=lambda _arguments: WaitRequest(reason),
        )
        provider = FakeProvider(
            [
                ProviderResponse(
                    tool_calls=(ProviderToolCall("call_1", "wait_here", {}),)
                )
            ]
        )
        profile = _profile(
            tmp_path,
            AgentBuilder().provider(provider).tools([wait_tool]),
        )
        agent = await profile.build_agent("session_1")
        loop_ref = weakref.ref(agent.query_loop)
        stream = await agent.run("checkpoint owner", stream=True)

        del agent
        gc.collect()
        assert loop_ref() is not None

        events = [event async for event in stream]
        assert any(isinstance(event, TurnStreamWaiting) for event in events)

        del stream
        gc.collect()
        assert loop_ref() is None
        hydrated = await profile.build_agent("session_1")
        assert hydrated.query_loop.message_runtime.store.all()[0].content == (
            "checkpoint owner"
        )
        profile.close()

    asyncio.run(scenario())


def test_profile_close_rejects_active_stream_without_closing_store(
    tmp_path,
) -> None:
    async def scenario() -> None:
        profile = _profile(tmp_path, AgentBuilder().provider(FakeProvider(["done"])))
        agent = await profile.build_agent("session_1")
        stream = await agent.run("start", stream=True)

        with pytest.raises(AgentBusyError, match="active execution"):
            profile.close()

        rebuilt = await profile.build_agent("session_1")
        assert rebuilt.query_loop is agent.query_loop
        await stream.aclose()
        profile.close()
        profile.close()

    asyncio.run(scenario())


def test_profile_close_observes_execution_reservation_during_prepare(
    tmp_path,
    monkeypatch,
) -> None:
    profile = _profile(tmp_path, AgentBuilder().provider(FakeProvider(["done"])))
    agent = asyncio.run(profile.build_agent("session_1"))
    prepare_entered = Event()
    allow_prepare = Event()
    original_create = profile._store.create
    errors: list[BaseException] = []

    async def blocked_create(state):  # type: ignore[no-untyped-def]
        prepare_entered.set()
        if not allow_prepare.wait(5):
            raise TimeoutError("test did not release run prepare")
        return await original_create(state)

    monkeypatch.setattr(profile._store, "create", blocked_create)

    def execute() -> None:
        async def scenario() -> None:
            stream = await agent.run("start", stream=True)
            await stream.aclose()

        try:
            asyncio.run(scenario())
        except BaseException as error:
            errors.append(error)

    thread = Thread(target=execute)
    thread.start()
    assert prepare_entered.wait(5)
    try:
        with pytest.raises(AgentBusyError, match="active execution"):
            profile.close()
    finally:
        allow_prepare.set()
        thread.join(5)

    assert not thread.is_alive()
    assert errors == []
    profile.close()


def test_profile_close_reservation_blocks_new_run_until_store_is_closed(
    tmp_path,
    monkeypatch,
) -> None:
    profile = _profile(tmp_path, AgentBuilder().provider(FakeProvider(["done"])))
    agent = asyncio.run(profile.build_agent("session_1"))
    close_entered = Event()
    allow_close = Event()
    original_close = profile._skill_activation_store.close
    close_errors: list[BaseException] = []

    def blocked_close() -> None:
        close_entered.set()
        if not allow_close.wait(5):
            raise TimeoutError("test did not release profile close")
        original_close()

    monkeypatch.setattr(profile._skill_activation_store, "close", blocked_close)

    def close_profile() -> None:
        try:
            profile.close()
        except BaseException as error:
            close_errors.append(error)

    thread = Thread(target=close_profile)
    thread.start()
    assert close_entered.wait(5)

    async def attempt_run() -> bool:
        try:
            stream = await agent.run("must not start", stream=True)
        except AgentBusyError:
            return True
        await stream.aclose()
        return False

    try:
        blocked = asyncio.run(attempt_run())
    finally:
        allow_close.set()
        thread.join(5)

    assert blocked
    assert not thread.is_alive()
    assert close_errors == []


def test_profile_close_rolls_back_other_session_reservations_when_busy(
    tmp_path,
) -> None:
    async def scenario() -> None:
        profile = _profile(
            tmp_path,
            AgentBuilder().provider(FakeProvider(["one", "two"])),
        )
        idle_agent = await profile.build_agent("session_idle")
        busy_agent = await profile.build_agent("session_busy")
        busy_stream = await busy_agent.run("busy", stream=True)

        with pytest.raises(AgentBusyError, match="active execution"):
            profile.close()

        idle_stream = await idle_agent.run("still available", stream=True)
        await idle_stream.aclose()
        await busy_stream.aclose()
        profile.close()

    asyncio.run(scenario())
