from dataclasses import dataclass, field
from typing import Literal, Protocol

from agentos.providers import ProviderToolSpec


ToolKind = Literal["external", "context", "skill", "mcp"]


class ToolHandler(Protocol):
    """外部工具 handler。"""

    def __call__(self, arguments: dict[str, object]) -> str:
        """执行工具并返回可写入 tool result 的字符串。"""


@dataclass(frozen=True, slots=True)
class RegisteredTool:
    """ToolRegistry 中的工具声明和 handler。"""

    name: str
    description: str
    parameters: dict[str, object]
    handler: ToolHandler
    kind: ToolKind = "external"
    metadata: dict[str, object] = field(default_factory=dict)

    def provider_spec(self) -> ProviderToolSpec:
        """转换为 provider tools 参数中的 schema。"""

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": dict(self.parameters),
            },
        }
