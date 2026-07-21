from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agentos._waiting import WaitReason
from agentos.distributed.models import RunReadModel
from agentos.runtime.run import AgentResult
from agentos.runtime.run_state import RunStatus
from agentos.transports.a2a.mapping import (
    A2ACommandNotDueError,
    A2ACommandStateError,
    a2a_message_to_command,
    a2a_message_to_submission,
    run_read_model_to_a2a_task,
)
from agentos.transports.a2a.message_types import (
    A2AMessage,
    A2APart,
    A2ARole,
    A2ATaskState,
)


def _message(*, task_id: str | None = None) -> A2AMessage:
    return A2AMessage(
        message_id="msg_1",
        role=A2ARole.ROLE_USER,
        context_id="session_1",
        task_id=task_id,
        parts=(
            A2APart(text="answer"),
            A2APart(data={"value": 4}),
            A2APart(data=None),
            A2APart(raw=b"file", media_type="application/octet-stream"),
        ),
        metadata={"source": "peer", "tenant_id": "untrusted"},
    )


def _run(reason: WaitReason) -> RunReadModel:
    return RunReadModel(
        tenant_id="tenant_1",
        session_id="session_1",
        run_id="run_1",
        status=RunStatus.WAITING,
        wait_reason=reason,
        aggregate_version=4,
        result=None,
    )


def test_new_message_maps_original_part_order_to_submission() -> None:
    submission = a2a_message_to_submission(
        _message(),
        session_id="resolved_session",
        artifact_handles=("art_123e4567-e89b-42d3-a456-426614174000",),
    )

    assert submission.session_id == "resolved_session"
    assert submission.submission_id == "msg_1"
    assert submission.content == 'answer\n{"value":4}\nnull'
    assert submission.artifact_handles == ("art_123e4567-e89b-42d3-a456-426614174000",)


@pytest.mark.parametrize(
    ("reason", "kind"),
    [
        (WaitReason("human_input", "approval_1"), "hitl_answer"),
        (WaitReason("remote_result", "remote_1"), "wakeup"),
        (WaitReason("resource_availability", "resource_1"), "wakeup"),
    ],
)
def test_followup_message_maps_wait_reason_to_durable_command(
    reason: WaitReason,
    kind: str,
) -> None:
    command = a2a_message_to_command(
        _message(task_id="run_1"),
        run=_run(reason),
        artifact_handles=("art_123e4567-e89b-42d3-a456-426614174000",),
    )

    assert command.kind == kind
    assert command.payload == {
        "artifact_handles": ("art_123e4567-e89b-42d3-a456-426614174000",),
        "content": 'answer\n{"value":4}\nnull',
        "metadata": {"source": "peer", "tenant_id": "untrusted"},
    }


def test_timer_and_side_effect_waits_are_not_plain_messages() -> None:
    timed = WaitReason("timer", "timer_1", not_before=datetime(2026, 7, 21, tzinfo=UTC))
    with pytest.raises(A2ACommandNotDueError):
        a2a_message_to_command(_message(task_id="run_1"), run=_run(timed))

    side_effect = WaitReason(
        "side_effect_reconciliation",
        "operation_0123456789abcdef0123456789abcdef",
    )
    with pytest.raises(A2ACommandStateError):
        a2a_message_to_command(_message(task_id="run_1"), run=_run(side_effect))


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (RunStatus.CREATED, A2ATaskState.TASK_STATE_SUBMITTED),
        (RunStatus.QUEUED, A2ATaskState.TASK_STATE_SUBMITTED),
        (RunStatus.RUNNING, A2ATaskState.TASK_STATE_WORKING),
        (RunStatus.COMPLETED, A2ATaskState.TASK_STATE_COMPLETED),
        (RunStatus.FAILED, A2ATaskState.TASK_STATE_FAILED),
        (RunStatus.CANCELLED, A2ATaskState.TASK_STATE_CANCELED),
    ],
)
def test_run_status_maps_to_official_task_state(
    status: RunStatus,
    expected: A2ATaskState,
) -> None:
    result = AgentResult("done") if status is RunStatus.COMPLETED else None
    run = RunReadModel(
        tenant_id="tenant_1",
        session_id="session_1",
        run_id="run_1",
        status=status,
        wait_reason=None,
        aggregate_version=5,
        result=result,
    )

    task = run_read_model_to_a2a_task(run, history_length=None)

    assert task.id == "run_1"
    assert task.context_id == "session_1"
    assert task.status.state is expected
    assert task.artifacts is None
    if status is RunStatus.COMPLETED:
        assert len(task.history or ()) == 1
        assert task.history[0].role is A2ARole.ROLE_AGENT
        assert task.history[0].parts == (A2APart(text="done"),)
        assert run_read_model_to_a2a_task(run, history_length=0).history is None
    else:
        assert task.history is None


def test_list_projection_can_explicitly_include_empty_artifacts() -> None:
    run = _run(WaitReason("human_input", "approval_1", detail="secret detail"))

    task = run_read_model_to_a2a_task(run, include_artifacts=True)

    assert task.status.state is A2ATaskState.TASK_STATE_INPUT_REQUIRED
    assert task.artifacts == ()
    assert task.metadata is None
