from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from agentos.capabilities.result_refs import (
    ArtifactToolResultRef,
    InlineToolResultRef,
    ToolResultRef,
)
from agentos.capabilities.tools import SideEffectPolicy
from agentos.runtime._side_effect_validation import (
    require_digest as _require_digest,
    require_identifier as _require_identifier,
    require_positive as _require_positive,
    require_stable_id as _require_stable_id,
    validate_fence as _validate_fence,
)
from agentos.runtime.payloads import ProtectedPayloadRef
from agentos.runtime.side_effect_integrity import (
    result_ref_digest,
    wait_reason_digest,
)
from agentos.runtime.tool_identity import compensation_operation_id


_SIDE_EFFECT_RESOLUTION_INLINE_MAX_CHARS = 4_096


class SideEffectContractError(RuntimeError):
    """Side Effect canonical contract 错误基类。"""


class SideEffectRecordConflictError(SideEffectContractError):
    """同一 operation 的 immutable identity 发生冲突。"""

    def __init__(self) -> None:
        super().__init__("side effect record conflicts with existing identity")


class SideEffectTransitionError(SideEffectContractError):
    """Ledger 状态转换不合法或 completion 已过期。"""

    def __init__(self) -> None:
        super().__init__("side effect state transition is invalid")


class SideEffectResultIntegrityError(SideEffectContractError):
    """Tool Result ref 与 digest 不一致。"""

    def __init__(self) -> None:
        super().__init__("side effect result integrity check failed")


class SideEffectInFlightError(SideEffectContractError):
    """当前副作用尚未到达 cancel safe-stop。"""

    def __init__(self) -> None:
        super().__init__("side effect is still in flight")


class SideEffectStatus(str, Enum):
    """Side Effect Ledger attempt 状态。"""

    RESERVED = "reserved"
    STARTED = "started"
    COMPLETED = "completed"
    AMBIGUOUS = "ambiguous"
    COMPENSATING = "compensating"
    COMPENSATED = "compensated"
    RESOLVED = "resolved"


class SideEffectOutcomeKind(str, Enum):
    """COMPLETED attempt 的确定 outcome。"""

    PROVIDER_RESULT = "provider_result"
    WAIT_CONTROL = "wait_control"
    HANDLER_ERROR = "handler_error"


class SideEffectResolutionOutcome(str, Enum):
    """RESOLVED attempt 的审计结论。"""

    CANCELLED_BEFORE_START = "cancelled_before_start"
    SUPERSEDED = "superseded"
    ACCEPTED = "accepted"
    RETRY_SAFE = "retry_safe"
    FAILED = "failed"


class SideEffectResolutionKind(str, Enum):
    """应用可以提交的 Side Effect resolution。"""

    ACCEPT_RESULT = "accept_result"
    RETRY_PROVEN_SAFE = "retry_proven_safe"
    COMPENSATE = "compensate"
    FAIL = "fail"


@dataclass(frozen=True, slots=True)
class SideEffectAttemptId:
    """一个 Ledger attempt 的 tenant/session-scoped identity。"""

    tenant_id: str | None
    session_id: str
    operation_id: str
    attempt: int

    def __post_init__(self) -> None:
        if self.tenant_id is not None:
            _require_identifier(self.tenant_id, "tenant_id")
        _require_identifier(self.session_id, "session_id")
        _require_stable_id(self.operation_id, "operation", "operation_id")
        _require_positive(self.attempt, "attempt")


@dataclass(frozen=True, slots=True)
class CompensationAttemptId:
    """补偿 completion 必须精确匹配的 attempt identity。"""

    side_effect_attempt: SideEffectAttemptId
    compensation_operation_id: str
    compensation_attempt: int

    def __post_init__(self) -> None:
        if type(self.side_effect_attempt) is not SideEffectAttemptId:
            raise TypeError("side_effect_attempt must be SideEffectAttemptId")
        _require_stable_id(
            self.compensation_operation_id,
            "operation",
            "compensation_operation_id",
        )
        _require_positive(self.compensation_attempt, "compensation_attempt")


