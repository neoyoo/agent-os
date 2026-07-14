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

    assert form.summary == "QueryLoop and ASGI/SSE primitives for a single host."
    assert form.dimensions["concurrency"].evidence == ("QueryLoop", "AsgiAgentApp")


def test_web_distributed_session_names_lease_and_snapshot_gap() -> None:
    form = get_agent_form_readiness("web-distributed-session")

    assert form.overall_level == "primitives-ready"
    assert form.recommended_profile == "DistributedWebRuntimeProfile"
    assert "DistributedWebSessionOperationsProfile" in (
        form.dimensions["session_state"].evidence
    )
    assert "ProductionStatePlaneDeploymentProfile" in (
        form.dimensions["session_state"].evidence
    )
    assert "ProductionStatePlaneDeploymentProfile" in (
        form.dimensions["concurrency"].evidence
    )
    assert "distributed lease" in " ".join(form.required_app_glue)
    assert "snapshot" in " ".join(form.required_app_glue)
    assert form.dimensions["session_state"].level == "primitives-ready"
    assert "DistributedWebRuntimeProfile" in form.dimensions["session_state"].evidence
    assert "lease" in form.dimensions["concurrency"].gap
    assert "stale lease recovery" in form.dimensions["concurrency"].gap
    assert "live backend verification" in form.dimensions["persistence"].gap


