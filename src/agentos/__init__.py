"""Agent OS：以 context-first 架构构建的 Python agent runtime SDK。"""

from agentos.capabilities import ToolCallRouter
from agentos.channels import (
    A2AAdapter,
    A2AServerAdapter,
    AsgiAgentApp,
    HttpAgentChannel,
    InMemoryAgentSessionProvider,
    SseAgentChannel,
)
from agentos.hooks import HookManager
from agentos.memory import (
    CompressedSegmentPackage,
    MemoryRuntime,
    QdrantRecallIndex,
    RedisHotSessionStore,
    SegmentRecallDocument,
)
from agentos.multi import (
    AgentCard,
    AgentCoordinator,
    AgentInbox,
    InMemoryRegistry,
    RemoteTaskExecutor,
    TaskTable,
)
from agentos.providers import Provider
from agentos.persistence import PostgresDurableSessionStore
from agentos.registry import (
    AgentResolver,
    PersistentAgentRegistry,
    PostgresAgentRegistryStore,
    ServiceResolver,
    StaticResolver,
)
from agentos.runtime import (
    Agent,
    AgentResult,
    ProviderRequestBuilder,
    QueryLoop,
    RunOptions,
)

__all__ = [
    "A2AAdapter",
    "A2AServerAdapter",
    "Agent",
    "AgentCard",
    "AgentCoordinator",
    "AgentInbox",
    "AgentResolver",
    "AgentResult",
    "AsgiAgentApp",
    "CompressedSegmentPackage",
    "HookManager",
    "HttpAgentChannel",
    "InMemoryAgentSessionProvider",
    "InMemoryRegistry",
    "MemoryRuntime",
    "PersistentAgentRegistry",
    "PostgresAgentRegistryStore",
    "PostgresDurableSessionStore",
    "Provider",
    "ProviderRequestBuilder",
    "QdrantRecallIndex",
    "QueryLoop",
    "RedisHotSessionStore",
    "RemoteTaskExecutor",
    "RunOptions",
    "SegmentRecallDocument",
    "ServiceResolver",
    "SseAgentChannel",
    "StaticResolver",
    "TaskTable",
    "ToolCallRouter",
    "__version__",
]

__version__ = "0.1.0"
