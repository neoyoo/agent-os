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
from agentos.multi.spawn import SpawnExecutor
from agentos.multi.task_store import TaskClaim, TaskStore
from agentos.multi.tasks import TaskTable
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
    if name == "RedisAgentMessageQueue":
        from agentos.multi.redis_queue import RedisAgentMessageQueue

        return RedisAgentMessageQueue
    raise AttributeError(name)

__all__ = [
    "AgentCard",
    "AgentCoordinator",
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
    "ContinuationErrorRecord",
    "ContinuationTrigger",
    "ExpertAgentRunner",
    "AgentLifecycle",
    "AgentRegistry",
    "AgentStatus",
    "ContextInitStrategy",
    "CoordinationMode",
    "InMemoryRegistry",
    "LocalContinuationTrigger",
    "PostgresTaskStore",
    "QueueDelivery",
    "RemoteTaskExecutor",
    "RedisAgentMessageQueue",
    "SpawnExecutor",
    "SubagentInitRequest",
    "SubagentFactory",
    "TaskHandle",
    "TaskClaim",
    "TaskRecord",
    "TaskRequest",
    "TaskResult",
    "TaskStatus",
    "TaskStore",
    "TaskTable",
]
