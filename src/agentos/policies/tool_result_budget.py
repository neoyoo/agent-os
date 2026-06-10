from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import os

from agentos.tokens import TokenCounter


@dataclass(frozen=True, slots=True)
class ToolResultBudget:
    """工具结果进入 message history 前的 token 上限。"""

    default_max_tokens: int = 25_000
    overrides: Mapping[str, int] = field(default_factory=dict)
    env_var: str = "AGENTOS_TOOL_RESULT_MAX_TOKENS"

    def __post_init__(self) -> None:
        """校验默认上限。"""

        if self.default_max_tokens < 1:
            raise ValueError("default_max_tokens must be at least 1")

    def cap_for(self, tool_name: str) -> int:
        """返回某个工具适用的 token cap。"""

        env_cap = self._env_cap()
        if env_cap is not None:
            return env_cap
        override = self.overrides.get(tool_name)
        if isinstance(override, int) and override > 0:
            return override
        return self.default_max_tokens

    def _env_cap(self) -> int | None:
        value = os.environ.get(self.env_var)
        if value is None:
            return None
        try:
            parsed = int(value)
        except ValueError:
            return None
        if parsed < 1:
            return None
        return parsed


@dataclass(frozen=True, slots=True)
class CappedToolResult:
    """工具结果 cap 检查后的可写入内容。"""

    content: str
    actual_tokens: int
    cap: int
    capped: bool


def cap_tool_result_content(
    *,
    tool_name: str,
    content: str,
    budget: ToolResultBudget,
    token_counter: TokenCounter,
) -> CappedToolResult:
    """按预算检查工具结果，超限时返回小体积 nudge。"""

    actual_tokens = token_counter.count_text(content)
    cap = budget.cap_for(tool_name)
    if actual_tokens <= cap:
        return CappedToolResult(
            content=content,
            actual_tokens=actual_tokens,
            cap=cap,
            capped=False,
        )
    return CappedToolResult(
        content=_tool_result_nudge(tool_name, actual_tokens, cap),
        actual_tokens=actual_tokens,
        cap=cap,
        capped=True,
    )


def _tool_result_nudge(tool_name: str, actual_tokens: int, cap: int) -> str:
    return (
        f'[tool result omitted: ~{actual_tokens} tokens exceeds the {cap} '
        f'token limit for "{tool_name}". Re-run this tool with a narrower '
        "request; use pagination, range, or filter parameters if available, "
        "or request a more specific subset.]"
    )
