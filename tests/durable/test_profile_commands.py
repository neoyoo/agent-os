import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from agentos import AgentBuilder
from agentos.capabilities import RegisteredTool, SideEffectPolicy, WaitRequest
from agentos.providers import FakeProvider, ProviderResponse, ProviderToolCall
from agentos.runtime import (
    AgentWaiting,
    DurableCommandReceipt,
    DurableRunCommand,
    WaitReason,
)
from agentos.runtime.errors import AgentBusyError, CommandNotDueError
from agentos.durable import DurableRuntimeProfile
from agentos.runtime.run_state import RunStatus
from agentos.security import FernetPayloadProtector


NOW = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)
_PAYLOAD_PROTECTOR = FernetPayloadProtector(
    FernetPayloadProtector.generate_key(),
)


class _Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


def _wait_tool(reason: WaitReason) -> RegisteredTool:
    return RegisteredTool(
        name="wait_here",
        description="Pause this durable run.",
        parameters={"type": "object", "properties": {}},
        handler=lambda _invocation: WaitRequest(reason),
        side_effect_policy=SideEffectPolicy.PURE,
        wait_capable=True,
    )


def _profile(tmp_path, builder: AgentBuilder, clock: _Clock) -> DurableRuntimeProfile:
    return DurableRuntimeProfile(
        agent_builder=builder,
        database_path=tmp_path / "state.db",
        artifact_root=tmp_path / "artifacts",
        clock=clock,
        payload_protector=_PAYLOAD_PROTECTOR,
    )


def test_cancel_command_persists_across_profile_restart_without_provider_reentry(
    tmp_path,
) -> None:
    async def scenario():  # type: ignore[no-untyped-def]
        provider = FakeProvider(
            [ProviderResponse(tool_calls=(ProviderToolCall("call_1", "wait_here", {}),))]
        )
        builder = AgentBuilder().provider(provider).tools(
            [_wait_tool(WaitReason("human_input", "approval_1"))]
        )
        clock = _Clock()

        async with _profile(tmp_path, builder, clock) as first:
            agent = await first.build_agent("session_1")
            waiting = await agent.run("start")
            assert isinstance(waiting, AgentWaiting)
            command = DurableRunCommand(waiting.run_id, "cancel_1", "cancel", {})
            receipt = await agent.run(command)
            assert isinstance(receipt, DurableCommandReceipt)
            assert receipt.duplicate is False

        async with _profile(tmp_path, builder, clock) as restarted:
            agent = await restarted.build_agent("session_1")
            assert (
                await agent.query_loop.run_runtime.get_run(waiting.run_id)
            ).status is RunStatus.CANCELLED
            duplicate = await agent.run(command)

        return provider, duplicate

    provider, duplicate = asyncio.run(scenario())

    assert isinstance(duplicate, DurableCommandReceipt)
    assert duplicate.duplicate is True
    assert len(provider.requests) == 1


@pytest.mark.parametrize(
    ("wait_kind", "command_kind"),
    (("timer", "wakeup"), ("retry_backoff", "retry")),
)
def test_scheduled_command_is_rejected_until_due_then_continues_same_run(
    tmp_path,
    wait_kind: str,
    command_kind: str,
) -> None:
    async def scenario():  # type: ignore[no-untyped-def]
        due = NOW + timedelta(minutes=5)
        provider = FakeProvider(
            [
                ProviderResponse(
                    tool_calls=(ProviderToolCall("call_1", "wait_here", {}),)
                ),
                "timer resumed",
            ]
        )
        builder = AgentBuilder().provider(provider).tools(
            [
                _wait_tool(
                    WaitReason(
                        wait_kind,  # type: ignore[arg-type]
                        "scheduled_1",
                        not_before=due,
                    )
                )
            ]
        )
        clock = _Clock()

        async with _profile(tmp_path, builder, clock) as profile:
            agent = await profile.build_agent("session_1")
            waiting = await agent.run("start")
            assert isinstance(waiting, AgentWaiting)
            command = DurableRunCommand(
                waiting.run_id,
                "scheduled_command_1",
                command_kind,  # type: ignore[arg-type]
                {},
            )

            with pytest.raises(CommandNotDueError, match="not due"):
                await agent.run(command)
            assert (
                await agent.query_loop.run_runtime.get_run(waiting.run_id)
            ).status is RunStatus.WAITING

            clock.now = due
            result = await agent.run(command)
            assert agent.query_loop.session_state.next_turn_number() == 3

        return provider, result

    provider, result = asyncio.run(scenario())

    assert result.content == "timer resumed"
    assert len(provider.requests) == 2


def test_command_accept_reservation_blocks_profile_close(
    tmp_path,
    monkeypatch,
) -> None:
    async def scenario() -> None:
        provider = FakeProvider(
            [
                ProviderResponse(
                    tool_calls=(ProviderToolCall("call_1", "wait_here", {}),)
                ),
                "resumed",
            ]
        )
        builder = AgentBuilder().provider(provider).tools(
            [_wait_tool(WaitReason("human_input", "approval_1"))]
        )
        profile = _profile(tmp_path, builder, _Clock())
        await profile.open()
        agent = await profile.build_agent("session_1")
        waiting = await agent.run("start")
        assert isinstance(waiting, AgentWaiting)
        command = DurableRunCommand(waiting.run_id, "resume_1", "resume", {})
        accept_entered = asyncio.Event()
        allow_accept = asyncio.Event()
        original_accept = profile._store.accept_command

        async def blocked_accept(**kwargs):  # type: ignore[no-untyped-def]
            accept_entered.set()
            await allow_accept.wait()
            return await original_accept(**kwargs)

        monkeypatch.setattr(profile._store, "accept_command", blocked_accept)
        accepting = asyncio.create_task(agent.run(command))
        await asyncio.wait_for(accept_entered.wait(), timeout=5)
        try:
            with pytest.raises(AgentBusyError, match="active execution"):
                await profile.close()
        finally:
            allow_accept.set()

        outcome = await accepting
        assert outcome.content == "resumed"
        await profile.close()

    asyncio.run(scenario())
