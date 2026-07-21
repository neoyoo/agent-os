from __future__ import annotations

import json

from agentos._json_values import FrozenJsonObject, thaw_json_value
from agentos.distributed.models import ReplayItem, StreamGap
from agentos.transports.run_stream import (
    is_terminal_event,
    project_replay_item,
    project_stream_gap,
)
from agentos.transports.sse.frames import SseCommentFrame, SseEventFrame


def encode_replay_event(item: ReplayItem) -> str:
    """把 typed replay item 编码为 scoped SSE frame。"""

    projection = project_replay_item(item)
    frame = SseEventFrame(
        event=projection.event.kind,
        data=_json(projection.data),
        event_id=projection.cursor,
    )
    return frame.render()


def encode_heartbeat() -> str:
    """编码无 cursor、无 data 的 heartbeat comment。"""

    return SseCommentFrame("heartbeat").render()


def encode_gap(gap: StreamGap) -> str:
    """编码单次 gap frame，不伪造可继续 cursor。"""

    return SseEventFrame(
        event="stream_gap",
        data=_json(project_stream_gap(gap)),
    ).render()


def _json(value: FrozenJsonObject) -> str:
    return json.dumps(
        thaw_json_value(value),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )
__all__ = [
    "encode_gap",
    "encode_heartbeat",
    "encode_replay_event",
    "is_terminal_event",
]
