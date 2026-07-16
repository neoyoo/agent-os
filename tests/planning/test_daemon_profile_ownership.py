from __future__ import annotations

from pathlib import Path


def test_daemon_and_profile_types_have_single_planning_identity() -> None:
    import agentos.planning as planning
    from agentos.planning import daemons, profiles, scheduling_profiles

    names_by_module = {
        daemons: (
            "PlannerClaimedSchedulerDaemon",
            "PlannerClaimedSchedulerDaemonError",
            "PlannerClaimedSchedulerDaemonState",
            "PlannerClaimedSchedulerDaemonStatus",
            "PlannerSchedulerDaemon",
            "PlannerSchedulerDaemonError",
            "PlannerSchedulerDaemonState",
            "PlannerSchedulerDaemonStatus",
        ),
        profiles: (
            "PlannerDecompositionPolicyDeploymentProfile",
            "PlannerLlmDecompositionGovernanceProfile",
            "PlannerOrchestrationDeploymentProfile",
        ),
        scheduling_profiles: (
            "PlannerSchedulerGovernanceDeploymentProfile",
            "PlannerStaleClaimSweepProfile",
            "PlannerWorkerDispatchSupervisionProfile",
        ),
    }

    for module, names in names_by_module.items():
        for name in names:
            assert getattr(planning, name) is getattr(module, name)


def test_daemon_and_profile_modules_do_not_depend_on_multi() -> None:
    project_root = Path(__file__).resolve().parents[2]

    for filename in ("daemons.py", "profiles.py", "scheduling_profiles.py"):
        text = (
            project_root / "src" / "agentos" / "planning" / filename
        ).read_text(encoding="utf-8")
        assert "agentos.multi" not in text
