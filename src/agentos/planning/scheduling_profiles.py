from __future__ import annotations

from dataclasses import dataclass

from agentos.planning.scheduling_reports import (
    PlanClaimedSchedulerTickReport,
    PlanClaimSweepReport,
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


__all__ = [
    "PLANNER_SCHEDULER_GOVERNANCE_REQUIRED_COMPONENTS",
    "PLANNER_STALE_CLAIM_SWEEP_REQUIRED_COMPONENTS",
    "PLANNER_WORKER_DISPATCH_SUPERVISION_REQUIRED_COMPONENTS",
    "PlannerSchedulerGovernanceDeploymentProfile",
    "PlannerStaleClaimSweepProfile",
    "PlannerWorkerDispatchSupervisionProfile",
]
