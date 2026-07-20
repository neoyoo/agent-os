from dataclasses import replace

import pytest

from agentos.artifacts import ArtifactRef
from agentos.capabilities import (
    ArtifactToolResultRef,
    InlineToolResultRef,
    SideEffectPolicy,
)
from agentos.runtime.durable_commands import (
    AcceptedContinuationInput,
    DurableRunCommand,
)
from agentos.runtime.continuation import project_durable_continuation
from agentos.runtime.execution import (
    AcceptedTurnExecution,
    ApplyAcceptedInput,
    PendingToolInvocation,
    RestoreAcceptedTurn,
    RunExecutionCursor,
)
from agentos.runtime.payloads import ProtectedPayloadRef
from agentos.runtime.run_runtime import RunWriteGuard
from agentos.runtime.side_effect_integrity import result_ref_digest
from agentos.runtime.side_effect_resolution import (
    side_effect_resolution_from_payload,
    side_effect_resolution_to_payload,
)
from agentos.runtime.side_effect_resume import SideEffectResume
from agentos.runtime.side_effect_types import (
    SideEffectAttemptId,
    SideEffectRecord,
    SideEffectResolution,
    SideEffectResolutionKind,
    SideEffectResolutionOutcome,
    SideEffectStatus,
)
from agentos.runtime.tool_identity import compensation_operation_id


_INVOCATION_ID = "invocation_ea91f27fdf596bceb7c90d2578c5e988"
_OPERATION_ID = "operation_d340f3861e0c6a7eefbaf707fdc69d3d"
_DIGEST = f"sha256:{'1' * 64}"
_RESULT = InlineToolResultRef("accepted")
_ARTIFACT_RESULT = ArtifactToolResultRef(
    ArtifactRef(
        "art_123e4567-e89b-42d3-a456-426614174000",
        "result.json",
        "application/json",
    ),
    "stored result",
)
_INVOCATION_REF = ProtectedPayloadRef("sealed", _DIGEST)
_ATTESTATION_REF = ProtectedPayloadRef("attestation", _DIGEST)


def _resolution() -> SideEffectResolution:
    return SideEffectResolution(
        operation_id=_OPERATION_ID,
        kind=SideEffectResolutionKind.ACCEPT_RESULT,
        result_ref=_RESULT,
        result_digest=result_ref_digest(_RESULT),
    )


def _record() -> SideEffectRecord:
    return SideEffectRecord(
        attempt_id=SideEffectAttemptId(None, "session_1", _OPERATION_ID, 1),
        run_id="run_1",
        turn_id="turn_1",
        invocation_id=_INVOCATION_ID,
        tool_name="charge",
        policy=SideEffectPolicy.NON_RETRYABLE,
        status=SideEffectStatus.RESOLVED,
        invocation_digest=_DIGEST,
        invocation_ref=_INVOCATION_REF,
        result_ref=_RESULT,
        result_digest=result_ref_digest(_RESULT),
        resolution=SideEffectResolutionOutcome.ACCEPTED,
    )


def _cursor() -> RunExecutionCursor:
    return RunExecutionCursor(
        turn_id="turn_1",
        stage="pending_tools",
        provider_call_index=0,
        assistant_message_id="message_1",
        pending_tools=(
            PendingToolInvocation(
                invocation_id=_INVOCATION_ID,
                provider_tool_call_id="call_1",
                tool_name="charge",
                invocation_ref=_INVOCATION_REF,
            ),
        ),
    )


def test_resolution_payload_round_trips_with_fixed_versioned_shape() -> None:
    payload = side_effect_resolution_to_payload(_resolution())

    assert dict(payload) == {
        "version": 1,
        "operation_id": _OPERATION_ID,
        "kind": "accept_result",
        "result_ref": {"kind": "inline", "content": "accepted"},
        "result_digest": result_ref_digest(_RESULT),
        "attestation_ref": None,
        "attestation_digest": None,
    }
    assert side_effect_resolution_from_payload(payload) == _resolution()


@pytest.mark.parametrize(
    "resolution",
    [
        _resolution(),
        SideEffectResolution(
            _OPERATION_ID,
            SideEffectResolutionKind.ACCEPT_RESULT,
            result_ref=_ARTIFACT_RESULT,
            result_digest=result_ref_digest(_ARTIFACT_RESULT),
        ),
        SideEffectResolution(
            _OPERATION_ID,
            SideEffectResolutionKind.RETRY_PROVEN_SAFE,
            attestation_ref=_ATTESTATION_REF,
            attestation_digest=_ATTESTATION_REF.digest,
        ),
        SideEffectResolution(_OPERATION_ID, SideEffectResolutionKind.COMPENSATE),
        SideEffectResolution(_OPERATION_ID, SideEffectResolutionKind.FAIL),
    ],
    ids=("inline", "artifact", "retry-proven-safe", "compensate", "fail"),
)
def test_resolution_payload_round_trips_every_resolution_kind(
    resolution: SideEffectResolution,
) -> None:
    payload = side_effect_resolution_to_payload(resolution)

    assert side_effect_resolution_from_payload(payload) == resolution


