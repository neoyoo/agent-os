from __future__ import annotations

import pytest

from agentos.planning import (
    PlanClaimRecord,
    PlanClaimResult,
    PlannerStaleClaimSweepProfile,
    PlannerWorkerDispatchSupervisionProfile,
)
from agentos.planning.scheduling_reports import (
    PlanClaimedSchedulerTickReport,
    PlanClaimedSchedulerTickSkip,
    PlanClaimSweepReport,
    PlanClaimSweepSkip,
    PlanSchedulerTickReport,
)


def test_planner_worker_dispatch_supervision_profile_reports_missing_components() -> None:
    profile = PlannerWorkerDispatchSupervisionProfile()

    metadata = profile.readiness_metadata()
    readiness = profile.readiness_check()
    health = profile.health_payload()

    assert metadata["profile"] == "PlannerWorkerDispatchSupervisionProfile"
    assert metadata["ready"] is False
    assert metadata["configured_components"] == ()
    assert set(metadata["missing_components"]) == {
        "claimed_scheduler_tick_loop",
        "worker_process_lifecycle",
        "plan_claim_store",
        "scheduler_lock_policy",
        "stale_lease_recovery",
        "compensation_policy",
        "metrics_alerting",
        "live_backend_verification",
    }
    assert "PlanClaimedSchedulerTickReport" in metadata["sdk_owned"]
    assert "process supervisor or job runner" in metadata["deployment_owned"]
    assert health["status"] == "unstarted"
    assert health["ok"] is False
    assert readiness["status"] == "failed"
    assert readiness["ok"] is False


def test_planner_worker_dispatch_supervision_profile_summarizes_dispatch_reports() -> None:
    profile = PlannerWorkerDispatchSupervisionProfile(
        reports=(
            PlanClaimedSchedulerTickReport(
                worker_id="scheduler_a",
                claims=(
                    PlanClaimResult(status="claimed"),
                    PlanClaimResult(status="busy"),
                ),
                tick_reports=(
                    PlanSchedulerTickReport(plan_id="plan_1"),
                ),
                skipped=(
                    PlanClaimedSchedulerTickSkip(
                        plan_id="busy_plan",
                        reason="busy",
                    ),
                ),
                released_plan_ids=("plan_1",),
            ),
        ),
        configured_components=(
            "claimed_scheduler_tick_loop",
            "worker_process_lifecycle",
            "plan_claim_store",
            "scheduler_lock_policy",
            "stale_lease_recovery",
            "compensation_policy",
            "metrics_alerting",
            "live_backend_verification",
        ),
    )

    health = profile.health_payload()
    readiness = profile.readiness_check()

    assert health["status"] == "healthy"
    assert health["ok"] is True
    assert health["report_count"] == 1
    assert health["last_worker_id"] == "scheduler_a"
    assert health["tick_report_count"] == 1
    assert health["claim_count"] == 2
    assert health["claimed_count"] == 1
    assert health["busy_count"] == 1
    assert health["tick_failed_count"] == 0
    assert health["released_count"] == 1
    assert health["consecutive_failed_batches"] == 0
    assert readiness["status"] == "ok"
    assert readiness["ok"] is True
    assert readiness["health_status"] == "healthy"


def test_planner_worker_dispatch_supervision_profile_fails_on_consecutive_tick_failures() -> None:
    profile = PlannerWorkerDispatchSupervisionProfile(
        reports=(
            PlanClaimedSchedulerTickReport(
                worker_id="scheduler_a",
                claims=(PlanClaimResult(status="claimed"),),
                skipped=(
                    PlanClaimedSchedulerTickSkip(
                        plan_id="plan_1",
                        reason="tick-failed",
                        detail="missing template",
                    ),
                ),
            ),
            PlanClaimedSchedulerTickReport(
                worker_id="scheduler_a",
                claims=(PlanClaimResult(status="claimed"),),
                skipped=(
                    PlanClaimedSchedulerTickSkip(
                        plan_id="plan_2",
                        reason="tick-failed",
                        detail="coordinator unavailable",
                    ),
                ),
            ),
        ),
        configured_components=(
            "claimed_scheduler_tick_loop",
            "worker_process_lifecycle",
            "plan_claim_store",
            "scheduler_lock_policy",
            "stale_lease_recovery",
            "compensation_policy",
            "metrics_alerting",
            "live_backend_verification",
        ),
        max_consecutive_failed_batches=1,
    )

    health = profile.health_payload()
    readiness = profile.readiness_check()

    assert health["status"] == "unhealthy"
    assert health["ok"] is False
    assert health["tick_failed_count"] == 2
    assert health["consecutive_failed_batches"] == 2
    assert health["last_tick_failed_plan_ids"] == ("plan_2",)
    assert readiness["status"] == "failed"
    assert readiness["health_status"] == "unhealthy"
    assert readiness["ok"] is False


