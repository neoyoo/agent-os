from __future__ import annotations

from dataclasses import asdict, is_dataclass
import json

from agentos.runtime.stream_events import (
    AssistantContentDelta,
    AssistantThinkingDelta,
    ToolStreamCompleted,
    ToolStreamFailed,
    ToolStreamStarted,
    TurnStreamCompleted,
)


def event_type(event: object) -> str:
    """返回 channel adapter 使用的稳定 event type。"""

    if isinstance(event, AssistantContentDelta):
        return "content_delta"
    if isinstance(event, AssistantThinkingDelta):
        return "thinking_delta"
    if isinstance(event, ToolStreamStarted):
        return "tool_started"
    if isinstance(event, ToolStreamCompleted):
        return "tool_completed"
    if isinstance(event, ToolStreamFailed):
        return "tool_failed"
    if isinstance(event, TurnStreamCompleted):
        return "done"
    return type(event).__name__


def event_payload(event: object) -> dict[str, object]:
    """把 typed event 转成 JSON-safe payload。"""

    if not is_dataclass(event):
        return {}
    return {
        key: value
        for key, value in asdict(event).items()
        if isinstance(value, (str, int, float, bool, type(None), list, dict))
    }


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
