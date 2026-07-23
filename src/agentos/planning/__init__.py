"""Planner 领域、状态真值与可选调度能力。"""

from typing import TYPE_CHECKING

from agentos.planning.errors import (
    PlanClaimLostError,
    PlanConflictError,
    PlanDispatchAlreadySubmittedError,
    PlanError,
    PlanNotFoundError,
    PlanProjectionError,
    PlanStepNotFoundError,
    PlannerToolAuthorizationError,
)
from agentos.planning.dispatch import (
    PlanDispatchReport,
    PlanDispatchSkip,
    PlanDispatchSkipReason,
    PlanStepDispatcher,
)
from agentos.planning.decomposition import (
    PlanDecomposition,
    PlanDecompositionGatePolicy,
    PlanDecompositionGateReport,
    PlanDecompositionValidationReport,
    PlanStepSpec,
)
from agentos.planning.decomposition_governance import (
    PLANNER_LLM_GOVERNANCE_EXECUTION_REQUIRED_EVIDENCE,
    PlannerLlmGovernanceEvidenceGateReport,
    PlannerLlmGovernanceEvidenceRecord,
)
from agentos.planning.daemons import (
    PlannerClaimedSchedulerDaemon,
    PlannerClaimedSchedulerDaemonError,
    PlannerClaimedSchedulerDaemonState,
    PlannerClaimedSchedulerDaemonStatus,
    PlannerSchedulerDaemon,
    PlannerSchedulerDaemonError,
    PlannerSchedulerDaemonState,
    PlannerSchedulerDaemonStatus,
)
from agentos.planning.in_memory import InMemoryPlanClaimStore, InMemoryPlanStore
from agentos.planning.models import (
    EVIDENCE_KINDS,
    PLAN_STATUSES,
    EvidenceHandle,
    EvidenceKind,
    PlanAssignment,
    PlanAssignmentDispatchStatus,
    PlanRetryPolicy,
    PlanState,
    PlanStatus,
    PlanStep,
    PlanStepRetryStatus,
    PlanStepStatus,
    SubAgentTemplate,
)
from agentos.planning.projection import (
    AuthorizedPlanSource,
    BoundPlanProjectionProvider,
)
from agentos.planning.profiles import (
    PLANNER_DECOMPOSITION_POLICY_REQUIRED_COMPONENTS,
    PLANNER_LLM_DECOMPOSITION_GOVERNANCE_REQUIRED_COMPONENTS,
    PLANNER_ORCHESTRATION_REQUIRED_COMPONENTS,
    PlannerDecompositionPolicyDeploymentProfile,
    PlannerLlmDecompositionGovernanceProfile,
    PlannerOrchestrationDeploymentProfile,
)
from agentos.planning.runtime import PlannerRuntime
from agentos.planning.scheduling import (
    PlanClaimedSchedulerTickReport,
    PlanClaimedSchedulerTickSkip,
    PlanClaimSweepReport,
    PlanClaimSweepSkip,
    PlanSchedulerRetryReset,
    PlanSchedulerTickReport,
    PlannerSchedulablePlan,
    PlannerSchedulablePlanReason,
)
from agentos.planning.scheduling_profiles import (
    PLANNER_SCHEDULER_GOVERNANCE_REQUIRED_COMPONENTS,
    PLANNER_STALE_CLAIM_SWEEP_REQUIRED_COMPONENTS,
    PLANNER_WORKER_DISPATCH_SUPERVISION_REQUIRED_COMPONENTS,
    PlannerSchedulerGovernanceDeploymentProfile,
    PlannerStaleClaimSweepProfile,
    PlannerWorkerDispatchSupervisionProfile,
)
from agentos.planning.store import (
    ClaimGuardedPlanStore,
    CompareAndSavePlanStore,
    PlanClaimRecord,
    PlanClaimResult,
    PlanClaimStatus,
    PlanClaimStore,
    PlanClaimSweepStore,
    PlanStore,
    PlanStoreRecord,
)
from agentos.planning.tools import (
    AllowAllPlannerToolAuthorizationPolicy,
    DefaultPlannerToolAuthorizationPolicy,
    PlannerToolAuthorizationPolicy,
    PlannerTools,
)

