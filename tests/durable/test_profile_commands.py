import asyncio
from datetime import UTC, datetime, timedelta
from threading import Event, Thread

import pytest

from agentos import AgentBuilder
from agentos.capabilities import RegisteredTool, WaitRequest
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
        handler=lambda _arguments: WaitRequest(reason),
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
    provider = FakeProvider(
        [ProviderResponse(tool_calls=(ProviderToolCall("call_1", "wait_here", {}),))]
    )
    builder = AgentBuilder().provider(provider).tools(
        [_wait_tool(WaitReason("human_input", "approval_1"))]
    )
    clock = _Clock()

    with _profile(tmp_path, builder, clock) as first:
        agent = asyncio.run(first.build_agent("session_1"))
        waiting = asyncio.run(agent.run("start"))
        assert isinstance(waiting, AgentWaiting)
        command = DurableRunCommand(waiting.run_id, "cancel_1", "cancel", {})
        receipt = asyncio.run(agent.run(command))
        assert isinstance(receipt, DurableCommandReceipt)
        assert receipt.duplicate is False

    with _profile(tmp_path, builder, clock) as restarted:
        agent = asyncio.run(restarted.build_agent("session_1"))
        assert asyncio.run(
            agent.query_loop.run_runtime.get_run(waiting.run_id),
        ).status is RunStatus.CANCELLED
        duplicate = asyncio.run(agent.run(command))

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

    with _profile(tmp_path, builder, clock) as profile:
        agent = asyncio.run(profile.build_agent("session_1"))
        waiting = asyncio.run(agent.run("start"))
        assert isinstance(waiting, AgentWaiting)
        command = DurableRunCommand(
            waiting.run_id,
            "scheduled_command_1",
            command_kind,  # type: ignore[arg-type]
            {},
        )

        with pytest.raises(CommandNotDueError, match="not due"):
            asyncio.run(agent.run(command))
        assert asyncio.run(
            agent.query_loop.run_runtime.get_run(waiting.run_id),
        ).status is RunStatus.WAITING

        clock.now = due
        result = asyncio.run(agent.run(command))
        assert agent.query_loop.session_state.next_turn_number() == 3

    assert result.content == "timer resumed"
    assert len(provider.requests) == 2


def test_command_accept_reservation_blocks_profile_close(
    tmp_path,
    monkeypatch,
) -> None:
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
    agent = asyncio.run(profile.build_agent("session_1"))
    waiting = asyncio.run(agent.run("start"))
    assert isinstance(waiting, AgentWaiting)
    command = DurableRunCommand(waiting.run_id, "resume_1", "resume", {})
    accept_entered = Event()
    allow_accept = Event()
    original_accept = profile._store.accept_command
    outcomes: list[object] = []
    errors: list[BaseException] = []

    async def blocked_accept(**kwargs):  # type: ignore[no-untyped-def]
        accept_entered.set()
        if not allow_accept.wait(5):
            raise TimeoutError("test did not release command accept")
        return await original_accept(**kwargs)

    monkeypatch.setattr(profile._store, "accept_command", blocked_accept)

    def execute() -> None:
        try:
            outcomes.append(asyncio.run(agent.run(command)))
        except BaseException as error:
            errors.append(error)

    thread = Thread(target=execute)
    thread.start()
    assert accept_entered.wait(5)
    try:
        with pytest.raises(AgentBusyError, match="active execution"):
            profile.close()
    finally:
        allow_accept.set()
        thread.join(5)

    assert not thread.is_alive()
    assert errors == []
    assert [outcome.content for outcome in outcomes] == ["resumed"]
    profile.close()
