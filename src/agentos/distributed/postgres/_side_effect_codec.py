from __future__ import annotations

import json

from agentos.artifacts import ArtifactRef
from agentos.capabilities.result_refs import (
    ArtifactToolResultRef,
    InlineToolResultRef,
    ToolResultRef,
)
from agentos.capabilities.tools import SideEffectPolicy
from agentos.runtime.payloads import ProtectedPayloadRef
from agentos.runtime.side_effect_types import (
    SideEffectAttemptId,
    SideEffectOutcomeKind,
    SideEffectRecord,
    SideEffectResolutionOutcome,
    SideEffectStatus,
    SideEffectTransitionError,
)


_FIELDS = {
    "attempt",
    "attestation_digest",
    "attestation_ref",
    "claim_id",
    "compensation_attempt",
    "compensation_operation_id",
    "failure_code",
    "fencing_token",
    "invocation_digest",
    "invocation_id",
    "invocation_ref",
    "operation_id",
    "outcome_kind",
    "policy",
    "resolution",
    "result_digest",
    "result_ref",
    "run_id",
    "session_id",
    "status",
    "tenant_id",
    "tool_name",
    "turn_id",
    "version",
    "wait_reason_digest",
}


def side_effect_record_to_json(record: SideEffectRecord) -> str:
    payload = {
        "attempt": record.attempt_id.attempt,
        "attestation_digest": record.attestation_digest,
        "attestation_ref": _protected_to_data(record.attestation_ref),
        "claim_id": record.claim_id,
        "compensation_attempt": record.compensation_attempt,
        "compensation_operation_id": record.compensation_operation_id,
        "failure_code": record.failure_code,
        "fencing_token": record.fencing_token,
        "invocation_digest": record.invocation_digest,
        "invocation_id": record.invocation_id,
        "invocation_ref": _protected_to_data(record.invocation_ref),
        "operation_id": record.attempt_id.operation_id,
        "outcome_kind": _enum_value(record.outcome_kind),
        "policy": record.policy.value,
        "resolution": _enum_value(record.resolution),
        "result_digest": record.result_digest,
        "result_ref": _result_to_data(record.result_ref),
        "run_id": record.run_id,
        "session_id": record.attempt_id.session_id,
        "status": record.status.value,
        "tenant_id": record.attempt_id.tenant_id,
        "tool_name": record.tool_name,
        "turn_id": record.turn_id,
        "version": 1,
        "wait_reason_digest": record.wait_reason_digest,
    }
    return _canonical_json(payload)


def side_effect_record_from_json(value: str) -> SideEffectRecord:
    try:
        payload = json.loads(value, parse_constant=_reject_constant)
        if type(payload) is not dict or set(payload) != _FIELDS:
            raise ValueError
        if payload["version"] != 1 or type(payload["tenant_id"]) is not str:
            raise ValueError
        record = SideEffectRecord(
            attempt_id=SideEffectAttemptId(
                payload["tenant_id"],
                payload["session_id"],
                payload["operation_id"],
                payload["attempt"],
            ),
            run_id=payload["run_id"],
            turn_id=payload["turn_id"],
            invocation_id=payload["invocation_id"],
            tool_name=payload["tool_name"],
            policy=SideEffectPolicy(payload["policy"]),
            status=SideEffectStatus(payload["status"]),
            invocation_digest=payload["invocation_digest"],
            invocation_ref=_protected_from_data(payload["invocation_ref"]),
            result_ref=_result_from_data(payload["result_ref"]),
            result_digest=payload["result_digest"],
            outcome_kind=_optional_enum(
                SideEffectOutcomeKind,
                payload["outcome_kind"],
            ),
            failure_code=payload["failure_code"],
            wait_reason_digest=payload["wait_reason_digest"],
            resolution=_optional_enum(
                SideEffectResolutionOutcome,
                payload["resolution"],
            ),
            attestation_ref=_protected_from_data(payload["attestation_ref"]),
            attestation_digest=payload["attestation_digest"],
            compensation_operation_id=payload["compensation_operation_id"],
            compensation_attempt=payload["compensation_attempt"],
            claim_id=payload["claim_id"],
            fencing_token=payload["fencing_token"],
        )
        if side_effect_record_to_json(record) != value:
            raise ValueError
        return record
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise SideEffectTransitionError() from None


def _protected_to_data(reference: ProtectedPayloadRef | None) -> object:
    if reference is None:
        return None
    return {"digest": reference.digest, "token": reference.token}


def _protected_from_data(value: object) -> ProtectedPayloadRef | None:
    if value is None:
        return None
    if type(value) is not dict or set(value) != {"digest", "token"}:
        raise ValueError
    return ProtectedPayloadRef(value["token"], value["digest"])


def _result_to_data(reference: ToolResultRef | None) -> object:
    if reference is None:
        return None
    if type(reference) is InlineToolResultRef:
        return {"content": reference.content, "kind": "inline"}
    if type(reference) is ArtifactToolResultRef:
        return {
            "artifact_id": reference.artifact.artifact_id,
            "filename": reference.artifact.filename,
            "kind": "artifact",
            "media_type": reference.artifact.media_type,
            "preview": reference.preview,
        }
    raise TypeError("result reference is invalid")


def _result_from_data(value: object) -> ToolResultRef | None:
    if value is None:
        return None
    if type(value) is not dict:
        raise ValueError
    if set(value) == {"content", "kind"} and value["kind"] == "inline":
        return InlineToolResultRef(value["content"])
    if set(value) == {
        "artifact_id",
        "filename",
        "kind",
        "media_type",
        "preview",
    } and value["kind"] == "artifact":
        return ArtifactToolResultRef(
            ArtifactRef(
                value["artifact_id"],
                value["filename"],
                value["media_type"],
            ),
            value["preview"],
        )
    raise ValueError


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _enum_value(value: object) -> str | None:
    return None if value is None else value.value  # type: ignore[union-attr]


def _optional_enum(enum_type, value):  # type: ignore[no-untyped-def]
    return None if value is None else enum_type(value)


def _reject_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


__all__ = ["side_effect_record_from_json", "side_effect_record_to_json"]
