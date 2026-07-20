from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from agentos._waiting import WaitRequest
from agentos.artifacts import ArtifactRef
from agentos.capabilities.executor import ToolExecutionOutcome, ToolExecutionResult
from agentos.capabilities.tools import ToolConcurrencyPolicy
from agentos.context import ContextState
from agentos.messages import MessageRuntime
from agentos.policies import ToolResultBudget
from agentos.policies.tool_result_budget import cap_tool_result_content
from agentos.providers import ProviderToolCall
from agentos._json_values import thaw_json
from agentos.runtime.event_bus import ToolResultCappedEvent
from agentos.runtime.execution import RunExecutionCursor
from agentos.runtime.continuation import ContinuationNotice
from agentos.runtime.stream_events import SkillLoaded, ToolStreamCompleted
from agentos.runtime.tool_scheduler import ScheduledToolCallResult
from agentos.tokens import TokenCounter


@dataclass(frozen=True, slots=True)
class _FinalContent:
    content: str


class _ToolCallFailure(Exception):
    def __init__(
        self,
        call: ProviderToolCall,
        error: Exception,
        *,
        expose_error: bool = True,
    ) -> None:
        super().__init__(str(error))
        self.call = call
        self.error = error
        self.expose_error = expose_error


class ContextRuntimeBoundary(Protocol):
    """QueryLoop 依赖的 context runtime 边界。"""

    def snapshot(self) -> ContextState:
        """返回可渲染的 context snapshot。"""

class ArtifactRuntimeBoundary(Protocol):
    """TurnLifecycle 依赖的 Session Artifact 边界。"""

    async def prepare_user_uploads(
        self,
        handles: tuple[str, ...],
    ) -> tuple[ArtifactRef, ...]:
        """解析用户 Artifact handle 并建立当前 Turn mount。"""

    async def prepare_projection_cache(self) -> None:
        """加载当前 Provider attempt 使用的 Artifact 投影。"""

    def clear_mounts(self) -> object:
        """清理当前 Turn 的 Artifact mounts。"""


class TurnNoticeProvider(Protocol):
    """QueryLoop 依赖的一次性 turn notice 边界。"""

    def consume_notices(self) -> tuple[ContinuationNotice, ...]:
        """返回并消费本轮 runtime notices。"""


class ToolCallRouterBoundary(Protocol):
    """QueryLoop 依赖的 tool call router 边界。"""

    async def async_execute_tool_call(
        self,
        tool_call: ProviderToolCall,
    ) -> ToolExecutionOutcome:
        """异步执行单个 provider tool call。"""

    def concurrency_policy_for(
        self,
        tool_call: ProviderToolCall,
    ) -> ToolConcurrencyPolicy:
        """返回单个调用的正式并发策略。"""


class StructuredLoggerBoundary(Protocol):
    """QueryLoop 依赖的结构化日志边界。"""

    def log(self, event: str, **fields: object) -> None:
        """记录一个结构化 runtime 事件。"""


@dataclass(frozen=True, slots=True)
class ToolResultCapMapping:
    """保存 tool result 裁剪后的结果与可选 typed event。"""

    result: ToolExecutionResult
    event: ToolResultCappedEvent | None


@dataclass(frozen=True, slots=True)
class WaitingToolBatchResolution:
    """描述等待请求选定后仍可对外完成的工具调用。"""

    request: WaitRequest
    visible_started_call_ids: frozenset[str]
    peer_results: tuple[tuple[ProviderToolCall, ToolExecutionResult], ...]


def resolve_waiting_tool_batch(
    *,
    calls: tuple[ProviderToolCall, ...],
    immediate: Mapping[str, ToolExecutionResult],
    completed: tuple[ScheduledToolCallResult, ...],
) -> WaitingToolBatchResolution | None:
    """按 Provider 顺序选择等待请求及其已成功的同批调用。"""

    wait_item = next(
        (item for item in completed if isinstance(item.result, WaitRequest)),
        None,
    )
    if wait_item is None:
        return None
    raw_results = dict(immediate)
    raw_results.update((item.tool_call.id, item.result) for item in completed)
    peer_results: list[tuple[ProviderToolCall, ToolExecutionResult]] = []
    for call in calls:
        result = raw_results.get(call.id)
        if isinstance(result, ToolExecutionResult):
            peer_results.append((call, result))
    visible_ids = {call.id for call, _result in peer_results}
    visible_ids.add(wait_item.tool_call.id)
    return WaitingToolBatchResolution(
        request=wait_item.result,
        visible_started_call_ids=frozenset(visible_ids),
        peer_results=tuple(peer_results),
    )


def map_tool_result_cap(
    tool_call: ProviderToolCall,
    result: ToolExecutionResult,
    *,
    budget: ToolResultBudget,
    token_counter: TokenCounter,
    session_id: str | None,
    turn_id: str | None,
) -> ToolResultCapMapping:
    """纯映射 tool result 裁剪结果，不发出 EventBus 副作用。"""

    capped = cap_tool_result_content(
        tool_name=tool_call.name,
        content=result.content,
        budget=budget,
        token_counter=token_counter,
    )
    if not capped.capped:
        return ToolResultCapMapping(result, None)
    return ToolResultCapMapping(
        ToolExecutionResult(result.tool_call_id, capped.content),
        ToolResultCappedEvent(
            session_id=session_id,
            turn_id=turn_id,
            tool_name=tool_call.name,
            tool_call_id=tool_call.id,
            actual_tokens=capped.actual_tokens,
            cap=capped.cap,
        ),
    )


def waiting_peer_completion(
    tool_call: ProviderToolCall,
    result: ToolExecutionResult,
    after_tool_call: Callable[
        [ProviderToolCall, ToolExecutionResult],
        ToolExecutionResult,
    ],
    cap_result: Callable[
        [ProviderToolCall, ToolExecutionResult],
        ToolResultCapMapping,
    ],
) -> tuple[ToolStreamCompleted, ToolResultCappedEvent | None]:
    """把 WAITING 同批成功结果映射为预算受限的终止流事件。"""

    try:
        result = after_tool_call(tool_call, result)
    except Exception as error:
        raise _ToolCallFailure(tool_call, error) from error
    capped = cap_result(tool_call, result)
    return (
        ToolStreamCompleted(tool_call.name, tool_call.id, capped.result.content),
        capped.event,
    )


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


def restored_tool_loop_state(
    messages: MessageRuntime,
    cursor: RunExecutionCursor,
) -> tuple[int, set[str]]:
    """从持久化消息和执行游标重建当前 Turn 的工具循环状态。"""

    completed_batches = cursor.provider_call_index
    stored = messages.store.all()
    boundary = len(stored)
    if cursor.assistant_message_id is not None:
        matched_boundary = next(
            (
                index
                for index, message in enumerate(stored)
                if message.id == cursor.assistant_message_id
            ),
            None,
        )
        if matched_boundary is None:
            raise RuntimeError("recovery cursor assistant message is missing")
        boundary = matched_boundary
    if cursor.stage == "after_tools":
        completed_batches += 1
        boundary += 1
    if completed_batches == 0:
        return 0, set()
    assistant_batches = [
        message
        for message in stored[:boundary]
        if message.role == "assistant" and message.tool_calls
    ]
    signatures = {
        tool_call_signature(
            ProviderToolCall(call.id, call.name, call.arguments),
        )
        for message in assistant_batches[-completed_batches:]
        for call in message.tool_calls
    }
    return completed_batches, signatures