def test_artifact_resolution_payload_has_closed_reference_shape() -> None:
    resolution = SideEffectResolution(
        _OPERATION_ID,
        SideEffectResolutionKind.ACCEPT_RESULT,
        result_ref=_ARTIFACT_RESULT,
        result_digest=result_ref_digest(_ARTIFACT_RESULT),
    )

    assert side_effect_resolution_to_payload(resolution)["result_ref"] == {
        "kind": "artifact",
        "artifact_id": "art_123e4567-e89b-42d3-a456-426614174000",
        "filename": "result.json",
        "media_type": "application/json",
        "preview": "stored result",
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"version": 1, "operation_id": _OPERATION_ID, "kind": "fail"},
        {
            "version": 1,
            "operation_id": _OPERATION_ID,
            "kind": "fail",
            "result_ref": None,
            "result_digest": None,
            "attestation_ref": None,
            "attestation_digest": None,
            "extra": True,
        },
        {
            "version": 2,
            "operation_id": _OPERATION_ID,
            "kind": "fail",
            "result_ref": None,
            "result_digest": None,
            "attestation_ref": None,
            "attestation_digest": None,
        },
        {
            "version": True,
            "operation_id": _OPERATION_ID,
            "kind": "fail",
            "result_ref": None,
            "result_digest": None,
            "attestation_ref": None,
            "attestation_digest": None,
        },
        {
            "version": 1,
            "operation_id": 1,
            "kind": "fail",
            "result_ref": None,
            "result_digest": None,
            "attestation_ref": None,
            "attestation_digest": None,
        },
        {
            "version": 1,
            "operation_id": _OPERATION_ID,
            "kind": "unknown",
            "result_ref": None,
            "result_digest": None,
            "attestation_ref": None,
            "attestation_digest": None,
        },
        {
            "version": 1,
            "operation_id": _OPERATION_ID,
            "kind": "accept_result",
            "result_ref": {"kind": "inline", "content": 1},
            "result_digest": _DIGEST,
            "attestation_ref": None,
            "attestation_digest": None,
        },
        {
            "version": 1,
            "operation_id": _OPERATION_ID,
            "kind": "retry_proven_safe",
            "result_ref": None,
            "result_digest": None,
            "attestation_ref": {
                "token": "attestation",
                "digest": _DIGEST,
                "extra": True,
            },
            "attestation_digest": _DIGEST,
        },
    ],
)
def test_resolution_payload_rejects_noncanonical_shape(payload: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="resolution payload"):
        side_effect_resolution_from_payload(payload)


def test_durable_resolve_command_accepts_typed_resolution_and_freezes_payload() -> None:
    command = DurableRunCommand(
        "run_1",
        "command_1",
        "resolve_side_effect",
        _resolution(),
    )
    accepted = AcceptedContinuationInput(
        command.run_id,
        command.command_id,
        command.kind,
        command.payload,
        "turn_2",
    )

    assert command.payload == side_effect_resolution_to_payload(_resolution())
    assert accepted.payload == command.payload
    assert accepted.kind == "resolve_side_effect"


def test_durable_resolve_command_rejects_untyped_or_extra_payload() -> None:
    payload = dict(side_effect_resolution_to_payload(_resolution()))
    payload["extra"] = "not allowed"

    with pytest.raises(ValueError, match="resolution payload"):
        DurableRunCommand(
            "run_1",
            "command_1",
            "resolve_side_effect",
            payload,
        )

    with pytest.raises(TypeError, match="SideEffectResolution"):
        DurableRunCommand("run_1", "command_1", "resolve_side_effect")


def test_side_effect_resume_accepts_exact_source_cursor_pair() -> None:
    resume = SideEffectResume(
        tenant_id=None,
        session_id="session_1",
        run_id="run_1",
        continuation_turn_id="turn_2",
        source_cursor=_cursor(),
        record=_record(),
        resolution=_resolution(),
    )

    assert resume.source_cursor.pending_tools[0].provider_tool_call_id == "call_1"


