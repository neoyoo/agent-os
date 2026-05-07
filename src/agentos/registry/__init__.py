"""AgentCard 注册发现和远程 resolver 边界。"""

from agentos.multi import AgentCard
from agentos.registry.persistent import (
    AgentRegistryStore,
    InMemoryAgentRegistryStore,
    JsonFileAgentRegistryStore,
    PersistentAgentRegistry,
)
from agentos.registry.postgres import PostgresAgentRegistryStore
from agentos.registry.resolver import AgentResolver, ServiceResolver, StaticResolver
from agentos.registry.types import AgentRegistryRecord, SessionAffinity

__all__ = [
    "AgentCard",
    "AgentRegistryRecord",
    "AgentRegistryStore",
    "AgentResolver",
    "InMemoryAgentRegistryStore",
    "JsonFileAgentRegistryStore",
    "PersistentAgentRegistry",
    "PostgresAgentRegistryStore",
    "ServiceResolver",
    "SessionAffinity",
    "StaticResolver",
]
