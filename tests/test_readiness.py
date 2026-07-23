from __future__ import annotations

import pytest

from agentos.readiness import (
    REQUIRED_READINESS_DIMENSIONS,
    AgentFormReadiness,
    ReadinessEvidenceCheck,
    ProductionReadinessEvidenceBundle,
    get_agent_form_readiness,
    list_agent_form_readiness,
)


def test_readiness_matrix_covers_initial_agent_forms() -> None:
    forms = {form.form_id: form for form in list_agent_form_readiness()}

    assert set(forms) == {
        "terminal-script",
        "async-web-host",
        "web-distributed-session",
        "a2a-discovery",
        "team-discussion",
        "planner-intent-router",
    }
    for form in forms.values():
        assert isinstance(form, AgentFormReadiness)
        assert set(form.dimensions) == set(REQUIRED_READINESS_DIMENSIONS)
        for dimension in form.dimensions.values():
            assert dimension.evidence
            if dimension.level not in {"direct", "not-applicable"}:
                assert dimension.gap


def test_terminal_script_is_direct_with_no_required_app_glue() -> None:
    form = get_agent_form_readiness("terminal-script")

    assert form.overall_level == "direct"
    assert form.recommended_profile == "LocalRuntimeProfile"
    assert form.required_app_glue == ()
    assert form.dimensions["session_state"].level == "direct"
    assert form.dimensions["auth"].level == "not-applicable"


def test_readiness_uses_single_query_loop_vocabulary() -> None:
    form = get_agent_form_readiness("async-web-host")

    assert "Canonical HTTP/SSE" in form.summary
    assert form.dimensions["concurrency"].evidence == (
        "DistributedAsgiApp",
        "ChannelServices",
    )


def test_web_distributed_session_names_canonical_runtime_and_state_plane() -> None:
    form = get_agent_form_readiness("web-distributed-session")

    assert form.overall_level == "primitives-ready"
    assert form.recommended_profile == "DistributedRuntimeProfile"
    assert "DistributedRuntimeProfile" in (
        form.dimensions["session_state"].evidence
    )
    assert "ProductionStatePlaneDeploymentProfile" in (
        form.dimensions["session_state"].evidence
    )
    assert "ProductionStatePlaneDeploymentProfile" in (
        form.dimensions["concurrency"].evidence
    )
    assert "Redis delivery" in " ".join(form.required_app_glue)
    assert "PostgreSQL migration" in " ".join(form.required_app_glue)
    assert form.dimensions["session_state"].level == "primitives-ready"
    assert "PostgresStateStore" in form.dimensions["session_state"].evidence
    assert "RedisQueueAdapter" in form.dimensions["concurrency"].evidence
    assert "claim" in form.dimensions["concurrency"].gap
    assert "live backend verification" in form.dimensions["persistence"].gap


def test_a2a_discovery_describes_canonical_distributed_boundaries() -> None:
    form = get_agent_form_readiness("a2a-discovery")

    assert form.overall_level == "primitives-ready"
    assert form.recommended_profile == "DistributedRuntimeProfile"
    assert {
        "A2AEndpoint",
        "A2ATaskService",
        "A2ATaskCatalogService",
        "A2APushService",
        "A2APushWorker",
    } <= set(form.dimensions["protocol"].evidence)
    assert "External certification" in form.dimensions["protocol"].gap

    assert {
        "ChannelAuthenticator",
        "A2AAgentCardProvider",
        "A2APushUrlPolicy",
    } <= set(form.dimensions["auth"].evidence)
    assert "Credential issuance" in form.dimensions["auth"].gap
    assert "egress enforcement" in form.dimensions["auth"].gap

    assert form.dimensions["persistence"].evidence == (
        "PostgresA2ATaskStore",
        "PostgresA2ATaskCatalogStore",
        "PostgresA2APushStore",
        "PostgresA2APushDeliveryStore",
    )
    assert "discovery storage" in form.dimensions["persistence"].gap
    assert "A2APushWorker process supervision and retry policy" in (
        form.required_app_glue
    )
    assert "external A2A conformance" in " ".join(form.required_app_glue)

    assert form.dimensions["rate_limit"].evidence == (
        "DistributedAsgiApp request limits",
    )
    assert "distributed quotas" in form.dimensions["rate_limit"].gap
    assert form.dimensions["workspace"].level == "not-applicable"
    assert "2026-07-20-postgres-distributed-runtime.sql" in (
        form.dimensions["schema_migration"].evidence
    )