@dataclass(frozen=True, slots=True)
class WaitingToolCompletion:
    """WAITING composite commit 使用的精确 Tool attempt completion。"""

    invocation_id: str
    operation_id: str
    attempt: int
    wait_reason_digest: str

    def __post_init__(self) -> None:
        _require_stable_id(self.invocation_id, "invocation", "invocation_id")
        _require_stable_id(self.operation_id, "operation", "operation_id")
        _require_positive(self.attempt, "attempt")
        _require_digest(self.wait_reason_digest, "wait_reason_digest")


@dataclass(frozen=True, slots=True)
class SideEffectCompletion:
    """一次 STARTED attempt 的确定 completion。"""

    outcome_kind: SideEffectOutcomeKind
    result_ref: ToolResultRef | None = None
    result_digest: str | None = None
    failure_code: str | None = None

    def __post_init__(self) -> None:
        if type(self.outcome_kind) is not SideEffectOutcomeKind:
            raise TypeError("outcome_kind must be SideEffectOutcomeKind")
        if self.outcome_kind is SideEffectOutcomeKind.PROVIDER_RESULT:
            _require_result_pair(self.result_ref, self.result_digest)
            if self.failure_code is not None:
                raise ValueError("provider result cannot carry failure_code")
            return
        if self.outcome_kind is SideEffectOutcomeKind.WAIT_CONTROL:
            if any(
                value is not None
                for value in (self.result_ref, self.result_digest, self.failure_code)
            ):
                raise ValueError("wait control cannot carry result or failure")
            return
        if self.result_ref is not None or self.result_digest is not None:
            raise ValueError("handler error cannot carry a result")
        _require_failure_code(self.failure_code)


@dataclass(frozen=True, slots=True)
class SideEffectResolution:
    """受权 Application 提交的类型化 reconciliation 决议。"""

    operation_id: str
    kind: SideEffectResolutionKind
    result_ref: ToolResultRef | None = None
    result_digest: str | None = None
    attestation_ref: ProtectedPayloadRef | None = None
    attestation_digest: str | None = None

    def __post_init__(self) -> None:
        _require_stable_id(self.operation_id, "operation", "operation_id")
        if type(self.kind) is not SideEffectResolutionKind:
            raise TypeError("kind must be SideEffectResolutionKind")
        if self.kind is SideEffectResolutionKind.ACCEPT_RESULT:
            _require_result_pair(self.result_ref, self.result_digest)
            if (
                type(self.result_ref) is InlineToolResultRef
                and len(self.result_ref.content)
                > _SIDE_EFFECT_RESOLUTION_INLINE_MAX_CHARS
            ):
                raise ValueError(
                    "inline side effect resolution must not exceed "
                    "4096 Unicode characters",
                )
            _require_absent_attestation(self)
            return
        if self.kind is SideEffectResolutionKind.RETRY_PROVEN_SAFE:
            if self.result_ref is not None or self.result_digest is not None:
                raise ValueError("retry resolution cannot carry a result")
            if type(self.attestation_ref) is not ProtectedPayloadRef:
                raise ValueError("retry resolution requires protected attestation")
            _require_digest(self.attestation_digest, "attestation_digest")
            if self.attestation_ref.digest != self.attestation_digest:
                raise ValueError("attestation_digest does not match attestation_ref")
            return
        if any(
            value is not None
            for value in (
                self.result_ref,
                self.result_digest,
                self.attestation_ref,
                self.attestation_digest,
            )
        ):
            raise ValueError("compensate and fail resolutions carry no extra payload")


