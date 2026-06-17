from __future__ import annotations

from collections.abc import Mapping as MappingABC
import time
from dataclasses import dataclass, field, replace
import json
from threading import Event, RLock, Thread
from typing import Literal, Mapping, Protocol, cast
from uuid import uuid4

from agentos.capabilities import RegisteredTool, ToolRegistry
from agentos.multi.types import (
    TaskAlreadySubmittedError as PlanDispatchAlreadySubmittedError,
    TaskHandle,
)
from agentos.workspace import WorkspaceHandle, WorkspaceScope


PlanStatus = Literal["draft", "running", "completed", "failed", "cancelled"]
PLAN_STATUSES: tuple[PlanStatus, ...] = (
    "draft",
    "running",
    "completed",
    "failed",
    "cancelled",
)
PlanStepStatus = Literal[
    "pending",
    "assigned",
    "running",
    "completed",
    "failed",
    "blocked",
    "cancelled",
]
PlanStepRetryStatus = Literal["scheduled", "exhausted"]
PlanAssignmentDispatchStatus = Literal["pending", "submitted", "failed"]
PlannerSchedulerDaemonStatus = Literal["idle", "running", "stopping", "stopped"]
PlannerClaimedSchedulerDaemonStatus = Literal[
    "idle",
    "running",
    "stopping",
    "stopped",
]
PlannerSchedulablePlanReason = Literal[
    "ready-steps",
    "due-retries",
    "pending-dispatch",
]
PlanClaimStatus = Literal["claimed", "busy"]
PlanClaimedSchedulerTickSkipReason = Literal["busy", "tick-failed", "claim-lost"]
PlanClaimSweepSkipReason = Literal["release-race"]
PlanDispatchSkipReason = Literal[
    "missing-template",
    "unknown-template",
    "dispatch-failed",
]
EvidenceKind = Literal["text", "artifact", "task_result", "team_message", "external"]
EVIDENCE_KINDS: tuple[str, ...] = (
    "text",
    "artifact",
    "task_result",
    "team_message",
    "external",
)
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
PLANNER_LLM_GOVERNANCE_EXECUTION_REQUIRED_EVIDENCE: tuple[str, ...] = (
    "prompt_evidence",
    "model_evidence",
    "approval_evidence",
    "evaluation_evidence",
    "validation_evidence",
)
PLANNER_WORKER_DISPATCH_SUPERVISION_REQUIRED_COMPONENTS: tuple[str, ...] = (
    "claimed_scheduler_tick_loop",
    "worker_process_lifecycle",
    "plan_claim_store",
    "scheduler_lock_policy",
    "stale_lease_recovery",
    "compensation_policy",
    "metrics_alerting",
    "live_backend_verification",
)
PLANNER_STALE_CLAIM_SWEEP_REQUIRED_COMPONENTS: tuple[str, ...] = (
    "stale_claim_sweep_schedule",
    "plan_claim_store",
    "scheduler_lock_policy",
    "sweep_safety_window",
    "metrics_alerting",
    "live_backend_verification",
)
PLANNER_SCHEDULER_GOVERNANCE_REQUIRED_COMPONENTS: tuple[str, ...] = (
    "plan_discovery_policy",
    "tenant_routing_policy",
    "global_fairness_policy",
    "scheduler_lock_policy",
    "leader_election_policy",
    "stale_lease_recovery_policy",
    "worker_dispatch_supervision",
    "live_backend_verification",
)


class PlanError(RuntimeError):
    """planner 基础错误。"""


class PlanNotFoundError(PlanError):
    """plan 不存在。"""


class PlanStepNotFoundError(PlanError):
    """plan step 不存在。"""


class PlanClaimLostError(PlanError):
    """Scheduler worker lost the exact plan claim before mutation save."""


class PlannerToolAuthorizationError(PlanError, PermissionError):
    """Planner tool authorization policy rejected an LLM-callable operation."""


class PlannerToolAuthorizationPolicy(Protocol):
    """Authorization boundary for LLM-callable planner tools."""

    def authorize_planner_tool(
        self,
        *,
        tool_name: str,
        owner_agent_id: str,
        plan_id: str | None,
    ) -> None:
        """Raise PlannerToolAuthorizationError when a call is not allowed."""


class DefaultPlannerToolAuthorizationPolicy:
    """Least-privilege planner tool policy for production SDK defaults."""

    _scheduler_tools: frozenset[str] = frozenset(
        {
            "plan_claim_schedulable_plans",
            "plan_claimed_scheduler_tick",
            "plan_dispatch_ready_steps",
            "plan_scheduler_tick",
        },
    )

    def authorize_planner_tool(
        self,
        *,
        tool_name: str,
        owner_agent_id: str,
        plan_id: str | None,
    ) -> None:
        """Deny scheduler/dispatch tools unless deployment injects a policy."""

        if tool_name in self._scheduler_tools:
            raise PlannerToolAuthorizationError(
                f"planner tool requires explicit authorization: {tool_name}",
            )


class AllowAllPlannerToolAuthorizationPolicy:
    """Local/dev policy that allows every planner tool operation."""

    def authorize_planner_tool(
        self,
        *,
        tool_name: str,
        owner_agent_id: str,
        plan_id: str | None,
    ) -> None:
        """Allow all planner tool calls."""


class PlanConflictError(PlanError):
    """Plan changed after it was read; callers must reload before retrying."""


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