def test_a2a_discovery_does_not_claim_operation_parity() -> None:
    form = get_agent_form_readiness("a2a-discovery")

    assert form.overall_level == "primitives-ready"
    assert form.dimensions["protocol"].level == "primitives-ready"
    assert "message/send route" in form.dimensions["protocol"].evidence
    assert "A2AAgentSkill input/output modes" in form.dimensions["protocol"].evidence
    assert "A2AAgentInterface" in form.dimensions["protocol"].evidence
    assert "A2AAgentExtension" in form.dimensions["protocol"].evidence
    assert "A2AProtocolVersionPolicy" in form.dimensions["protocol"].evidence
    assert "A2A-Version header" in form.dimensions["protocol"].evidence
    assert "A2A 1.0 text part payload shape" in (
        form.dimensions["protocol"].evidence
    )
    assert "A2A 1.0 file/data part payload shape" in (
        form.dimensions["protocol"].evidence
    )
    assert "A2A artifact payload shape" in form.dimensions["protocol"].evidence
    assert "A2A status/artifact event wrapper shape" in (
        form.dimensions["protocol"].evidence
    )
    assert "A2AExtensionNegotiationPolicy" in form.dimensions["protocol"].evidence
    assert "A2A-Extensions header" in form.dimensions["protocol"].evidence
    assert "A2AConformanceHarness" in form.dimensions["protocol"].evidence
    assert "message/stream self-conformance check" in (
        form.dimensions["protocol"].evidence
    )
    assert "message stream event self-conformance check" in (
        form.dimensions["protocol"].evidence
    )
    assert "tasks/resubscribe self-conformance check" in (
        form.dimensions["protocol"].evidence
    )
    assert "task resubscribe event self-conformance check" in (
        form.dimensions["protocol"].evidence
    )
    assert "A2AExternalConformanceReportImporter" in (
        form.dimensions["protocol"].evidence
    )
    assert "A2AExternalConformanceExecutionProfile" in (
        form.dimensions["protocol"].evidence
    )
    assert "A2AExternalConformanceExecutionRecord" in (
        form.dimensions["protocol"].evidence
    )
    assert "A2AExternalConformanceGateReport" in (
        form.dimensions["protocol"].evidence
    )
    assert "A2AExternalConformanceInvocationPlan" in (
        form.dimensions["protocol"].evidence
    )
    assert "A2AExternalConformanceInvocationGateReport" in (
        form.dimensions["protocol"].evidence
    )
    assert "PublicHttpsA2AEgressUrlPolicy" in form.dimensions["protocol"].evidence
    assert "HostAllowListA2AEgressUrlPolicy" in form.dimensions["protocol"].evidence
    assert "RejectAllA2AInboundAuthPolicy" in form.dimensions["protocol"].evidence
    assert "default inbound A2A operation auth is fail-closed" in (
        form.dimensions["protocol"].evidence
    )
    assert "AllowAllA2AInboundAuthPolicy is explicit local/dev opt-in" in (
        form.dimensions["protocol"].evidence
    )
    assert "A2AOperationClient default public HTTPS egress policy" in (
        form.dimensions["protocol"].evidence
    )
    assert "SDK A2A self-conformance report" in (
        form.dimensions["schema_migration"].evidence
    )
    assert "external A2A conformance result import" in (
        form.dimensions["schema_migration"].evidence
    )
    assert "external A2A conformance execution profile" in (
        form.dimensions["schema_migration"].evidence
    )
    assert "external conformance execution record" in (
        form.dimensions["schema_migration"].evidence
    )
    assert "external conformance gate report" in (
        form.dimensions["schema_migration"].evidence
    )
    assert "external conformance invocation plan" in (
        form.dimensions["schema_migration"].evidence
    )
    assert "external conformance invocation gate report" in (
        form.dimensions["schema_migration"].evidence
    )
    assert "task get/cancel routes" in form.dimensions["protocol"].evidence
    assert "message/stream route" in form.dimensions["protocol"].evidence
    assert "message stream operation boundary" in (
        form.dimensions["protocol"].evidence
    )
    assert "A2AOperationClient.stream_message" in (
        form.dimensions["protocol"].evidence
    )
    assert "A2AOperationClient.stream_message_events" in (
        form.dimensions["protocol"].evidence
    )
    assert "A2AMessageStreamEvent" in form.dimensions["protocol"].evidence
    assert "parse_a2a_sse_events" in form.dimensions["protocol"].evidence
    assert "SSE client consumption" not in form.dimensions["protocol"].gap
    assert "backpressure" in form.dimensions["protocol"].gap
    assert "durable" in form.dimensions["protocol"].gap
    assert "cursor" in form.dimensions["protocol"].gap
    assert "task subscribe route" in form.dimensions["protocol"].evidence
    assert "tasks/resubscribe operation" in form.dimensions["protocol"].evidence
    assert "A2AOperationClient.task_resubscribe" in (
        form.dimensions["protocol"].evidence
    )
    assert "task subscribe operation boundary" in (
        form.dimensions["protocol"].evidence
    )
    assert "push notification config routes" in form.dimensions["protocol"].evidence
    assert "A2APushNotificationDispatcher" in form.dimensions["protocol"].evidence
    assert "A2APushNotificationDeliveryWorker" in form.dimensions["protocol"].evidence
    assert "A2APushNotificationDaemon" in form.dimensions["protocol"].evidence
    assert "A2APushNotificationDeploymentProfile" in (
        form.dimensions["protocol"].evidence
    )
    assert "A2AStreamLifecycleDeploymentProfile" in (
        form.dimensions["protocol"].evidence
    )
    assert "A2APushNotificationHealthPolicy" in form.dimensions["protocol"].evidence
    assert "A2APushNotificationHealthReport" in form.dimensions["protocol"].evidence
    assert "HostAllowListA2APushNotificationUrlPolicy" in (
        form.dimensions["protocol"].evidence
    )
    assert "InMemoryA2APushNotificationDeliveryStore" in (
        form.dimensions["persistence"].evidence
    )
    assert "PostgresA2APushNotificationDeliveryStore" in (
        form.dimensions["persistence"].evidence
    )
    assert "InMemoryA2APushNotificationConfigStore" in (
        form.dimensions["persistence"].evidence
    )
    assert "NacosAgentRegistryAdapter" in form.dimensions["persistence"].evidence
    assert "NacosAgentCardResolver" in form.dimensions["persistence"].evidence
    assert "NacosRegistryClient" in form.dimensions["persistence"].evidence
    assert "Nacos namespace_id evidence" in form.dimensions["persistence"].evidence
    assert "discovery-only Nacos metadata" in (
        form.dimensions["persistence"].evidence
    )
    assert "Nacos is not task/plan/session/queue/worker runtime truth" in (
        form.dimensions["persistence"].gap
    )
    assert "durable webhook delivery queue" not in " ".join(form.required_app_glue)
    assert "production scheduler" not in " ".join(form.required_app_glue)
    assert "deployment process supervision" in " ".join(form.required_app_glue)
    assert "worker monitoring" not in " ".join(form.required_app_glue)
    assert "worker alerting and restart supervision" in " ".join(
        form.required_app_glue
    )
    assert "SSRF allow-list policy" not in " ".join(form.required_app_glue)
    assert "DNS pinning and egress proxy policy" in " ".join(form.required_app_glue)
    assert "tenant directory and role assignment lifecycle policy" in " ".join(
        form.required_app_glue
    )
    assert "credential issuance and secret distribution policy" in " ".join(
        form.required_app_glue
    )
    assert "credential rotation policy" not in " ".join(form.required_app_glue)
    assert "tenant RBAC mapping policy" not in " ".join(form.required_app_glue)
    assert "task resubscribe" not in form.dimensions["protocol"].gap
    assert "message stream" not in form.dimensions["protocol"].gap.lower()
    assert "streaming task updates" not in form.dimensions["protocol"].gap
    assert "retry/dead-letter scheduling" not in form.dimensions["protocol"].gap
    assert "url allow-list" not in form.dimensions["protocol"].gap.lower()
    assert "sdk url policy" in form.dimensions["protocol"].gap.lower()
    assert "push notification config" not in form.dimensions["protocol"].gap.lower()
    assert "version negotiation" not in form.dimensions["protocol"].gap.lower()
    assert "text part" not in form.dimensions["protocol"].gap.lower()
    assert "file/data" not in form.dimensions["protocol"].gap.lower()
    assert "artifact/event" not in form.dimensions["protocol"].gap.lower()
    assert "extension negotiation" not in form.dimensions["protocol"].gap.lower()
    assert "sdk self-conformance" not in form.dimensions["protocol"].gap.lower()
    assert "external a2a conformance suite selection/invocation" not in (
        form.dimensions["protocol"].gap.lower()
    )
    assert "external suite execution" in form.dimensions["protocol"].gap.lower()
    assert "certification attestation" in form.dimensions["protocol"].gap.lower()
    assert "sdk-owned external conformance suite execution" not in (
        form.dimensions["protocol"].gap.lower()
    )
    assert "push notification semantics" not in " ".join(form.required_app_glue)
    assert "complete skills/capabilities metadata" not in (
        form.dimensions["protocol"].gap.lower()
    )
    assert "HmacA2ACardVerifier" in form.dimensions["auth"].evidence
    assert "JwksA2ACardTrustStore" in form.dimensions["auth"].evidence
    assert "RotatingA2ACardTrustStore" in form.dimensions["auth"].evidence
    assert "RotatingHmacA2ACardSigner" in form.dimensions["auth"].evidence
    assert "StaticBearerA2AAuthProvider" in form.dimensions["auth"].evidence
    assert "StaticBearerA2AInboundAuthPolicy" in form.dimensions["auth"].evidence
    assert "A2ABearerCredential" in form.dimensions["auth"].evidence
    assert "RotatingBearerA2ACredentialStore" in (
        form.dimensions["auth"].evidence
    )
    assert "RotatingBearerA2AAuthProvider" in form.dimensions["auth"].evidence
    assert "RotatingBearerA2AInboundAuthPolicy" in (
        form.dimensions["auth"].evidence
    )
    assert "HmacA2AJwtVerifier" in form.dimensions["auth"].evidence
    assert "OidcDiscoveryMetadataProvider" in form.dimensions["auth"].evidence
    assert "JwksA2AJwtVerifier" in form.dimensions["auth"].evidence
    assert "PublicHttpsA2AEgressUrlPolicy" in form.dimensions["auth"].evidence
    assert "HostAllowListA2AEgressUrlPolicy" in form.dimensions["auth"].evidence
    assert "RejectAllA2AInboundAuthPolicy" in form.dimensions["auth"].evidence
    assert "OidcClaimsA2AInboundAuthPolicy" in form.dimensions["auth"].evidence
    assert "PeerAllowListA2AInboundAuthPolicy" in form.dimensions["auth"].evidence
    assert "OperationAllowListA2AInboundAuthPolicy" in (
        form.dimensions["auth"].evidence
    )
    assert "ResourceAllowListA2AInboundAuthPolicy" in (
        form.dimensions["auth"].evidence
    )
    assert "A2ATenantRbacRule" in form.dimensions["auth"].evidence
    assert "ClaimsTenantRbacA2AInboundAuthPolicy" in (
        form.dimensions["auth"].evidence
    )
    assert "claims-backed tenant RBAC policy" in form.dimensions["auth"].evidence
    assert "A2AOperationRateLimitPolicy" in form.dimensions["rate_limit"].evidence
    assert "PeerKeyA2AOperationRateLimitPolicy" in (
        form.dimensions["rate_limit"].evidence
    )
    assert "A2ARateLimitError" in form.dimensions["rate_limit"].evidence
    assert "per-peer a2a rate policy is app-owned" not in (
        form.dimensions["rate_limit"].gap.lower()
    )
    assert "distributed/global quota" in form.dimensions["rate_limit"].gap.lower()
    assert "gateway" in form.dimensions["rate_limit"].gap.lower()
    assert "billing" in form.dimensions["rate_limit"].gap.lower()
    assert "inbound auth enforcement" not in form.dimensions["auth"].gap.lower()
    assert "inbound peer authorization enforcement" not in " ".join(
        form.required_app_glue
    )
    assert "per-agent allow-list policy" not in " ".join(form.required_app_glue)
    assert "per-agent allow-list policy" not in form.dimensions["auth"].gap.lower()
    assert "per-method authorization policy" not in form.dimensions["auth"].gap.lower()
    assert "per-task/resource authorization policy" not in (
        form.dimensions["auth"].gap.lower()
    )
    assert "tenant rbac mapping" not in form.dimensions["auth"].gap.lower()
    assert "tenant directory" in form.dimensions["auth"].gap.lower()
    assert "role assignment lifecycle" in form.dimensions["auth"].gap.lower()
    assert "idp administration" in form.dimensions["auth"].gap.lower()
    assert "oidc discovery" not in form.dimensions["auth"].gap.lower()
    assert "oidc metadata" not in form.dimensions["auth"].gap.lower()
    assert "rs256/jwks jwt verification" not in form.dimensions["auth"].gap.lower()
    assert "public-key jwt verification" not in form.dimensions["auth"].gap.lower()
    assert "signed cards" not in form.dimensions["auth"].gap.lower()
    assert "trust stores" not in form.dimensions["auth"].gap.lower()
    assert "peer auth" not in form.dimensions["auth"].gap.lower()
    assert "jwt/oidc claims validation" not in form.dimensions["auth"].gap.lower()
    assert "hmac key rotation" not in form.dimensions["auth"].gap.lower()
    assert "credential rotation" not in form.dimensions["auth"].gap.lower()
    assert "credential issuance" in form.dimensions["auth"].gap.lower()
    assert "secret distribution" in form.dimensions["auth"].gap.lower()
    assert "kms" in form.dimensions["auth"].gap.lower()
    assert "ca trust" in form.dimensions["auth"].gap.lower()
    assert "sdk url policy" in form.dimensions["auth"].gap.lower()
    assert "full A2A" not in form.summary.lower()