def test_planner_worker_dispatch_supervision_profile_rejects_invalid_configuration() -> None:
    with pytest.raises(ValueError, match="probe_name"):
        PlannerWorkerDispatchSupervisionProfile(probe_name=" ")
    with pytest.raises(ValueError, match="configured_components"):
        PlannerWorkerDispatchSupervisionProfile(configured_components=("",))
    with pytest.raises(ValueError, match="max_consecutive_failed_batches"):
        PlannerWorkerDispatchSupervisionProfile(max_consecutive_failed_batches=-1)


def test_planner_stale_claim_sweep_profile_reports_missing_components() -> None:
    profile = PlannerStaleClaimSweepProfile()

    metadata = profile.readiness_metadata()
    readiness = profile.readiness_check()
    health = profile.health_payload()

    assert metadata["profile"] == "PlannerStaleClaimSweepProfile"
    assert metadata["ready"] is False
    assert metadata["configured_components"] == ()
    assert set(metadata["missing_components"]) == {
        "stale_claim_sweep_schedule",
        "plan_claim_store",
        "scheduler_lock_policy",
        "sweep_safety_window",
        "metrics_alerting",
        "live_backend_verification",
    }
    assert "PlanClaimSweepReport" in metadata["sdk_owned"]
    assert "cron or scheduler" in metadata["deployment_owned"]
    assert health["status"] == "unstarted"
    assert health["ok"] is False
    assert readiness["status"] == "failed"
    assert readiness["ok"] is False


def test_planner_stale_claim_sweep_profile_summarizes_reports() -> None:
    report = PlanClaimSweepReport(
        checked_claims=(
            PlanClaimRecord(
                plan_id="expired_plan",
                owner_agent_id="leader",
                worker_id="scheduler_a",
                claimed_at=10.0,
                lease_expires_at=20.0,
                generation=1,
            ),
        ),
        released_claims=(
            PlanClaimRecord(
                plan_id="expired_plan",
                owner_agent_id="leader",
                worker_id="scheduler_a",
                claimed_at=10.0,
                lease_expires_at=20.0,
                generation=1,
            ),
        ),
        dry_run=False,
        now=30.0,
    )
    profile = PlannerStaleClaimSweepProfile(
        reports=(report,),
        configured_components=(
            "stale_claim_sweep_schedule",
            "plan_claim_store",
            "scheduler_lock_policy",
            "sweep_safety_window",
            "metrics_alerting",
            "live_backend_verification",
        ),
    )

    health = profile.health_payload()
    readiness = profile.readiness_check()

    assert health["status"] == "healthy"
    assert health["ok"] is True
    assert health["report_count"] == 1
    assert health["checked_count"] == 1
    assert health["released_count"] == 1
    assert health["skipped_count"] == 0
    assert health["last_released_plan_ids"] == ("expired_plan",)
    assert readiness["status"] == "ok"
    assert readiness["ok"] is True


def test_planner_stale_claim_sweep_profile_degrades_on_skips() -> None:
    report = PlanClaimSweepReport(
        checked_claims=(
            PlanClaimRecord(
                plan_id="expired_plan",
                owner_agent_id="leader",
                worker_id="scheduler_a",
                claimed_at=10.0,
                lease_expires_at=20.0,
                generation=1,
            ),
        ),
        skipped_claims=(
            PlanClaimSweepSkip(
                plan_id="expired_plan",
                reason="release-race",
                detail="claim changed before release",
            ),
        ),
        dry_run=False,
        now=30.0,
    )
    profile = PlannerStaleClaimSweepProfile(
        reports=(report,),
        configured_components=(
            "stale_claim_sweep_schedule",
            "plan_claim_store",
            "scheduler_lock_policy",
            "sweep_safety_window",
            "metrics_alerting",
            "live_backend_verification",
        ),
    )

    health = profile.health_payload()
    readiness = profile.readiness_check()

    assert health["status"] == "degraded"
    assert health["ok"] is False
    assert health["skipped_count"] == 1
    assert health["last_skipped_plan_ids"] == ("expired_plan",)
    assert readiness["status"] == "failed"
    assert readiness["health_status"] == "degraded"


def test_planner_stale_claim_sweep_profile_rejects_invalid_configuration() -> None:
    with pytest.raises(ValueError, match="probe_name"):
        PlannerStaleClaimSweepProfile(probe_name=" ")
    with pytest.raises(ValueError, match="configured_components"):
        PlannerStaleClaimSweepProfile(configured_components=("",))
