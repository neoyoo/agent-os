from __future__ import annotations

from typing import Literal

from agentos.runtime import (
    AssistantContentDelta,
    TurnStreamCompleted,
    TurnStreamEvent,
    event_to_json,
    event_to_sse,
)

StreamOutputFormat = Literal["text", "stream-json", "sse"]


def write_stream_event(
    event: TurnStreamEvent,
    *,
    output_format: StreamOutputFormat,
    show_thinking: bool,
) -> None:
    """按 CLI 输出格式投影并写出一个 stream event。"""

    if output_format == "text":
        if isinstance(event, AssistantContentDelta):
            print(event.text, end="", flush=True)
        elif isinstance(event, TurnStreamCompleted):
            print()
        return
    if output_format == "stream-json":
        payload = event_to_json(event, show_thinking=show_thinking)
        if payload is not None:
            print(payload)
        return
    chunk = event_to_sse(event, show_thinking=show_thinking)
    if chunk is not None:
        print(chunk, end="")
