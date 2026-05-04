from dataclasses import dataclass

from agentos.capabilities.registry import ToolRegistry
from agentos.policies import SecurityPolicy
from agentos.providers import ProviderToolCall


class ToolExecutionError(RuntimeError):
    """工具执行失败。"""


@dataclass(frozen=True, slots=True)
class ToolExecutionResult:
    """标准化工具执行结果。"""

    tool_call_id: str
    content: str


@dataclass(slots=True)
class ToolExecutor:
    """执行外部工具，并在执行前应用安全策略。"""

    registry: ToolRegistry
    security_policy: SecurityPolicy

    def execute(self, tool_call: ProviderToolCall) -> ToolExecutionResult:
        """执行 provider tool call 对应的外部工具。"""

        self.security_policy.ensure_tool_allowed(tool_call.name)
        try:
            tool = self.registry.get(tool_call.name)
        except KeyError as error:
            raise ToolExecutionError(f"unknown tool: {tool_call.name}") from error
        return ToolExecutionResult(
            tool_call_id=tool_call.id,
            content=tool.handler(dict(tool_call.arguments)),
        )
