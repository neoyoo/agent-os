"""本地单进程 multi-agent coordination。"""

from agentos.multi.continuation import (
    AgentTaskNoticeProvider,
    AgentTaskNoticeStore,
    ContinuationTrigger,
    ContinuationErrorRecord,
    LocalContinuationTrigger,
)
from agentos.multi.coordinator import AgentCoordinator, SubagentFactory
from agentos.multi.expert import ExpertAgentRunner
from agentos.multi.registry import AgentRegistry, InMemoryRegistry
from agentos.multi.inbox import (
    AgentInbox,
    AgentInboxError,
    AgentInboxFullError,
    AgentInboxMissingError,
)
from agentos.multi.message_queue import AgentMessageQueue, QueueDelivery
from agentos.multi.planning_dispatch import AgentCoordinatorPlanStepDispatcher
from agentos.multi.reconciler import OutboxEntry, OutboxReconciler
from agentos.multi.redis_continuation import RedisContinuationTrigger
from agentos.multi.spawn import SpawnExecutor
from agentos.multi.task_store import TaskClaim, TaskStore
from agentos.multi.tasks import TaskTable
from agentos.multi.team import (
    AllowAllTeamToolAuthorizationPolicy,
    DefaultTeamToolAuthorizationPolicy,
    InMemoryTeamStore,
    InMemoryTeamWorkerSessionProvider,
    LocalTeamWakeupTrigger,
    TeamError,
    TeamMemberRecord,
    TeamMemberRole,
    TeamMembershipError,
    TeamMemberStatus,
    TeamMessage,
    TeamMessageKind,
    TeamNotFoundError,
    TeamRecord,
    TeamRuntime,
    TeamStatus,
    TeamStore,
    TeamToolAuthorizationError,
    TeamToolAuthorizationPolicy,
    TeamTools,
    TeamWakeupTrigger,
    InMemoryTeamUiStreamStore,
    TeamUiEvent,
    TeamUiEventKind,
    TeamUiStreamStore,
    TeamWorkerAgentProvider,
    InMemoryTeamWorkerCancellationStore,
    TeamWorkerCancellationRecord,
    TeamWorkerCancellationStatus,
    TeamWorkerCancellationStore,
    TeamWorkerDaemon,
    TeamWorkerDaemonState,
    TeamWorkerDaemonStatus,
    TeamWorkerPermissionError,
    TeamWorkerPermissionPolicy,
    TeamWorkerRetryPolicy,
    TeamWorkerRetryRecord,
    TeamWorkerRetryStatus,
    TeamWorkerRetryStore,
    TeamWorkerRunError,
    TeamWorkerRunResult,
    TeamWorkerRunner,
    TeamWorkerSession,
    TeamWorkerSessionProvider,
    TeamWorkerSessionRequest,
    TeamWorkerSessionStatus,
    InMemoryTeamWorkerRetryStore,
)
from agentos.multi.team_notices import TeamNoticeProvider, TeamNoticeStore
from agentos.multi.tools import AgentCoordinationTools
from agentos.multi.types import (
    AgentCard,
    AgentEnvelope,
    AgentEnvelopeType,
    AgentLifecycle,
    AgentStatus,
    ContextInitStrategy,
    CoordinationMode,
    SubagentInitRequest,
    TaskAlreadySubmittedError,
    TaskHandle,
    TaskRecord,
    TaskRequest,
    TaskResult,
    TaskStatus,
)


def __getattr__(name: str) -> object:
    """延迟导入 remote executor，避免 channels/multi package import 环。"""

    if name == "RemoteTaskExecutor":
        from agentos.multi.remote import RemoteTaskExecutor

        return RemoteTaskExecutor
    if name == "PostgresTaskStore":
        from agentos.multi.postgres_tasks import PostgresTaskStore

        return PostgresTaskStore
    if name == "PostgresTeamStore":
        from agentos.multi.postgres_team import PostgresTeamStore

        return PostgresTeamStore
    if name == "PostgresTeamUiStreamStore":
        from agentos.multi.postgres_team import PostgresTeamUiStreamStore

        return PostgresTeamUiStreamStore
    if name == "PostgresTeamWorkerRetryStore":
        from agentos.multi.postgres_team import PostgresTeamWorkerRetryStore

        return PostgresTeamWorkerRetryStore
    if name == "PostgresTeamWorkerCancellationStore":
        from agentos.multi.postgres_team import PostgresTeamWorkerCancellationStore

        return PostgresTeamWorkerCancellationStore
    if name == "RedisAgentMessageQueue":
        from agentos.multi.redis_queue import RedisAgentMessageQueue

        return RedisAgentMessageQueue
    if name == "RedisAgentMessageQueueConsumerScopeError":
        from agentos.multi.redis_queue import RedisAgentMessageQueueConsumerScopeError

        return RedisAgentMessageQueueConsumerScopeError
    raise AttributeError(name)


