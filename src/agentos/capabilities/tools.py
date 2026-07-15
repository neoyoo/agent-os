from dataclasses import dataclass, field
from collections.abc import Awaitable, Callable, Mapping
from enum import Enum
from typing import Literal, TypeAlias

from agentos._waiting import WaitRequest
from agentos.providers import (
    ProviderFunctionSpec,
    ProviderToolSpec,
)
from agentos._json_values import (
    FrozenJsonValue,
    freeze_json_mapping,
)


ToolKind = Literal["external", "context", "skill", "mcp"]

ToolHandlerResult: TypeAlias = str | WaitRequest

ToolHandler = Callable[[dict[str, object]], ToolHandlerResult]
"""同步外部工具 handler。"""

AsyncToolHandler = Callable[[dict[str, object]], Awaitable[ToolHandlerResult]]
"""异步外部工具 handler。"""


class ToolConcurrencyPolicy(str, Enum):
    """Level 1 工具调用的显式并发策略。"""

    EXCLUSIVE = "exclusive"
    PARALLEL_SAFE = "parallel_safe"


@dataclass(frozen=True, slots=True)
class RegisteredTool:
    """ToolRegistry 中的工具声明和 handler。"""

    name: str
    description: str
    parameters: Mapping[str, FrozenJsonValue]
    handler: ToolHandler | AsyncToolHandler
    kind: ToolKind = "external"
    concurrency_policy: ToolConcurrencyPolicy = ToolConcurrencyPolicy.EXCLUSIVE
    metadata: Mapping[str, FrozenJsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "parameters", freeze_json_mapping(self.parameters))
        object.__setattr__(self, "metadata", freeze_json_mapping(self.metadata))

    def provider_spec(self) -> ProviderToolSpec:
        """转换为 provider tools 参数中的 schema。"""

        return ProviderToolSpec(
            function=ProviderFunctionSpec(
                name=self.name,
                description=self.description,
                parameters=self.parameters,
            ),
        )
