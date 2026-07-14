from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_objective_coverage_audit_covers_original_goal_areas() -> None:
    text = (ROOT / "docs" / "agentos-objective-coverage-audit.md").read_text(
        encoding="utf-8",
    )

    for expected in [
        "Terminal / Script Agents",
        "Single-Node Async Web Agents",
        "Distributed Web Dynamic Context Hydration",
        "AgentScope2 / A2A Card, Registry, And Discovery",
        "A2A Operation Interaction",
        "Multi-Agent Team Discussion",
        "Single Async QueryLoop Guidance",
        "Planner / Plan-And-Execute",
        "Main-Agent Intent Routing With Subagent Execution",
        "Workspace Layer Expansion",
        "SDK Developer Skill Guidance",
    ]:
        assert expected in text


def test_objective_coverage_audit_names_evidence_and_remaining_blockers() -> None:
    text = (ROOT / "docs" / "agentos-objective-coverage-audit.md").read_text(
        encoding="utf-8",
    )

    for expected in [
        "Overall completion estimate: 96%",
        "Phase 96: Release Scope Re-baseline",
        "Phase 97: Reference State Plane Stack",
        "Overall completion estimate: 97%",
        "Phase 98: Live Backend Probe Pack",
        "Overall completion estimate: 98%",
        "Phase 99: SDK Skill / Spec Generator Finalization",
        "Overall completion estimate: 99%",
        "Phase 100: Release Hardening",
        "Phase 101: Production Reference Example",
        "SDK-side checklist coverage: 100%",
        "non-certifying SDK evidence",
        "not a production certification",
        "production reference web agent",
        "AgentServiceReference",
        "DistributedWebRuntimeProfile",
        "Nacos/Redis/Postgres state plane",
        "readiness endpoint",
        "backend verification",
        "ProductionReadinessEvidenceBundle",
        "ReferenceStatePlaneStack",
        "ReferenceLiveBackendProbePack",
        "planner primitive",
        "src/agentos/examples/production_reference_web_agent.py",
        "tests/examples/test_production_reference_web_agent.py",
        "spec generator finalization",
        "production agent design constraint generator",
        "production_design_constraints",
        "must explicitly choose",
        "agent form",
        "runtime profile",
        "state plane components",
        "persistence backend",
        "registry backend",
        "queue backend",
        "worker supervisor",
        "A2A exposure",
        "planner/team mode",
        "production readiness checklist",
        "sandbox posture: trusted tools only | deployment-owned isolation | future adapter",
        "does not create deployment-owned infrastructure",
        "SDK-owned constraint template",
        "ReferenceLiveBackendProbePack",
        "ReferenceLiveBackendProbeSpec",
        "REFERENCE_LIVE_BACKEND_PROBE_PACK_NAME",
        "agentos.examples.live_backend_probe",
        "Nacos probe",
        "Redis probe",
        "Postgres task/plan/session probe",
        "worker supervisor probe",
        "readiness bundle aggregation",
        "ReferenceStatePlaneStack",
        "ReferenceStatePlaneStackProfile",
        "REFERENCE_STATE_PLANE_REQUIRED_COMPONENTS",
        "reference state plane",
        "readiness source aggregation",
        "component identity evidence",
        "does not create backend clients",
        "credentials, migrations, CI matrix execution, alert routing and runbooks remain deployment-owned",
        "release scope re-baseline",
        "first production SDK release",
        "trusted tools",
        "internal service orchestration",
        "terminal agent",
        "single-node web agent",
        "distributed web agent",
        "team/planner/A2A primitive",
        "production state plane",
        "readiness evidence",
        "Sandbox / Docker / E2B / microVM / enterprise runner adapter",
        "non-blocking future adapter",
        "not a release blocker",
        "does not promise physical isolation for untrusted code execution",
        "policy/capability/path pre-check",
        "audit evidence",
        "sandbox posture",
        "trusted tools only",
        "deployment-owned isolation",
        "future adapter",
        "docs/release-scope.md",
        "LocalRuntimeProfile",
        "QueryLoop",
        "agentos.sync",
        "DistributedWebRuntimeProfile",
        "DistributedWebSessionOperationsProfile",
        "A2AAgentCard",
        "PersistentAgentRegistry",
        "A2AOperationClient.stream_message_events",
        "A2AStreamLifecycleDeploymentProfile",
        "A2AExternalConformanceExecutionProfile",
        "A2AExternalConformanceExecutionRecord",
        "A2AExternalConformanceGateReport",
        "A2AExternalConformanceRunner",
        "A2AExternalConformanceCliRunner",
        "A2AExternalConformanceInvocationPlan",
        "A2AExternalConformanceInvocationGateReport",
        "external conformance execution record",
        "external conformance gate report",
        "external conformance CLI runner",
        "report path",
        "stdout JSON",
        "bounded stdout/stderr summaries",
        "env_keys",
        "no shell parsing",
        "external conformance invocation plan",
        "external conformance invocation gate report",
        "DistributedTeamRuntimeProfile",
        "PlannerOrchestrationDeploymentProfile",
        "PlannerDecompositionPolicyDeploymentProfile",
        "PlanDecompositionGatePolicy",
        "PlanDecompositionGateReport",
        "PlannerRuntime.gate_decomposition_proposal",
        "plan_gate_decomposition_proposal",
        "PlanDecompositionValidationReport",
        "PlannerRuntime.validate_decomposition",
        "PlannerLlmDecompositionGovernanceProfile",
        "governance reference readiness payloads",
        "component_refs",
        "evidence_refs",
        "budget_policy",
        "PlanSchedulerTickReport",
        "PlannerSchedulablePlan",
        "PlannerRuntime.schedulable_plans",
        "plan_schedulable_plans",
        "PlanClaimStore",
        "InMemoryPlanClaimStore",
        "PostgresPlanClaimStore",
        "PlanClaimRecord",
        "PlannerRuntime.claim_schedulable_plans",
        "plan_claim_schedulable_plans",
        "PlannerRuntime.claimed_scheduler_tick",
        "plan_claimed_scheduler_tick",
        "PlanClaimedSchedulerTickReport",
        "PlanClaimedSchedulerTickSkip",
        "PlannerClaimedSchedulerDaemon",
        "PlannerClaimedSchedulerDaemonState",
        "claimed scheduler daemon polling",
        "PlannerSchedulerGovernanceDeploymentProfile",
        "planner scheduler governance profile",
        "plan_discovery_policy",
        "tenant_routing_policy",
        "global_fairness_policy",
        "leader_election_policy",
        "PlannerWorkerDispatchSupervisionProfile",
        "planner worker dispatch supervision profile",
        "PlanClaimSweepReport",
        "PlanClaimSweepSkip",
        "PlanClaimSweepStore",
        "PlannerRuntime.sweep_expired_claims",
        "PlannerStaleClaimSweepProfile",
        "planner stale claim sweep profile",
        "stale claim sweep boundary",
        "stale_claim_sweep_schedule",
        "sweep_safety_window",
        "claimed_scheduler_tick_loop",
        "metrics_alerting",
        "claim-before-tick scheduler boundary",
        "2026-06-16-postgres-plan-claims.sql",
        "PlannerSchedulerDaemon",
        "PlannerSchedulerDaemonState",
        "WorkerProcessLifecycleDeploymentProfile",
        "ProductionStatePlaneDeploymentProfile",
        "BackendVerificationRecord",
        "DeploymentLiveBackendVerificationGateReport",
        "DeploymentLiveBackendVerificationProfile",
        "DeploymentLiveBackendVerificationRunResult",
        "BackendVerificationInvocationPlan",
        "BackendVerificationRunner",
        "BackendVerificationCliRunner",
        "BackendVerificationReportImporter",
        "BackendVerificationReportImportError",
        "LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS",
        "deployment_live_backend_verification",
        "block_production_readiness",
        "missing or failed backend evidence",
        "reference runner",
        "report path",
        "stdout JSON",
        "bounded stdout/stderr summaries",
        "env_keys",
        "no backend client claim",
        "WorkspaceExecutionIsolationProfile",
        "WorkspaceExecutionBackend",
        "LocalWorkspaceExecutionBackend",
        "SandboxBackend",
        "JSON-safe execution evidence",
        "Docker/E2B/enterprise runner",
        "SkillReleaseManifest",
        "SkillReleaseDriftReport",
        "build_skill_release_manifest",
        "compare_skill_release_manifests",
        ".claude/skills/agent-os/modules/agent-forms.md",
        "repository skill",
        "installed user-level skill",
        "release manifest",
        "drift report",
        "install/copy/publish/sign approval remains deployment-owned",
        "external conformance execution",
        "external conformance execution profile",
        "external suite invocation planning",
        "automatic LLM decomposition policy",
        "LLM prompt/model/approval/evaluation policy",
        "distributed scheduler locks",
        "plan discovery",
        "tenant routing",
        "global fairness",
        "stale claim sweep scheduling policy",
        "process supervision",
        "worker process lifecycle",
        "OS/container sandboxing",
        "credential issuance and secret distribution",
        "live backend verification",
        "NacosAgentRegistryAdapter",
        "NacosAgentCardResolver",
        "NacosRegistryClient",
        "NacosRegistryConfig",
        "NacosRegistryEvidence",
        "discovery-only Nacos metadata",
        "AgentCard-to-Nacos metadata projection",
        "healthy Nacos instances",
        "capability-based discovery",
        "not task truth",
        "not plan truth",
        "not session snapshot storage",
        "not message queue",
        "not worker runtime state",
        "RedisAgentMessageQueue",
        "PostgresTaskStore",
        "PostgresPlanStore",
        "WorkerProcessSupervisor",
        "WorkerProcessSpec",
        "WorkerProcessState",
        "LocalSubprocessWorkerSupervisor",
        "JSON-safe lifecycle evidence",
        "argv-only",
        "no shell parsing",
        "SessionSnapshotPersistence",
        "registry is not task truth",
        "queue is not final task or plan state",
        "AgentServiceReference",
        "AgentServiceReferenceProfile",
        "AGENT_SERVICE_REFERENCE_REQUIRED_COMPONENTS",
        "Agent Service Reference Layer",
        "AsgiAgentApp composition",
        "DistributedWebRuntimeProfile injection",
        "auth/rate-limit hook injection",
        "readiness check aggregation",
        "reference service",
        "not a platform",
        "gateway/TLS/CORS/WAF",
        "Kubernetes/systemd/autoscaling",
        "ProductionReadinessEvidenceBundle",
        "ReadinessEvidenceCheck",
        "ReadinessEvidenceStatus",
        "blocking_checks",
        "missing_required_checks",
        "release gate evidence bundle",
        "consumes existing readiness/profile/backend evidence",
        "does not execute real infrastructure checks",
        "JSON-safe evidence bundle",
    ]:
        assert expected in text

    assert "Goal status: active" in text
    assert "Do not mark the long-running goal complete" in text


