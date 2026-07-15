from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Mapping


PLANNER_LLM_GOVERNANCE_EXECUTION_REQUIRED_EVIDENCE: tuple[str, ...] = (
    "prompt_evidence",
    "model_evidence",
    "approval_evidence",
    "evaluation_evidence",
    "validation_evidence",
)

_PLANNER_LLM_GOVERNANCE_METADATA_BLOCKED_KEYS = (
    "raw_prompt",
    "prompt_text",
    "secret",
    "token",
    "password",
    "credential",
    "api_key",
    "apikey",
    "provider_output",
    "model_output",
)


@dataclass(frozen=True, slots=True)
class PlannerLlmGovernanceEvidenceRecord:
    """部署侧 LLM 治理对单次提案生成的 JSON-safe 证据。"""

    proposal_id: str
    objective: str
    prompt_ref: str | None = None
    prompt_hash: str | None = None
    model_ref: str | None = None
    model_version: str | None = None
    approval_ref: str | None = None
    approved: bool | None = None
    evaluation_ref: str | None = None
    evaluation_passed: bool | None = None
    validation_ref: str | None = None
    validation_passed: bool | None = None
    output_schema_ref: str | None = None
    trace_ref: str | None = None
    budget_ref: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._validate_required_text(self.proposal_id, field_name="proposal_id")
        self._validate_required_text(self.objective, field_name="objective")
        for field_name, value in (
            ("prompt_ref", self.prompt_ref),
            ("prompt_hash", self.prompt_hash),
            ("model_ref", self.model_ref),
            ("model_version", self.model_version),
            ("approval_ref", self.approval_ref),
            ("evaluation_ref", self.evaluation_ref),
            ("validation_ref", self.validation_ref),
            ("output_schema_ref", self.output_schema_ref),
            ("trace_ref", self.trace_ref),
            ("budget_ref", self.budget_ref),
        ):
            self._validate_optional_text(value, field_name=field_name)
        validate_planner_llm_governance_metadata(
            self.metadata,
            field_name="metadata",
        )

    def as_dict(self) -> dict[str, object]:
        """返回 JSON-safe 治理证据。"""

        return {
            "proposal_id": self.proposal_id,
            "objective": self.objective,
            "prompt_ref": self.prompt_ref,
            "prompt_hash": self.prompt_hash,
            "model_ref": self.model_ref,
            "model_version": self.model_version,
            "approval_ref": self.approval_ref,
            "approved": self.approved,
            "evaluation_ref": self.evaluation_ref,
            "evaluation_passed": self.evaluation_passed,
            "validation_ref": self.validation_ref,
            "validation_passed": self.validation_passed,
            "output_schema_ref": self.output_schema_ref,
            "trace_ref": self.trace_ref,
            "budget_ref": self.budget_ref,
            "metadata": dict(self.metadata),
        }

    def _validate_required_text(self, value: str, *, field_name: str) -> None:
        if not value.strip():
            raise ValueError(f"{field_name} must not be empty")

    def _validate_optional_text(
        self,
        value: str | None,
        *,
        field_name: str,
    ) -> None:
        if value is not None and not value.strip():
            raise ValueError(f"{field_name} must not be empty")


@dataclass(frozen=True, slots=True)
class PlannerLlmGovernanceEvidenceGateReport:
    """Planner LLM 治理执行证据的 JSON-safe 门禁报告。"""

    accepted: bool
    block_plan_creation: bool
    errors: tuple[str, ...] = ()
    missing_evidence: tuple[str, ...] = ()
    failed_evidence: tuple[str, ...] = ()
    record: PlannerLlmGovernanceEvidenceRecord | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_planner_llm_governance_metadata(
            self.metadata,
            field_name="metadata",
        )

    def as_dict(self) -> dict[str, object]:
        """返回 JSON-safe 证据门禁报告。"""

        return {
            "accepted": self.accepted,
            "block_plan_creation": self.block_plan_creation,
            "errors": self.errors,
            "missing_evidence": self.missing_evidence,
            "failed_evidence": self.failed_evidence,
            "record": self.record.as_dict() if self.record is not None else None,
            "metadata": dict(self.metadata),
        }


def gate_llm_governance_evidence(
    record: PlannerLlmGovernanceEvidenceRecord,
    *,
    required_evidence: tuple[str, ...] = (
        PLANNER_LLM_GOVERNANCE_EXECUTION_REQUIRED_EVIDENCE
    ),
    metadata: Mapping[str, object] | None = None,
) -> PlannerLlmGovernanceEvidenceGateReport:
    """校验单次提案的 LLM 治理证据，不执行持久化。"""

    _validate_required_evidence(required_evidence)
    gate_metadata = {} if metadata is None else dict(metadata)
    validate_planner_llm_governance_metadata(gate_metadata, field_name="metadata")

    missing: list[str] = []
    failed: list[str] = []
    errors: list[str] = []
    for evidence_name in required_evidence:
        ref_present, status_present, status_passed = _evidence_state(
            record,
            evidence_name,
        )
        if not ref_present or not status_present:
            missing.append(evidence_name)
            errors.append(
                f"{evidence_name.removesuffix('_evidence')} evidence is required",
            )
            continue
        if not status_passed:
            failed.append(evidence_name)
            errors.append(
                f"{evidence_name.removesuffix('_evidence')} evidence did not pass",
            )

    deduped_errors = tuple(dict.fromkeys(errors))
    accepted = not deduped_errors
    return PlannerLlmGovernanceEvidenceGateReport(
        accepted=accepted,
        block_plan_creation=not accepted,
        errors=deduped_errors,
        missing_evidence=tuple(dict.fromkeys(missing)),
        failed_evidence=tuple(dict.fromkeys(failed)),
        record=record,
        metadata=gate_metadata,
    )


def validate_planner_llm_governance_metadata(
    metadata: Mapping[str, object],
    *,
    field_name: str,
) -> None:
    try:
        json.dumps(dict(metadata))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be JSON serializable") from exc

    for key in metadata:
        normalized = key.lower()
        if any(
            blocked in normalized
            for blocked in _PLANNER_LLM_GOVERNANCE_METADATA_BLOCKED_KEYS
        ):
            raise ValueError(f"{field_name} must not include raw prompts or secrets")


def _validate_required_evidence(required_evidence: tuple[str, ...]) -> None:
    if not required_evidence:
        raise ValueError("required_evidence must not be empty")
    allowed = set(PLANNER_LLM_GOVERNANCE_EXECUTION_REQUIRED_EVIDENCE)
    for evidence_name in required_evidence:
        if not evidence_name.strip():
            raise ValueError("required_evidence must not contain empty names")
        if evidence_name not in allowed:
            raise ValueError(f"unknown required evidence: {evidence_name}")


def _evidence_state(
    record: PlannerLlmGovernanceEvidenceRecord,
    evidence_name: str,
) -> tuple[bool, bool, bool]:
    if evidence_name == "prompt_evidence":
        return (record.prompt_ref is not None, True, True)
    if evidence_name == "model_evidence":
        return (record.model_ref is not None, True, True)
    if evidence_name == "approval_evidence":
        return (
            record.approval_ref is not None,
            record.approved is not None,
            record.approved is True,
        )
    if evidence_name == "evaluation_evidence":
        return (
            record.evaluation_ref is not None,
            record.evaluation_passed is not None,
            record.evaluation_passed is True,
        )
    if evidence_name == "validation_evidence":
        return (
            record.validation_ref is not None,
            record.validation_passed is not None,
            record.validation_passed is True,
        )
    raise ValueError(f"unknown required evidence: {evidence_name}")
