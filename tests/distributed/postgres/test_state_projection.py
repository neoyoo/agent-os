import pytest

from agentos._json_values import thaw_json_value
from agentos._waiting import WaitReason
from agentos.context import WorkingStateField
from agentos.context.state import CompressedSegment
from agentos.distributed.errors import (
    CheckpointConflictError,
    CommandConflictError,
    CommandStateError,
    RunSubmissionConflictError,
)
from agentos.distributed.models import (
    RequestScope,
    RunSubmission,
    canonical_submission_digest,
)
from agentos.distributed.postgres._commands import (
    _duplicate_receipt,
    _validate_resolution,
)
from agentos.distributed.postgres._records import (
    terminal_result_from_checkpoint,
)
from agentos.distributed.postgres._state_records import (
    duplicate_submission,
    run_read_model,
)
from agentos.durable.serialization import dump_json
from agentos.runtime.checkpoint import (
    CheckpointStoredMessage,
    CheckpointToolCall,
    ContextCheckpoint,
    SessionCheckpoint,
)
from agentos.runtime.durable_commands import DurableRunCommand
from agentos.runtime.payloads import ProtectedPayloadRef
from agentos.runtime.run_state import RunState, RunStatus
from agentos.runtime.side_effect_types import (
    SideEffectResolution,
    SideEffectResolutionKind,
)
from tests.planning._async import async_test


def _checkpoint(*messages: CheckpointStoredMessage) -> SessionCheckpoint:
    return SessionCheckpoint(
        session_id="session_1",
        session_status="running",
        next_turn_number=2,
        messages=messages,
        active_refs=tuple(message.id for message in messages),
        context=ContextCheckpoint(
            schema=(WorkingStateField("goal", "string", "task goal"),),
            working_state={},
            compressed_history=(CompressedSegment("seg_1", "topic", "summary"),),
            inherited_state=(),
        ),
    )


def test_completed_result_is_only_the_terminal_assistant_content() -> None:
    checkpoint = _checkpoint(
        CheckpointStoredMessage("message_1", "assistant", "draft"),
        CheckpointStoredMessage("message_2", "assistant", "final answer"),
    )

    result = terminal_result_from_checkpoint(checkpoint, RunStatus.COMPLETED)

    assert result is not None
    assert result.content == "final answer"


@pytest.mark.parametrize("status", [RunStatus.FAILED, RunStatus.CANCELLED])
def test_non_completed_terminal_does_not_project_result(status: RunStatus) -> None:
    checkpoint = _checkpoint(
        CheckpointStoredMessage("message_1", "assistant", "not a result"),
    )

    assert terminal_result_from_checkpoint(checkpoint, status) is None


def test_completed_without_terminal_assistant_fails_closed() -> None:
    checkpoint = _checkpoint(
        CheckpointStoredMessage("message_1", "user", "question"),
    )

    with pytest.raises(CheckpointConflictError):
        terminal_result_from_checkpoint(checkpoint, RunStatus.COMPLETED)


def test_completed_with_dangling_tool_pair_fails_closed() -> None:
    call = CheckpointToolCall(
        id="provider_call_1",
        name="lookup",
        run_id="run_1",
        turn_id="turn_1",
        invocation_id="invocation_0123456789abcdef0123456789abcdef",
        invocation_ref=ProtectedPayloadRef("sealed", "digest"),
    )
    checkpoint = _checkpoint(
        CheckpointStoredMessage(
            "message_1",
            "assistant",
            "",
            tool_calls=(call,),
        ),
        CheckpointStoredMessage("message_2", "assistant", "final answer"),
    )

    with pytest.raises(CheckpointConflictError):
        terminal_result_from_checkpoint(checkpoint, RunStatus.COMPLETED)


