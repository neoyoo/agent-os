from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import fields, is_dataclass
import json
from enum import Enum

from agentos.runtime.stream_events import (
    AssistantContentDelta,
    AssistantThinkingDelta,
    ContextLoaded,
    FinalResult,
    PlanUpdated,
    SkillLoaded,
    StatusUpdate,
    ToolStreamCompleted,
    ToolStreamFailed,
    ToolStreamStarted,
    TurnStreamCompleted,
    TurnStreamEvent,
    TurnStreamWaiting,
)


def event_type(event: object) -> str:
    """返回 channel adapter 使用的稳定 event type。"""

    if isinstance(event, AssistantContentDelta):
        return "content_delta"
    if isinstance(event, AssistantThinkingDelta):
        return "thinking_delta"
    if isinstance(event, StatusUpdate):
        return "status_update"
    if isinstance(event, ContextLoaded):
        return "context_loaded"
    if isinstance(event, SkillLoaded):
        return "skill_loaded"
    if isinstance(event, PlanUpdated):
        return "plan_updated"
    if isinstance(event, FinalResult):
        return "final_result"
    if isinstance(event, ToolStreamStarted):
        return "tool_started"
    if isinstance(event, ToolStreamCompleted):
        return "tool_completed"
    if isinstance(event, ToolStreamFailed):
        return "tool_failed"
    if isinstance(event, TurnStreamCompleted):
        return "done"
    if isinstance(event, TurnStreamWaiting):
        return "waiting"
    return type(event).__name__


def event_payload(event: object) -> dict[str, object]:
    """把 typed event 转成 JSON-safe payload。"""

    if not is_dataclass(event):
        return {}
    payload: dict[str, object] = {}
    for field in fields(event):
        key = field.name
        value = getattr(event, key)
        payload[key] = _json_safe(value)
    return payload


def _json_safe(value: object) -> object:
    if isinstance(value, BaseException):
        return str(value)
    if is_dataclass(value):
        return {
            field.name: _json_safe(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    return str(value)


def event_to_json(event: object, *, show_thinking: bool = True) -> str | None:
    """把 typed event 转成 JSONL 字符串。"""

    if isinstance(event, AssistantThinkingDelta) and not show_thinking:
        return None
    payload = {"type": event_type(event), **event_payload(event)}
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def event_to_sse(event: object, *, show_thinking: bool = True) -> str | None:
    """把 typed event 转成 SSE chunk。"""

    payload = event_to_json(event, show_thinking=show_thinking)
    if payload is None:
        return None
    return f"event: {event_type(event)}\ndata: {payload}\n\n"


async def iter_jsonl(
    events: AsyncIterator[TurnStreamEvent],
    *,
    show_thinking: bool = False,
) -> AsyncIterator[str]:
    """把调用方已有的事件流投影为 JSON Lines。"""

    async for event in events:
        payload = event_to_json(event, show_thinking=show_thinking)
        if payload is not None:
            yield payload + "\n"


async def iter_sse(
    events: AsyncIterator[TurnStreamEvent],
    *,
    show_thinking: bool = False,
) -> AsyncIterator[str]:
    """把调用方已有的事件流投影为 SSE chunk。"""

    async for event in events:
        payload = event_to_sse(event, show_thinking=show_thinking)
        if payload is not None:
            yield payload
