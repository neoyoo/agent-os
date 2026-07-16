from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Mapping


PLANNER_ORCHESTRATION_REQUIRED_COMPONENTS: tuple[str, ...] = (
    "decomposition_policy",
    "dag_scheduler",
    "worker_dispatch_loop",
    "compensation_policy",
    "plan_store",
    "worker_supervision",
)
PLANNER_DECOMPOSITION_POLICY_REQUIRED_COMPONENTS: tuple[str, ...] = (
    "prompt_policy",
    "output_schema",
    "validation_gate",
    "template_mapping_policy",
    "approval_policy",
    "model_routing_policy",
    "evaluation_policy",
    "trace_logging",
    "rollback_policy",
)
PLANNER_LLM_DECOMPOSITION_GOVERNANCE_REQUIRED_COMPONENTS: tuple[str, ...] = (
    "prompt_policy",
    "model_routing_policy",
    "approval_policy",
    "evaluation_policy",
    "trace_logging",
    "rollback_policy",
    "output_schema",
    "validation_gate",
    "template_mapping_policy",
    "budget_policy",
    "live_backend_verification",
)


@dataclass(frozen=True, slots=True)
class PlannerOrchestrationDeploymentProfile:
    """Deployment-facing readiness contract for planner orchestration."""

    configured_components: tuple[str, ...] = ()
    required_components: tuple[str, ...] = (
        PLANNER_ORCHESTRATION_REQUIRED_COMPONENTS
    )
    probe_name: str = "planner_orchestration"

    def __post_init__(self) -> None:
        if not self.probe_name.strip():
            raise ValueError("probe_name must not be empty")
        if not self.required_components:
            raise ValueError("required_components must not be empty")
        self._validate_component_names(
            self.required_components,
            field_name="required_components",
        )
        self._validate_component_names(
            self.configured_components,
            field_name="configured_components",
        )

    def missing_components(self) -> tuple[str, ...]:
        """Return required orchestration components not configured."""

        configured = set(self.configured_components)
        return tuple(
            component
            for component in self.required_components
            if component not in configured
        )

    def readiness_metadata(self) -> dict[str, object]:
        """Return JSON-safe deployment guidance for planner orchestration."""

        missing = self.missing_components()
        return {
            "profile": self.__class__.__name__,
            "probe_name": self.probe_name,
            "ready": not missing,
            "required_components": self.required_components,
            "configured_components": self.configured_components,
            "missing_components": missing,
            "sdk_owned": (
                "PlannerRuntime",
                "PlannerTools",
                "PlanDecomposition ingestion",
                "dependency-ready step query",
                "bounded ready-step dispatch",
                "step failure and retry metadata",
                "PlanStore protocol",
                "working-state summary projection",
            ),
            "deployment_owned": (
                "automatic LLM decomposition policy",
                "production DAG scheduler",
                "worker dispatch loop",
                "compensation orchestration",
                "worker process lifecycle",
                "live backend verification",
                "migration execution",
                "credentials and secret distribution",
                "OS/container sandboxing",
            ),
        }

    def readiness_check(self) -> dict[str, object]:
        """Return an ASGI readiness-compatible check payload."""

        metadata = self.readiness_metadata()
        ok = bool(metadata["ready"])
        return {
            **metadata,
            "status": "ok" if ok else "failed",
            "ok": ok,
        }

    def _validate_component_names(
        self,
        components: tuple[str, ...],
        *,
        field_name: str,
    ) -> None:
        if any(not component.strip() for component in components):
            raise ValueError(f"{field_name} must not contain empty names")


@dataclass(frozen=True, slots=True)
class PlannerDecompositionPolicyDeploymentProfile:
    """Deployment-facing readiness contract for LLM decomposition policy."""

    configured_components: tuple[str, ...] = ()
    required_components: tuple[str, ...] = (
        PLANNER_DECOMPOSITION_POLICY_REQUIRED_COMPONENTS
    )
    probe_name: str = "planner_decomposition_policy"

    def __post_init__(self) -> None:
        if not self.probe_name.strip():
            raise ValueError("probe_name must not be empty")
        if not self.required_components:
            raise ValueError("required_components must not be empty")
        self._validate_component_names(
            self.required_components,
            field_name="required_components",
        )
        self._validate_component_names(
            self.configured_components,
            field_name="configured_components",
        )

    def missing_components(self) -> tuple[str, ...]:
        """Return required decomposition-policy components not configured."""

        configured = set(self.configured_components)
        return tuple(
            component
            for component in self.required_components
            if component not in configured
        )

    def readiness_metadata(self) -> dict[str, object]:
        """Return JSON-safe deployment guidance for decomposition policy."""

        missing = self.missing_components()
        return {
            "profile": self.__class__.__name__,
            "probe_name": self.probe_name,
            "ready": not missing,
            "required_components": self.required_components,
            "configured_components": self.configured_components,
            "missing_components": missing,
            "sdk_owned": (
                "PlanDecomposition",
                "PlanStepSpec",
                "PlanDecompositionValidationReport",
                "PlannerRuntime.validate_decomposition",
                "PlannerRuntime.create_plan_from_decomposition",
                "PlannerTools.plan_create_from_decomposition",
                "SubAgentTemplate",
                "objective, step, template, dependency, duplicate-id, and cycle validation",
            ),
            "deployment_owned": (
                "intent classification prompt/policy",
                "LLM decomposition prompt/policy",
                "model selection and routing",
                "retrieval and tool-grounding policy",
                "human approval gate",
                "decomposition evaluation suite",
                "cost and latency budgets",
                "rollout and rollback policy",
                "live backend verification",
            ),
        }

    def readiness_check(self) -> dict[str, object]:
        """Return an ASGI readiness-compatible check payload."""

        metadata = self.readiness_metadata()
        ok = bool(metadata["ready"])
        return {
            **metadata,
            "status": "ok" if ok else "failed",
            "ok": ok,
        }

    def _validate_component_names(
        self,
        components: tuple[str, ...],
        *,
        field_name: str,
    ) -> None:
        if any(not component.strip() for component in components):
            raise ValueError(f"{field_name} must not contain empty names")


