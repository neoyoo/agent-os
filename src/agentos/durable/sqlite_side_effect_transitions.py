from __future__ import annotations

from dataclasses import replace

from agentos.capabilities.invocation import ToolInvocation
from agentos.capabilities.result_refs import ToolResultRef
from agentos.capabilities.tools import SideEffectPolicy
from agentos.runtime.payloads import ProtectedPayloadRef
from agentos.runtime.side_effect_integrity import result_ref_digest
from agentos.runtime.side_effect_types import (
    SideEffectAttemptId,
    SideEffectRecord,
    SideEffectResolution,
    SideEffectResolutionOutcome,
    SideEffectResultIntegrityError,
    SideEffectStatus,
    SideEffectTransitionError,
)


def invocation_attempt_id(invocation: ToolInvocation) -> SideEffectAttemptId:
    """从 canonical invocation 取得 Ledger attempt identity。"""

    context = invocation.context
    return SideEffectAttemptId(
        context.tenant_id,
        context.session_id,
        context.operation_id,
        context.attempt,
    )


def reservation_matches(
    record: SideEffectRecord,
    invocation: ToolInvocation,
    policy: SideEffectPolicy,
    digest: str,
    reference: ProtectedPayloadRef,
) -> bool:
    """判断已有 RESERVED record 是否与重复请求完全一致。"""

    return (
        record.attempt_id == invocation_attempt_id(invocation)
        and operation_matches(record, invocation, policy, digest, reference)
    )


def validate_supersede(
    current: SideEffectRecord | None,
    supersedes: SideEffectAttemptId,
    invocation: ToolInvocation,
    policy: SideEffectPolicy,
    digest: str,
    reference: ProtectedPayloadRef,
) -> None:
    """校验 safe retry 只能替代当前 STARTED 的安全策略 attempt。"""

    if (
        current is None
        or current.attempt_id != supersedes
        or current.status is not SideEffectStatus.STARTED
        or current.policy not in {SideEffectPolicy.PURE, SideEffectPolicy.IDEMPOTENT}
        or invocation.context.attempt != current.attempt_id.attempt + 1
        or not operation_matches(current, invocation, policy, digest, reference)
    ):
        raise SideEffectTransitionError


def resolved_record(
    current: SideEffectRecord,
    outcome: SideEffectResolutionOutcome,
    *,
    result_ref: ToolResultRef | None = None,
    result_digest: str | None = None,
) -> SideEffectRecord:
    """构造 accept/fail resolution 的 canonical record。"""

    if result_ref is not None and result_ref_digest(result_ref) != result_digest:
        raise SideEffectResultIntegrityError
    return replace(
        current,
        status=SideEffectStatus.RESOLVED,
        resolution=outcome,
        result_ref=result_ref,
        result_digest=result_digest,
    )


def retry_resolution_records(
    current: SideEffectRecord,
    resolution: SideEffectResolution,
) -> tuple[SideEffectRecord, SideEffectRecord]:
    """构造 retry-safe 的旧 RESOLVED 与新 RESERVED attempts。"""

    if (
        resolution.attestation_ref is None
        or resolution.attestation_ref.digest != resolution.attestation_digest
    ):
        raise SideEffectTransitionError
    resolved = replace(
        current,
        status=SideEffectStatus.RESOLVED,
        resolution=SideEffectResolutionOutcome.RETRY_SAFE,
        attestation_ref=resolution.attestation_ref,
        attestation_digest=resolution.attestation_digest,
    )
    reserved = replace(
        current,
        attempt_id=replace(
            current.attempt_id,
            attempt=current.attempt_id.attempt + 1,
        ),
        status=SideEffectStatus.RESERVED,
        resolution=None,
        attestation_ref=None,
        attestation_digest=None,
    )
    return resolved, reserved


def operation_matches(
    record: SideEffectRecord,
    invocation: ToolInvocation,
    policy: SideEffectPolicy,
    digest: str,
    reference: ProtectedPayloadRef,
) -> bool:
    context = invocation.context
    return (
        record.run_id == context.run_id
        and record.turn_id == context.turn_id
        and record.invocation_id == context.invocation_id
        and record.tool_name == invocation.tool_name
        and record.policy is policy
        and record.invocation_digest == digest
        and record.invocation_ref == reference
    )


__all__: list[str] = []