__all__ = [
    "AgentCard",
    "AgentCoordinator",
    "AgentCoordinatorPlanStepDispatcher",
    "AgentCoordinationTools",
    "AgentEnvelope",
    "AgentEnvelopeType",
    "AgentInbox",
    "AgentInboxError",
    "AgentInboxFullError",
    "AgentInboxMissingError",
    "AgentMessageQueue",
    "AgentTaskNoticeProvider",
    "AgentTaskNoticeStore",
    "AllowAllTeamToolAuthorizationPolicy",
    "ContinuationErrorRecord",
    "ContinuationTrigger",
    "ExpertAgentRunner",
    "AgentLifecycle",
    "AgentRegistry",
    "AgentStatus",
    "ContextInitStrategy",
    "CoordinationMode",
    "DefaultTeamToolAuthorizationPolicy",
    "InMemoryTeamStore",
    "InMemoryTeamWorkerCancellationStore",
    "InMemoryTeamWorkerSessionProvider",
    "InMemoryTeamWorkerRetryStore",
    "InMemoryRegistry",
    "LocalContinuationTrigger",
    "LocalTeamWakeupTrigger",
    "InMemoryTeamUiStreamStore",
    "OutboxEntry",
    "OutboxReconciler",
    "PostgresTaskStore",
    "PostgresTeamStore",
    "PostgresTeamUiStreamStore",
    "PostgresTeamWorkerCancellationStore",
    "PostgresTeamWorkerRetryStore",
    "QueueDelivery",
    "RemoteTaskExecutor",
    "RedisAgentMessageQueue",
    "RedisAgentMessageQueueConsumerScopeError",
    "RedisContinuationTrigger",
    "SpawnExecutor",
    "SubagentInitRequest",
    "SubagentFactory",
    "TaskAlreadySubmittedError",
    "TaskHandle",
    "TaskClaim",
    "TaskRecord",
    "TaskRequest",
    "TaskResult",
    "TaskStatus",
    "TaskStore",
    "TaskTable",
    "TeamError",
    "TeamMemberRecord",
    "TeamMemberRole",
    "TeamMemberStatus",
    "TeamMembershipError",
    "TeamMessage",
    "TeamMessageKind",
    "TeamNotFoundError",
    "TeamNoticeProvider",
    "TeamNoticeStore",
    "TeamRecord",
    "TeamRuntime",
    "TeamStatus",
    "TeamStore",
    "TeamToolAuthorizationError",
    "TeamToolAuthorizationPolicy",
    "TeamTools",
    "TeamWakeupTrigger",
    "TeamUiEvent",
    "TeamUiEventKind",
    "TeamUiStreamStore",
    "TeamWorkerAgentProvider",
    "TeamWorkerCancellationRecord",
    "TeamWorkerCancellationStatus",
    "TeamWorkerCancellationStore",
    "TeamWorkerDaemon",
    "TeamWorkerDaemonState",
    "TeamWorkerDaemonStatus",
    "TeamWorkerPermissionError",
    "TeamWorkerPermissionPolicy",
    "TeamWorkerRetryPolicy",
    "TeamWorkerRetryRecord",
    "TeamWorkerRetryStatus",
    "TeamWorkerRetryStore",
    "TeamWorkerRunError",
    "TeamWorkerRunResult",
    "TeamWorkerRunner",
    "TeamWorkerSession",
    "TeamWorkerSessionProvider",
    "TeamWorkerSessionRequest",
    "TeamWorkerSessionStatus",
]
