import json
from typing import Protocol

from agentos._frozen_json import thaw_json
from agentos.capabilities.executor import ToolExecutionResult
from agentos.context import ContextState
from agentos.providers import ProviderToolCall
from agentos.runtime.stream_events import SkillLoaded


class ContextRuntimeBoundary(Protocol):
    """QueryLoop 依赖的 context runtime 边界。"""

    def snapshot(self) -> ContextState:
        """返回可渲染的 context snapshot。"""

    def set_runtime_notices(self, notices: tuple[str, ...]) -> None:
        """设置本轮 provider request 可见的一次性 runtime notice。"""

    def clear_runtime_notices(self) -> None:
        """清空一次性 runtime notice。"""


class TurnNoticeProvider(Protocol):
    """QueryLoop 依赖的一次性 turn notice 边界。"""

    def consume_notices(self) -> tuple[str, ...]:
        """返回并消费本轮 runtime notices。"""


class ToolCallRouterBoundary(Protocol):
    """QueryLoop 依赖的 tool call router 边界。"""

    def execute_tool_call(self, tool_call: object) -> object:
        """执行 provider tool call。"""


class StructuredLoggerBoundary(Protocol):
    """QueryLoop 依赖的结构化日志边界。"""

    def log(self, event: str, **fields: object) -> None:
        """记录一个结构化 runtime 事件。"""


def skill_loaded_event(
    tool_call: ProviderToolCall,
    result: ToolExecutionResult,
) -> SkillLoaded | None:
    """把 skill loader 工具结果提升为用户可见事件。"""

    if tool_call.name not in {"load_skill", "load_skill_resource"}:
        return None
    skill_name = str(
        tool_call.arguments.get("skill_name")
        or tool_call.arguments.get("name")
        or tool_call.arguments.get("skill")
        or "unknown",
    )
    resource = tool_call.arguments.get("resource")
    if resource is None:
        resource = tool_call.arguments.get("path")
    summary = result.content.strip().splitlines()[0] if result.content.strip() else None
    return SkillLoaded(
        skill_name=skill_name,
        resource=None if resource is None else str(resource),
        summary=summary,
    )


def duplicate_tool_call_result(
    tool_call: ProviderToolCall,
    applied_tool_signatures: set[str],
) -> ToolExecutionResult | None:
    """对同一 Turn 内完全相同的工具调用生成确定性抑制结果。"""

    signature = tool_call_signature(tool_call)
    if signature not in applied_tool_signatures:
        return None
    return ToolExecutionResult(
        tool_call_id=tool_call.id,
        content=(
            f"duplicate tool call ignored: {tool_call.name} with identical "
            "arguments was already applied in this turn; continue with the "
            "next step or return the final answer"
        ),
    )


def tool_call_signature(tool_call: ProviderToolCall) -> str:
    """返回工具名称和参数的稳定调用签名。"""

    return json.dumps(
        {
            "name": tool_call.name,
            "arguments": thaw_json(tool_call.arguments),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
