from __future__ import annotations

from dataclasses import dataclass
import re


_EVENT_NAME = re.compile(r"[A-Za-z0-9_]+")


@dataclass(frozen=True, slots=True)
class SseEventFrame:
    """一个确定性 SSE event frame。"""

    event: str
    data: str
    event_id: str | None = None

    def __post_init__(self) -> None:
        if type(self.event) is not str or _EVENT_NAME.fullmatch(self.event) is None:
            raise ValueError("SSE event name is invalid")
        if type(self.data) is not str or "\n" in self.data or "\r" in self.data:
            raise ValueError("SSE data must be single-line JSON")
        if self.event_id is not None and (
            type(self.event_id) is not str
            or not self.event_id
            or "\n" in self.event_id
            or "\r" in self.event_id
        ):
            raise ValueError("SSE event id is invalid")

    def render(self) -> str:
        """按 id/event/data 固定顺序输出 frame。"""

        event_id = "" if self.event_id is None else f"id: {self.event_id}\n"
        return f"{event_id}event: {self.event}\ndata: {self.data}\n\n"


@dataclass(frozen=True, slots=True)
class SseCommentFrame:
    """不推进 cursor 的 SSE comment frame。"""

    comment: str

    def __post_init__(self) -> None:
        if (
            type(self.comment) is not str
            or not self.comment
            or "\n" in self.comment
            or "\r" in self.comment
        ):
            raise ValueError("SSE comment is invalid")

    def render(self) -> str:
        return f": {self.comment}\n\n"


__all__ = ["SseCommentFrame", "SseEventFrame"]
