from dataclasses import dataclass
from typing import Literal, Mapping, Protocol


HookFailurePolicy = Literal["continue", "raise"]
HookName = Literal[
    "before_provider_call",
    "after_provider_call",
    "before_tool_call",
    "after_tool_call",
]
HookAction = Literal["allow", "deny", "modify"]


@dataclass(frozen=True, slots=True)
class HookContext:
    """传给 hook handler 的只读调用上下文。"""

    name: HookName
    payload: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class HookResult:
    """hook 对执行流的显式决策。"""

    action: HookAction = "allow"
    payload: dict[str, object] | None = None
    reason: str | None = None


class HookHandler(Protocol):
    """处理显式 hook point 的 callable。"""

    def __call__(self, context: HookContext) -> HookResult | None:
        """处理 hook context，并可返回执行决策。"""


@dataclass(frozen=True, slots=True)
class HookExecutionFailure:
    """记录 hook 执行失败，供测试和后续 observability 使用。"""

    hook_name: HookName
    error: str