@dataclass(frozen=True, slots=True)
class SideEffectRecord:
    """一个 Ledger attempt 提交后的 canonical immutable record。"""

    attempt_id: SideEffectAttemptId
    run_id: str
    turn_id: str
    invocation_id: str
    tool_name: str
    policy: SideEffectPolicy
    status: SideEffectStatus
    invocation_digest: str
    invocation_ref: ProtectedPayloadRef | None = None
    result_ref: ToolResultRef | None = None
    result_digest: str | None = None
    outcome_kind: SideEffectOutcomeKind | None = None
    failure_code: str | None = None
    wait_reason_digest: str | None = None
    resolution: SideEffectResolutionOutcome | None = None
    attestation_ref: ProtectedPayloadRef | None = None
    attestation_digest: str | None = None
    compensation_operation_id: str | None = None
    compensation_attempt: int | None = None
    claim_id: str | None = None
    fencing_token: int | None = None

    def __post_init__(self) -> None:
        if type(self.attempt_id) is not SideEffectAttemptId:
            raise TypeError("attempt_id must be SideEffectAttemptId")
        for name, value in (
            ("run_id", self.run_id),
            ("turn_id", self.turn_id),
            ("tool_name", self.tool_name),
        ):
            _require_identifier(value, name)
        _require_stable_id(self.invocation_id, "invocation", "invocation_id")
        if type(self.policy) is not SideEffectPolicy:
            raise TypeError("policy must be SideEffectPolicy")
        if type(self.status) is not SideEffectStatus:
            raise TypeError("status must be SideEffectStatus")
        _require_digest(self.invocation_digest, "invocation_digest")
        _validate_fence(self.claim_id, self.fencing_token)
        self._validate_state_fields()

    def _validate_state_fields(self) -> None:
        if self.status in {SideEffectStatus.RESERVED, SideEffectStatus.STARTED}:
            self._require_clear_state_fields()
        elif self.status is SideEffectStatus.COMPLETED:
            self._validate_completed()
        elif self.status is SideEffectStatus.AMBIGUOUS:
            self._require_clear_state_fields()
        elif self.status in {
            SideEffectStatus.COMPENSATING,
            SideEffectStatus.COMPENSATED,
        }:
            self._validate_compensation()
        else:
            self._validate_resolved()

    def _require_clear_state_fields(self) -> None:
        if any(value is not None for value in self._state_fields()):
            raise ValueError("ledger state carries incompatible outcome fields")

    def _validate_completed(self) -> None:
        if type(self.outcome_kind) is not SideEffectOutcomeKind:
            raise ValueError("completed side effect requires outcome_kind")
        if any(
            value is not None
            for value in (
                self.resolution,
                self.attestation_ref,
                self.attestation_digest,
                self.compensation_operation_id,
                self.compensation_attempt,
            )
        ):
            raise ValueError("completed side effect carries incompatible fields")
        if self.outcome_kind is SideEffectOutcomeKind.PROVIDER_RESULT:
            _require_matching_result_pair(self.result_ref, self.result_digest)
            if self.failure_code is not None or self.wait_reason_digest is not None:
                raise ValueError("provider result carries incompatible fields")
        elif self.outcome_kind is SideEffectOutcomeKind.WAIT_CONTROL:
            if self.policy is not SideEffectPolicy.PURE:
                raise ValueError("wait control requires pure policy")
            if any(
                value is not None
                for value in (self.result_ref, self.result_digest, self.failure_code)
            ):
                raise ValueError("wait control carries incompatible fields")
            _require_digest(self.wait_reason_digest, "wait_reason_digest")
        else:
            if self.result_ref is not None or self.result_digest is not None:
                raise ValueError("handler error cannot carry result")
            if self.wait_reason_digest is not None:
                raise ValueError("handler error cannot carry wait reason")
            _require_failure_code(self.failure_code)
            if self.policy not in {
                SideEffectPolicy.PURE,
                SideEffectPolicy.IDEMPOTENT,
            }:
                raise ValueError("unsafe policy cannot complete as handler_error")

    def _validate_compensation(self) -> None:
        if self.policy is not SideEffectPolicy.COMPENSATABLE:
            raise ValueError("only compensatable effects enter compensation")
        if any(
            value is not None
            for value in (
                self.result_ref,
                self.result_digest,
                self.outcome_kind,
                self.failure_code,
                self.wait_reason_digest,
                self.resolution,
                self.attestation_ref,
                self.attestation_digest,
            )
        ):
            raise ValueError("compensation state carries incompatible fields")
        _require_identifier(
            self.compensation_operation_id,
            "compensation_operation_id",
        )
        if self.compensation_operation_id != compensation_operation_id(
            self.attempt_id.operation_id,
        ):
            raise ValueError("compensation_operation_id is not canonical")
        _require_positive(self.compensation_attempt, "compensation_attempt")

    def _validate_resolved(self) -> None:
        if type(self.resolution) is not SideEffectResolutionOutcome:
            raise ValueError("resolved side effect requires resolution")
        if any(
            value is not None
            for value in (
                self.outcome_kind,
                self.failure_code,
                self.wait_reason_digest,
                self.compensation_operation_id,
                self.compensation_attempt,
            )
        ):
            raise ValueError("resolved side effect carries incompatible fields")
        if self.resolution is SideEffectResolutionOutcome.ACCEPTED:
            _require_matching_result_pair(self.result_ref, self.result_digest)
            _require_absent_record_attestation(self)
        elif self.resolution is SideEffectResolutionOutcome.RETRY_SAFE:
            if self.result_ref is not None or self.result_digest is not None:
                raise ValueError("retry-safe resolution cannot carry result")
            if type(self.attestation_ref) is not ProtectedPayloadRef:
                raise ValueError("retry-safe resolution requires attestation")
            _require_digest(self.attestation_digest, "attestation_digest")
            if self.attestation_ref.digest != self.attestation_digest:
                raise ValueError("attestation_digest does not match attestation_ref")
        else:
            if (
                self.resolution is SideEffectResolutionOutcome.SUPERSEDED
                and self.policy
                not in {SideEffectPolicy.PURE, SideEffectPolicy.IDEMPOTENT}
            ):
                raise ValueError("superseded requires pure or idempotent policy")
            if any(
                value is not None
                for value in (
                    self.result_ref,
                    self.result_digest,
                    self.attestation_ref,
                    self.attestation_digest,
                )
            ):
                raise ValueError("resolution carries incompatible evidence")

    def _state_fields(self) -> tuple[object, ...]:
        return (
            self.result_ref,
            self.result_digest,
            self.outcome_kind,
            self.failure_code,
            self.wait_reason_digest,
            self.resolution,
            self.attestation_ref,
            self.attestation_digest,
            self.compensation_operation_id,
            self.compensation_attempt,
        )


