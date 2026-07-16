from __future__ import annotations

from pathlib import Path

import pytest

from agentos.planning import (
    PLANNER_SCHEDULER_GOVERNANCE_REQUIRED_COMPONENTS,
    PlannerSchedulerGovernanceDeploymentProfile,
)


def test_planner_scheduler_governance_profile_reports_missing_defaults() -> None:
    profile = PlannerSchedulerGovernanceDeploymentProfile()

    metadata = profile.readiness_metadata()
    readiness = profile.readiness_check()

    assert metadata["profile"] == "PlannerSchedulerGovernanceDeploymentProfile"
    assert metadata["probe_name"] == "planner_scheduler_governance"
    assert metadata["ready"] is False
    assert metadata["required_components"] == (
        PLANNER_SCHEDULER_GOVERNANCE_REQUIRED_COMPONENTS
    )
    assert metadata["configured_components"] == ()
    assert metadata["missing_components"] == (
        PLANNER_SCHEDULER_GOVERNANCE_REQUIRED_COMPONENTS
    )
    assert "PlannerClaimedSchedulerDaemon" in metadata["sdk_owned"]
    assert "PlannerRuntime.schedulable_plans" in metadata["sdk_owned"]
    assert "tenant routing policy" in metadata["deployment_owned"]
    assert "global fairness policy" in metadata["deployment_owned"]
    assert "distributed scheduler locks" in metadata["deployment_owned"]
    assert readiness["ok"] is False
    assert readiness["status"] == "failed"


def test_planner_scheduler_governance_profile_reports_ready_when_configured() -> None:
    profile = PlannerSchedulerGovernanceDeploymentProfile(
        configured_components=PLANNER_SCHEDULER_GOVERNANCE_REQUIRED_COMPONENTS,
    )

    metadata = profile.readiness_metadata()
    readiness = profile.readiness_check()

    assert profile.missing_components() == ()
    assert metadata["ready"] is True
    assert metadata["missing_components"] == ()
    assert readiness["ok"] is True
    assert readiness["status"] == "ok"


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"probe_name": " "}, "probe_name"),
        ({"required_components": ()}, "required_components"),
        ({"configured_components": ("tenant_routing_policy", "")}, "configured_components"),
        ({"required_components": ("plan_discovery_policy", " ")}, "required_components"),
    ],
)
def test_planner_scheduler_governance_profile_rejects_invalid_configuration(
    kwargs: dict[str, object],
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        PlannerSchedulerGovernanceDeploymentProfile(**kwargs)


def test_runtime_execution_core_does_not_import_planner_scheduler_governance_profile() -> None:
    project_root = Path(__file__).resolve().parents[2]

    for path in [
        project_root / "src" / "agentos" / "runtime" / "query_loop.py",
        project_root / "src" / "agentos" / "runtime" / "provider_attempt.py",
    ]:
        text = path.read_text(encoding="utf-8")
        assert "PlannerSchedulerGovernanceDeploymentProfile" not in text
        assert "PLANNER_SCHEDULER_GOVERNANCE_REQUIRED_COMPONENTS" not in text