def test_production_readiness_and_roadmap_link_objective_audit() -> None:
    production_readiness = (ROOT / "docs" / "production-readiness.md").read_text(
        encoding="utf-8",
    )
    audit = (ROOT / "docs" / "agentos-objective-coverage-audit.md").read_text(
        encoding="utf-8",
    )
    roadmap = (
        ROOT
        / "docs"
        / "plans"
        / "2026-06-11-agentos-sdk-architecture-review-roadmap.md"
    ).read_text(encoding="utf-8")

    assert "docs/agentos-objective-coverage-audit.md" in production_readiness
    assert "Phase 67: AgentOS Objective Coverage Audit" in roadmap
    assert "Phase 68: A2A External Conformance Execution Profile" in roadmap
    assert (
        "2026-06-16-a2a-external-conformance-execution-profile-design.md"
        in roadmap
    )
    assert "Phase 69: Planner Scheduler Tick Boundary" in roadmap
    assert "2026-06-16-planner-scheduler-tick-boundary-design.md" in roadmap
    assert "Phase 70: Worker Process Lifecycle Profile Boundary" in roadmap
    assert (
        "2026-06-16-worker-process-lifecycle-profile-boundary-design.md"
        in roadmap
    )
    assert "Phase 71: Planner Decomposition Policy Boundary" in roadmap
    assert (
        "2026-06-16-planner-decomposition-policy-boundary-design.md"
        in roadmap
    )
    assert "Phase 72: Planner Scheduler Daemon Boundary" in roadmap
    assert (
        "2026-06-16-planner-scheduler-daemon-boundary-design.md"
        in roadmap
    )
    assert "Phase 73: Planner Schedulable Plan Selection Boundary" in roadmap
    assert (
        "2026-06-16-planner-schedulable-plan-selection-boundary-design.md"
        in roadmap
    )
    assert "Phase 74: Planner Plan Claim Lease Boundary" in roadmap
    assert (
        "2026-06-16-planner-plan-claim-lease-boundary-design.md"
        in roadmap
    )
    assert "Phase 75: Postgres Plan Claim Store Boundary" in roadmap
    assert (
        "2026-06-16-postgres-plan-claim-store-boundary-design.md"
        in roadmap
    )
    assert "Phase 76: Planner Claimed Scheduler Tick Boundary" in roadmap
    assert (
        "2026-06-16-planner-claimed-scheduler-tick-boundary-design.md"
        in roadmap
    )
    assert "Phase 77: Planner Worker Dispatch Supervision Profile" in roadmap
    assert (
        "2026-06-16-planner-worker-dispatch-supervision-profile-design.md"
        in roadmap
    )
    assert "Phase 78: Planner Stale Claim Sweep Boundary" in roadmap
    assert (
        "2026-06-16-planner-stale-claim-sweep-boundary-design.md"
        in roadmap
    )
    assert "Phase 79: A2A External Conformance Execution Record Boundary" in roadmap
    assert (
        "2026-06-16-a2a-external-conformance-execution-record-boundary-design.md"
        in roadmap
    )
    assert "Phase 80: Planner LLM Decomposition Gate Boundary" in roadmap
    assert (
        "2026-06-16-planner-llm-decomposition-gate-boundary-design.md"
        in roadmap
    )
    assert "Phase 81: Planner Claimed Scheduler Daemon Boundary" in roadmap
    assert (
        "2026-06-16-planner-claimed-scheduler-daemon-boundary-design.md"
        in roadmap
    )
    assert "Phase 82: Planner Scheduler Governance Profile Boundary" in roadmap
    assert (
        "2026-06-16-planner-scheduler-governance-profile-boundary-design.md"
        in roadmap
    )
    assert "Phase 83: A2A External Conformance Invocation Plan Boundary" in roadmap
    assert (
        "2026-06-16-a2a-external-conformance-invocation-plan-boundary-design.md"
        in roadmap
    )
    assert "Phase 84: Planner LLM Governance Profile Boundary" in roadmap
    assert (
        "2026-06-16-planner-llm-governance-profile-boundary-design.md"
        in roadmap
    )
    assert "Phase 85: Skill Release Governance Boundary" in roadmap
    assert (
        "2026-06-16-skill-release-governance-boundary-design.md"
        in roadmap
    )
    assert "Phase 86: Production State Plane Boundary" in roadmap
    assert (
        "2026-06-16-production-state-plane-boundary-design.md"
        in roadmap
    )
    assert "Phase 87: Worker Lifecycle Reference Supervisor" in roadmap
    assert (
        "2026-06-16-worker-lifecycle-reference-supervisor-design.md"
        in roadmap
    )
    assert "Phase 88: Sandbox / Workspace Backend Adapter Boundary" in roadmap
    assert (
        "2026-06-16-sandbox-workspace-backend-boundary-design.md"
        in roadmap
    )
    assert "Phase 89: Nacos Registry Adapter Boundary" in roadmap
    assert (
        "2026-06-16-nacos-registry-adapter-boundary-design.md"
        in roadmap
    )
    assert "Phase 90: Agent Service Reference Layer" in roadmap
    assert (
        "2026-06-16-agent-service-reference-layer-design.md"
        in roadmap
    )
    assert (
        "2026-06-16-agent-service-reference-layer-implementation-plan.md"
        in roadmap
    )
    assert "Phase 91: A2A External Conformance Runner Boundary" in roadmap
    assert (
        "2026-06-16-a2a-external-conformance-runner-boundary-design.md"
        in roadmap
    )
    assert (
        "2026-06-16-a2a-external-conformance-runner-boundary-implementation-plan.md"
        in roadmap
    )
    assert "A2AExternalConformanceCliRunner" in roadmap
    assert "Phase 92: Planner LLM Governance Execution Boundary" in roadmap
    assert (
        "2026-06-16-planner-llm-governance-execution-boundary-design.md"
        in roadmap
    )
    assert (
        "2026-06-16-planner-llm-governance-execution-boundary-implementation-plan.md"
        in roadmap
    )
    assert "PlannerLlmGovernanceEvidenceRecord" in audit
    assert "PlannerLlmGovernanceEvidenceGateReport" in audit
    assert "PlannerRuntime.gate_llm_governance_evidence" in audit
    assert "per-proposal governance evidence gate" in audit
    assert "deployment-owned prompt/model/approval/evaluation/validation execution" in audit
    assert "Phase 93: Live Backend Verification Evidence Boundary" in roadmap
    assert (
        "2026-06-16-live-backend-verification-evidence-boundary-design.md"
        in roadmap
    )
    assert (
        "2026-06-16-live-backend-verification-evidence-boundary-implementation-plan.md"
        in roadmap
    )
    assert "BackendVerificationRecord" in audit
    assert "DeploymentLiveBackendVerificationGateReport" in audit
    assert "DeploymentLiveBackendVerificationProfile" in audit
    assert "BackendVerificationCliRunner" in audit
    assert "DeploymentLiveBackendVerificationRunResult" in audit
    assert "block_production_readiness" in audit
    assert "Phase 94: Live Backend Verification Reference Runner Boundary" in roadmap
    assert (
        "2026-06-16-live-backend-verification-reference-runner-boundary-design.md"
        in roadmap
    )
    assert (
        "2026-06-16-live-backend-verification-reference-runner-boundary-implementation-plan.md"
        in roadmap
    )
    assert "BackendVerificationCliRunner" in roadmap
    assert "Phase 95: Production Readiness Evidence Bundle Boundary" in roadmap
    assert (
        "2026-06-16-production-readiness-evidence-bundle-boundary-design.md"
        in roadmap
    )
    assert (
        "2026-06-16-production-readiness-evidence-bundle-boundary-implementation-plan.md"
        in roadmap
    )
    assert "ProductionReadinessEvidenceBundle" in roadmap
    assert "AgentServiceReference" in roadmap
    assert "AgentServiceReferenceProfile" in roadmap
    assert "AGENT_SERVICE_REFERENCE_REQUIRED_COMPONENTS" in roadmap
    assert "Phase 96: Release Scope Re-baseline" in roadmap
    assert (
        "2026-06-16-release-scope-rebaseline-design.md"
        in roadmap
    )
    assert (
        "2026-06-16-release-scope-rebaseline-implementation-plan.md"
        in roadmap
    )
    assert "docs/release-scope.md" in roadmap
    assert "Sandbox / Docker / E2B / microVM / enterprise runner adapter" in roadmap
    assert "not a release blocker" in roadmap
    assert "Overall completion estimate: 96%" in roadmap
    assert "Phase 97: Reference State Plane Stack" in roadmap
    assert (
        "2026-06-16-reference-state-plane-stack-design.md"
        in roadmap
    )
    assert (
        "2026-06-16-reference-state-plane-stack-implementation-plan.md"
        in roadmap
    )
    assert "ReferenceStatePlaneStack" in roadmap
    assert "ReferenceStatePlaneStackProfile" in roadmap
    assert "REFERENCE_STATE_PLANE_REQUIRED_COMPONENTS" in roadmap
    assert "Overall completion estimate: 97%" in roadmap
    assert "Phase 98: Live Backend Probe Pack" in roadmap
    assert "ReferenceLiveBackendProbePack" in roadmap
    assert "ReferenceLiveBackendProbeSpec" in roadmap
    assert "REFERENCE_LIVE_BACKEND_PROBE_PACK_NAME" in roadmap
    assert "agentos.examples.live_backend_probe" in roadmap
    assert "Overall completion estimate: 98%" in roadmap
    assert "Phase 99: SDK Skill / Spec Generator Finalization" in roadmap
    assert (
        "2026-06-16-sdk-skill-spec-generator-finalization-design.md"
        in roadmap
    )
    assert (
        "2026-06-16-sdk-skill-spec-generator-finalization-implementation-plan.md"
        in roadmap
    )
    assert "production agent design constraint generator" in roadmap
    assert "production_design_constraints" in roadmap
    assert "Overall completion estimate: 99%" in roadmap
    assert "Phase 101: Production Reference Example" in roadmap
    assert (
        "2026-06-16-production-reference-example-design.md"
        in roadmap
    )
    assert (
        "2026-06-16-production-reference-example-implementation-plan.md"
        in roadmap
    )
    assert "src/agentos/examples/production_reference_web_agent.py" in roadmap
    assert "tests/examples/test_production_reference_web_agent.py" in roadmap
    assert "SDK-side checklist coverage: 100%" in roadmap
    assert "Overall completion estimate: 100%" not in audit