if TYPE_CHECKING:
    from agentos.planning.sqlite import SQLitePlanStore


def __getattr__(name: str) -> object:
    """惰性导出 Durable Adapter，保持 Planning 领域导入轻量。"""

    if name == "SQLitePlanStore":
        from agentos.planning.sqlite import SQLitePlanStore

        return SQLitePlanStore
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "AuthorizedPlanSource",
    "AllowAllPlannerToolAuthorizationPolicy",
    "BoundPlanProjectionProvider",
    "ClaimGuardedPlanStore",
    "CompareAndSavePlanStore",
    "DefaultPlannerToolAuthorizationPolicy",
    "EVIDENCE_KINDS",
    "EvidenceHandle",
    "EvidenceKind",
    "InMemoryPlanClaimStore",
    "InMemoryPlanStore",
    "PLAN_STATUSES",
    "PLANNER_DECOMPOSITION_POLICY_REQUIRED_COMPONENTS",
    "PLANNER_LLM_DECOMPOSITION_GOVERNANCE_REQUIRED_COMPONENTS",
    "PLANNER_LLM_GOVERNANCE_EXECUTION_REQUIRED_EVIDENCE",
    "PLANNER_ORCHESTRATION_REQUIRED_COMPONENTS",
    "PLANNER_SCHEDULER_GOVERNANCE_REQUIRED_COMPONENTS",
    "PLANNER_STALE_CLAIM_SWEEP_REQUIRED_COMPONENTS",
    "PLANNER_WORKER_DISPATCH_SUPERVISION_REQUIRED_COMPONENTS",
    "PlanAssignment",
    "PlanAssignmentDispatchStatus",
    "PlanClaimLostError",
    "PlanClaimRecord",
    "PlanClaimResult",
    "PlanClaimStatus",
    "PlanClaimStore",
    "PlanClaimedSchedulerTickReport",
    "PlanClaimedSchedulerTickSkip",
    "PlanClaimSweepReport",
    "PlanClaimSweepSkip",
    "PlanClaimSweepStore",
    "PlanConflictError",
    "PlanDecomposition",
    "PlanDecompositionGatePolicy",
    "PlanDecompositionGateReport",
    "PlanDecompositionValidationReport",
    "PlanDispatchAlreadySubmittedError",
    "PlanDispatchReport",
    "PlanDispatchSkip",
    "PlanDispatchSkipReason",
    "PlanError",
    "PlanNotFoundError",
    "PlanProjectionError",
    "PlanRetryPolicy",
    "PlanSchedulerRetryReset",
    "PlanSchedulerTickReport",
    "PlanState",
    "PlanStatus",
    "PlanStep",
    "PlanStepDispatcher",
    "PlanStepSpec",
    "PlanStepNotFoundError",
    "PlanStepRetryStatus",
    "PlanStepStatus",
    "PlanStore",
    "PlanStoreRecord",
    "PlannerToolAuthorizationError",
    "PlannerToolAuthorizationPolicy",
    "PlannerTools",
    "PlannerClaimedSchedulerDaemon",
    "PlannerClaimedSchedulerDaemonError",
    "PlannerClaimedSchedulerDaemonState",
    "PlannerClaimedSchedulerDaemonStatus",
    "PlannerDecompositionPolicyDeploymentProfile",
    "PlannerLlmDecompositionGovernanceProfile",
    "PlannerOrchestrationDeploymentProfile",
    "PlannerRuntime",
    "PlannerSchedulablePlan",
    "PlannerSchedulablePlanReason",
    "PlannerSchedulerDaemon",
    "PlannerSchedulerDaemonError",
    "PlannerSchedulerDaemonState",
    "PlannerSchedulerDaemonStatus",
    "PlannerSchedulerGovernanceDeploymentProfile",
    "PlannerStaleClaimSweepProfile",
    "PlannerWorkerDispatchSupervisionProfile",
    "SQLitePlanStore",
    "PlannerLlmGovernanceEvidenceGateReport",
    "PlannerLlmGovernanceEvidenceRecord",
    "SubAgentTemplate",
]