def test_side_effect_resume_accepts_completed_compensation_for_terminal_retry() -> None:
    resolution = SideEffectResolution(
        _OPERATION_ID,
        SideEffectResolutionKind.COMPENSATE,
    )
    record = replace(
        _record(),
        policy=SideEffectPolicy.COMPENSATABLE,
        status=SideEffectStatus.COMPENSATED,
        result_ref=None,
        result_digest=None,
        resolution=None,
        compensation_operation_id=compensation_operation_id(_OPERATION_ID),
        compensation_attempt=1,
    )

    resume = SideEffectResume(
        tenant_id=None,
        session_id="session_1",
        run_id="run_1",
        continuation_turn_id="turn_2",
        source_cursor=_cursor(),
        record=record,
        resolution=resolution,
    )

    assert resume.record.status is SideEffectStatus.COMPENSATED


@pytest.mark.parametrize(
    "field,value",
    [
        ("continuation_turn_id", "turn_1"),
        ("session_id", "session_2"),
        ("run_id", "run_2"),
        (
            "resolution",
            SideEffectResolution(_OPERATION_ID, SideEffectResolutionKind.FAIL),
        ),
    ],
)
def test_side_effect_resume_rejects_mismatched_authoritative_state(
    field: str,
    value: object,
) -> None:
    values = {
        "tenant_id": None,
        "session_id": "session_1",
        "run_id": "run_1",
        "continuation_turn_id": "turn_2",
        "source_cursor": _cursor(),
        "record": _record(),
        "resolution": _resolution(),
    }
    values[field] = value

    with pytest.raises(ValueError, match="side effect resume"):
        SideEffectResume(**values)  # type: ignore[arg-type]


def test_side_effect_resume_rejects_cursor_without_matching_protected_invocation() -> None:
    cursor = _cursor()
    other = replace(
        cursor.pending_tools[0],
        invocation_id="invocation_00000000000000000000000000000000",
    )

    with pytest.raises(ValueError, match="side effect resume"):
        SideEffectResume(
            tenant_id=None,
            session_id="session_1",
            run_id="run_1",
            continuation_turn_id="turn_2",
            source_cursor=replace(cursor, pending_tools=(other,)),
            record=_record(),
            resolution=_resolution(),
        )


def _accepted_resolution() -> AcceptedContinuationInput:
    return AcceptedContinuationInput(
        "run_1",
        "command_1",
        "resolve_side_effect",
        side_effect_resolution_to_payload(_resolution()),
        "turn_2",
    )


def _fenced_resume() -> SideEffectResume:
    return SideEffectResume(
        tenant_id=None,
        session_id="session_1",
        run_id="run_1",
        continuation_turn_id="turn_2",
        source_cursor=_cursor(),
        record=replace(_record(), claim_id="claim_1", fencing_token=7),
        resolution=_resolution(),
    )


def test_accepted_resolution_execution_requires_exact_typed_resume() -> None:
    guard = RunWriteGuard(4, "claim_1", 7)

    execution = AcceptedTurnExecution(
        _accepted_resolution(),
        guard,
        _fenced_resume(),
    )

    assert execution.preparation == _fenced_resume()
    with pytest.raises(ValueError, match="typed side effect preparation"):
        AcceptedTurnExecution(_accepted_resolution(), guard, ApplyAcceptedInput())


def test_recovered_resolution_allows_consumed_source_cursor_recovery() -> None:
    execution = AcceptedTurnExecution(
        _accepted_resolution(),
        RunWriteGuard(5, "claim_1", 7),
        RestoreAcceptedTurn(
            RunExecutionCursor("turn_2", "before_provider", 0),
        ),
    )

    assert execution.preparation == RestoreAcceptedTurn(
        RunExecutionCursor("turn_2", "before_provider", 0),
    )


@pytest.mark.parametrize(
    "resume",
    [
        replace(_fenced_resume(), continuation_turn_id="turn_3"),
        replace(
            _fenced_resume(),
            run_id="run_2",
            record=replace(_fenced_resume().record, run_id="run_2"),
        ),
        replace(
            _fenced_resume(),
            record=replace(_fenced_resume().record, claim_id="claim_other"),
        ),
    ],
    ids=("turn", "run", "fence"),
)
def test_accepted_resolution_execution_rejects_resume_mismatch(
    resume: SideEffectResume,
) -> None:
    with pytest.raises(ValueError, match="typed side effect resume"):
        AcceptedTurnExecution(
            _accepted_resolution(),
            RunWriteGuard(4, "claim_1", 7),
            resume,
        )


def test_non_resolution_execution_rejects_side_effect_resume() -> None:
    continuation = AcceptedContinuationInput(
        "run_1",
        "command_1",
        "resume",
        {},
        "turn_2",
    )

    with pytest.raises(ValueError, match="typed side effect resume"):
        AcceptedTurnExecution(
            continuation,
            RunWriteGuard(4, "claim_1", 7),
            _fenced_resume(),
        )


def test_resolution_payload_cannot_be_projected_to_provider() -> None:
    with pytest.raises(ValueError, match="runtime control"):
        project_durable_continuation(_accepted_resolution())