def test_completed_with_valid_tool_pair_projects_final_assistant() -> None:
    call = CheckpointToolCall(
        id="provider_call_1",
        name="lookup",
        run_id="run_1",
        turn_id="turn_1",
        invocation_id="invocation_0123456789abcdef0123456789abcdef",
        invocation_ref=ProtectedPayloadRef("sealed", "digest"),
    )
    checkpoint = _checkpoint(
        CheckpointStoredMessage(
            "message_1",
            "assistant",
            "",
            tool_calls=(call,),
        ),
        CheckpointStoredMessage(
            "message_2",
            "tool",
            "lookup result",
            tool_call_id="provider_call_1",
        ),
        CheckpointStoredMessage("message_3", "assistant", "final answer"),
    )

    result = terminal_result_from_checkpoint(checkpoint, RunStatus.COMPLETED)

    assert result is not None
    assert result.content == "final answer"


def test_submission_duplicate_requires_exact_canonical_input() -> None:
    scope = RequestScope("tenant_1", "principal_1")
    submission = RunSubmission("session_1", "submission_1", "question")
    digest = canonical_submission_digest(scope, submission)
    row = {
        "session_id": submission.session_id,
        "run_id": "run_1",
        "input_digest": digest,
        "aggregate_version": 1,
    }

    receipt = duplicate_submission(row, submission, digest)

    assert receipt.duplicate is True
    with pytest.raises(RunSubmissionConflictError):
        duplicate_submission(
            row,
            RunSubmission("session_1", "submission_1", "different"),
            "different-digest",
        )


def test_command_duplicate_requires_exact_immutable_command() -> None:
    command = DurableRunCommand("run_1", "command_1", "resume")
    payload_json = dump_json(thaw_json_value(command.payload))
    row = {
        "session_id": "session_1",
        "run_id": command.run_id,
        "kind": command.kind,
        "payload_json": payload_json,
        "aggregate_version": 4,
    }

    receipt = _duplicate_receipt(row, "session_1", command, payload_json)

    assert receipt.duplicate is True
    with pytest.raises(CommandConflictError):
        _duplicate_receipt(row, "different_session", command, payload_json)


def _run_row(status: RunStatus, result_content: str | None) -> dict[str, object]:
    return {
        "tenant_id": "tenant_1",
        "session_id": "session_1",
        "run_id": "run_1",
        "status": status.value,
        "wait_kind": None,
        "wait_handle": None,
        "wait_detail": None,
        "wait_not_before": None,
        "aggregate_version": 3,
        "result_content": result_content,
    }


def test_completed_read_model_requires_and_returns_result() -> None:
    model = run_read_model(_run_row(RunStatus.COMPLETED, "answer"))

    assert model.result is not None
    assert model.result.content == "answer"
    with pytest.raises(ValueError, match="requires a result"):
        run_read_model(_run_row(RunStatus.COMPLETED, None))


@pytest.mark.parametrize("status", [RunStatus.FAILED, RunStatus.CANCELLED])
def test_non_completed_read_model_rejects_result(status: RunStatus) -> None:
    with pytest.raises(ValueError, match="cannot contain a result"):
        run_read_model(_run_row(status, "must not persist"))


@async_test
async def test_resolution_maps_corrupt_ledger_state_to_command_error() -> None:
    operation_id = "operation_0123456789abcdef0123456789abcdef"
    state = RunState(
        "run_1",
        "session_1",
        RunStatus.WAITING,
        WaitReason("side_effect_reconciliation", operation_id),
        3,
    )
    command = DurableRunCommand(
        "run_1",
        "command_1",
        "resolve_side_effect",
        SideEffectResolution(operation_id, SideEffectResolutionKind.FAIL),
    )

    class CorruptLedgerCursor:
        async def fetchone(self) -> dict[str, object]:
            return {"payload_json": "{}"}

    class CorruptLedgerConnection:
        async def execute(
            self,
            query: str,
            params: tuple[object, ...] = (),
        ) -> CorruptLedgerCursor:
            del query, params
            return CorruptLedgerCursor()

    with pytest.raises(CommandStateError):
        await _validate_resolution(  # type: ignore[arg-type]
            CorruptLedgerConnection(),
            RequestScope("tenant_1", "principal_1"),
            state,
            command,
        )