@dataclass(frozen=True, slots=True)
class PlannerSchedulerGovernanceDeploymentProfile:
    """Deployment-facing readiness contract for planner scheduler governance."""

    configured_components: tuple[str, ...] = ()
    required_components: tuple[str, ...] = (
        PLANNER_SCHEDULER_GOVERNANCE_REQUIRED_COMPONENTS
    )
    probe_name: str = "planner_scheduler_governance"

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
        """Return required scheduler governance components not configured."""

        configured = set(self.configured_components)
        return tuple(
            component
            for component in self.required_components
            if component not in configured
        )

    def readiness_metadata(self) -> dict[str, object]:
        """Return JSON-safe deployment guidance for scheduler governance."""

        missing = self.missing_components()
        return {
            "profile": self.__class__.__name__,
            "probe_name": self.probe_name,
            "ready": not missing,
            "required_components": self.required_components,
            "configured_components": self.configured_components,
            "missing_components": missing,
            "sdk_owned": (
                "PlannerRuntime.schedulable_plans",
                "PlannerRuntime.claim_schedulable_plans",
                "PlanClaimStore",
                "PostgresPlanClaimStore",
                "PlannerRuntime.claimed_scheduler_tick",
                "PlannerClaimedSchedulerDaemon",
                "PlannerWorkerDispatchSupervisionProfile",
                "PlannerRuntime.sweep_expired_claims",
                "PlannerStaleClaimSweepProfile",
                "readiness-compatible governance payloads",
            ),
            "deployment_owned": (
                "plan discovery policy",
                "tenant routing policy",
                "global fairness policy",
                "distributed scheduler locks",
                "leader election mechanism",
                "stale lease recovery policy",
                "worker dispatch execution",
                "compensation orchestration",
                "credentials and secret distribution",
                "schema migration execution",
                "live backend verification",
                "alert routing and runbooks",
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
class PlannerWorkerDispatchSupervisionProfile:
    """Deployment-facing readiness for planner worker dispatch supervision."""

    reports: tuple[PlanClaimedSchedulerTickReport, ...] = ()
    configured_components: tuple[str, ...] = ()
    required_components: tuple[str, ...] = (
        PLANNER_WORKER_DISPATCH_SUPERVISION_REQUIRED_COMPONENTS
    )
    probe_name: str = "planner_worker_dispatch_supervision"
    max_consecutive_failed_batches: int = 1

    def __post_init__(self) -> None:
        if not self.probe_name.strip():
            raise ValueError("probe_name must not be empty")
        if not self.required_components:
            raise ValueError("required_components must not be empty")
        if self.max_consecutive_failed_batches < 0:
            raise ValueError(
                "max_consecutive_failed_batches must be >= 0",
            )
        self._validate_component_names(
            self.required_components,
            field_name="required_components",
        )
        self._validate_component_names(
            self.configured_components,
            field_name="configured_components",
        )

    def missing_components(self) -> tuple[str, ...]:
        """Return required dispatch supervision components not configured."""

        configured = set(self.configured_components)
        return tuple(
            component
            for component in self.required_components
            if component not in configured
        )

    def health_payload(self) -> dict[str, object]:
        """Return a JSON-safe summary of recent claimed scheduler reports."""

        report_count = len(self.reports)
        claim_count = sum(len(report.claims) for report in self.reports)
        claimed_count = sum(
            1
            for report in self.reports
            for claim in report.claims
            if claim.status == "claimed"
        )
        busy_count = sum(
            1
            for report in self.reports
            for claim in report.claims
            if claim.status == "busy"
        )
        tick_report_count = sum(
            len(report.tick_reports) for report in self.reports
        )
        released_count = sum(
            len(report.released_plan_ids) for report in self.reports
        )
        tick_failed_count = sum(
            1
            for report in self.reports
            for skip in report.skipped
            if skip.reason == "tick-failed"
        )
        consecutive_failed_batches = self._consecutive_failed_batches()
        last_report = self.reports[-1] if self.reports else None
        last_tick_failed_plan_ids = (
            tuple(
                skip.plan_id
                for skip in last_report.skipped
                if skip.reason == "tick-failed"
            )
            if last_report is not None
            else ()
        )
        if report_count == 0:
            status = "unstarted"
        elif consecutive_failed_batches > self.max_consecutive_failed_batches:
            status = "unhealthy"
        elif tick_failed_count:
            status = "degraded"
        else:
            status = "healthy"
        ok = status == "healthy"
        return {
            "status": status,
            "ok": ok,
            "probe_name": self.probe_name,
            "report_count": report_count,
            "last_worker_id": (
                last_report.worker_id
                if last_report is not None
                else None
            ),
            "claim_count": claim_count,
            "claimed_count": claimed_count,
            "busy_count": busy_count,
            "tick_report_count": tick_report_count,
            "tick_failed_count": tick_failed_count,
            "released_count": released_count,
            "consecutive_failed_batches": consecutive_failed_batches,
            "max_consecutive_failed_batches": (
                self.max_consecutive_failed_batches
            ),
            "last_tick_failed_plan_ids": last_tick_failed_plan_ids,
        }

    def readiness_metadata(self) -> dict[str, object]:
        """Return JSON-safe deployment guidance for dispatch supervision."""

        missing = self.missing_components()
        health = self.health_payload()
        return {
            "profile": self.__class__.__name__,
            "probe_name": self.probe_name,
            "ready": not missing and bool(health["ok"]),
            "required_components": self.required_components,
            "configured_components": self.configured_components,
            "missing_components": missing,
            "health_status": health["status"],
            "health": health,
            "sdk_owned": (
                "PlanClaimedSchedulerTickReport",
                "PlanClaimedSchedulerTickSkip",
                "PlannerRuntime.claimed_scheduler_tick",
                "plan_claimed_scheduler_tick",
                "PlannerWorkerDispatchSupervisionProfile",
                "JSON-safe dispatch supervision payloads",
            ),
            "deployment_owned": (
                "process supervisor or job runner",
                "worker lifecycle execution",
                "plan discovery sources",
                "tenant filtering",
                "distributed scheduler locks",
                "leader election",
                "stale lease sweepers",
                "fairness policy",
                "compensation orchestration",
                "credentials and secret distribution",
                "schema migration execution",
                "alert routing and runbooks",
                "live backend verification",
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

    def _consecutive_failed_batches(self) -> int:
        count = 0
        for report in reversed(self.reports):
            if any(skip.reason == "tick-failed" for skip in report.skipped):
                count += 1
                continue
            break
        return count

    def _validate_component_names(
        self,
        components: tuple[str, ...],
        *,
        field_name: str,
    ) -> None:
        if any(not component.strip() for component in components):
            raise ValueError(f"{field_name} must not contain empty names")


@dataclass(frozen=True, slots=True)
class PlannerStaleClaimSweepProfile:
    """Deployment-facing readiness for planner stale claim sweeping."""

    reports: tuple[PlanClaimSweepReport, ...] = ()
    configured_components: tuple[str, ...] = ()
    required_components: tuple[str, ...] = (
        PLANNER_STALE_CLAIM_SWEEP_REQUIRED_COMPONENTS
    )
    probe_name: str = "planner_stale_claim_sweep"

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
        """Return required stale-claim sweep components not configured."""

        configured = set(self.configured_components)
        return tuple(
            component
            for component in self.required_components
            if component not in configured
        )

    def health_payload(self) -> dict[str, object]:
        """Return a JSON-safe summary of recent stale-claim sweeps."""

        report_count = len(self.reports)
        checked_count = sum(
            len(report.checked_claims) for report in self.reports
        )
        released_count = sum(
            len(report.released_claims) for report in self.reports
        )
        skipped_count = sum(
            len(report.skipped_claims) for report in self.reports
        )
        dry_run_count = sum(1 for report in self.reports if report.dry_run)
        last_report = self.reports[-1] if self.reports else None
        last_released_plan_ids = (
            tuple(claim.plan_id for claim in last_report.released_claims)
            if last_report is not None
            else ()
        )
        last_skipped_plan_ids = (
            tuple(skip.plan_id for skip in last_report.skipped_claims)
            if last_report is not None
            else ()
        )
        if report_count == 0:
            status = "unstarted"
        elif skipped_count:
            status = "degraded"
        else:
            status = "healthy"
        ok = status == "healthy"
        return {
            "status": status,
            "ok": ok,
            "probe_name": self.probe_name,
            "report_count": report_count,
            "checked_count": checked_count,
            "released_count": released_count,
            "skipped_count": skipped_count,
            "dry_run_count": dry_run_count,
            "last_now": (
                last_report.now
                if last_report is not None
                else None
            ),
            "last_owner_agent_id": (
                last_report.owner_agent_id
                if last_report is not None
                else None
            ),
            "last_released_plan_ids": last_released_plan_ids,
            "last_skipped_plan_ids": last_skipped_plan_ids,
        }

    def readiness_metadata(self) -> dict[str, object]:
        """Return JSON-safe deployment guidance for stale-claim sweeping."""

        missing = self.missing_components()
        health = self.health_payload()
        return {
            "profile": self.__class__.__name__,
            "probe_name": self.probe_name,
            "ready": not missing and bool(health["ok"]),
            "required_components": self.required_components,
            "configured_components": self.configured_components,
            "missing_components": missing,
            "health_status": health["status"],
            "health": health,
            "sdk_owned": (
                "PlanClaimSweepStore",
                "PlanClaimSweepReport",
                "PlanClaimSweepSkip",
                "PlannerRuntime.sweep_expired_claims",
                "InMemoryPlanClaimStore.expired_claims",
                "PostgresPlanClaimStore.expired_claims",
                "exact expired-claim release guard",
                "JSON-safe stale claim sweep payloads",
            ),
            "deployment_owned": (
                "cron or scheduler",
                "distributed scheduler locks",
                "leader election",
                "tenant filters and fairness policy",
                "sweep safety window policy",
                "alert routing and runbooks",
                "compensation orchestration",
                "credentials and secret distribution",
                "schema migration execution",
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
class PlanRetryPolicy:
    """Retry/backoff policy for failed planner steps."""

    max_attempts: int = 1
    backoff_seconds: float = 0.0
    backoff_multiplier: float = 1.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.backoff_seconds < 0:
            raise ValueError("backoff_seconds must be >= 0")
        if self.backoff_multiplier < 1:
            raise ValueError("backoff_multiplier must be >= 1")

    def delay_for_attempt(self, attempt: int) -> float:
        """Return retry delay after the given failed attempt."""

        return self.backoff_seconds * (
            self.backoff_multiplier ** max(0, attempt - 1)
        )


@dataclass(frozen=True, slots=True)
class SubAgentTemplate:
    """可复用 subagent 执行模板。"""

    template_id: str
    name: str
    role: str
    capabilities: tuple[str, ...] = ()
    allowed_tool_names: tuple[str, ...] = ()
    context_seed: tuple[str, ...] = ()
    workspace_scope: WorkspaceScope = "task"
    target_agent_id: str | None = None
    timeout_seconds: float = 300


@dataclass(frozen=True, slots=True)
class EvidenceHandle:
    """plan 中引用的 evidence/artifact 句柄。"""

    evidence_id: str
    kind: EvidenceKind
    summary: str
    uri: str | None = None
    producer_agent_id: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PlanStepSpec:
    """One structured step proposed by a decomposition policy."""

    instruction: str
    step_id: str | None = None
    required_capabilities: tuple[str, ...] = ()
    template_id: str | None = None
    depends_on: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PlanDecomposition:
    """Structured plan proposal produced by an app-owned decomposition policy."""

    objective: str
    steps: tuple[PlanStepSpec, ...]


@dataclass(frozen=True, slots=True)
class PlanDecompositionValidationReport:
    """Validation result for an app-owned planner decomposition proposal."""

    ok: bool
    errors: tuple[str, ...] = ()
    step_count: int = 0
    required_templates: tuple[str, ...] = ()
    unknown_templates: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-safe validation report payload."""

        return {
            "ok": self.ok,
            "errors": self.errors,
            "step_count": self.step_count,
            "required_templates": self.required_templates,
            "unknown_templates": self.unknown_templates,
        }


@dataclass(frozen=True, slots=True)
class PlanDecompositionGatePolicy:
    """Policy for gating raw LLM decomposition proposals before persistence."""

    max_steps: int | None = None
    require_template: bool = False
    require_approval: bool = False
    approved: bool = False
    allowed_template_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.max_steps is not None and self.max_steps < 1:
            raise ValueError("max_steps must be >= 1")
        if any(not template_id.strip() for template_id in self.allowed_template_ids):
            raise ValueError("allowed_template_ids must not contain empty names")


@dataclass(frozen=True, slots=True)
class PlanDecompositionGateReport:
    """JSON-safe report for a raw planner decomposition proposal gate."""

    accepted: bool
    requires_approval: bool = False
    errors: tuple[str, ...] = ()
    validation: PlanDecompositionValidationReport | None = None
    step_count: int = 0
    required_templates: tuple[str, ...] = ()
    unknown_templates: tuple[str, ...] = ()
    missing_templates: tuple[str, ...] = ()
    disallowed_templates: tuple[str, ...] = ()
    normalized_decomposition: PlanDecomposition | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-safe gate report payload."""

        return {
            "accepted": self.accepted,
            "requires_approval": self.requires_approval,
            "errors": self.errors,
            "validation": (
                self.validation.as_dict()
                if self.validation is not None
                else None
            ),
            "step_count": self.step_count,
            "required_templates": self.required_templates,
            "unknown_templates": self.unknown_templates,
            "missing_templates": self.missing_templates,
            "disallowed_templates": self.disallowed_templates,
            "normalized_decomposition": (
                self._decomposition_to_dict(self.normalized_decomposition)
                if self.normalized_decomposition is not None
                else None
            ),
            "metadata": dict(self.metadata),
        }

    def _decomposition_to_dict(
        self,
        decomposition: PlanDecomposition,
    ) -> dict[str, object]:
        return {
            "objective": decomposition.objective,
            "steps": [
                {
                    "step_id": step.step_id,
                    "instruction": step.instruction,
                    "required_capabilities": step.required_capabilities,
                    "template_id": step.template_id,
                    "depends_on": step.depends_on,
                }
                for step in decomposition.steps
            ],
        }


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
    """JSON-safe per-proposal evidence for deployment-owned LLM governance."""

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
        self._validate_metadata(self.metadata, field_name="metadata")

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-safe governance evidence payload."""

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

    def _validate_metadata(
        self,
        metadata: Mapping[str, object],
        *,
        field_name: str,
    ) -> None:
        _validate_planner_llm_governance_metadata(metadata, field_name=field_name)


@dataclass(frozen=True, slots=True)
class PlannerLlmGovernanceEvidenceGateReport:
    """JSON-safe gate report for planner LLM governance execution evidence."""

    accepted: bool
    block_plan_creation: bool
    errors: tuple[str, ...] = ()
    missing_evidence: tuple[str, ...] = ()
    failed_evidence: tuple[str, ...] = ()
    record: PlannerLlmGovernanceEvidenceRecord | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_planner_llm_governance_metadata(
            self.metadata,
            field_name="metadata",
        )

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-safe evidence gate report payload."""

        return {
            "accepted": self.accepted,
            "block_plan_creation": self.block_plan_creation,
            "errors": self.errors,
            "missing_evidence": self.missing_evidence,
            "failed_evidence": self.failed_evidence,
            "record": self.record.as_dict() if self.record is not None else None,
            "metadata": dict(self.metadata),
        }


def _validate_planner_llm_governance_metadata(
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
        if any(blocked in normalized for blocked in _PLANNER_LLM_GOVERNANCE_METADATA_BLOCKED_KEYS):
            raise ValueError(
                f"{field_name} must not include raw prompts or secrets",
            )


@dataclass(frozen=True, slots=True)
class PlanStep:
    """plan 中的一个可分配步骤。"""

    step_id: str
    instruction: str
    status: PlanStepStatus = "pending"
    required_capabilities: tuple[str, ...] = ()
    assigned_agent_id: str | None = None
    template_id: str | None = None
    task_id: str | None = None
    depends_on: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    error: str | None = None
    attempts: int = 0
    last_failed_at: float | None = None
    next_retry_at: float | None = None
    retry_status: PlanStepRetryStatus | None = None
    retry_exhausted_at: float | None = None


@dataclass(frozen=True, slots=True)
class PlanAssignment:
    """step -> task/subagent 的分配记录。"""

    plan_id: str
    step_id: str
    template_id: str
    task_id: str
    target_agent_id: str
    created_at: float
    dispatch_status: PlanAssignmentDispatchStatus = "pending"
    submitted_at: float | None = None
    dispatch_error: str | None = None


@dataclass(frozen=True, slots=True)
class PlanDispatchSkip:
    """One ready step that was not submitted during a dispatch batch."""

    plan_id: str
    step_id: str
    reason: PlanDispatchSkipReason
    detail: str = ""


@dataclass(frozen=True, slots=True)
class PlanDispatchReport:
    """Transient report for one ready-step dispatch batch."""

    plan_id: str
    assigned: tuple[PlanAssignment, ...] = ()
    skipped: tuple[PlanDispatchSkip, ...] = ()


@dataclass(frozen=True, slots=True)
class PlanSchedulerRetryReset:
    """One retryable failed step reset during a scheduler tick."""

    plan_id: str
    step_id: str
    attempts: int


@dataclass(frozen=True, slots=True)
class PlanSchedulerTickReport:
    """Transient report for one bounded planner scheduler pass."""

    plan_id: str
    retry_resets: tuple[PlanSchedulerRetryReset, ...] = ()
    dispatch: PlanDispatchReport = field(
        default_factory=lambda: PlanDispatchReport(plan_id=""),
    )


@dataclass(frozen=True, slots=True)
class PlanClaimedSchedulerTickSkip:
    """One schedulable plan skipped during a claimed scheduler tick batch."""

    plan_id: str
    reason: PlanClaimedSchedulerTickSkipReason
    detail: str = ""
    claim_result: PlanClaimResult | None = None

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-safe skip payload."""

        return {
            "plan_id": self.plan_id,
            "reason": self.reason,
            "detail": self.detail,
            "claim_result": (
                self.claim_result.as_dict()
                if self.claim_result is not None
                else None
            ),
        }


@dataclass(frozen=True, slots=True)
class PlanClaimedSchedulerTickReport:
    """Report for claiming schedulable plans before scheduler ticks."""

    worker_id: str
    claims: tuple[PlanClaimResult, ...] = ()
    tick_reports: tuple[PlanSchedulerTickReport, ...] = ()
    skipped: tuple[PlanClaimedSchedulerTickSkip, ...] = ()
    released_plan_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PlannerSchedulablePlan:
    """Read-only summary of a plan with work worth ticking."""

    plan_id: str
    owner_agent_id: str
    status: PlanStatus
    ready_step_ids: tuple[str, ...] = ()
    retryable_step_ids: tuple[str, ...] = ()
    reasons: tuple[PlannerSchedulablePlanReason, ...] = ()
    updated_at: float = 0

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-safe schedulable-plan summary."""

        return {
            "plan_id": self.plan_id,
            "owner_agent_id": self.owner_agent_id,
            "status": self.status,
            "ready_step_ids": list(self.ready_step_ids),
            "retryable_step_ids": list(self.retryable_step_ids),
            "reasons": list(self.reasons),
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class PlanClaimRecord:
    """Lease record for one schedulable planner plan."""

    plan_id: str
    owner_agent_id: str
    worker_id: str
    claimed_at: float
    lease_expires_at: float
    generation: int

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-safe claim record."""

        return {
            "plan_id": self.plan_id,
            "owner_agent_id": self.owner_agent_id,
            "worker_id": self.worker_id,
            "claimed_at": self.claimed_at,
            "lease_expires_at": self.lease_expires_at,
            "generation": self.generation,
        }


@dataclass(frozen=True, slots=True)
class PlanClaimResult:
    """Result from attempting to claim one planner plan."""

    status: PlanClaimStatus
    claim: PlanClaimRecord | None = None
    existing_claim: PlanClaimRecord | None = None

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-safe claim result."""

        return {
            "status": self.status,
            "claim": (
                self.claim.as_dict()
                if self.claim is not None
                else None
            ),
            "existing_claim": (
                self.existing_claim.as_dict()
                if self.existing_claim is not None
                else None
            ),
        }


@dataclass(frozen=True, slots=True)
class PlanClaimSweepSkip:
    """One expired claim that could not be released safely."""

    plan_id: str
    reason: PlanClaimSweepSkipReason
    detail: str = ""

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-safe stale-claim sweep skip."""

        return {
            "plan_id": self.plan_id,
            "reason": self.reason,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class PlanClaimSweepReport:
    """Report from one stale plan-claim sweep pass."""

    checked_claims: tuple[PlanClaimRecord, ...] = ()
    released_claims: tuple[PlanClaimRecord, ...] = ()
    skipped_claims: tuple[PlanClaimSweepSkip, ...] = ()
    dry_run: bool = False
    now: float = 0
    owner_agent_id: str | None = None

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-safe stale-claim sweep report."""

        return {
            "now": self.now,
            "owner_agent_id": self.owner_agent_id,
            "dry_run": self.dry_run,
            "checked_claims": [
                claim.as_dict()
                for claim in self.checked_claims
            ],
            "released_claims": [
                claim.as_dict()
                for claim in self.released_claims
            ],
            "skipped_claims": [
                skip.as_dict()
                for skip in self.skipped_claims
            ],
        }


@dataclass(frozen=True, slots=True)
class PlannerSchedulerDaemonError:
    """Failure recorded while ticking one planner plan."""

    plan_id: str
    error: str


@dataclass(frozen=True, slots=True)
class PlannerSchedulerDaemonState:
    """Snapshot of a planner scheduler daemon lifecycle."""

    status: PlannerSchedulerDaemonStatus
    plan_ids: tuple[str, ...]
    poll_interval_seconds: float
    default_template_id: str | None = None
    retry_limit: int | None = None
    dispatch_limit: int | None = None
    iterations: int = 0
    started_at: float | None = None
    stopped_at: float | None = None
    last_run_at: float | None = None
    last_reports: tuple[PlanSchedulerTickReport, ...] = ()
    errors: tuple[PlannerSchedulerDaemonError, ...] = ()


@dataclass(frozen=True, slots=True)
class PlannerClaimedSchedulerDaemonError:
    """Failure recorded while running a claimed scheduler tick."""

    error: str


@dataclass(frozen=True, slots=True)
class PlannerClaimedSchedulerDaemonState:
    """Snapshot of a claimed scheduler daemon lifecycle."""

    status: PlannerClaimedSchedulerDaemonStatus
    worker_id: str
    lease_seconds: float
    owner_agent_id: str | None = None
    statuses: tuple[PlanStatus, ...] = ("draft", "running")
    limit: int | None = None
    default_template_id: str | None = None
    retry_limit: int | None = None
    dispatch_limit: int | None = None
    release_after_tick: bool = False
    poll_interval_seconds: float = 1.0
    iterations: int = 0
    started_at: float | None = None
    stopped_at: float | None = None
    last_run_at: float | None = None
    last_report: PlanClaimedSchedulerTickReport | None = None
    errors: tuple[PlannerClaimedSchedulerDaemonError, ...] = ()


@dataclass(frozen=True, slots=True)
class PlanState:
    """planner state 的 truth projection。"""

    plan_id: str
    objective: str
    owner_agent_id: str
    status: PlanStatus = "draft"
    steps: tuple[PlanStep, ...] = ()
    evidence: tuple[EvidenceHandle, ...] = ()
    assignments: tuple[PlanAssignment, ...] = ()
    created_at: float = 0
    updated_at: float = 0
    workspace: WorkspaceHandle | None = None

    def with_status(self, status: PlanStatus, *, now: float) -> "PlanState":
        """返回更新状态后的 plan。"""

        return replace(self, status=status, updated_at=now)


class PlanStore(Protocol):
    """plan state 的 truth source。"""

    def create_plan(self, plan: PlanState) -> None:
        """创建 plan。"""

    def save_plan(self, plan: PlanState) -> None:
        """保存 plan。"""

    def get_plan(self, plan_id: str) -> PlanState | None:
        """返回 plan。"""

    def list_plans(self, owner_agent_id: str | None = None) -> list[PlanState]:
        """列出 plans。"""


@dataclass(frozen=True, slots=True)
class PlanStoreRecord:
    """Plan payload plus store-owned optimistic concurrency revision."""

    plan: PlanState
    revision: int


class CompareAndSavePlanStore(Protocol):
    """Optional PlanStore API for optimistic concurrency control."""

    def get_plan_record(self, plan_id: str) -> PlanStoreRecord | None:
        """Return a plan and the revision that must be used for CAS saves."""

    def save_plan_if_unchanged(
        self,
        plan: PlanState,
        *,
        expected_revision: int,
    ) -> bool:
        """Save only when the current revision matches expected_revision."""


class ClaimGuardedPlanStore(Protocol):
    """Optional plan store API for atomic claim-guarded mutation saves."""

    def save_plan_if_claimed(
        self,
        plan: PlanState,
        claim: "PlanClaimRecord",
        *,
        expected_revision: int,
        now: float,
    ) -> bool:
        """Save only when the exact claim and read revision are still current."""


class PlanClaimStore(Protocol):
    """Transient claim/lease boundary for planner scheduler workers."""

    def claim_plan(
        self,
        *,
        plan_id: str,
        owner_agent_id: str,
        worker_id: str,
        lease_seconds: float,
        now: float,
    ) -> PlanClaimResult:
        """Attempt to claim a plan lease for one worker."""

    def release_plan(self, *, plan_id: str, worker_id: str) -> bool:
        """Release a plan lease if owned by the worker."""

    def get_claim(self, plan_id: str) -> PlanClaimRecord | None:
        """Return the current claim record when present."""


class PlanClaimSweepStore(Protocol):
    """Optional boundary for listing and releasing expired plan claims."""

    def expired_claims(
        self,
        *,
        now: float,
        owner_agent_id: str | None = None,
        limit: int | None = None,
    ) -> tuple[PlanClaimRecord, ...]:
        """Return expired claims ordered deterministically."""

    def release_expired_claim(
        self,
        claim: PlanClaimRecord,
        *,
        now: float,
    ) -> bool:
        """Release the exact expired claim if it has not changed."""


class PlanCoordinator(Protocol):
    """PlannerRuntime 需要的 coordinator 子集。"""

    def spawn(self, **kwargs: object) -> TaskHandle:
        """创建 isolated subagent task。"""

    def dispatch(self, **kwargs: object) -> TaskHandle:
        """派发 persistent expert task。"""


class InMemoryPlanStore:
    """线程安全的本地 plan store。"""

    def __init__(self) -> None:
        self._plans: dict[str, PlanState] = {}
        self._revisions: dict[str, int] = {}
        self._claim_store: object | None = None
        self._lock = RLock()

    def create_plan(self, plan: PlanState) -> None:
        with self._lock:
            if plan.plan_id in self._plans:
                raise ValueError(f"plan already exists: {plan.plan_id}")
            self._plans[plan.plan_id] = plan
            self._revisions[plan.plan_id] = 0

    def save_plan(self, plan: PlanState) -> None:
        with self._lock:
            if plan.plan_id not in self._plans:
                raise PlanNotFoundError(plan.plan_id)
            self._plans[plan.plan_id] = plan
            self._revisions[plan.plan_id] += 1

    def get_plan_record(self, plan_id: str) -> PlanStoreRecord | None:
        with self._lock:
            plan = self._plans.get(plan_id)
            if plan is None:
                return None
            return PlanStoreRecord(
                plan=plan,
                revision=self._revisions[plan_id],
            )

    def save_plan_if_unchanged(
        self,
        plan: PlanState,
        *,
        expected_revision: int,
    ) -> bool:
        with self._lock:
            if plan.plan_id not in self._plans:
                raise PlanNotFoundError(plan.plan_id)
            if self._revisions[plan.plan_id] != expected_revision:
                return False
            self._plans[plan.plan_id] = plan
            self._revisions[plan.plan_id] += 1
            return True

    def save_plan_if_claimed(
        self,
        plan: PlanState,
        claim: PlanClaimRecord,
        *,
        expected_revision: int,
        now: float,
    ) -> bool:
        claim_store = self._claim_store
        if not isinstance(claim_store, InMemoryPlanClaimStore):
            return False
        with claim_store._lock:
            with self._lock:
                if plan.plan_id not in self._plans:
                    raise PlanNotFoundError(plan.plan_id)
                current_claim = claim_store._claims.get(plan.plan_id)
                if current_claim != claim or claim.lease_expires_at <= float(now):
                    return False
                if self._revisions[plan.plan_id] != expected_revision:
                    return False
                self._plans[plan.plan_id] = plan
                self._revisions[plan.plan_id] += 1
                return True

    def bind_claim_store(self, claim_store: object) -> None:
        """Bind the local claim store used for in-memory atomic save checks."""

        self._claim_store = claim_store

    def get_plan(self, plan_id: str) -> PlanState | None:
        with self._lock:
            return self._plans.get(plan_id)

    def list_plans(self, owner_agent_id: str | None = None) -> list[PlanState]:
        with self._lock:
            plans = list(self._plans.values())
        if owner_agent_id is None:
            return plans
        return [plan for plan in plans if plan.owner_agent_id == owner_agent_id]


class InMemoryPlanClaimStore:
    """Thread-safe local plan claim/lease store."""

    def __init__(self) -> None:
        self._claims: dict[str, PlanClaimRecord] = {}
        self._lock = RLock()

    def claim_plan(
        self,
        *,
        plan_id: str,
        owner_agent_id: str,
        worker_id: str,
        lease_seconds: float,
        now: float,
    ) -> PlanClaimResult:
        """Claim or renew a plan lease when it is free or expired."""

        self._validate_non_empty(plan_id, field_name="plan_id")
        self._validate_non_empty(owner_agent_id, field_name="owner_agent_id")
        self._validate_non_empty(worker_id, field_name="worker_id")
        lease_seconds = float(lease_seconds)
        now = float(now)
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be > 0")

        with self._lock:
            existing = self._claims.get(plan_id)
            if (
                existing is not None
                and existing.lease_expires_at > now
                and existing.worker_id != worker_id
            ):
                return PlanClaimResult(
                    status="busy",
                    existing_claim=existing,
                )
            generation = 1 if existing is None else existing.generation + 1
            claim = PlanClaimRecord(
                plan_id=plan_id,
                owner_agent_id=owner_agent_id,
                worker_id=worker_id,
                claimed_at=now,
                lease_expires_at=now + lease_seconds,
                generation=generation,
            )
            self._claims[plan_id] = claim
            return PlanClaimResult(status="claimed", claim=claim)

    def release_plan(self, *, plan_id: str, worker_id: str) -> bool:
        """Release the claim for this worker only."""

        self._validate_non_empty(plan_id, field_name="plan_id")
        self._validate_non_empty(worker_id, field_name="worker_id")
        with self._lock:
            existing = self._claims.get(plan_id)
            if existing is None or existing.worker_id != worker_id:
                return False
            del self._claims[plan_id]
            return True

    def get_claim(self, plan_id: str) -> PlanClaimRecord | None:
        """Return a claim without evaluating lease expiry."""

        self._validate_non_empty(plan_id, field_name="plan_id")
        with self._lock:
            return self._claims.get(plan_id)

    def expired_claims(
        self,
        *,
        now: float,
        owner_agent_id: str | None = None,
        limit: int | None = None,
    ) -> tuple[PlanClaimRecord, ...]:
        """Return expired claims without mutating the store."""

        now = float(now)
        if owner_agent_id is not None:
            self._validate_non_empty(
                owner_agent_id,
                field_name="owner_agent_id",
            )
        if limit is not None and limit < 1:
            raise ValueError("limit must be >= 1")
        with self._lock:
            claims = [
                claim
                for claim in self._claims.values()
                if claim.lease_expires_at <= now
                and (
                    owner_agent_id is None
                    or claim.owner_agent_id == owner_agent_id
                )
            ]
        claims.sort(key=lambda claim: (claim.lease_expires_at, claim.plan_id))
        if limit is not None:
            claims = claims[:limit]
        return tuple(claims)

    def release_expired_claim(
        self,
        claim: PlanClaimRecord,
        *,
        now: float,
    ) -> bool:
        """Release the exact expired claim only when it has not changed."""

        now = float(now)
        with self._lock:
            existing = self._claims.get(claim.plan_id)
            if existing != claim or existing.lease_expires_at > now:
                return False
            del self._claims[claim.plan_id]
            return True

    def _validate_non_empty(self, value: str, *, field_name: str) -> None:
        if not value.strip():
            raise ValueError(f"{field_name} must not be empty")


class PlannerRuntime:
    """plan state runtime，不直接执行 QueryLoop。"""

    def __init__(
        self,
        *,
        store: PlanStore,
        templates: tuple[SubAgentTemplate, ...] = (),
        coordinator: PlanCoordinator | None = None,
        retry_policy: PlanRetryPolicy | None = None,
        claim_store: PlanClaimStore | None = None,
        clock: object | None = None,
        id_factory: object | None = None,
    ) -> None:
        self.store = store
        self.templates = {template.template_id: template for template in templates}
        self.coordinator = coordinator
        self.retry_policy = retry_policy or PlanRetryPolicy(max_attempts=1)
        self.claim_store = claim_store
        self._clock = clock if callable(clock) else time.time
        self._id_factory = id_factory if callable(id_factory) else self._default_id
        self._active_plan_claims: dict[str, PlanClaimRecord] = {}
        bind_claim_store = getattr(self.store, "bind_claim_store", None)
        if claim_store is not None and callable(bind_claim_store):
            bind_claim_store(claim_store)

    def create_plan(
        self,
        *,
        objective: str,
        owner_agent_id: str,
        plan_id: str | None = None,
        workspace: WorkspaceHandle | None = None,
    ) -> PlanState:
        """创建 draft plan。"""

        now = float(self._clock())
        plan = PlanState(
            plan_id=plan_id or str(self._id_factory("plan")),
            objective=objective,
            owner_agent_id=owner_agent_id,
            created_at=now,
            updated_at=now,
            workspace=workspace,
        )
        self.store.create_plan(plan)
        return plan

    def add_step(
        self,
        plan_id: str,
        *,
        instruction: str,
        required_capabilities: tuple[str, ...] = (),
        template_id: str | None = None,
    ) -> PlanState:
        """向 plan 追加 pending step。"""

        if template_id is not None:
            self._require_template(template_id)
        record = self._require_plan_record(plan_id)
        plan = record.plan
        step = PlanStep(
            step_id=str(self._id_factory("step")),
            instruction=instruction,
            required_capabilities=tuple(required_capabilities),
            template_id=template_id,
        )
        updated = replace(
            plan,
            steps=plan.steps + (step,),
            updated_at=float(self._clock()),
        )
        self._save_plan(updated, expected_revision=record.revision)
        return updated

    def create_plan_from_decomposition(
        self,
        decomposition: PlanDecomposition,
        *,
        owner_agent_id: str,
        plan_id: str | None = None,
        workspace: WorkspaceHandle | None = None,
    ) -> PlanState:
        """Create a draft plan from a structured decomposition proposal."""

        objective = decomposition.objective.strip()
        if not objective:
            raise ValueError("decomposition objective is required")
        if not decomposition.steps:
            raise ValueError("decomposition must include at least one step")
        steps = tuple(
            self._step_from_spec(spec)
            for spec in decomposition.steps
        )
        self._validate_step_dependencies(steps)
        now = float(self._clock())
        plan = PlanState(
            plan_id=plan_id or str(self._id_factory("plan")),
            objective=objective,
            owner_agent_id=owner_agent_id,
            steps=steps,
            created_at=now,
            updated_at=now,
            workspace=workspace,
        )
        self.store.create_plan(plan)
        return plan

    def validate_decomposition(
        self,
        decomposition: PlanDecomposition,
    ) -> PlanDecompositionValidationReport:
        """Validate a structured decomposition without creating a plan."""

        errors: list[str] = []
        objective = decomposition.objective.strip()
        if not objective:
            errors.append("decomposition objective is required")
        if not decomposition.steps:
            errors.append("decomposition must include at least one step")

        required_templates = tuple(
            dict.fromkeys(
                spec.template_id
                for spec in decomposition.steps
                if spec.template_id is not None
            ),
        )
        unknown_templates = tuple(
            template_id
            for template_id in required_templates
            if template_id not in self.templates
        )
        for template_id in unknown_templates:
            errors.append(f"unknown template: {template_id}")

        steps: list[PlanStep] = []
        for index, spec in enumerate(decomposition.steps, start=1):
            instruction = spec.instruction.strip()
            if not instruction:
                errors.append("decomposition step instruction is required")
            if spec.template_id is not None and spec.template_id not in self.templates:
                if spec.template_id not in unknown_templates:
                    errors.append(f"unknown template: {spec.template_id}")
            steps.append(
                PlanStep(
                    step_id=spec.step_id or f"__generated_step_{index}",
                    instruction=instruction,
                    required_capabilities=tuple(spec.required_capabilities),
                    template_id=spec.template_id,
                    depends_on=tuple(spec.depends_on),
                ),
            )

        try:
            self._validate_step_dependencies(tuple(steps))
        except ValueError as error:
            errors.append(str(error))

        deduped_errors = tuple(dict.fromkeys(errors))
        return PlanDecompositionValidationReport(
            ok=not deduped_errors,
            errors=deduped_errors,
            step_count=len(decomposition.steps),
            required_templates=required_templates,
            unknown_templates=unknown_templates,
        )

    def gate_decomposition_proposal(
        self,
        proposal: Mapping[str, object],
        *,
        policy: PlanDecompositionGatePolicy | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> PlanDecompositionGateReport:
        """Parse and validate a raw LLM decomposition proposal.

        The gate never persists a plan. Deployments own the LLM prompt, approval
        workflow, and follow-up call to create_plan_from_decomposition().
        """

        gate_policy = policy or PlanDecompositionGatePolicy()
        errors: list[str] = []
        try:
            decomposition = self._decomposition_from_proposal(proposal)
        except ValueError as error:
            return PlanDecompositionGateReport(
                accepted=False,
                errors=(str(error),),
                metadata={} if metadata is None else dict(metadata),
            )

        validation = self.validate_decomposition(decomposition)
        errors.extend(validation.errors)
        step_count = len(decomposition.steps)
        missing_templates = self._proposal_steps_missing_templates(
            decomposition,
            require_template=gate_policy.require_template,
        )
        disallowed_templates = self._proposal_disallowed_templates(
            validation.required_templates,
            allowed_template_ids=gate_policy.allowed_template_ids,
        )

        if gate_policy.max_steps is not None and step_count > gate_policy.max_steps:
            errors.append(
                f"decomposition exceeds max_steps: "
                f"{step_count} > {gate_policy.max_steps}",
            )
        for step_id in missing_templates:
            errors.append(f"step {step_id} requires a template_id")
        for template_id in disallowed_templates:
            errors.append(f"template not allowed: {template_id}")

        requires_approval = (
            gate_policy.require_approval
            and not gate_policy.approved
        )
        if requires_approval:
            errors.append("decomposition approval is required")

        deduped_errors = tuple(dict.fromkeys(errors))
        accepted = not deduped_errors and validation.ok
        return PlanDecompositionGateReport(
            accepted=accepted,
            requires_approval=requires_approval,
            errors=deduped_errors,
            validation=validation,
            step_count=step_count,
            required_templates=validation.required_templates,
            unknown_templates=validation.unknown_templates,
            missing_templates=missing_templates,
            disallowed_templates=disallowed_templates,
            normalized_decomposition=(
                decomposition
                if accepted
                else None
            ),
            metadata={} if metadata is None else dict(metadata),
        )

    def gate_llm_governance_evidence(
        self,
        record: PlannerLlmGovernanceEvidenceRecord,
        *,
        required_evidence: tuple[str, ...] = (
            PLANNER_LLM_GOVERNANCE_EXECUTION_REQUIRED_EVIDENCE
        ),
        metadata: Mapping[str, object] | None = None,
    ) -> PlannerLlmGovernanceEvidenceGateReport:
        """Gate per-proposal LLM governance evidence before plan creation."""

        self._validate_llm_governance_required_evidence(required_evidence)
        gate_metadata = {} if metadata is None else dict(metadata)
        _validate_planner_llm_governance_metadata(
            gate_metadata,
            field_name="metadata",
        )

        missing: list[str] = []
        failed: list[str] = []
        errors: list[str] = []
        for evidence_name in required_evidence:
            ref_present, status_present, status_passed = (
                self._llm_governance_evidence_state(record, evidence_name)
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
        missing_evidence = tuple(dict.fromkeys(missing))
        failed_evidence = tuple(dict.fromkeys(failed))
        accepted = not deduped_errors
        return PlannerLlmGovernanceEvidenceGateReport(
            accepted=accepted,
            block_plan_creation=not accepted,
            errors=deduped_errors,
            missing_evidence=missing_evidence,
            failed_evidence=failed_evidence,
            record=record,
            metadata=gate_metadata,
        )

    def ready_steps(self, plan_id: str) -> tuple[PlanStep, ...]:
        """Return pending steps whose dependencies are already completed."""

        plan = self._require_plan(plan_id)
        completed = {
            step.step_id
            for step in plan.steps
            if step.status == "completed"
        }
        return tuple(
            step
            for step in plan.steps
            if step.status == "pending"
            and set(step.depends_on).issubset(completed)
        )

    def retryable_steps(self, plan_id: str) -> tuple[PlanStep, ...]:
        """Return failed steps whose retry delay has elapsed."""

        plan = self._require_plan(plan_id)
        completed = {
            step.step_id
            for step in plan.steps
            if step.status == "completed"
        }
        now = float(self._clock())
        return tuple(
            step
            for step in plan.steps
            if step.status == "failed"
            and step.retry_status == "scheduled"
            and step.next_retry_at is not None
            and step.next_retry_at <= now
            and step.attempts < self.retry_policy.max_attempts
            and set(step.depends_on).issubset(completed)
        )

    def _pending_dispatch_step_ids(self, plan: PlanState) -> tuple[str, ...]:
        step_by_id = {step.step_id: step for step in plan.steps}
        pending_step_ids: list[str] = []
        for assignment in plan.assignments:
            if assignment.dispatch_status != "pending":
                continue
            step = step_by_id.get(assignment.step_id)
            if step is None:
                continue
            if (
                step.status != "assigned"
                or step.task_id != assignment.task_id
                or step.assigned_agent_id != assignment.target_agent_id
            ):
                continue
            pending_step_ids.append(assignment.step_id)
        return tuple(dict.fromkeys(pending_step_ids))

    def get_plan(
        self,
        plan_id: str,
        *,
        owner_agent_id: str | None = None,
    ) -> PlanState:
        """Return one plan or raise when it does not exist."""

        plan = self._require_plan(plan_id)
        if owner_agent_id is not None and plan.owner_agent_id != owner_agent_id:
            raise PlanNotFoundError(plan_id)
        return plan

    def list_plans(self, owner_agent_id: str | None = None) -> list[PlanState]:
        """Return plans scoped by owner when provided."""

        return self.store.list_plans(owner_agent_id)

    def schedulable_plans(
        self,
        *,
        owner_agent_id: str | None = None,
        statuses: tuple[PlanStatus, ...] = ("draft", "running"),
        limit: int | None = None,
    ) -> tuple[PlannerSchedulablePlan, ...]:
        """Return plans with dependency-ready or due-retry work.

        This is a selection helper only. Callers still own tenant policy,
        distributed locks, claims, fairness, and process supervision.
        """

        if limit is not None and limit < 1:
            raise ValueError("limit must be >= 1")
        allowed_statuses = self._validate_plan_statuses(statuses)
        summaries: list[PlannerSchedulablePlan] = []
        for plan in self.store.list_plans(owner_agent_id):
            if plan.status not in allowed_statuses:
                continue
            ready_step_ids = tuple(
                step.step_id for step in self.ready_steps(plan.plan_id)
            )
            retryable_step_ids = tuple(
                step.step_id for step in self.retryable_steps(plan.plan_id)
            )
            pending_dispatch_step_ids = self._pending_dispatch_step_ids(plan)
            reasons: list[PlannerSchedulablePlanReason] = []
            if ready_step_ids:
                reasons.append("ready-steps")
            if retryable_step_ids:
                reasons.append("due-retries")
            if pending_dispatch_step_ids:
                reasons.append("pending-dispatch")
            if not reasons:
                continue
            summaries.append(
                PlannerSchedulablePlan(
                    plan_id=plan.plan_id,
                    owner_agent_id=plan.owner_agent_id,
                    status=plan.status,
                    ready_step_ids=ready_step_ids,
                    retryable_step_ids=retryable_step_ids,
                    reasons=tuple(reasons),
                    updated_at=plan.updated_at,
                ),
            )
            if limit is not None and len(summaries) >= limit:
                break
        return tuple(summaries)

    def claim_schedulable_plans(
        self,
        *,
        worker_id: str,
        lease_seconds: float,
        owner_agent_id: str | None = None,
        statuses: tuple[PlanStatus, ...] = ("draft", "running"),
        limit: int | None = None,
    ) -> tuple[PlanClaimResult, ...]:
        """Claim schedulable plans through the injected claim store.

        This helper coordinates local SDK primitives only. Production
        deployments still own distributed lock semantics, tenant policy, and
        worker supervision.
        """

        if self.claim_store is None:
            raise RuntimeError("claim_store is required to claim schedulable plans")
        if not worker_id.strip():
            raise ValueError("worker_id must not be empty")
        lease_seconds = float(lease_seconds)
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be > 0")
        now = float(self._clock())
        return tuple(
            self.claim_store.claim_plan(
                plan_id=summary.plan_id,
                owner_agent_id=summary.owner_agent_id,
                worker_id=worker_id,
                lease_seconds=lease_seconds,
                now=now,
            )
            for summary in self.schedulable_plans(
                owner_agent_id=owner_agent_id,
                statuses=statuses,
                limit=limit,
            )
        )

    def sweep_expired_claims(
        self,
        *,
        owner_agent_id: str | None = None,
        now: float | None = None,
        limit: int | None = None,
        dry_run: bool = False,
    ) -> PlanClaimSweepReport:
        """Report and optionally release expired planner claim leases.

        This composes SDK claim-store primitives only. Deployment code still
        owns cron scheduling, distributed locks, leader election, fairness,
        alerting, and compensation policy.
        """

        if self.claim_store is None:
            raise RuntimeError("claim_store is required to sweep expired claims")
        if not hasattr(self.claim_store, "expired_claims") or not hasattr(
            self.claim_store,
            "release_expired_claim",
        ):
            raise RuntimeError(
                "claim_store must implement PlanClaimSweepStore",
            )
        if owner_agent_id is not None and not owner_agent_id.strip():
            raise ValueError("owner_agent_id must not be empty")
        if limit is not None and limit < 1:
            raise ValueError("limit must be >= 1")
        now_value = float(self._clock() if now is None else now)
        sweep_store = cast(PlanClaimSweepStore, self.claim_store)
        checked = sweep_store.expired_claims(
            now=now_value,
            owner_agent_id=owner_agent_id,
            limit=limit,
        )
        released: list[PlanClaimRecord] = []
        skipped: list[PlanClaimSweepSkip] = []
        if not dry_run:
            for claim in checked:
                if sweep_store.release_expired_claim(claim, now=now_value):
                    released.append(claim)
                    continue
                skipped.append(
                    PlanClaimSweepSkip(
                        plan_id=claim.plan_id,
                        reason="release-race",
                        detail="claim changed before stale release",
                    ),
                )
        return PlanClaimSweepReport(
            checked_claims=checked,
            released_claims=tuple(released),
            skipped_claims=tuple(skipped),
            dry_run=dry_run,
            now=now_value,
            owner_agent_id=owner_agent_id,
        )

    def assign_step(
        self,
        plan_id: str,
        step_id: str,
        *,
        template_id: str,
    ) -> PlanState:
        """通过 coordinator 把 step 分配给 spawn 或 dispatch task。"""

        if self.coordinator is None:
            raise RuntimeError("coordinator is required to assign plan steps")
        record = self._require_plan_record(plan_id)
        plan = record.plan
        template = self._require_template(template_id)
        step = self._require_step(plan, step_id)
        task_id = str(self._id_factory("task"))
        target_agent_id = (
            str(self._id_factory("subagent"))
            if template.target_agent_id is None
            else template.target_agent_id
        )
        assignment = PlanAssignment(
            plan_id=plan_id,
            step_id=step_id,
            template_id=template.template_id,
            task_id=task_id,
            target_agent_id=target_agent_id,
            created_at=float(self._clock()),
        )
        updated_step = replace(
            step,
            status="assigned",
            template_id=template.template_id,
            task_id=task_id,
            assigned_agent_id=target_agent_id,
        )
        updated = self._replace_step(
            plan,
            updated_step,
            assignments=plan.assignments + (assignment,),
        )
        self._save_plan(updated, expected_revision=record.revision)
        try:
            self._submit_assignment_to_coordinator(
                plan=updated,
                step=updated_step,
                assignment=assignment,
                template=template,
            )
        except PlanDispatchAlreadySubmittedError as error:
            self._mark_assignment_dispatch_failed(
                updated,
                updated_step,
                assignment,
                error=str(error) or error.__class__.__name__,
            )
            raise
        except Exception as error:
            self._mark_assignment_dispatch_failed(
                updated,
                updated_step,
                assignment,
                error=str(error) or error.__class__.__name__,
            )
            raise
        return self._mark_assignment_dispatch_submitted(
            plan_id,
            assignment,
        )

    def dispatch_ready_steps(
        self,
        plan_id: str,
        *,
        default_template_id: str | None = None,
        limit: int | None = None,
    ) -> PlanDispatchReport:
        """Submit dependency-ready pending steps through the coordinator boundary."""

        if limit is not None and limit < 1:
            raise ValueError("limit must be >= 1")
        assigned: list[PlanAssignment] = []
        skipped: list[PlanDispatchSkip] = []
        recovered = self.recover_pending_dispatches(plan_id, limit=limit)
        assigned.extend(recovered.assigned)
        skipped.extend(recovered.skipped)
        for step in self.ready_steps(plan_id):
            if limit is not None and len(assigned) >= limit:
                break
            template_id = step.template_id or default_template_id
            if template_id is None:
                skipped.append(
                    PlanDispatchSkip(
                        plan_id=plan_id,
                        step_id=step.step_id,
                        reason="missing-template",
                        detail="step has no template_id and no default_template_id was provided",
                    ),
                )
                continue
            if template_id not in self.templates:
                skipped.append(
                    PlanDispatchSkip(
                        plan_id=plan_id,
                        step_id=step.step_id,
                        reason="unknown-template",
                        detail=template_id,
                    ),
                )
                continue
            try:
                updated = self.assign_step(
                    plan_id,
                    step.step_id,
                    template_id=template_id,
                )
            except PlanClaimLostError:
                raise
            except Exception as error:
                skipped.append(
                    PlanDispatchSkip(
                        plan_id=plan_id,
                        step_id=step.step_id,
                        reason="dispatch-failed",
                        detail=str(error) or error.__class__.__name__,
                    ),
                )
                continue
            assigned.append(self._latest_assignment(updated, step.step_id))
        return PlanDispatchReport(
            plan_id=plan_id,
            assigned=tuple(assigned),
            skipped=tuple(skipped),
        )

    def recover_pending_dispatches(
        self,
        plan_id: str,
        *,
        limit: int | None = None,
    ) -> PlanDispatchReport:
        """Replay saved assignments that were not yet submitted to a coordinator."""

        if limit is not None and limit < 1:
            raise ValueError("limit must be >= 1")
        plan = self._require_plan(plan_id)
        assigned: list[PlanAssignment] = []
        skipped: list[PlanDispatchSkip] = []
        for assignment in plan.assignments:
            if limit is not None and len(assigned) >= limit:
                break
            if assignment.dispatch_status != "pending":
                continue
            if self.coordinator is None:
                raise RuntimeError("coordinator is required to recover plan dispatches")
            try:
                template = self._require_template(assignment.template_id)
            except KeyError:
                skipped.append(
                    PlanDispatchSkip(
                        plan_id=plan_id,
                        step_id=assignment.step_id,
                        reason="unknown-template",
                        detail=assignment.template_id,
                    ),
                )
                continue
            current_plan = self._require_plan(plan_id)
            step = self._require_step(current_plan, assignment.step_id)
            if (
                step.status != "assigned"
                or step.task_id != assignment.task_id
                or step.assigned_agent_id != assignment.target_agent_id
            ):
                skipped.append(
                    PlanDispatchSkip(
                        plan_id=plan_id,
                        step_id=assignment.step_id,
                        reason="dispatch-failed",
                        detail="assignment no longer matches assigned step",
                    ),
                )
                continue
            try:
                self._submit_assignment_to_coordinator(
                    plan=current_plan,
                    step=step,
                    assignment=assignment,
                    template=template,
                )
            except PlanDispatchAlreadySubmittedError:
                pass
            except Exception as error:
                self._mark_assignment_dispatch_failed(
                    current_plan,
                    step,
                    assignment,
                    error=str(error) or error.__class__.__name__,
                )
                skipped.append(
                    PlanDispatchSkip(
                        plan_id=plan_id,
                        step_id=assignment.step_id,
                        reason="dispatch-failed",
                        detail=str(error) or error.__class__.__name__,
                    ),
                )
                continue
            updated = self._mark_assignment_dispatch_submitted(
                plan_id,
                assignment,
            )
            assigned.append(self._latest_assignment(updated, assignment.step_id))
        return PlanDispatchReport(
            plan_id=plan_id,
            assigned=tuple(assigned),
            skipped=tuple(skipped),
        )

    def scheduler_tick(
        self,
        plan_id: str,
        *,
        default_template_id: str | None = None,
        retry_limit: int | None = None,
        dispatch_limit: int | None = None,
    ) -> PlanSchedulerTickReport:
        """Run one bounded planner scheduling pass.

        This resets due retryable steps before dispatching dependency-ready
        work. Callers own polling, locking, worker process lifecycle, and
        deployment supervision.
        """

        if retry_limit is not None and retry_limit < 1:
            raise ValueError("retry_limit must be >= 1")
        if dispatch_limit is not None and dispatch_limit < 1:
            raise ValueError("dispatch_limit must be >= 1")
        retry_resets: list[PlanSchedulerRetryReset] = []
        for step in self.retryable_steps(plan_id):
            if retry_limit is not None and len(retry_resets) >= retry_limit:
                break
            self.retry_step(plan_id, step.step_id)
            retry_resets.append(
                PlanSchedulerRetryReset(
                    plan_id=plan_id,
                    step_id=step.step_id,
                    attempts=step.attempts,
                ),
            )
        dispatch = self.dispatch_ready_steps(
            plan_id,
            default_template_id=default_template_id,
            limit=dispatch_limit,
        )
        return PlanSchedulerTickReport(
            plan_id=plan_id,
            retry_resets=tuple(retry_resets),
            dispatch=dispatch,
        )

    def claimed_scheduler_tick(
        self,
        *,
        worker_id: str,
        lease_seconds: float,
        owner_agent_id: str | None = None,
        statuses: tuple[PlanStatus, ...] = ("draft", "running"),
        limit: int | None = None,
        default_template_id: str | None = None,
        retry_limit: int | None = None,
        dispatch_limit: int | None = None,
        release_after_tick: bool = False,
    ) -> PlanClaimedSchedulerTickReport:
        """Claim schedulable plans before running bounded scheduler ticks.

        This composes existing SDK primitives for multi-node scheduler workers.
        Deployment code still owns plan discovery, distributed lock policy,
        process supervision, stale-lease recovery, and fairness.
        """

        if self.claim_store is None:
            raise RuntimeError("claim_store is required to claim schedulable plans")
        if not worker_id.strip():
            raise ValueError("worker_id must not be empty")
        lease_seconds = float(lease_seconds)
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be > 0")
        if limit is not None and limit < 1:
            raise ValueError("limit must be >= 1")
        if retry_limit is not None and retry_limit < 1:
            raise ValueError("retry_limit must be >= 1")
        if dispatch_limit is not None and dispatch_limit < 1:
            raise ValueError("dispatch_limit must be >= 1")
        statuses = self._validate_plan_statuses(statuses)

        claims: list[PlanClaimResult] = []
        tick_reports: list[PlanSchedulerTickReport] = []
        skipped: list[PlanClaimedSchedulerTickSkip] = []
        released_plan_ids: list[str] = []
        now = float(self._clock())

        for summary in self.schedulable_plans(
            owner_agent_id=owner_agent_id,
            statuses=statuses,
            limit=limit,
        ):
            claim_result = self.claim_store.claim_plan(
                plan_id=summary.plan_id,
                owner_agent_id=summary.owner_agent_id,
                worker_id=worker_id,
                lease_seconds=lease_seconds,
                now=now,
            )
            claims.append(claim_result)
            if claim_result.status != "claimed":
                skipped.append(
                    PlanClaimedSchedulerTickSkip(
                        plan_id=summary.plan_id,
                        reason="busy",
                        detail="plan is leased by another scheduler worker",
                        claim_result=claim_result,
                    ),
                )
                continue

            try:
                tick_reports.append(
                    self._run_scheduler_tick_with_claim(
                        claim_result.claim,
                        default_template_id=default_template_id,
                        retry_limit=retry_limit,
                        dispatch_limit=dispatch_limit,
                    ),
                )
            except PlanClaimLostError as error:
                skipped.append(
                    PlanClaimedSchedulerTickSkip(
                        plan_id=summary.plan_id,
                        reason="claim-lost",
                        detail=str(error) or "claim changed before scheduler save",
                        claim_result=claim_result,
                    ),
                )
            except Exception as error:
                skipped.append(
                    PlanClaimedSchedulerTickSkip(
                        plan_id=summary.plan_id,
                        reason="tick-failed",
                        detail=str(error) or error.__class__.__name__,
                        claim_result=claim_result,
                    ),
                )
            finally:
                if release_after_tick and self.claim_store.release_plan(
                    plan_id=summary.plan_id,
                    worker_id=worker_id,
                ):
                    released_plan_ids.append(summary.plan_id)

        return PlanClaimedSchedulerTickReport(
            worker_id=worker_id,
            claims=tuple(claims),
            tick_reports=tuple(tick_reports),
            skipped=tuple(skipped),
            released_plan_ids=tuple(released_plan_ids),
        )

    def record_evidence(
        self,
        plan_id: str,
        *,
        step_ids: tuple[str, ...] = (),
        kind: EvidenceKind,
        summary: str,
        uri: str | None = None,
        producer_agent_id: str | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> EvidenceHandle:
        """记录 evidence handle，并可挂载到指定 steps。"""

        record = self._require_plan_record(plan_id)
        plan = record.plan
        kind = self._require_evidence_kind(kind)
        evidence = EvidenceHandle(
            evidence_id=str(self._id_factory("evidence")),
            kind=kind,
            summary=summary,
            uri=uri,
            producer_agent_id=producer_agent_id,
            metadata=dict(metadata or {}),
        )
        steps = plan.steps
        for step_id in step_ids:
            step = self._require_step(replace(plan, steps=steps), step_id)
            updated_step = replace(
                step,
                evidence_ids=step.evidence_ids + (evidence.evidence_id,),
            )
            steps = tuple(
                updated_step if current.step_id == step_id else current
                for current in steps
            )
        updated = replace(
            plan,
            steps=steps,
            evidence=plan.evidence + (evidence,),
            updated_at=float(self._clock()),
        )
        self._save_plan(updated, expected_revision=record.revision)
        return evidence

    def complete_step(
        self,
        plan_id: str,
        step_id: str,
        *,
        evidence_ids: tuple[str, ...] = (),
    ) -> PlanState:
        """完成一个 step，并在所有 step 完成时完成 plan。"""

        record = self._require_plan_record(plan_id)
        plan = record.plan
        step = self._require_step(plan, step_id)
        merged_evidence_ids = step.evidence_ids + tuple(
            evidence_id
            for evidence_id in evidence_ids
            if evidence_id not in step.evidence_ids
        )
        updated_step = replace(
            step,
            status="completed",
            evidence_ids=merged_evidence_ids,
            error=None,
            next_retry_at=None,
            retry_status=None,
            retry_exhausted_at=None,
        )
        updated = self._replace_step(plan, updated_step)
        if all(step.status == "completed" for step in updated.steps):
            updated = replace(
                updated,
                status="completed",
                updated_at=float(self._clock()),
            )
        self._save_plan(updated, expected_revision=record.revision)
        return updated

    def fail_step(
        self,
        plan_id: str,
        step_id: str,
        *,
        error: str,
    ) -> PlanState:
        """Record a step failure and schedule or exhaust its retry state."""

        record = self._require_plan_record(plan_id)
        plan = record.plan
        step = self._require_step(plan, step_id)
        if step.status == "completed":
            raise ValueError(f"completed step cannot be failed: {step_id}")
        now = float(self._clock())
        attempts = step.attempts + 1
        retry_status: PlanStepRetryStatus = (
            "exhausted"
            if attempts >= self.retry_policy.max_attempts
            else "scheduled"
        )
        next_retry_at = (
            None
            if retry_status == "exhausted"
            else now + self.retry_policy.delay_for_attempt(attempts)
        )
        updated_step = replace(
            step,
            status="failed",
            error=error,
            attempts=attempts,
            last_failed_at=now,
            next_retry_at=next_retry_at,
            retry_status=retry_status,
            retry_exhausted_at=(
                now if retry_status == "exhausted" else None
            ),
        )
        updated = self._replace_step(plan, updated_step)
        if retry_status == "exhausted":
            updated = replace(updated, status="failed", updated_at=now)
        self._save_plan(updated, expected_revision=record.revision)
        return updated

    def retry_step(self, plan_id: str, step_id: str) -> PlanState:
        """Move a due failed step back to pending while keeping attempt audit."""

        record = self._require_plan_record(plan_id)
        plan = record.plan
        step = self._require_step(plan, step_id)
        if step not in self.retryable_steps(plan_id):
            raise ValueError(f"step is not retryable: {step_id}")
        updated_step = replace(
            step,
            status="pending",
            assigned_agent_id=None,
            task_id=None,
            error=None,
            next_retry_at=None,
            retry_status=None,
            retry_exhausted_at=None,
        )
        updated = self._replace_step(plan, updated_step)
        self._save_plan(updated, expected_revision=record.revision)
        return updated

    def _run_scheduler_tick_with_claim(
        self,
        claim: PlanClaimRecord | None,
        *,
        default_template_id: str | None,
        retry_limit: int | None,
        dispatch_limit: int | None,
    ) -> PlanSchedulerTickReport:
        if claim is None:
            raise PlanClaimLostError("missing scheduler claim record")
        existing = self._active_plan_claims.get(claim.plan_id)
        self._active_plan_claims[claim.plan_id] = claim
        try:
            return self.scheduler_tick(
                claim.plan_id,
                default_template_id=default_template_id,
                retry_limit=retry_limit,
                dispatch_limit=dispatch_limit,
            )
        finally:
            if existing is None:
                self._active_plan_claims.pop(claim.plan_id, None)
            else:
                self._active_plan_claims[claim.plan_id] = existing

    def _save_plan(
        self,
        plan: PlanState,
        *,
        expected_revision: int | None = None,
    ) -> None:
        claim = self._active_plan_claims.get(plan.plan_id)
        if claim is None:
            if expected_revision is not None:
                compare_save = getattr(self.store, "save_plan_if_unchanged", None)
                if callable(compare_save):
                    if compare_save(plan, expected_revision=expected_revision):
                        return
                    raise PlanConflictError(
                        f"plan changed before saving: {plan.plan_id}",
                    )
                raise PlanConflictError(
                    "PlanStore must implement save_plan_if_unchanged for "
                    f"mutation safety: {plan.plan_id}",
                )
            self.store.save_plan(plan)
            return

        now = float(self._clock())
        guarded_save = getattr(self.store, "save_plan_if_claimed", None)
        if callable(guarded_save):
            if expected_revision is None:
                raise PlanClaimLostError(
                    "expected_revision is required for claim-guarded save: "
                    f"{plan.plan_id}",
                )
            if guarded_save(
                plan,
                claim,
                expected_revision=expected_revision,
                now=now,
            ):
                return
            raise PlanClaimLostError(
                f"claim changed before saving plan: {plan.plan_id}",
            )

        compare_save = getattr(self.store, "save_plan_if_unchanged", None)
        if expected_revision is None or not callable(compare_save):
            raise PlanClaimLostError(
                "PlanStore must implement save_plan_if_claimed or "
                f"save_plan_if_unchanged for claim-guarded save: {plan.plan_id}",
            )
        if self.claim_store is None:
            raise PlanClaimLostError(
                f"claim_store is required to guard plan save: {plan.plan_id}",
            )
        current_claim = self.claim_store.get_claim(plan.plan_id)
        if current_claim != claim or claim.lease_expires_at <= now:
            raise PlanClaimLostError(
                f"claim changed before saving plan: {plan.plan_id}",
            )
        if compare_save(plan, expected_revision=expected_revision):
            return
        raise PlanClaimLostError(
            f"plan changed before saving plan: {plan.plan_id}",
        )

    def _require_plan(self, plan_id: str) -> PlanState:
        plan = self.store.get_plan(plan_id)
        if plan is None:
            raise PlanNotFoundError(plan_id)
        return plan

    def _require_plan_record(self, plan_id: str) -> PlanStoreRecord:
        get_record = getattr(self.store, "get_plan_record", None)
        if callable(get_record):
            record = get_record(plan_id)
            if record is None:
                raise PlanNotFoundError(plan_id)
            return record
        return PlanStoreRecord(plan=self._require_plan(plan_id), revision=0)

    def _require_template(self, template_id: str) -> SubAgentTemplate:
        try:
            return self.templates[template_id]
        except KeyError as error:
            raise KeyError(template_id) from error

    def _require_step(self, plan: PlanState, step_id: str) -> PlanStep:
        for step in plan.steps:
            if step.step_id == step_id:
                return step
        raise PlanStepNotFoundError(step_id)

    def _require_evidence_kind(self, kind: str) -> EvidenceKind:
        if kind not in EVIDENCE_KINDS:
            raise ValueError(f"unsupported evidence kind: {kind}")
        return cast(EvidenceKind, kind)

    def _validate_plan_statuses(
        self,
        statuses: tuple[PlanStatus, ...],
    ) -> tuple[PlanStatus, ...]:
        return _validate_plan_status_tuple(statuses)

    def _step_from_spec(self, spec: PlanStepSpec) -> PlanStep:
        instruction = spec.instruction.strip()
        if not instruction:
            raise ValueError("decomposition step instruction is required")
        if spec.template_id is not None:
            self._require_template(spec.template_id)
        return PlanStep(
            step_id=spec.step_id or str(self._id_factory("step")),
            instruction=instruction,
            required_capabilities=tuple(spec.required_capabilities),
            template_id=spec.template_id,
            depends_on=tuple(spec.depends_on),
        )

    def _decomposition_from_proposal(
        self,
        proposal: Mapping[str, object],
    ) -> PlanDecomposition:
        if not isinstance(proposal, MappingABC):
            raise ValueError("decomposition proposal must be an object")
        objective_value = proposal.get("objective")
        if objective_value is None:
            raise ValueError("decomposition objective is required")
        steps_value = proposal.get("steps")
        if not isinstance(steps_value, list | tuple):
            raise ValueError("decomposition steps must be a list")
        return PlanDecomposition(
            objective=str(objective_value).strip(),
            steps=self._proposal_step_specs(steps_value),
        )

    def _proposal_step_specs(
        self,
        value: list[object] | tuple[object, ...],
    ) -> tuple[PlanStepSpec, ...]:
        specs: list[PlanStepSpec] = []
        for index, item in enumerate(value, start=1):
            if not isinstance(item, MappingABC):
                raise ValueError(f"step {index} must be an object")
            instruction_value = item.get("instruction")
            if instruction_value is None:
                raise ValueError(f"step {index} instruction is required")
            specs.append(
                PlanStepSpec(
                    instruction=str(instruction_value).strip(),
                    step_id=self._optional_string(item.get("step_id")),
                    required_capabilities=self._string_tuple_from_raw(
                        item.get("required_capabilities", ()),
                        field_name=f"step {index} required_capabilities",
                    ),
                    template_id=self._optional_string(item.get("template_id")),
                    depends_on=self._string_tuple_from_raw(
                        item.get("depends_on", ()),
                        field_name=f"step {index} depends_on",
                    ),
                ),
            )
        return tuple(specs)

    def _optional_string(self, value: object) -> str | None:
        if value is None:
            return None
        return str(value)

    def _string_tuple_from_raw(
        self,
        value: object,
        *,
        field_name: str,
    ) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            return (value,)
        if not isinstance(value, list | tuple):
            raise ValueError(f"{field_name} must be a list")
        return tuple(str(item) for item in value)

    def _proposal_steps_missing_templates(
        self,
        decomposition: PlanDecomposition,
        *,
        require_template: bool,
    ) -> tuple[str, ...]:
        if not require_template:
            return ()
        missing: list[str] = []
        for index, step in enumerate(decomposition.steps, start=1):
            if step.template_id is None or not step.template_id.strip():
                missing.append(step.step_id or f"step_{index}")
        return tuple(missing)

    def _proposal_disallowed_templates(
        self,
        required_templates: tuple[str, ...],
        *,
        allowed_template_ids: tuple[str, ...],
    ) -> tuple[str, ...]:
        if not allowed_template_ids:
            return ()
        allowed = set(allowed_template_ids)
        return tuple(
            template_id
            for template_id in required_templates
            if template_id not in allowed
        )

    def _validate_llm_governance_required_evidence(
        self,
        required_evidence: tuple[str, ...],
    ) -> None:
        if not required_evidence:
            raise ValueError("required_evidence must not be empty")
        allowed = set(PLANNER_LLM_GOVERNANCE_EXECUTION_REQUIRED_EVIDENCE)
        for evidence_name in required_evidence:
            if not evidence_name.strip():
                raise ValueError("required_evidence must not contain empty names")
            if evidence_name not in allowed:
                raise ValueError(f"unknown required evidence: {evidence_name}")

    def _llm_governance_evidence_state(
        self,
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

    def _validate_step_dependencies(self, steps: tuple[PlanStep, ...]) -> None:
        by_id = {step.step_id: step for step in steps}
        if len(by_id) != len(steps):
            raise ValueError("decomposition step ids must be unique")
        for step in steps:
            unknown = sorted(set(step.depends_on).difference(by_id))
            if unknown:
                raise ValueError(
                    f"unknown dependency for {step.step_id}: {', '.join(unknown)}",
                )
            if step.step_id in step.depends_on:
                raise ValueError(f"dependency cycle includes {step.step_id}")

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(step_id: str) -> None:
            if step_id in visited:
                return
            if step_id in visiting:
                raise ValueError(f"dependency cycle includes {step_id}")
            visiting.add(step_id)
            for dependency_id in by_id[step_id].depends_on:
                visit(dependency_id)
            visiting.remove(step_id)
            visited.add(step_id)

        for step in steps:
            visit(step.step_id)

    def _replace_step(
        self,
        plan: PlanState,
        step: PlanStep,
        *,
        assignments: tuple[PlanAssignment, ...] | None = None,
    ) -> PlanState:
        return replace(
            plan,
            status="running" if plan.status == "draft" else plan.status,
            steps=tuple(
                step if current.step_id == step.step_id else current
                for current in plan.steps
            ),
            assignments=plan.assignments if assignments is None else assignments,
            updated_at=float(self._clock()),
        )

    def _latest_assignment(
        self,
        plan: PlanState,
        step_id: str,
    ) -> PlanAssignment:
        for assignment in reversed(plan.assignments):
            if assignment.step_id == step_id:
                return assignment
        raise PlanStepNotFoundError(step_id)

    def _submit_assignment_to_coordinator(
        self,
        *,
        plan: PlanState,
        step: PlanStep,
        assignment: PlanAssignment,
        template: SubAgentTemplate,
    ) -> None:
        if self.coordinator is None:
            raise RuntimeError("coordinator is required to assign plan steps")
        instruction = self._instruction_for_template(step, template)
        if template.target_agent_id is None:
            self.coordinator.spawn(
                instruction=instruction,
                allowed_tool_names=template.allowed_tool_names,
                parent_agent_id=plan.owner_agent_id,
                timeout_seconds=template.timeout_seconds,
                task_id=assignment.task_id,
                child_agent_id=assignment.target_agent_id,
            )
            return
        self.coordinator.dispatch(
            instruction=instruction,
            required_capabilities=(
                step.required_capabilities or template.capabilities
            ),
            parent_agent_id=plan.owner_agent_id,
            target_agent_id=template.target_agent_id,
            allowed_tool_names=template.allowed_tool_names,
            timeout_seconds=template.timeout_seconds,
            task_id=assignment.task_id,
        )

    def _replace_assignment(
        self,
        plan: PlanState,
        old_assignment: PlanAssignment,
        new_assignment: PlanAssignment,
    ) -> PlanState:
        return replace(
            plan,
            assignments=tuple(
                new_assignment if current == old_assignment else current
                for current in plan.assignments
            ),
            updated_at=float(self._clock()),
        )

    def _mark_assignment_dispatch_submitted(
        self,
        plan_id: str,
        assignment: PlanAssignment,
    ) -> PlanState:
        last_plan: PlanState | None = None
        for _ in range(3):
            record = self._require_plan_record(plan_id)
            last_plan = record.plan
            try:
                current_step = self._require_step(record.plan, assignment.step_id)
            except PlanStepNotFoundError:
                return record.plan
            if (
                current_step.status != "assigned"
                or current_step.task_id != assignment.task_id
                or current_step.assigned_agent_id != assignment.target_agent_id
            ):
                return record.plan
            current_assignment = next(
                (
                    current
                    for current in record.plan.assignments
                    if current == assignment
                ),
                None,
            )
            if current_assignment is None:
                current_assignment = next(
                    (
                        current
                        for current in record.plan.assignments
                        if (
                            current.plan_id == assignment.plan_id
                            and current.step_id == assignment.step_id
                            and current.task_id == assignment.task_id
                            and current.target_agent_id == assignment.target_agent_id
                            and current.dispatch_status == "submitted"
                        )
                    ),
                    None,
                )
                if current_assignment is None:
                    return record.plan
                return record.plan
            if current_assignment.dispatch_status == "submitted":
                return record.plan
            submitted = replace(
                current_assignment,
                dispatch_status="submitted",
                submitted_at=float(self._clock()),
                dispatch_error=None,
            )
            updated = self._replace_assignment(
                record.plan,
                current_assignment,
                submitted,
            )
            try:
                self._save_plan(updated, expected_revision=record.revision)
            except PlanConflictError:
                continue
            return updated
        if last_plan is None:
            raise PlanNotFoundError(plan_id)
        raise PlanConflictError(
            f"plan changed before saving submitted dispatch marker: {plan_id}",
        )

    def _mark_assignment_dispatch_failed(
        self,
        plan: PlanState,
        step: PlanStep,
        assignment: PlanAssignment,
        *,
        error: str,
    ) -> None:
        record = self._require_plan_record(plan.plan_id)
        current_step = self._require_step(record.plan, step.step_id)
        if (
            current_step.status != step.status
            or current_step.task_id != step.task_id
            or current_step.assigned_agent_id != step.assigned_agent_id
        ):
            return
        current_assignment = next(
            (
                current
                for current in record.plan.assignments
                if current == assignment
            ),
            None,
        )
        if current_assignment is None:
            return
        failed_step = replace(
            current_step,
            status="failed",
            error=error,
            last_failed_at=float(self._clock()),
        )
        failed_assignment = replace(
            current_assignment,
            dispatch_status="failed",
            dispatch_error=error,
        )
        failed_plan = self._replace_step(
            record.plan,
            failed_step,
            assignments=tuple(
                failed_assignment if current == current_assignment else current
                for current in record.plan.assignments
            ),
        )
        self._save_plan(failed_plan, expected_revision=record.revision)

    def _instruction_for_template(
        self,
        step: PlanStep,
        template: SubAgentTemplate,
    ) -> str:
        context = "\n".join(template.context_seed)
        if not context:
            return step.instruction
        return f"{context}\n\nTask: {step.instruction}"

    def _default_id(self, prefix: str) -> str:
        return f"{prefix}_{uuid4().hex}"


class PlannerSchedulerDaemon:
    """Host loop that repeatedly runs bounded planner scheduler ticks."""

    def __init__(
        self,
        *,
        runtime: PlannerRuntime,
        plan_ids: tuple[str, ...],
        default_template_id: str | None = None,
        retry_limit: int | None = None,
        dispatch_limit: int | None = None,
        poll_interval_seconds: float = 1.0,
        clock: object | None = None,
    ) -> None:
        self._validate_configuration(
            plan_ids=plan_ids,
            retry_limit=retry_limit,
            dispatch_limit=dispatch_limit,
            poll_interval_seconds=poll_interval_seconds,
        )
        self.runtime = runtime
        self.plan_ids = tuple(plan_ids)
        self.default_template_id = default_template_id
        self.retry_limit = retry_limit
        self.dispatch_limit = dispatch_limit
        self.poll_interval_seconds = poll_interval_seconds
        self._clock = clock if callable(clock) else time.time
        self._lock = RLock()
        self._stop_event = Event()
        self._thread: Thread | None = None
        self._state = PlannerSchedulerDaemonState(
            status="idle",
            plan_ids=self.plan_ids,
            poll_interval_seconds=poll_interval_seconds,
            default_template_id=default_template_id,
            retry_limit=retry_limit,
            dispatch_limit=dispatch_limit,
        )

    def run_once(self) -> tuple[PlanSchedulerTickReport, ...]:
        """Run one daemon iteration without starting a background thread."""

        reports: list[PlanSchedulerTickReport] = []
        errors: list[PlannerSchedulerDaemonError] = []
        for plan_id in self.plan_ids:
            try:
                reports.append(
                    self.runtime.scheduler_tick(
                        plan_id,
                        default_template_id=self.default_template_id,
                        retry_limit=self.retry_limit,
                        dispatch_limit=self.dispatch_limit,
                    ),
                )
            except Exception as error:
                errors.append(
                    PlannerSchedulerDaemonError(
                        plan_id=plan_id,
                        error=str(error) or error.__class__.__name__,
                    ),
                )
        self._record_run(tuple(reports), tuple(errors))
        return tuple(reports)

    def start(self) -> None:
        """Start the background polling loop if it is not already running."""

        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._state = replace(
                self._state,
                status="running",
                started_at=float(self._clock()),
                stopped_at=None,
            )
            self._thread = Thread(
                target=self._run_loop,
                name="agentos-planner-scheduler-daemon",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        """Request the background polling loop to stop."""

        self._stop_event.set()
        with self._lock:
            if self._state.status == "running":
                self._state = replace(self._state, status="stopping")

    def join(self, timeout: float | None = None) -> bool:
        """Wait for the background loop to exit."""

        thread = self._thread
        if thread is None:
            return True
        thread.join(timeout=timeout)
        return not thread.is_alive()

    def is_running(self) -> bool:
        """Return whether the daemon thread is currently alive."""

        thread = self._thread
        return thread is not None and thread.is_alive()

    def state(self) -> PlannerSchedulerDaemonState:
        """Return an immutable daemon state snapshot."""

        with self._lock:
            return self._state

    def _run_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                self.run_once()
                self._stop_event.wait(self.poll_interval_seconds)
        finally:
            with self._lock:
                self._state = replace(
                    self._state,
                    status="stopped",
                    stopped_at=float(self._clock()),
                )

    def _record_run(
        self,
        reports: tuple[PlanSchedulerTickReport, ...],
        errors: tuple[PlannerSchedulerDaemonError, ...],
    ) -> None:
        with self._lock:
            self._state = replace(
                self._state,
                iterations=self._state.iterations + 1,
                last_run_at=float(self._clock()),
                last_reports=reports,
                errors=errors,
            )

    def _validate_configuration(
        self,
        *,
        plan_ids: tuple[str, ...],
        retry_limit: int | None,
        dispatch_limit: int | None,
        poll_interval_seconds: float,
    ) -> None:
        if not plan_ids:
            raise ValueError("plan_ids must not be empty")
        if any(not plan_id.strip() for plan_id in plan_ids):
            raise ValueError("plan_ids must not contain empty ids")
        if retry_limit is not None and retry_limit < 1:
            raise ValueError("retry_limit must be >= 1")
        if dispatch_limit is not None and dispatch_limit < 1:
            raise ValueError("dispatch_limit must be >= 1")
        if poll_interval_seconds < 0:
            raise ValueError("poll_interval_seconds must be >= 0")


class PlannerClaimedSchedulerDaemon:
    """Host loop that repeatedly runs claimed planner scheduler ticks."""

    def __init__(
        self,
        *,
        runtime: PlannerRuntime,
        worker_id: str,
        lease_seconds: float,
        owner_agent_id: str | None = None,
        statuses: tuple[PlanStatus, ...] = ("draft", "running"),
        limit: int | None = None,
        default_template_id: str | None = None,
        retry_limit: int | None = None,
        dispatch_limit: int | None = None,
        release_after_tick: bool = False,
        poll_interval_seconds: float = 1.0,
        clock: object | None = None,
    ) -> None:
        self._validate_configuration(
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            statuses=statuses,
            limit=limit,
            retry_limit=retry_limit,
            dispatch_limit=dispatch_limit,
            poll_interval_seconds=poll_interval_seconds,
        )
        self.runtime = runtime
        self.worker_id = worker_id
        self.lease_seconds = float(lease_seconds)
        self.owner_agent_id = owner_agent_id
        self.statuses = tuple(statuses)
        self.limit = limit
        self.default_template_id = default_template_id
        self.retry_limit = retry_limit
        self.dispatch_limit = dispatch_limit
        self.release_after_tick = release_after_tick
        self.poll_interval_seconds = poll_interval_seconds
        self._clock = clock if callable(clock) else time.time
        self._lock = RLock()
        self._stop_event = Event()
        self._thread: Thread | None = None
        self._state = PlannerClaimedSchedulerDaemonState(
            status="idle",
            worker_id=worker_id,
            lease_seconds=self.lease_seconds,
            owner_agent_id=owner_agent_id,
            statuses=self.statuses,
            limit=limit,
            default_template_id=default_template_id,
            retry_limit=retry_limit,
            dispatch_limit=dispatch_limit,
            release_after_tick=release_after_tick,
            poll_interval_seconds=poll_interval_seconds,
        )

    def run_once(self) -> PlanClaimedSchedulerTickReport:
        """Run one claim-before-tick daemon iteration."""

        try:
            report = self.runtime.claimed_scheduler_tick(
                worker_id=self.worker_id,
                lease_seconds=self.lease_seconds,
                owner_agent_id=self.owner_agent_id,
                statuses=self.statuses,
                limit=self.limit,
                default_template_id=self.default_template_id,
                retry_limit=self.retry_limit,
                dispatch_limit=self.dispatch_limit,
                release_after_tick=self.release_after_tick,
            )
        except Exception as error:
            self._record_error(
                PlannerClaimedSchedulerDaemonError(
                    error=str(error) or error.__class__.__name__,
                ),
            )
            raise
        self._record_success(report)
        return report

    def start(self) -> None:
        """Start the background claim-before-tick polling loop."""

        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._state = replace(
                self._state,
                status="running",
                started_at=float(self._clock()),
                stopped_at=None,
            )
            self._thread = Thread(
                target=self._run_loop,
                name="agentos-planner-claimed-scheduler-daemon",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        """Request the background polling loop to stop."""

        self._stop_event.set()
        with self._lock:
            if self._state.status == "running":
                self._state = replace(self._state, status="stopping")

    def join(self, timeout: float | None = None) -> bool:
        """Wait for the background loop to exit."""

        thread = self._thread
        if thread is None:
            return True
        thread.join(timeout=timeout)
        return not thread.is_alive()

    def is_running(self) -> bool:
        """Return whether the daemon thread is currently alive."""

        thread = self._thread
        return thread is not None and thread.is_alive()

    def state(self) -> PlannerClaimedSchedulerDaemonState:
        """Return an immutable daemon state snapshot."""

        with self._lock:
            return self._state

    def _run_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                self.run_once()
                self._stop_event.wait(self.poll_interval_seconds)
        finally:
            with self._lock:
                self._state = replace(
                    self._state,
                    status="stopped",
                    stopped_at=float(self._clock()),
                )

    def _record_success(self, report: PlanClaimedSchedulerTickReport) -> None:
        with self._lock:
            self._state = replace(
                self._state,
                iterations=self._state.iterations + 1,
                last_run_at=float(self._clock()),
                last_report=report,
                errors=(),
            )

    def _record_error(self, error: PlannerClaimedSchedulerDaemonError) -> None:
        with self._lock:
            self._state = replace(
                self._state,
                iterations=self._state.iterations + 1,
                last_run_at=float(self._clock()),
                last_report=None,
                errors=(error,),
            )

    def _validate_configuration(
        self,
        *,
        worker_id: str,
        lease_seconds: float,
        statuses: tuple[PlanStatus, ...],
        limit: int | None,
        retry_limit: int | None,
        dispatch_limit: int | None,
        poll_interval_seconds: float,
    ) -> None:
        if not worker_id.strip():
            raise ValueError("worker_id must not be empty")
        if float(lease_seconds) <= 0:
            raise ValueError("lease_seconds must be > 0")
        _validate_plan_status_tuple(statuses)
        if limit is not None and limit < 1:
            raise ValueError("limit must be >= 1")
        if retry_limit is not None and retry_limit < 1:
            raise ValueError("retry_limit must be >= 1")
        if dispatch_limit is not None and dispatch_limit < 1:
            raise ValueError("dispatch_limit must be >= 1")
        if poll_interval_seconds < 0:
            raise ValueError("poll_interval_seconds must be >= 0")


def _validate_plan_status_tuple(
    statuses: tuple[PlanStatus, ...],
) -> tuple[PlanStatus, ...]:
    if not statuses:
        raise ValueError("statuses must not be empty")
    for status in statuses:
        if status not in PLAN_STATUSES:
            raise ValueError(f"unsupported plan status in statuses: {status}")
    return tuple(statuses)


def plan_to_working_state_summary(
    plan: PlanState,
    *,
    max_next_steps: int = 5,
    max_recent_evidence: int = 5,
) -> dict[str, object]:
    """Project plan state into a compact JSON-safe working-state summary."""

    return {
        "plan_id": plan.plan_id,
        "objective": plan.objective,
        "status": plan.status,
        "step_counts": _step_counts(plan),
        "next_steps": [
            _step_summary(step)
            for step in plan.steps
            if step.status != "completed"
        ][:max_next_steps],
        "recent_evidence": [
            _evidence_summary(evidence)
            for evidence in plan.evidence[-max_recent_evidence:]
        ],
    }


def _step_counts(plan: PlanState) -> dict[str, int]:
    counts = {
        "assigned": 0,
        "blocked": 0,
        "cancelled": 0,
        "completed": 0,
        "failed": 0,
        "pending": 0,
        "running": 0,
    }
    for step in plan.steps:
        counts[step.status] += 1
    return counts


def _step_summary(step: PlanStep) -> dict[str, object]:
    return {
        "step_id": step.step_id,
        "instruction": step.instruction,
        "status": step.status,
        "template_id": step.template_id,
        "assigned_agent_id": step.assigned_agent_id,
        "required_capabilities": list(step.required_capabilities),
        "depends_on": list(step.depends_on),
        "evidence_ids": list(step.evidence_ids),
        "attempts": step.attempts,
        "next_retry_at": step.next_retry_at,
        "retry_status": step.retry_status,
    }


def _evidence_summary(evidence: EvidenceHandle) -> dict[str, object]:
    return {
        "evidence_id": evidence.evidence_id,
        "kind": evidence.kind,
        "summary": evidence.summary,
        "producer_agent_id": evidence.producer_agent_id,
    }


class PlannerTools:
    """Register PlannerRuntime operations as external tools."""

    def __init__(
        self,
        *,
        runtime: PlannerRuntime,
        owner_agent_id: str,
        authorization_policy: PlannerToolAuthorizationPolicy | None = None,
    ) -> None:
        self.runtime = runtime
        self.owner_agent_id = owner_agent_id
        self.authorization_policy = (
            authorization_policy or DefaultPlannerToolAuthorizationPolicy()
        )

    def register(self, registry: ToolRegistry) -> None:
        registry.register(
            RegisteredTool(
                name="plan_create",
                description="Create a structured execution plan.",
                parameters=self._plan_create_parameters(),
                handler=self._plan_create,
            ),
        )
        registry.register(
            RegisteredTool(
                name="plan_gate_decomposition_proposal",
                description=(
                    "Parse and gate a raw LLM decomposition proposal without "
                    "creating a plan."
                ),
                parameters=self._plan_gate_decomposition_proposal_parameters(),
                handler=self._plan_gate_decomposition_proposal,
            ),
        )
        registry.register(
            RegisteredTool(
                name="plan_create_from_decomposition",
                description="Create a plan from a structured decomposition.",
                parameters=self._plan_create_from_decomposition_parameters(),
                handler=self._plan_create_from_decomposition,
            ),
        )
        registry.register(
            RegisteredTool(
                name="plan_add_step",
                description="Add a step to an existing execution plan.",
                parameters=self._plan_add_step_parameters(),
                handler=self._plan_add_step,
            ),
        )
        registry.register(
            RegisteredTool(
                name="plan_assign_step",
                description="Assign a plan step to a subagent template.",
                parameters=self._plan_assign_step_parameters(),
                handler=self._plan_assign_step,
            ),
        )
        registry.register(
            RegisteredTool(
                name="plan_ready_steps",
                description="Return pending plan steps whose dependencies are complete.",
                parameters=self._plan_ready_steps_parameters(),
                handler=self._plan_ready_steps,
            ),
        )
        registry.register(
            RegisteredTool(
                name="plan_schedulable_plans",
                description=(
                    "Return owned plans with dependency-ready or due-retry "
                    "work for deployment-owned schedulers."
                ),
                parameters=self._plan_schedulable_plans_parameters(),
                handler=self._plan_schedulable_plans,
            ),
        )
        registry.register(
            RegisteredTool(
                name="plan_claim_schedulable_plans",
                description=(
                    "Claim owned schedulable plans for this scheduler worker "
                    "through the configured claim store."
                ),
                parameters=self._plan_claim_schedulable_plans_parameters(),
                handler=self._plan_claim_schedulable_plans,
            ),
        )
        registry.register(
            RegisteredTool(
                name="plan_claimed_scheduler_tick",
                description=(
                    "Claim owned schedulable plans and run one scheduler tick "
                    "only for plans claimed by this worker."
                ),
                parameters=self._plan_claimed_scheduler_tick_parameters(),
                handler=self._plan_claimed_scheduler_tick,
            ),
        )
        registry.register(
            RegisteredTool(
                name="plan_dispatch_ready_steps",
                description="Dispatch dependency-ready plan steps to subagent templates.",
                parameters=self._plan_dispatch_ready_steps_parameters(),
                handler=self._plan_dispatch_ready_steps,
            ),
        )
        registry.register(
            RegisteredTool(
                name="plan_scheduler_tick",
                description=(
                    "Run one planner scheduler pass: reset due retries and "
                    "dispatch dependency-ready steps."
                ),
                parameters=self._plan_scheduler_tick_parameters(),
                handler=self._plan_scheduler_tick,
            ),
        )
        registry.register(
            RegisteredTool(
                name="plan_fail_step",
                description="Record a failed plan step and its retry metadata.",
                parameters=self._plan_fail_step_parameters(),
                handler=self._plan_fail_step,
            ),
        )
        registry.register(
            RegisteredTool(
                name="plan_retryable_steps",
                description="Return failed plan steps whose retry delay has elapsed.",
                parameters=self._plan_retryable_steps_parameters(),
                handler=self._plan_retryable_steps,
            ),
        )
        registry.register(
            RegisteredTool(
                name="plan_retry_step",
                description="Move a retryable failed plan step back to pending.",
                parameters=self._plan_retry_step_parameters(),
                handler=self._plan_retry_step,
            ),
        )
        registry.register(
            RegisteredTool(
                name="plan_record_evidence",
                description="Record evidence and optionally attach it to plan steps.",
                parameters=self._plan_record_evidence_parameters(),
                handler=self._plan_record_evidence,
            ),
        )
        registry.register(
            RegisteredTool(
                name="plan_complete_step",
                description="Mark a plan step completed and attach evidence handles.",
                parameters=self._plan_complete_step_parameters(),
                handler=self._plan_complete_step,
            ),
        )
        registry.register(
            RegisteredTool(
                name="plan_status",
                description="Return one plan or all plans owned by this agent.",
                parameters=self._plan_status_parameters(),
                handler=self._plan_status,
            ),
        )

    def _plan_create(self, arguments: dict[str, object]) -> str:
        self._authorize("plan_create", None)
        plan = self.runtime.create_plan(
            objective=str(arguments["objective"]),
            owner_agent_id=self.owner_agent_id,
            plan_id=(
                str(arguments["plan_id"])
                if arguments.get("plan_id") is not None
                else None
            ),
        )
        return json.dumps(self._plan_to_dict(plan), sort_keys=True)

    def _plan_create_from_decomposition(self, arguments: dict[str, object]) -> str:
        self._authorize("plan_create_from_decomposition", None)
        plan = self.runtime.create_plan_from_decomposition(
            PlanDecomposition(
                objective=str(arguments["objective"]),
                steps=self._plan_step_specs(arguments["steps"]),
            ),
            owner_agent_id=self.owner_agent_id,
            plan_id=(
                str(arguments["plan_id"])
                if arguments.get("plan_id") is not None
                else None
            ),
        )
        return json.dumps(self._plan_to_dict(plan), sort_keys=True)

    def _plan_gate_decomposition_proposal(self, arguments: dict[str, object]) -> str:
        self._authorize("plan_gate_decomposition_proposal", None)
        proposal = arguments.get("proposal")
        if not isinstance(proposal, MappingABC):
            raise ValueError("proposal must be an object")
        report = self.runtime.gate_decomposition_proposal(
            proposal,
            policy=self._plan_decomposition_gate_policy(
                arguments.get("policy"),
            ),
            metadata=self._object_mapping(arguments.get("metadata")),
        )
        return json.dumps(report.as_dict(), sort_keys=True)

    def _plan_add_step(self, arguments: dict[str, object]) -> str:
        plan_id = str(arguments["plan_id"])
        self._authorize("plan_add_step", plan_id)
        self._require_owned_plan(plan_id)
        updated = self.runtime.add_step(
            plan_id,
            instruction=str(arguments["instruction"]),
            required_capabilities=self._string_tuple(
                arguments.get("required_capabilities", ()),
            ),
            template_id=(
                str(arguments["template_id"])
                if arguments.get("template_id") is not None
                else None
            ),
        )
        return json.dumps(self._plan_to_dict(updated), sort_keys=True)

    def _plan_status(self, arguments: dict[str, object]) -> str:
        if arguments.get("plan_id") is not None:
            plan_id = str(arguments["plan_id"])
            self._authorize("plan_status", plan_id)
            plan = self._require_owned_plan(plan_id)
            return json.dumps(self._plan_to_dict(plan), sort_keys=True)
        self._authorize("plan_status", None)
        plans = self.runtime.list_plans(self.owner_agent_id)
        return json.dumps(
            {
                "plans": [self._plan_to_dict(plan) for plan in plans],
            },
            sort_keys=True,
        )

    def _plan_assign_step(self, arguments: dict[str, object]) -> str:
        plan_id = str(arguments["plan_id"])
        self._authorize("plan_assign_step", plan_id)
        self._require_owned_plan(plan_id)
        updated = self.runtime.assign_step(
            plan_id,
            str(arguments["step_id"]),
            template_id=str(arguments["template_id"]),
        )
        return json.dumps(self._plan_to_dict(updated), sort_keys=True)

    def _plan_ready_steps(self, arguments: dict[str, object]) -> str:
        plan_id = str(arguments["plan_id"])
        self._authorize("plan_ready_steps", plan_id)
        self._require_owned_plan(plan_id)
        return json.dumps(
            {
                "plan_id": plan_id,
                "ready_steps": [
                    self._step_to_dict(step)
                    for step in self.runtime.ready_steps(plan_id)
                ],
            },
            sort_keys=True,
        )

    def _plan_dispatch_ready_steps(self, arguments: dict[str, object]) -> str:
        plan_id = str(arguments["plan_id"])
        self._authorize("plan_dispatch_ready_steps", plan_id)
        self._require_owned_plan(plan_id)
        limit_arg = arguments.get("limit")
        report = self.runtime.dispatch_ready_steps(
            plan_id,
            default_template_id=(
                str(arguments["default_template_id"])
                if arguments.get("default_template_id") is not None
                else None
            ),
            limit=None if limit_arg is None else int(limit_arg),
        )
        return json.dumps(self._dispatch_report_to_dict(report), sort_keys=True)

    def _plan_schedulable_plans(self, arguments: dict[str, object]) -> str:
        self._authorize("plan_schedulable_plans", None)
        limit_arg = arguments.get("limit")
        summaries = self.runtime.schedulable_plans(
            owner_agent_id=self.owner_agent_id,
            statuses=self._plan_status_tuple(
                arguments.get("statuses", ("draft", "running")),
            ),
            limit=None if limit_arg is None else int(limit_arg),
        )
        return json.dumps(
            {
                "plans": [
                    summary.as_dict()
                    for summary in summaries
                ],
            },
            sort_keys=True,
        )

    def _plan_claim_schedulable_plans(self, arguments: dict[str, object]) -> str:
        self._authorize("plan_claim_schedulable_plans", None)
        limit_arg = arguments.get("limit")
        claims = self.runtime.claim_schedulable_plans(
            owner_agent_id=self.owner_agent_id,
            worker_id=str(arguments["worker_id"]),
            lease_seconds=float(arguments["lease_seconds"]),
            statuses=self._plan_status_tuple(
                arguments.get("statuses", ("draft", "running")),
            ),
            limit=None if limit_arg is None else int(limit_arg),
        )
        return json.dumps(
            {
                "claims": [
                    claim.as_dict()
                    for claim in claims
                ],
            },
            sort_keys=True,
        )

    def _plan_claimed_scheduler_tick(self, arguments: dict[str, object]) -> str:
        self._authorize("plan_claimed_scheduler_tick", None)
        limit_arg = arguments.get("limit")
        retry_limit_arg = arguments.get("retry_limit")
        dispatch_limit_arg = arguments.get("dispatch_limit")
        report = self.runtime.claimed_scheduler_tick(
            owner_agent_id=self.owner_agent_id,
            worker_id=str(arguments["worker_id"]),
            lease_seconds=float(arguments["lease_seconds"]),
            statuses=self._plan_status_tuple(
                arguments.get("statuses", ("draft", "running")),
            ),
            limit=None if limit_arg is None else int(limit_arg),
            default_template_id=(
                str(arguments["default_template_id"])
                if arguments.get("default_template_id") is not None
                else None
            ),
            retry_limit=(
                None if retry_limit_arg is None else int(retry_limit_arg)
            ),
            dispatch_limit=(
                None if dispatch_limit_arg is None else int(dispatch_limit_arg)
            ),
            release_after_tick=bool(arguments.get("release_after_tick", False)),
        )
        return json.dumps(
            self._claimed_scheduler_tick_report_to_dict(report),
            sort_keys=True,
        )

    def _plan_scheduler_tick(self, arguments: dict[str, object]) -> str:
        plan_id = str(arguments["plan_id"])
        self._authorize("plan_scheduler_tick", plan_id)
        self._require_owned_plan(plan_id)
        retry_limit_arg = arguments.get("retry_limit")
        dispatch_limit_arg = arguments.get("dispatch_limit")
        report = self.runtime.scheduler_tick(
            plan_id,
            default_template_id=(
                str(arguments["default_template_id"])
                if arguments.get("default_template_id") is not None
                else None
            ),
            retry_limit=(
                None if retry_limit_arg is None else int(retry_limit_arg)
            ),
            dispatch_limit=(
                None if dispatch_limit_arg is None else int(dispatch_limit_arg)
            ),
        )
        return json.dumps(self._scheduler_tick_report_to_dict(report), sort_keys=True)

    def _plan_fail_step(self, arguments: dict[str, object]) -> str:
        plan_id = str(arguments["plan_id"])
        self._authorize("plan_fail_step", plan_id)
        self._require_owned_plan(plan_id)
        updated = self.runtime.fail_step(
            plan_id,
            str(arguments["step_id"]),
            error=str(arguments["error"]),
        )
        return json.dumps(self._plan_to_dict(updated), sort_keys=True)

    def _plan_retryable_steps(self, arguments: dict[str, object]) -> str:
        plan_id = str(arguments["plan_id"])
        self._authorize("plan_retryable_steps", plan_id)
        self._require_owned_plan(plan_id)
        return json.dumps(
            {
                "plan_id": plan_id,
                "retryable_steps": [
                    self._step_to_dict(step)
                    for step in self.runtime.retryable_steps(plan_id)
                ],
            },
            sort_keys=True,
        )

    def _plan_retry_step(self, arguments: dict[str, object]) -> str:
        plan_id = str(arguments["plan_id"])
        self._authorize("plan_retry_step", plan_id)
        self._require_owned_plan(plan_id)
        updated = self.runtime.retry_step(plan_id, str(arguments["step_id"]))
        return json.dumps(self._plan_to_dict(updated), sort_keys=True)

    def _plan_record_evidence(self, arguments: dict[str, object]) -> str:
        plan_id = str(arguments["plan_id"])
        self._authorize("plan_record_evidence", plan_id)
        self._require_owned_plan(plan_id)
        evidence = self.runtime.record_evidence(
            plan_id,
            step_ids=self._string_tuple(arguments.get("step_ids", ())),
            kind=self._evidence_kind(arguments["kind"]),
            summary=str(arguments["summary"]),
            uri=(
                str(arguments["uri"])
                if arguments.get("uri") is not None
                else None
            ),
            producer_agent_id=(
                str(arguments["producer_agent_id"])
                if arguments.get("producer_agent_id") is not None
                else None
            ),
            metadata=self._string_mapping(arguments.get("metadata")),
        )
        return json.dumps(self._evidence_to_dict(evidence), sort_keys=True)

    def _plan_complete_step(self, arguments: dict[str, object]) -> str:
        plan_id = str(arguments["plan_id"])
        self._authorize("plan_complete_step", plan_id)
        self._require_owned_plan(plan_id)
        updated = self.runtime.complete_step(
            plan_id,
            str(arguments["step_id"]),
            evidence_ids=self._string_tuple(arguments.get("evidence_ids", ())),
        )
        return json.dumps(self._plan_to_dict(updated), sort_keys=True)

    def _authorize(self, tool_name: str, plan_id: str | None) -> None:
        self.authorization_policy.authorize_planner_tool(
            tool_name=tool_name,
            owner_agent_id=self.owner_agent_id,
            plan_id=plan_id,
        )

    def _require_owned_plan(self, plan_id: str) -> PlanState:
        return self.runtime.get_plan(
            plan_id,
            owner_agent_id=self.owner_agent_id,
        )

    def _string_tuple(self, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            return (value,)
        if not isinstance(value, list | tuple):
            raise ValueError("expected list of strings")
        return tuple(str(item) for item in value)

    def _string_mapping(self, value: object) -> dict[str, str] | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ValueError("expected object with string values")
        return {str(key): str(item) for key, item in value.items()}

    def _object_mapping(self, value: object) -> dict[str, object] | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ValueError("expected object")
        return {str(key): item for key, item in value.items()}

    def _plan_decomposition_gate_policy(
        self,
        value: object,
    ) -> PlanDecompositionGatePolicy:
        if value is None:
            return PlanDecompositionGatePolicy()
        if not isinstance(value, dict):
            raise ValueError("policy must be an object")
        max_steps_value = value.get("max_steps")
        return PlanDecompositionGatePolicy(
            max_steps=(
                None
                if max_steps_value is None
                else int(max_steps_value)
            ),
            require_template=bool(value.get("require_template", False)),
            require_approval=bool(value.get("require_approval", False)),
            approved=bool(value.get("approved", False)),
            allowed_template_ids=self._string_tuple(
                value.get("allowed_template_ids", ()),
            ),
        )

    def _evidence_kind(self, value: object) -> EvidenceKind:
        kind = str(value)
        if kind not in EVIDENCE_KINDS:
            raise ValueError(f"unsupported evidence kind: {kind}")
        return cast(EvidenceKind, kind)

    def _plan_status_tuple(self, value: object) -> tuple[PlanStatus, ...]:
        statuses = self._string_tuple(value)
        if not statuses:
            raise ValueError("statuses must not be empty")
        for status in statuses:
            if status not in PLAN_STATUSES:
                raise ValueError(f"unsupported plan status: {status}")
        return cast(tuple[PlanStatus, ...], statuses)

    def _plan_step_specs(self, value: object) -> tuple[PlanStepSpec, ...]:
        if not isinstance(value, list | tuple):
            raise ValueError("expected list of plan step specs")
        specs: list[PlanStepSpec] = []
        for item in value:
            if not isinstance(item, dict):
                raise ValueError("expected object plan step spec")
            specs.append(
                PlanStepSpec(
                    instruction=str(item["instruction"]),
                    step_id=(
                        str(item["step_id"])
                        if item.get("step_id") is not None
                        else None
                    ),
                    required_capabilities=self._string_tuple(
                        item.get("required_capabilities", ()),
                    ),
                    template_id=(
                        str(item["template_id"])
                        if item.get("template_id") is not None
                        else None
                    ),
                    depends_on=self._string_tuple(item.get("depends_on", ())),
                ),
            )
        return tuple(specs)

    def _plan_to_dict(self, plan: PlanState) -> dict[str, object]:
        return {
            "plan_id": plan.plan_id,
            "objective": plan.objective,
            "owner_agent_id": plan.owner_agent_id,
            "status": plan.status,
            "steps": [
                self._step_to_dict(step)
                for step in plan.steps
            ],
            "evidence": [
                self._evidence_to_dict(evidence)
                for evidence in plan.evidence
            ],
            "assignments": [
                self._assignment_to_dict(assignment)
                for assignment in plan.assignments
            ],
            "created_at": plan.created_at,
            "updated_at": plan.updated_at,
        }

    def _step_to_dict(self, step: PlanStep) -> dict[str, object]:
        return {
            "step_id": step.step_id,
            "instruction": step.instruction,
            "status": step.status,
            "required_capabilities": list(step.required_capabilities),
            "assigned_agent_id": step.assigned_agent_id,
            "template_id": step.template_id,
            "task_id": step.task_id,
            "depends_on": list(step.depends_on),
            "evidence_ids": list(step.evidence_ids),
            "error": step.error,
            "attempts": step.attempts,
            "last_failed_at": step.last_failed_at,
            "next_retry_at": step.next_retry_at,
            "retry_status": step.retry_status,
            "retry_exhausted_at": step.retry_exhausted_at,
        }

    def _evidence_to_dict(self, evidence: EvidenceHandle) -> dict[str, object]:
        return {
            "evidence_id": evidence.evidence_id,
            "kind": evidence.kind,
            "summary": evidence.summary,
            "uri": evidence.uri,
            "producer_agent_id": evidence.producer_agent_id,
            "metadata": dict(evidence.metadata),
        }

    def _dispatch_report_to_dict(
        self,
        report: PlanDispatchReport,
    ) -> dict[str, object]:
        return {
            "plan_id": report.plan_id,
            "assigned": [
                self._assignment_to_dict(assignment)
                for assignment in report.assigned
            ],
            "skipped": [
                {
                    "plan_id": skip.plan_id,
                    "step_id": skip.step_id,
                    "reason": skip.reason,
                    "detail": skip.detail,
                }
                for skip in report.skipped
            ],
        }

    def _scheduler_tick_report_to_dict(
        self,
        report: PlanSchedulerTickReport,
    ) -> dict[str, object]:
        return {
            "plan_id": report.plan_id,
            "retry_resets": [
                {
                    "plan_id": reset.plan_id,
                    "step_id": reset.step_id,
                    "attempts": reset.attempts,
                }
                for reset in report.retry_resets
            ],
            "dispatch": self._dispatch_report_to_dict(report.dispatch),
        }

    def _claimed_scheduler_tick_report_to_dict(
        self,
        report: PlanClaimedSchedulerTickReport,
    ) -> dict[str, object]:
        return {
            "worker_id": report.worker_id,
            "claims": [claim.as_dict() for claim in report.claims],
            "tick_reports": [
                self._scheduler_tick_report_to_dict(tick_report)
                for tick_report in report.tick_reports
            ],
            "skipped": [
                skip.as_dict()
                for skip in report.skipped
            ],
            "released_plan_ids": list(report.released_plan_ids),
        }

    def _assignment_to_dict(self, assignment: PlanAssignment) -> dict[str, object]:
        return {
            "plan_id": assignment.plan_id,
            "step_id": assignment.step_id,
            "template_id": assignment.template_id,
            "task_id": assignment.task_id,
            "target_agent_id": assignment.target_agent_id,
            "created_at": assignment.created_at,
            "dispatch_status": assignment.dispatch_status,
            "submitted_at": assignment.submitted_at,
            "dispatch_error": assignment.dispatch_error,
        }

    def _plan_create_parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "objective": {"type": "string"},
                "plan_id": {"type": "string"},
            },
            "required": ["objective"],
        }

    def _plan_add_step_parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "plan_id": {"type": "string"},
                "instruction": {"type": "string"},
                "required_capabilities": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "template_id": {"type": "string"},
            },
            "required": ["plan_id", "instruction"],
        }

    def _plan_create_from_decomposition_parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "objective": {"type": "string"},
                "plan_id": {"type": "string"},
                "steps": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "instruction": {"type": "string"},
                            "step_id": {"type": "string"},
                            "required_capabilities": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "template_id": {"type": "string"},
                            "depends_on": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "required": ["instruction"],
                    },
                },
            },
            "required": ["objective", "steps"],
        }

    def _plan_gate_decomposition_proposal_parameters(self) -> dict[str, object]:
        step_properties = {
            "instruction": {"type": "string"},
            "step_id": {"type": "string"},
            "required_capabilities": {
                "type": "array",
                "items": {"type": "string"},
            },
            "template_id": {"type": "string"},
            "depends_on": {
                "type": "array",
                "items": {"type": "string"},
            },
        }
        return {
            "type": "object",
            "properties": {
                "proposal": {
                    "type": "object",
                    "properties": {
                        "objective": {"type": "string"},
                        "steps": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": step_properties,
                                "required": ["instruction"],
                            },
                        },
                    },
                    "required": ["objective", "steps"],
                },
                "policy": {
                    "type": "object",
                    "properties": {
                        "max_steps": {"type": "integer", "minimum": 1},
                        "require_template": {"type": "boolean"},
                        "require_approval": {"type": "boolean"},
                        "approved": {"type": "boolean"},
                        "allowed_template_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                },
                "metadata": {"type": "object"},
            },
            "required": ["proposal"],
        }

    def _plan_assign_step_parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "plan_id": {"type": "string"},
                "step_id": {"type": "string"},
                "template_id": {"type": "string"},
            },
            "required": ["plan_id", "step_id", "template_id"],
        }

    def _plan_ready_steps_parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "plan_id": {"type": "string"},
            },
            "required": ["plan_id"],
        }

    def _plan_dispatch_ready_steps_parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "plan_id": {"type": "string"},
                "default_template_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1},
            },
            "required": ["plan_id"],
        }

    def _plan_schedulable_plans_parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "statuses": {
                    "type": "array",
                    "items": {"type": "string", "enum": list(PLAN_STATUSES)},
                },
                "limit": {"type": "integer", "minimum": 1},
            },
        }

    def _plan_claim_schedulable_plans_parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "worker_id": {"type": "string"},
                "lease_seconds": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                },
                "statuses": {
                    "type": "array",
                    "items": {"type": "string", "enum": list(PLAN_STATUSES)},
                },
                "limit": {"type": "integer", "minimum": 1},
            },
            "required": ["worker_id", "lease_seconds"],
        }

    def _plan_claimed_scheduler_tick_parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "worker_id": {"type": "string"},
                "lease_seconds": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                },
                "statuses": {
                    "type": "array",
                    "items": {"type": "string", "enum": list(PLAN_STATUSES)},
                },
                "limit": {"type": "integer", "minimum": 1},
                "default_template_id": {"type": "string"},
                "retry_limit": {"type": "integer", "minimum": 1},
                "dispatch_limit": {"type": "integer", "minimum": 1},
                "release_after_tick": {"type": "boolean"},
            },
            "required": ["worker_id", "lease_seconds"],
        }

    def _plan_scheduler_tick_parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "plan_id": {"type": "string"},
                "default_template_id": {"type": "string"},
                "retry_limit": {"type": "integer", "minimum": 1},
                "dispatch_limit": {"type": "integer", "minimum": 1},
            },
            "required": ["plan_id"],
        }

    def _plan_fail_step_parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "plan_id": {"type": "string"},
                "step_id": {"type": "string"},
                "error": {"type": "string"},
            },
            "required": ["plan_id", "step_id", "error"],
        }

    def _plan_retryable_steps_parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "plan_id": {"type": "string"},
            },
            "required": ["plan_id"],
        }

    def _plan_retry_step_parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "plan_id": {"type": "string"},
                "step_id": {"type": "string"},
            },
            "required": ["plan_id", "step_id"],
        }

    def _plan_record_evidence_parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "plan_id": {"type": "string"},
                "step_ids": {"type": "array", "items": {"type": "string"}},
                "kind": {"type": "string", "enum": list(EVIDENCE_KINDS)},
                "summary": {"type": "string"},
                "uri": {"type": "string"},
                "producer_agent_id": {"type": "string"},
                "metadata": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                },
            },
            "required": ["plan_id", "kind", "summary"],
        }

    def _plan_complete_step_parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "plan_id": {"type": "string"},
                "step_id": {"type": "string"},
                "evidence_ids": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["plan_id", "step_id"],
        }

    def _plan_status_parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "plan_id": {"type": "string"},
            },
        }
