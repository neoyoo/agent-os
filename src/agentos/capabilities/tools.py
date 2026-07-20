from dataclasses import dataclass, field
from collections.abc import Awaitable, Callable, Mapping
from enum import Enum
from typing import Literal, TypeAlias

from agentos._waiting import WaitRequest
from agentos.capabilities.invocation import (
    ToolCompensationInvocation,
    ToolInvocation,
)
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

ToolHandler = Callable[
    [ToolInvocation],
    ToolHandlerResult | Awaitable[ToolHandlerResult],
]
"""统一 Tool handler；同步实现只允许在 ExecutionBackend 内部适配。"""

ToolCompensationHandler = Callable[
    [ToolCompensationInvocation],
    None | Awaitable[None],
]
"""compensatable Tool 的幂等补偿 handler。"""


class ToolConcurrencyPolicy(str, Enum):
    """Level 1 工具调用的显式并发策略。"""

    EXCLUSIVE = "exclusive"
    PARALLEL_SAFE = "parallel_safe"


class SideEffectPolicy(str, Enum):
    """Tool 外部副作用的显式恢复策略。"""

    PURE = "pure"
    IDEMPOTENT = "idempotent"
    DEDUPLICATED = "deduplicated"
    COMPENSATABLE = "compensatable"
    NON_RETRYABLE = "non_retryable"


@dataclass(frozen=True, slots=True)
class ToolExecutionContract:
    """一次调用在执行前冻结的并发与副作用声明。"""

    side_effect_policy: SideEffectPolicy
    concurrency_policy: ToolConcurrencyPolicy
    wait_capable: bool = False

    def __post_init__(self) -> None:
        if type(self.side_effect_policy) is not SideEffectPolicy:
            raise TypeError("side_effect_policy must be SideEffectPolicy")
        if type(self.concurrency_policy) is not ToolConcurrencyPolicy:
            raise TypeError("concurrency_policy must be ToolConcurrencyPolicy")
        if type(self.wait_capable) is not bool:
            raise TypeError("wait_capable must be bool")
        if self.wait_capable and (
            self.side_effect_policy is not SideEffectPolicy.PURE
            or self.concurrency_policy is not ToolConcurrencyPolicy.EXCLUSIVE
        ):
            raise ValueError("wait_capable requires pure and exclusive execution")


@dataclass(frozen=True, slots=True)
class RegisteredTool:
    """ToolRegistry 中的工具声明和 handler。"""

    name: str
    description: str
    parameters: Mapping[str, FrozenJsonValue]
    handler: ToolHandler
    side_effect_policy: SideEffectPolicy
    kind: ToolKind = "external"
    concurrency_policy: ToolConcurrencyPolicy = ToolConcurrencyPolicy.EXCLUSIVE
    wait_capable: bool = False
    compensation_handler: ToolCompensationHandler | None = None
    metadata: Mapping[str, FrozenJsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if type(self.side_effect_policy) is not SideEffectPolicy:
            raise TypeError("side_effect_policy must be SideEffectPolicy")
        if type(self.concurrency_policy) is not ToolConcurrencyPolicy:
            raise TypeError("concurrency_policy must be ToolConcurrencyPolicy")
        if self.kind not in {"external", "context", "skill", "mcp"}:
            raise ValueError("kind is invalid")
        if not callable(self.handler):
            raise TypeError("handler must be callable")
        if type(self.wait_capable) is not bool:
            raise TypeError("wait_capable must be bool")
        compensatable = self.side_effect_policy is SideEffectPolicy.COMPENSATABLE
        if compensatable != (self.compensation_handler is not None):
            raise ValueError(
                "compensation handler is required only for compensatable tools",
            )
        if self.compensation_handler is not None and not callable(
            self.compensation_handler,
        ):
            raise TypeError("compensation_handler must be callable")
        if self.wait_capable and (
            self.side_effect_policy is not SideEffectPolicy.PURE
            or self.concurrency_policy is not ToolConcurrencyPolicy.EXCLUSIVE
        ):
            raise ValueError(
                "wait_capable requires pure side effects and exclusive concurrency",
            )
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

    def execution_contract(self) -> ToolExecutionContract:
        """返回 runtime preflight 使用的 immutable 执行声明。"""

        return ToolExecutionContract(
            self.side_effect_policy,
            self.concurrency_policy,
            self.wait_capable,
        )
