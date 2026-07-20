from __future__ import annotations

import asyncio
import gc
import weakref
from datetime import UTC, datetime

import pytest

from agentos import AgentBuilder
from agentos.capabilities import RegisteredTool, WaitRequest
from agentos.capabilities.skills import (
    BuiltinSkillSource,
    SkillDefinition,
    SkillRegistry,
    SkillRuntime,
    SkillTrustDecision,
)
from agentos.memory.records import MemoryRecord, MemorySelectionContext
from agentos.memory.runtime import BoundMemoryProjectionProvider, MemoryRuntime
from agentos.planning.models import PlanState, PlanStep
from agentos.planning.projection import (
    AuthorizedPlanSource,
    BoundPlanProjectionProvider,
)
from agentos.providers import (
    FakeProvider,
    ProviderResponse,
    ProviderToolCall,
    TextPart,
)
from agentos.runtime import AgentWaiting, TurnStreamWaiting, WaitReason
from agentos.runtime.errors import AgentBusyError
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
    async def scenario():  # type: ignore[no-untyped-def]
        async with _profile(
            tmp_path,
            AgentBuilder().provider(FakeProvider([])),
        ) as first:
            await first.plan_store.create_plan(_plan())
            await first.memory_store.put(_memory())

        provider = FakeProvider(["restored"])
        builder = AgentBuilder().provider(provider)
        async with _profile(tmp_path, builder) as restarted:
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
            agent = await restarted.build_agent("session_1")
            result = await agent.run("继续")

        return provider, result

    provider, result = asyncio.run(scenario())

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
        async with _profile(
            tmp_path,
            AgentBuilder().provider(FakeProvider([])),
        ) as first:
            runtime = SkillRuntime(
                await SkillRegistry.aload(BuiltinSkillSource((_skill("1"),))),
                policy,
                activation_store=first.skill_activation_store,
            )
            await runtime.load("session_1", "review")

        async with _profile(
            tmp_path,
            AgentBuilder().provider(FakeProvider([])),
        ) as second:
            restored = SkillRuntime(
                await SkillRegistry.aload(BuiltinSkillSource((_skill("1"),))),
                policy,
                activation_store=second.skill_activation_store,
            )
            assert await restored.restore("session_1") == ("review",)
            assert restored.items("session_1")[0].text.startswith("# Review")

        async with _profile(
            tmp_path,
            AgentBuilder().provider(FakeProvider([])),
        ) as changed:
            invalidated = SkillRuntime(
                await SkillRegistry.aload(BuiltinSkillSource((_skill("2"),))),
                policy,
                activation_store=changed.skill_activation_store,
            )
            assert await invalidated.restore("session_1") == ()
            assert await changed.skill_activation_store.list("session_1") == ()

    asyncio.run(scenario())


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

    async def scenario() -> None:
        async with _profile(tmp_path, builder) as profile:
            first = await profile.build_agent("session_1")
            second = await profile.build_agent("session_1")
            assert second.query_loop is first.query_loop
            waiting = await first.run("checkpoint owner")
            assert isinstance(waiting, AgentWaiting)

        async with _profile(tmp_path, builder) as restarted:
            hydrated = await restarted.build_agent("session_1")
            assert hydrated.query_loop.message_runtime.store.all()[0].content == (
                "checkpoint owner"
            )

    asyncio.run(scenario())


def test_profile_does_not_retain_idle_session_query_loop(tmp_path) -> None:
    async def scenario() -> None:
        async with _profile(
            tmp_path,
            AgentBuilder().provider(FakeProvider([])),
        ) as profile:
            agent = await profile.build_agent("session_1")
            loop_ref = weakref.ref(agent.query_loop)

            del agent
            gc.collect()

            assert loop_ref() is None
            replacement = await profile.build_agent("session_1")
            assert replacement.query_loop is not loop_ref()

    asyncio.run(scenario())


def test_profile_reuses_stream_owned_query_loop_without_agent_facade(
    tmp_path,
) -> None:
    async def scenario() -> None:
        profile = _profile(tmp_path, AgentBuilder().provider(FakeProvider(["done"])))
        await profile.open()
        agent = await profile.build_agent("session_1")
        stream = await agent.run("start", stream=True)
        query_loop = agent.query_loop

        del agent
        gc.collect()

        replacement = await profile.build_agent("session_1")
        assert replacement.query_loop is query_loop
        await stream.aclose()
        await profile.close()

    asyncio.run(scenario())


def test_same_session_facades_share_execution_lease(tmp_path) -> None:
    async def scenario() -> None:
        profile = _profile(tmp_path, AgentBuilder().provider(FakeProvider(["done"])))
        await profile.open()
        first = await profile.build_agent("session_1")
        second = await profile.build_agent("session_1")
        stream = await first.run("start", stream=True)

        with pytest.raises(AgentBusyError, match="active execution"):
            await second.run("conflict", stream=True)

        await stream.aclose()
        await profile.close()

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
        await profile.open()
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
        await profile.close()

    asyncio.run(scenario())
