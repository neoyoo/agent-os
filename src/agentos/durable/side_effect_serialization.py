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
)


_RECORD_FIELDS = {
    "version",
    "tenant_id",
    "session_id",
    "operation_id",
    "attempt",
    "run_id",
    "turn_id",
    "invocation_id",
    "tool_name",
    "policy",
    "status",
    "invocation_digest",
    "invocation_ref",
    "result_ref",
    "result_digest",
    "outcome_kind",
    "failure_code",
    "wait_reason_digest",
    "resolution",
    "attestation_ref",
    "attestation_digest",
    "compensation_operation_id",
    "compensation_attempt",
    "claim_id",
    "fencing_token",
}


def side_effect_record_to_json(record: SideEffectRecord) -> str:
    """把 canonical Ledger record 编码为确定性 JSON。"""

    if type(record) is not SideEffectRecord:
        raise TypeError("record must be SideEffectRecord")
    payload = {
        "version": 1,
        "tenant_id": record.attempt_id.tenant_id,
        "session_id": record.attempt_id.session_id,
        "operation_id": record.attempt_id.operation_id,
        "attempt": record.attempt_id.attempt,
        "run_id": record.run_id,
        "turn_id": record.turn_id,
        "invocation_id": record.invocation_id,
        "tool_name": record.tool_name,
        "policy": record.policy.value,
        "status": record.status.value,
        "invocation_digest": record.invocation_digest,
        "invocation_ref": _protected_ref_to_data(record.invocation_ref),
        "result_ref": _result_ref_to_data(record.result_ref),
        "result_digest": record.result_digest,
        "outcome_kind": _enum_value(record.outcome_kind),
        "failure_code": record.failure_code,
        "wait_reason_digest": record.wait_reason_digest,
        "resolution": _enum_value(record.resolution),
        "attestation_ref": _protected_ref_to_data(record.attestation_ref),
        "attestation_digest": record.attestation_digest,
        "compensation_operation_id": record.compensation_operation_id,
        "compensation_attempt": record.compensation_attempt,
        "claim_id": record.claim_id,
        "fencing_token": record.fencing_token,
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def side_effect_record_from_json(value: str) -> SideEffectRecord:
    """严格解析 Durable Ledger JSON，不接受额外字段。"""

    payload = json.loads(value, parse_constant=_reject_json_constant)
    if type(payload) is not dict or set(payload) != _RECORD_FIELDS:
        raise ValueError("side effect record payload is invalid")
    if payload["version"] != 1 or payload["tenant_id"] is not None:
        raise ValueError("side effect record version or scope is invalid")
    record = SideEffectRecord(
        attempt_id=SideEffectAttemptId(
            None,
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
        invocation_ref=_protected_ref_from_data(payload["invocation_ref"]),
        result_ref=_result_ref_from_data(payload["result_ref"]),
        result_digest=payload["result_digest"],
        outcome_kind=_optional_enum(SideEffectOutcomeKind, payload["outcome_kind"]),
        failure_code=payload["failure_code"],
        wait_reason_digest=payload["wait_reason_digest"],
        resolution=_optional_enum(
            SideEffectResolutionOutcome,
            payload["resolution"],
        ),
        attestation_ref=_protected_ref_from_data(payload["attestation_ref"]),
        attestation_digest=payload["attestation_digest"],
        compensation_operation_id=payload["compensation_operation_id"],
        compensation_attempt=payload["compensation_attempt"],
        claim_id=payload["claim_id"],
        fencing_token=payload["fencing_token"],
    )
    if side_effect_record_to_json(record) != value:
        raise ValueError("side effect record payload is not canonical")
    return record


def _protected_ref_to_data(reference: ProtectedPayloadRef | None) -> object:
    if reference is None:
        return None
    return {"token": reference.token, "digest": reference.digest}


def _protected_ref_from_data(value: object) -> ProtectedPayloadRef | None:
    if value is None:
        return None
    if type(value) is not dict or set(value) != {"token", "digest"}:
        raise ValueError("protected payload reference is invalid")
    return ProtectedPayloadRef(value["token"], value["digest"])


def _result_ref_to_data(reference: ToolResultRef | None) -> object:
    if reference is None:
        return None
    if type(reference) is InlineToolResultRef:
        return {"kind": "inline", "content": reference.content}
    if type(reference) is ArtifactToolResultRef:
        return {
            "kind": "artifact",
            "artifact_id": reference.artifact.artifact_id,
            "filename": reference.artifact.filename,
            "media_type": reference.artifact.media_type,
            "preview": reference.preview,
        }
    raise TypeError("result reference is invalid")


def _result_ref_from_data(value: object) -> ToolResultRef | None:
    if value is None:
        return None
    if type(value) is not dict:
        raise ValueError("result reference is invalid")
    if set(value) == {"kind", "content"} and value["kind"] == "inline":
        return InlineToolResultRef(value["content"])
    if set(value) == {
        "kind",
        "artifact_id",
        "filename",
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
    raise ValueError("result reference is invalid")


def _enum_value(value: object) -> str | None:
    return None if value is None else value.value  # type: ignore[union-attr]


def _optional_enum(enum_type, value):  # type: ignore[no-untyped-def]
    return None if value is None else enum_type(value)


def _reject_json_constant(value: str) -> object:
    raise ValueError(f"invalid JSON constant: {value}")


__all__ = ["side_effect_record_from_json", "side_effect_record_to_json"]
