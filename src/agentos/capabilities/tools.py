from dataclasses import dataclass, field
from collections.abc import Awaitable, Callable
from typing import Literal

from agentos.providers import (
    ProviderFunctionSpec,
    ProviderToolSpec,
)


ToolKind = Literal["external", "context", "skill", "mcp"]


ToolHandler = Callable[[dict[str, object]], str]
"""同步外部工具 handler。"""

AsyncToolHandler = Callable[[dict[str, object]], Awaitable[str]]
"""异步外部工具 handler。"""


@dataclass(frozen=True, slots=True)
class RegisteredTool:
    """ToolRegistry 中的工具声明和 handler。"""

    name: str
    description: str
    parameters: dict[str, object]
    handler: ToolHandler | AsyncToolHandler
    kind: ToolKind = "external"
    metadata: dict[str, object] = field(default_factory=dict)

    def provider_spec(self) -> ProviderToolSpec:
        """转换为 provider tools 参数中的 schema。"""

        return ProviderToolSpec(
            function=ProviderFunctionSpec(
                name=self.name,
                description=self.description,
                parameters=dict(self.parameters),
            ),
        )