def test_team_and_planner_gaps_are_explicit() -> None:
    team = get_agent_form_readiness("team-discussion")
    planner = get_agent_form_readiness("planner-intent-router")

    assert team.overall_level == "primitives-ready"
    assert team.recommended_profile == "DistributedTeamRuntimeProfile"
    assert "worker session lifecycle" not in " ".join(team.required_app_glue)
    assert "worker execution runner" not in " ".join(team.required_app_glue)
    assert "persistent retry store" not in " ".join(team.required_app_glue)
    assert "cancellation scheduling" not in " ".join(team.required_app_glue)
    assert "persistent cancellation" not in " ".join(team.required_app_glue)
    assert "distributed TeamStore" not in " ".join(team.required_app_glue)
    assert "team tools" not in " ".join(team.required_app_glue)
    assert "permission downgrade policy" not in " ".join(team.required_app_glue)
    assert "UI stream protocol" not in " ".join(team.required_app_glue)
    assert "distributed stream storage" not in " ".join(team.required_app_glue)
    assert "network endpoint" not in " ".join(team.required_app_glue)
    assert "live team UI SSE" not in " ".join(team.required_app_glue)
    assert "tool sandbox enforcement policy" not in " ".join(team.required_app_glue)
    assert "TeamWorkerSessionProvider" in team.dimensions["session_state"].evidence
    assert "DistributedTeamRuntimeProfile" in (
        team.dimensions["session_state"].evidence
    )
    assert "TeamWorkerRunner" in team.dimensions["concurrency"].evidence
    assert "TeamWorkerDaemon" in team.dimensions["concurrency"].evidence
    assert "WorkerProcessLifecycleDeploymentProfile" in (
        team.dimensions["concurrency"].evidence
    )
    assert "ProductionStatePlaneDeploymentProfile" in (
        team.dimensions["concurrency"].evidence
    )
    assert "DistributedTeamRuntimeProfile" in (
        team.dimensions["concurrency"].evidence
    )
    assert "TeamWorkerRetryPolicy" in team.dimensions["concurrency"].evidence
    assert "InMemoryTeamWorkerRetryStore" in team.dimensions["concurrency"].evidence
    assert "PostgresTeamWorkerRetryStore" in team.dimensions["concurrency"].evidence
    assert "TeamWorkerCancellationStore" in team.dimensions["concurrency"].evidence
    assert "InMemoryTeamWorkerCancellationStore" in team.dimensions["concurrency"].evidence
    assert "PostgresTeamWorkerCancellationStore" in team.dimensions["concurrency"].evidence
    assert "persistent cancellation" not in team.dimensions["concurrency"].gap.lower()
    assert "TeamWorkerPermissionPolicy" in team.dimensions["workspace"].evidence
    assert "WorkspaceToolSandboxPolicy" in team.dimensions["workspace"].evidence
    assert "os/container" in team.dimensions["workspace"].gap.lower()
    assert "TeamTools" in team.dimensions["protocol"].evidence
    assert "TeamUiEvent" in team.dimensions["protocol"].evidence
    assert "InMemoryTeamUiStreamStore" in team.dimensions["protocol"].evidence
    assert "PostgresTeamUiStreamStore" in team.dimensions["protocol"].evidence
    assert "team UI JSON replay endpoint" in team.dimensions["protocol"].evidence
    assert "team UI SSE/follow endpoint" in team.dimensions["protocol"].evidence
    assert "live sse" not in team.dimensions["protocol"].gap.lower()
    assert "PostgresTeamStore" in team.dimensions["persistence"].evidence
    assert "PostgresTeamUiStreamStore" in team.dimensions["persistence"].evidence
    assert team.dimensions["schema_migration"].level == "primitives-ready"
    assert planner.overall_level == "primitives-ready"
    assert "automatic LLM decomposition policy" in " ".join(
        planner.required_app_glue
    )
    assert "PlanDecomposition" in planner.dimensions["session_state"].evidence
    assert "PlanDecompositionGatePolicy" in (
        planner.dimensions["session_state"].evidence
    )
    assert "PlanDecompositionGateReport" in (
        planner.dimensions["session_state"].evidence
    )
    assert "PlanDecompositionValidationReport" in (
        planner.dimensions["session_state"].evidence
    )
    assert "PlannerRuntime.gate_decomposition_proposal" in (
        planner.dimensions["session_state"].evidence
    )
    assert "PlannerRuntime.validate_decomposition" in (
        planner.dimensions["session_state"].evidence
    )
    assert "PlannerDecompositionPolicyDeploymentProfile" in (
        planner.dimensions["session_state"].evidence
    )
    assert "PlannerLlmDecompositionGovernanceProfile" in (
        planner.dimensions["session_state"].evidence
    )
    assert "governance reference readiness payloads" in (
        planner.dimensions["session_state"].evidence
    )
    assert "PlannerOrchestrationDeploymentProfile" in (
        planner.dimensions["session_state"].evidence
    )
    assert "PlannerSchedulerGovernanceDeploymentProfile" in (
        planner.dimensions["concurrency"].evidence
    )
    assert "ProductionStatePlaneDeploymentProfile" in (
        planner.dimensions["concurrency"].evidence
    )
    assert "plan_create_from_decomposition" in (
        planner.dimensions["protocol"].evidence
    )
    assert "plan_gate_decomposition_proposal" in (
        planner.dimensions["protocol"].evidence
    )
    assert "PlanStep.depends_on" in planner.dimensions["session_state"].evidence
    assert "plan_ready_steps" in planner.dimensions["protocol"].evidence
    assert "PlanRetryPolicy" in planner.dimensions["concurrency"].evidence
    assert "PlanClaimStore" in planner.dimensions["concurrency"].evidence
    assert "InMemoryPlanClaimStore" in planner.dimensions["concurrency"].evidence
    assert "PostgresPlanClaimStore" in planner.dimensions["concurrency"].evidence
    assert "plan claim/lease boundary" in (
        planner.dimensions["concurrency"].evidence
    )
    assert "plan_fail_step" in planner.dimensions["protocol"].evidence
    assert "plan_retryable_steps" in planner.dimensions["protocol"].evidence
    assert "plan_retry_step" in planner.dimensions["protocol"].evidence
    assert "plan_dispatch_ready_steps" in planner.dimensions["protocol"].evidence
    assert "PlanDispatchReport" in planner.dimensions["concurrency"].evidence
    assert "PlanSchedulerTickReport" in planner.dimensions["concurrency"].evidence
    assert "PlanClaimedSchedulerTickReport" in (
        planner.dimensions["concurrency"].evidence
    )
    assert "PlannerClaimedSchedulerDaemon" in (
        planner.dimensions["concurrency"].evidence
    )
    assert "PlannerClaimedSchedulerDaemonState" in (
        planner.dimensions["concurrency"].evidence
    )
    assert "PlannerWorkerDispatchSupervisionProfile" in (
        planner.dimensions["concurrency"].evidence
    )
    assert "PlanClaimSweepReport" in planner.dimensions["concurrency"].evidence
    assert "PlanClaimSweepSkip" in planner.dimensions["concurrency"].evidence
    assert "PlanClaimSweepStore" in planner.dimensions["concurrency"].evidence
    assert "PlannerStaleClaimSweepProfile" in (
        planner.dimensions["concurrency"].evidence
    )
    assert "PlannerSchedulablePlan" in planner.dimensions["concurrency"].evidence
    assert "PlannerRuntime.schedulable_plans" in (
        planner.dimensions["protocol"].evidence
    )
    assert "plan_schedulable_plans" in planner.dimensions["protocol"].evidence
    assert "PlannerRuntime.claim_schedulable_plans" in (
        planner.dimensions["protocol"].evidence
    )
    assert "plan_claim_schedulable_plans" in (
        planner.dimensions["protocol"].evidence
    )
    assert "PlannerRuntime.claimed_scheduler_tick" in (
        planner.dimensions["protocol"].evidence
    )
    assert "plan_claimed_scheduler_tick" in (
        planner.dimensions["protocol"].evidence
    )
    assert "PlannerClaimedSchedulerDaemon" in (
        planner.dimensions["protocol"].evidence
    )
    assert "PlannerRuntime.sweep_expired_claims" in (
        planner.dimensions["protocol"].evidence
    )
    assert "stale claim sweep boundary" in (
        planner.dimensions["protocol"].evidence
    )
    assert "PlannerSchedulerDaemon" in planner.dimensions["concurrency"].evidence
    assert "PlannerSchedulerDaemonState" in (
        planner.dimensions["concurrency"].evidence
    )
    assert "WorkerProcessLifecycleDeploymentProfile" in (
        planner.dimensions["concurrency"].evidence
    )
    assert "plan_scheduler_tick" in planner.dimensions["protocol"].evidence
    assert "claim-before-tick scheduler boundary" in (
        planner.dimensions["protocol"].evidence
    )
    assert "planner worker dispatch supervision profile" in (
        planner.dimensions["protocol"].evidence
    )
    assert "schedulable plan selection" in (
        planner.dimensions["protocol"].evidence
    )
    assert "explicit plan ids" in planner.dimensions["protocol"].evidence
    assert "worker dispatch loop supervision" not in " ".join(
        planner.required_app_glue
    )
    assert "worker dispatch loop execution" in " ".join(planner.required_app_glue)
    assert "plan claiming" not in " ".join(planner.required_app_glue)
    assert "distributed scheduler locks" in " ".join(planner.required_app_glue)
    assert "production distributed claim store" not in " ".join(
        planner.required_app_glue
    )
    assert "stale claim sweep scheduling policy" in " ".join(
        planner.required_app_glue
    )
    assert "stale lease recovery policy" not in " ".join(
        planner.required_app_glue
    )
    assert "plan discovery" in " ".join(planner.required_app_glue)
    assert "tenant routing" in planner.dimensions["concurrency"].gap
    assert "global fairness" in planner.dimensions["concurrency"].gap
    assert "planner scheduler governance profile" in (
        planner.dimensions["concurrency"].evidence
    )
    assert "plan_discovery_policy" in planner.dimensions["concurrency"].evidence
    assert "tenant_routing_policy" in planner.dimensions["concurrency"].evidence
    assert "global_fairness_policy" in planner.dimensions["concurrency"].evidence
    assert "leader_election_policy" in planner.dimensions["concurrency"].evidence
    assert "process supervision" in " ".join(planner.required_app_glue)
    assert "LLM prompt/model/approval/evaluation policy" in " ".join(
        planner.required_app_glue
    )
    assert "production retry policy" not in " ".join(planner.required_app_glue)
    assert "DAG scheduling policy" not in " ".join(planner.required_app_glue)
    assert "persistent PlanStore" not in " ".join(planner.required_app_glue)
    assert "retry orchestration" not in planner.dimensions["protocol"].gap.lower()
    assert "PostgresPlanStore" in planner.dimensions["persistence"].evidence
    assert "PostgresPlanClaimStore" in planner.dimensions["persistence"].evidence
    assert planner.dimensions["schema_migration"].level == "primitives-ready"
    assert "2026-06-16-postgres-plan-claims.sql" in (
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
            "service_reference": {"ok": True, "component": "AgentServiceReference"},
        },
        required_checks=(
            "service_reference",
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
