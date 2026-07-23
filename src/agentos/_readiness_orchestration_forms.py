from __future__ import annotations

from agentos._readiness_form_types import (
    AgentFormReadiness,
    WORKSPACE_BACKEND_EVIDENCE,
    _dimensions,
)


ORCHESTRATION_AGENT_FORMS: dict[str, AgentFormReadiness] = {
"team-discussion": AgentFormReadiness(
        form_id="team-discussion",
        name="Team Discussion Agent",
        overall_level="primitives-ready",
        summary=(
            "Tenant-scoped Team state, PostgreSQL delivery truth, Redis event "
            "replay, and delivery runners compose with the distributed runtime."
        ),
        recommended_profile="DistributedRuntimeProfile",
        required_app_glue=(
            "team delivery runner hosting",
            "team event relay hosting",
            "workspace and member authorization policy",
            "retry, monitoring, and process supervision policy",
        ),
        dimensions=_dimensions(
            {
                "session_state": (
                    "primitives-ready",
                    (
                        "TeamRuntime",
                        "TeamRecord",
                        "TeamMemberRecord",
                        "TeamMessage",
                        "TeamApplicationPort",
                        "PostgresTeamStore",
                    ),
                    "Team workers target normal distributed Sessions; product conversation policy remains application-owned.",
                ),
                "concurrency": (
                    "primitives-ready",
                    (
                        "TeamDeliveryRunner",
                        "TeamEventDeliveryRunner",
                        "TeamDeliveryPort",
                        "RedisQueueAdapter",
                        "ProductionStatePlaneDeploymentProfile",
                    ),
                    "Runner hosting, retry schedule, process supervision, and scaling remain deployment-owned.",
                ),
                "workspace": (
                    "primitives-ready",
                    (
                        "TeamRuntime.validate_member_workspace",
                        "TeamWorkspaceAuthorityPort",
                        "WorkspaceHandle",
                        "WorkspacePolicy",
                        "WorkspaceExecutionIsolationProfile",
                        "WorkspaceToolSandboxPolicy",
                        *WORKSPACE_BACKEND_EVIDENCE,
                    ),
                    "OS/container sandboxing remains deployment-owned; Docker/E2B/enterprise runner adapters are deployment-owned.",
                ),
                "protocol": (
                    "primitives-ready",
                    (
                        "TeamRuntime messages_for",
                        "TeamTools",
                        "TeamDeliveryRunner",
                        "TeamEventDeliveryRunner",
                        "TeamEventReplayPort",
                        "RedisTeamEventReplayAdapter",
                    ),
                    "A product-specific Team UI transport remains application-owned.",
                ),
                "persistence": (
                    "primitives-ready",
                    (
                        "InMemoryTeamStore",
                        "PostgresTeamStore",
                        "RedisTeamEventReplayAdapter",
                    ),
                    "Production deployments must run the Phase 6 migration and configure shared backends.",
                ),
                "schema_migration": (
                    "primitives-ready",
                    (
                        "TeamRecord dataclasses",
                        "2026-07-20-postgres-distributed-runtime.sql",
                    ),
                    "Application rollout/version policy remains deployment-owned.",
                ),
            },
            default_level="primitives-ready",
            default_evidence=("TeamRuntime", "TeamTools"),
            default_gap="Production team orchestration policy remains app-owned.",
        ),
    ),
    "planner-intent-router": AgentFormReadiness(
        form_id="planner-intent-router",
        name="Planner / Intent Router Agent",
        overall_level="primitives-ready",
        summary=(
            "PlannerRuntime, PlannerTools, templates, and summary projection "
            "support intent-router, plan-and-execute, auditable step retry, "
            "ready-step dispatch, schedulable plan selection, plan "
            "claim/lease, claim-before-tick scheduler batches, one-shot "
            "scheduler tick, scheduler daemon polling, and structured "
            "decomposition validation, dispatch supervision, and stale-claim "
            "sweep patterns."
        ),
        recommended_profile="DurableRuntimeProfile",
        required_app_glue=(
            "automatic LLM decomposition policy",
            "LLM prompt/model/approval/evaluation policy",
            "plan discovery and tenant filtering",
            "production PlanStore and PlanClaimStore adapters for distributed deployment",
            "planner scheduler governance profile configuration",
            "distributed scheduler locks and leader election",
            "stale claim sweep scheduling policy",
            "worker dispatch loop execution",
            "process supervision and restart policy",
            "compensation orchestration",
        ),
        dimensions=_dimensions(
            {
                "session_state": (
                    "primitives-ready",
                    (
                        "PlannerRuntime",
                        "PlanDecomposition",
                        "PlanDecompositionGatePolicy",
                        "PlanDecompositionGateReport",
                        "PlanDecompositionValidationReport",
                        "PlannerRuntime.gate_decomposition_proposal",
                        "PlannerRuntime.validate_decomposition",
                        "PlanStepSpec",
                        "PlanStep.depends_on",
                        "PlannerDecompositionPolicyDeploymentProfile",
                        "PlannerLlmDecompositionGovernanceProfile",
                        "governance reference readiness payloads",
                        "PlannerOrchestrationDeploymentProfile",
                        "PlanStore",
                        "InMemoryPlanStore",
                        "SQLitePlanStore",
                        "DurableRuntimeProfile",
                    ),
                    "LLM prompt/model/approval/evaluation policy, plan discovery, and distributed scheduler locks remain deployment-owned.",
                ),
                "concurrency": (
                    "primitives-ready",
                    (
                        "AgentCoordinator assignment boundary",
                        "PlanRetryPolicy",
                        "PlanDispatchReport",
                        "PlanDispatchSkip",
                        "PlanSchedulerTickReport",
                        "PlanClaimedSchedulerTickReport",
                        "PlanClaimedSchedulerTickSkip",
                        "PlannerClaimedSchedulerDaemon",
                        "PlannerClaimedSchedulerDaemonState",
                        "PlannerClaimedSchedulerDaemonError",
                        "PlannerSchedulerGovernanceDeploymentProfile",
                        "ProductionStatePlaneDeploymentProfile",
                        "planner scheduler governance profile",
                        "plan_discovery_policy",
                        "tenant_routing_policy",
                        "global_fairness_policy",
                        "scheduler_lock_policy",
                        "leader_election_policy",
                        "stale_lease_recovery_policy",
                        "live_backend_verification",
                        "PlannerWorkerDispatchSupervisionProfile",
                        "PlanClaimSweepReport",
                        "PlanClaimSweepSkip",
                        "PlanClaimSweepStore",
                        "PlannerStaleClaimSweepProfile",
                        "PlannerSchedulablePlan",
                        "PlanClaimStore",
                        "InMemoryPlanClaimStore",
                        "PlanClaimRecord",
                        "plan claim/lease boundary",
                        "claim-before-tick scheduler boundary",
                        "PlannerSchedulerDaemon",
                        "PlannerSchedulerDaemonState",
                        "PlannerSchedulerDaemonError",
                        "PlanStep attempts/next_retry_at/retry_status",
                    ),
                    "Schedulable-plan selection, in-memory claim/lease primitives, claim-before-tick scheduler batches, claimed scheduler daemon polling, scheduler governance readiness metadata, dispatch supervision payloads, and exact stale-claim sweep reports/releases are SDK-owned. Production distributed PlanStore and PlanClaimStore adapters, tenant routing, global fairness, distributed scheduler locks, leader election, stale claim sweep scheduling policy, worker dispatch loop execution, process supervision, and compensation policy remain application/deployment-owned.",
                ),
                "workspace": (
                    "primitives-ready",
                    (
                        "SubAgentTemplate.workspace_scope",
                        "WorkspaceHandle",
                        "WorkspaceExecutionIsolationProfile",
                        *WORKSPACE_BACKEND_EVIDENCE,
                    ),
                    "Workspace narrowing enforcement exists as SDK metadata; OS/container sandboxing remains deployment-owned; Docker/E2B/enterprise runner adapters are deployment-owned.",
                ),
                "protocol": (
                    "primitives-ready",
                    (
                        "PlannerTools",
                        "plan_gate_decomposition_proposal",
                        "plan_create_from_decomposition",
                        "PlannerRuntime.validate_decomposition",
                        "plan_ready_steps",
                        "plan_fail_step",
                        "plan_retryable_steps",
                        "plan_retry_step",
                        "plan_dispatch_ready_steps",
                        "PlannerRuntime.schedulable_plans",
                        "plan_schedulable_plans",
                        "schedulable plan selection",
                        "PlannerRuntime.claim_schedulable_plans",
                        "plan_claim_schedulable_plans",
                        "PlannerRuntime.claimed_scheduler_tick",
                        "plan_claimed_scheduler_tick",
                        "PlannerClaimedSchedulerDaemon",
                        "PlannerRuntime.sweep_expired_claims",
                        "claim-before-tick scheduler boundary",
                        "planner worker dispatch supervision profile",
                        "stale claim sweep boundary",
                        "plan_scheduler_tick",
                        "explicit plan ids",
                    ),
                    "PlannerRuntime.schedulable_plans filters owner/status/ready/retryable work, PlannerRuntime.claim_schedulable_plans composes that selection with the injected PlanClaimStore, PlannerRuntime.claimed_scheduler_tick only ticks plans successfully claimed by the current worker, PlannerClaimedSchedulerDaemon polls that claim-before-tick boundary, PlannerRuntime.sweep_expired_claims reports and safely releases exact expired claim records, and PlannerWorkerDispatchSupervisionProfile plus PlannerStaleClaimSweepProfile project those reports into health/readiness payloads. PlannerSchedulerDaemon polls explicitly supplied plan ids, while tenant routing, global fairness, distributed scheduler locks, stale claim sweep scheduling policy, worker dispatch loop execution, process supervision, and compensation orchestration remain deployment-owned.",
                ),
                "persistence": (
                    "primitives-ready",
                    (
                        "InMemoryPlanStore",
                        "SQLitePlanStore",
                        "InMemoryPlanClaimStore",
                        "PlanStore protocol",
                        "PlanClaimStore protocol",
                        "PlanClaimSweepStore protocol",
                    ),
                    "A distributed PlanStore and PlanClaimStore adapter is application-owned in Phase 6.",
                ),
                "schema_migration": (
                    "primitives-ready",
                    (
                        "PlanState dataclasses",
                        "SQLitePlanStore schema initialization",
                    ),
                    "Distributed planner schema and rollout policy remain deployment-owned.",
                ),
            },
            default_level="primitives-ready",
            default_evidence=("PlannerRuntime", "PlannerTools"),
            default_gap="Production planner policy remains app-owned.",
        ),
    ),
}


__all__ = ["ORCHESTRATION_AGENT_FORMS"]
