"""工具、context tools、MCP 和 skills 的能力层。"""

from typing import TYPE_CHECKING

from agentos.capabilities.backend import ExecutionBackend, InProcessExecutionBackend
from agentos.capabilities.builtin import BuiltinToolError, read_file_tool
from agentos.capabilities.executor import (
    ToolExecutionError,
    ToolExecutionOutcome,
    ToolExecutionResult,
)
from agentos.capabilities.mcp import (
    MCPClient,
    MCPRegistry,
    MCPServerRegistration,
    MCPToolAdapter,
    MCPToolInfo,
)
from agentos.capabilities.registry import ToolRegistry
from agentos.capabilities.router import ToolCallRouter
from agentos.capabilities.sandbox import (
    ToolPathSandboxRule,
    ToolSandboxError,
    ToolSandboxPolicy,
    WorkspaceToolSandboxPolicy,
)
from agentos.capabilities.skills import (
    BuiltinSkillSource,
    ChainedSkillSource,
    FileSystemSkillSource,
    SkillContentSource,
    SkillDefinition,
    SkillLoadResult,
    SkillRegistry,
    SkillResourceLoadResult,
    SkillResourceRef,
    builtin_schema_template_skill,
    register_skill_loader_tools,
)
from agentos.capabilities.tools import (
    RegisteredTool,
    SideEffectPolicy,
    ToolConcurrencyPolicy,
    ToolCompensationHandler,
    ToolHandler,
    ToolHandlerResult,
    WaitRequest,
)
from agentos.capabilities.invocation import (
    ToolCompensationContext,
    ToolCompensationInvocation,
    ToolInvocation,
    ToolInvocationContext,
)
from agentos.capabilities.result_refs import (
    ArtifactToolResultRef,
    InlineToolResultRef,
    ToolResultRef,
)

if TYPE_CHECKING:
    from agentos.capabilities.skill_activation import SQLiteSkillActivationStore


def __getattr__(name: str) -> object:
    """惰性导出 Durable Adapter，保持基础 Capability import 轻量。"""

    if name == "SQLiteSkillActivationStore":
        from agentos.capabilities.skill_activation import SQLiteSkillActivationStore

        return SQLiteSkillActivationStore
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "ArtifactToolResultRef",
    "BuiltinSkillSource",
    "BuiltinToolError",
    "ChainedSkillSource",
    "ExecutionBackend",
    "FileSystemSkillSource",
    "InProcessExecutionBackend",
    "MCPClient",
    "MCPRegistry",
    "MCPServerRegistration",
    "MCPToolAdapter",
    "MCPToolInfo",
    "RegisteredTool",
    "SideEffectPolicy",
    "SQLiteSkillActivationStore",
    "SkillContentSource",
    "SkillDefinition",
    "SkillLoadResult",
    "SkillRegistry",
    "SkillResourceLoadResult",
    "SkillResourceRef",
    "ToolPathSandboxRule",
    "ToolSandboxError",
    "ToolSandboxPolicy",
    "ToolExecutionError",
    "ToolExecutionOutcome",
    "ToolExecutionResult",
    "ToolCallRouter",
    "ToolCompensationContext",
    "ToolCompensationHandler",
    "ToolCompensationInvocation",
    "ToolConcurrencyPolicy",
    "ToolHandler",
    "ToolHandlerResult",
    "ToolInvocation",
    "ToolInvocationContext",
    "ToolRegistry",
    "ToolResultRef",
    "InlineToolResultRef",
    "WaitRequest",
    "WorkspaceToolSandboxPolicy",
    "builtin_schema_template_skill",
    "read_file_tool",
    "register_skill_loader_tools",
]