@dataclass(frozen=True, slots=True)
class PlannerLlmDecompositionGovernanceProfile:
    """JSON-safe references for deployment-owned LLM planner governance."""

    component_refs: Mapping[str, str] = field(default_factory=dict)
    evidence_refs: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    metadata: Mapping[str, object] = field(default_factory=dict)
    required_components: tuple[str, ...] = (
        PLANNER_LLM_DECOMPOSITION_GOVERNANCE_REQUIRED_COMPONENTS
    )
    probe_name: str = "planner_llm_decomposition_governance"

    def __post_init__(self) -> None:
        if not self.probe_name.strip():
            raise ValueError("probe_name must not be empty")
        if not self.required_components:
            raise ValueError("required_components must not be empty")
        self._validate_component_names(
            self.required_components,
            field_name="required_components",
        )
        self._validate_refs(
            self.component_refs,
            field_name="component_refs",
        )
        self._validate_evidence_refs(self.evidence_refs)
        self._validate_metadata(self.metadata)

    def configured_component_names(self) -> tuple[str, ...]:
        """Return required components with non-empty policy references."""

        configured = set(self.component_refs)
        return tuple(
            component
            for component in self.required_components
            if component in configured
        )

    def missing_components(self) -> tuple[str, ...]:
        """Return required governance references not configured."""

        configured = set(self.configured_component_names())
        return tuple(
            component
            for component in self.required_components
            if component not in configured
        )

    def readiness_metadata(self) -> dict[str, object]:
        """Return JSON-safe deployment guidance for LLM governance refs."""

        missing = self.missing_components()
        component_refs = {
            component: self.component_refs[component]
            for component in self.configured_component_names()
        }
        evidence_refs = {
            component: refs
            for component, refs in self.evidence_refs.items()
            if component in component_refs
        }
        return {
            "profile": self.__class__.__name__,
            "probe_name": self.probe_name,
            "ready": not missing,
            "required_components": self.required_components,
            "configured_components": self.configured_component_names(),
            "missing_components": missing,
            "component_refs": component_refs,
            "evidence_refs": evidence_refs,
            "metadata": dict(self.metadata),
            "sdk_owned": (
                "PlannerRuntime.gate_decomposition_proposal",
                "PlanDecompositionGatePolicy",
                "PlanDecompositionGateReport",
                "PlannerRuntime.validate_decomposition",
                "PlanDecompositionValidationReport",
                "PlannerDecompositionPolicyDeploymentProfile",
                "JSON-safe governance reference readiness payloads",
            ),
            "deployment_owned": (
                "prompt text and prompt review workflow",
                "model router implementation",
                "human approval workflow",
                "evaluation platform execution",
                "trace logging backend",
                "cost and latency budget enforcement",
                "rollout and rollback execution",
                "live backend verification execution",
                "secret storage and credential distribution",
            ),
        }

    def readiness_check(self) -> dict[str, object]:
        """Return an ASGI readiness-compatible check payload."""

        metadata = self.readiness_metadata()
        ok = bool(metadata["ready"])
        return {
            **metadata,
            "status": "ok" if ok else "failed",
            "ok": ok,
        }

    def _validate_component_names(
        self,
        components: tuple[str, ...],
        *,
        field_name: str,
    ) -> None:
        if any(not component.strip() for component in components):
            raise ValueError(f"{field_name} must not contain empty names")

    def _validate_refs(
        self,
        refs: Mapping[str, str],
        *,
        field_name: str,
    ) -> None:
        for component, ref in refs.items():
            if not component.strip() or not ref.strip():
                raise ValueError(f"{field_name} must not contain empty values")

    def _validate_evidence_refs(
        self,
        evidence_refs: Mapping[str, tuple[str, ...]],
    ) -> None:
        for component, refs in evidence_refs.items():
            if not component.strip() or any(not ref.strip() for ref in refs):
                raise ValueError("evidence_refs must not contain empty values")

    def _validate_metadata(self, metadata: Mapping[str, object]) -> None:
        try:
            json.dumps(dict(metadata))
        except (TypeError, ValueError) as exc:
            raise ValueError("metadata must be JSON serializable") from exc


__all__ = [
    "PLANNER_DECOMPOSITION_POLICY_REQUIRED_COMPONENTS",
    "PLANNER_LLM_DECOMPOSITION_GOVERNANCE_REQUIRED_COMPONENTS",
    "PLANNER_ORCHESTRATION_REQUIRED_COMPONENTS",
    "PlannerDecompositionPolicyDeploymentProfile",
    "PlannerLlmDecompositionGovernanceProfile",
    "PlannerOrchestrationDeploymentProfile",
]
