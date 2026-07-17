import asyncio
from datetime import UTC, datetime

import pytest

from agentos.channels.a2a_execution import (
    A2AExecutionResult,
    a2a_wait_metadata,
    execute_a2a_agent,
)
from agentos.runtime import AgentResult, AgentWaiting, WaitReason


_TIMER_DUE = datetime(2026, 7, 17, 12, tzinfo=UTC)


class OutcomeAgent:
    def __init__(self, outcome: AgentResult | AgentWaiting) -> None:
        self.outcome = outcome
        self.inputs: list[str] = []

    async def run(self, input: str) -> AgentResult | AgentWaiting:
        self.inputs.append(input)
        return self.outcome


def test_execute_a2a_agent_projects_completed_outcome() -> None:
    agent = OutcomeAgent(AgentResult("done"))

    result = asyncio.run(execute_a2a_agent(agent, "work"))

    assert result == A2AExecutionResult(state="completed", content="done")
    assert a2a_wait_metadata(result) == {}
    assert agent.inputs == ["work"]


def test_execute_a2a_agent_maps_human_wait_to_input_required() -> None:
    reason = WaitReason("human_input", "approval_1", "confirm")
    agent = OutcomeAgent(AgentWaiting("run_1", reason))

    result = asyncio.run(execute_a2a_agent(agent, "work"))

    assert result == A2AExecutionResult(
        state="input-required",
        run_id="run_1",
        wait_reason=reason,
    )
    assert a2a_wait_metadata(result) == {
        "runId": "run_1",
        "waitReason": {
            "kind": "human_input",
            "handle": "approval_1",
            "detail": "confirm",
        },
    }


def test_execute_a2a_agent_maps_non_human_wait_to_working() -> None:
    reason = WaitReason("timer", "timer_1", not_before=_TIMER_DUE)
    agent = OutcomeAgent(AgentWaiting("run_2", reason))

    result = asyncio.run(execute_a2a_agent(agent, "work"))

    assert result == A2AExecutionResult(
        state="working",
        run_id="run_2",
        wait_reason=reason,
    )
    assert a2a_wait_metadata(result) == {
        "runId": "run_2",
        "waitReason": {
            "kind": "timer",
            "handle": "timer_1",
            "detail": None,
        },
    }


@pytest.mark.parametrize(
    ("reason", "expected_state"),
    [
        (WaitReason("human_input", "approval_1", "confirm"), "input-required"),
        (WaitReason("timer", "timer_1", not_before=_TIMER_DUE), "working"),
    ],
)
def test_agent_a2a_operation_runner_projects_waiting_task(
    reason: WaitReason,
    expected_state: str,
) -> None:
    from agentos.channels.a2a_operations import (
        A2AMessage,
        A2AMessagePart,
        AgentA2AOperationRunner,
    )

    message = A2AMessage(
        role="user",
        parts=(A2AMessagePart.from_text("work"),),
        context_id="ctx_1",
        task_id="task_1",
    )
    agent = OutcomeAgent(AgentWaiting("run_1", reason))

    task = asyncio.run(
        AgentA2AOperationRunner(agent).send_message(message),  # type: ignore[arg-type]
    )

    assert task.state == expected_state
    assert task.messages == (message,)
    assert task.metadata == {
        "runId": "run_1",
        "waitReason": {
            "kind": reason.kind,
            "handle": reason.handle,
            "detail": reason.detail,
        },
    }