def test_team_and_planner_gaps_are_explicit() -> None:
    team = get_agent_form_readiness("team-discussion")
    planner = get_agent_form_readiness("planner-intent-router")

    assert team.overall_level == "primitives-ready"
    assert team.recommended_profile == "DistributedRuntimeProfile"
    assert "team delivery runner hosting" in team.required_app_glue
    assert "team event relay hosting" in team.required_app_glue
    assert "TeamRuntime" in team.dimensions["session_state"].evidence
    assert "TeamApplicationPort" in team.dimensions["session_state"].evidence
    assert "TeamDeliveryRunner" in team.dimensions["concurrency"].evidence
    assert "TeamEventDeliveryRunner" in team.dimensions["concurrency"].evidence
    assert "RedisQueueAdapter" in team.dimensions["concurrency"].evidence
    assert "ProductionStatePlaneDeploymentProfile" in (
        team.dimensions["concurrency"].evidence
    )
    assert "TeamRuntime.validate_member_workspace" in (
        team.dimensions["workspace"].evidence
    )
    assert "TeamWorkspaceAuthorityPort" in team.dimensions["workspace"].evidence
    assert "WorkspaceToolSandboxPolicy" in team.dimensions["workspace"].evidence
    assert "os/container" in team.dimensions["workspace"].gap.lower()
    assert "TeamTools" in team.dimensions["protocol"].evidence
    assert "TeamEventReplayPort" in team.dimensions["protocol"].evidence
    assert "RedisTeamEventReplayAdapter" in team.dimensions["protocol"].evidence
    assert "application-owned" in team.dimensions["protocol"].gap
    assert "PostgresTeamStore" in team.dimensions["persistence"].evidence
    assert "RedisTeamEventReplayAdapter" in team.dimensions["persistence"].evidence
    assert team.dimensions["schema_migration"].level == "primitives-ready"
    assert "2026-07-20-postgres-distributed-runtime.sql" in (
        team.dimensions["schema_migration"].evidence
    )

    assert planner.overall_level == "primitives-ready"
    assert planner.recommended_profile == "DurableRuntimeProfile"
    assert "automatic LLM decomposition policy" in " ".join(
        planner.required_app_glue
    )
    assert "production PlanStore and PlanClaimStore adapters" in " ".join(
        planner.required_app_glue
    )
    assert "PlanDecomposition" in planner.dimensions["session_state"].evidence
    assert "InMemoryPlanStore" in planner.dimensions["session_state"].evidence
    assert "SQLitePlanStore" in planner.dimensions["session_state"].evidence
    assert "DurableRuntimeProfile" in planner.dimensions["session_state"].evidence
    assert "plan_create_from_decomposition" in (
        planner.dimensions["protocol"].evidence
    )
    assert "PlanRetryPolicy" in planner.dimensions["concurrency"].evidence
    assert "PlanClaimStore" in planner.dimensions["concurrency"].evidence
    assert "InMemoryPlanClaimStore" in planner.dimensions["concurrency"].evidence
    assert "plan claim/lease boundary" in (
        planner.dimensions["concurrency"].evidence
    )
    assert "plan_dispatch_ready_steps" in planner.dimensions["protocol"].evidence
    assert "PlanDispatchReport" in planner.dimensions["concurrency"].evidence
    assert "PlannerClaimedSchedulerDaemon" in (
        planner.dimensions["concurrency"].evidence
    )
    assert "PlannerWorkerDispatchSupervisionProfile" in (
        planner.dimensions["concurrency"].evidence
    )
    assert "PlanClaimSweepReport" in planner.dimensions["concurrency"].evidence
    assert "PlanClaimSweepStore" in planner.dimensions["concurrency"].evidence
    assert "PlannerStaleClaimSweepProfile" in (
        planner.dimensions["concurrency"].evidence
    )
    assert "PlannerRuntime.sweep_expired_claims" in (
        planner.dimensions["protocol"].evidence
    )
    assert "PlannerSchedulerDaemon" in planner.dimensions["concurrency"].evidence
    assert "plan_scheduler_tick" in planner.dimensions["protocol"].evidence
    assert "worker dispatch loop execution" in " ".join(planner.required_app_glue)
    assert "distributed scheduler locks" in " ".join(planner.required_app_glue)
    assert "stale claim sweep scheduling policy" in " ".join(
        planner.required_app_glue
    )
    assert "tenant routing" in planner.dimensions["concurrency"].gap
    assert "global fairness" in planner.dimensions["concurrency"].gap
    assert "Postgres-backed" not in planner.dimensions["concurrency"].gap
    assert "Production distributed PlanStore and PlanClaimStore adapters" in (
        planner.dimensions["concurrency"].gap
    )
    assert "SQLitePlanStore" in planner.dimensions["persistence"].evidence
    assert "InMemoryPlanClaimStore" in planner.dimensions["persistence"].evidence
    assert "distributed PlanStore" in planner.dimensions["persistence"].gap
    assert planner.dimensions["schema_migration"].level == "primitives-ready"
    assert "SQLitePlanStore schema initialization" in (
        planner.dimensions["schema_migration"].evidence
    )


