"""Agent OS：以 context-first 架构构建的 Python agent runtime SDK。"""

from agentos.capabilities import ToolCallRouter
from agentos.hooks import HookManager
from agentos.memory import (
    CompressedSegmentPackage,
    MemoryRuntime,
    SegmentRecallDocument,
)
from agentos.multi import (
    AgentCard,
    AgentCoordinator,
    AgentInbox,
    InMemoryRegistry,
    TaskTable,
)
from agentos.providers import Provider
from agentos.runtime import (
    Agent,
    AgentResult,
    ProviderRequestBuilder,
    QueryLoop,
    RunOptions,
)

__all__ = [
    "Agent",
    "AgentCard",
    "AgentCoordinator",
    "AgentInbox",
    "AgentResult",
    "CompressedSegmentPackage",
    "HookManager",
    "InMemoryRegistry",
    "MemoryRuntime",
    "Provider",
    "ProviderRequestBuilder",
    "QueryLoop",
    "RunOptions",
    "SegmentRecallDocument",
    "TaskTable",
    "ToolCallRouter",
    "__version__",
]

__version__ = "0.1.0"