def _require_failure_code(value: object) -> None:
    _require_identifier(value, "failure_code")


def _require_result_pair(
    reference: ToolResultRef | None,
    digest: str | None,
) -> None:
    if type(reference) not in {InlineToolResultRef, ArtifactToolResultRef}:
        raise ValueError("result_ref must be a ToolResultRef")
    _require_digest(digest, "result_digest")


def _require_matching_result_pair(
    reference: ToolResultRef | None,
    digest: str | None,
) -> None:
    _require_result_pair(reference, digest)
    if result_ref_digest(reference) != digest:  # type: ignore[arg-type]
        raise ValueError("result_digest does not match result_ref")


def _require_absent_attestation(resolution: SideEffectResolution) -> None:
    if resolution.attestation_ref is not None or resolution.attestation_digest is not None:
        raise ValueError("accept-result resolution cannot carry attestation")


def _require_absent_record_attestation(record: SideEffectRecord) -> None:
    if record.attestation_ref is not None or record.attestation_digest is not None:
        raise ValueError("accepted resolution cannot carry attestation")


__all__ = [
    "CompensationAttemptId",
    "SideEffectAttemptId",
    "SideEffectCompletion",
    "SideEffectContractError",
    "SideEffectInFlightError",
    "SideEffectOutcomeKind",
    "SideEffectRecord",
    "SideEffectRecordConflictError",
    "SideEffectResolution",
    "SideEffectResolutionKind",
    "SideEffectResolutionOutcome",
    "SideEffectResultIntegrityError",
    "SideEffectStatus",
    "SideEffectTransitionError",
    "WaitingToolCompletion",
    "result_ref_digest",
    "wait_reason_digest",
]