def test_workspace_execution_isolation_profile_marks_deployment_boundary() -> None:
    for form_id in [
        "terminal-script",
        "async-web-host",
        "web-distributed-session",
        "team-discussion",
        "planner-intent-router",
    ]:
        workspace = get_agent_form_readiness(form_id).dimensions["workspace"]

        assert "WorkspaceExecutionIsolationProfile" in workspace.evidence
        assert "WorkspaceExecutionBackend" in workspace.evidence
        assert "LocalWorkspaceExecutionBackend" in workspace.evidence
        assert "SandboxBackend" in workspace.evidence
        assert "OS/container sandboxing" in workspace.gap
        assert "deployment-owned" in workspace.gap


def test_unknown_agent_form_raises_key_error() -> None:
    with pytest.raises(KeyError):
        get_agent_form_readiness("unknown-form")


def test_production_readiness_evidence_bundle_blocks_missing_required_checks() -> None:
    bundle = ProductionReadinessEvidenceBundle.from_sources(
        {
            "channel_services": {"ok": True, "component": "ChannelServices"},
        },
        required_checks=(
            "channel_services",
            "deployment_live_backend_verification",
        ),
    )

    assert bundle.accepted is False
    assert bundle.missing_required_checks == (
        "deployment_live_backend_verification",
    )
    assert bundle.blocking_checks == ()
    payload = bundle.as_dict()
    assert payload["status"] == "failed"
    assert payload["block_production_readiness"] is True
    assert payload["missing_required_checks"] == (
        "deployment_live_backend_verification",
    )
    assert "ProductionReadinessEvidenceBundle" in payload["sdk_owned"]


def test_production_readiness_evidence_bundle_normalizes_existing_payload_shapes() -> None:
    bundle = ProductionReadinessEvidenceBundle.from_sources(
        {
            "state_plane": {"ready": True, "profile": "state-plane"},
            "backend_verification": {
                "accepted": False,
                "block_production_readiness": True,
                "failed_backends": ("message_queue",),
            },
            "planner_governance": {
                "status": "ok",
                "block_plan_creation": False,
            },
            "optional_audit": {"status": "failed", "required": False},
        },
        required_checks=(
            "state_plane",
            "backend_verification",
            "planner_governance",
        ),
    )

    assert bundle.accepted is False
    assert bundle.missing_required_checks == ()
    assert bundle.blocking_checks == ("backend_verification",)
    assert bundle.checks_by_name["state_plane"].status == "passed"
    assert bundle.checks_by_name["backend_verification"].status == "failed"
    assert bundle.checks_by_name["planner_governance"].status == "passed"
    assert bundle.checks_by_name["optional_audit"].status == "failed"
    assert bundle.checks_by_name["optional_audit"].required is False


def test_production_readiness_evidence_bundle_consumes_objects_and_callables() -> None:
    class Profile:
        probe_name = "profile_probe"

        def readiness_check(self) -> dict[str, object]:
            return {"ok": True, "metadata": {"source": "profile"}}

    bundle = ProductionReadinessEvidenceBundle.from_sources(
        {
            "callable_probe": lambda: {"status": "ok"},
            "profile_probe": Profile(),
        },
        required_checks=("callable_probe", "profile_probe"),
    )

    assert bundle.accepted is True
    assert bundle.blocking_checks == ()
    assert bundle.missing_required_checks == ()
    assert bundle.as_dict()["status"] == "ok"


def test_production_readiness_evidence_bundle_invokes_callable_source_once() -> None:
    calls = 0

    def readiness_source() -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"ok": True}

    ProductionReadinessEvidenceBundle.from_sources(
        {"callable_probe": readiness_source},
        required_checks=("callable_probe",),
    )

    assert calls == 1


def test_readiness_evidence_check_redacts_secret_like_metadata_and_evidence() -> None:
    check = ReadinessEvidenceCheck.from_payload(
        "secret_probe",
        {
            "ok": True,
            "metadata": {"api_token": "secret-token"},
            "nested": {"password": "secret-password"},
        },
    )
    payload = check.as_dict()

    assert payload["status"] == "passed"
    assert payload["evidence"]["metadata"]["api_token"] == "<redacted>"
    assert payload["evidence"]["nested"]["password"] == "<redacted>"
    assert "secret-token" not in repr(payload)
    assert "secret-password" not in repr(payload)
