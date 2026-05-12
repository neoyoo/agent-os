from __future__ import annotations

from dataclasses import dataclass
import json


@dataclass(frozen=True, slots=True)
class ChannelTurnRequest:
    """Channel 层归一化后的 turn 请求。"""

    message: str
    thinking: bool = False
    show_thinking: bool = False


@dataclass(frozen=True, slots=True)
class ChannelTurnResult:
    """Channel 层归一化后的 turn 响应。"""

    session_id: str
    status: str
    content: str = ""
    error: str | None = None
    status_code: int = 200


class ChannelError(Exception):
    """Channel 请求无法映射到 agent turn。"""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def parse_channel_turn_request(body: bytes | str) -> ChannelTurnRequest:
    """解析 channel turn JSON body。"""

    raw = body.decode("utf-8") if isinstance(body, bytes) else body
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("invalid JSON request body") from error
    if not isinstance(payload, dict):
        raise ValueError("JSON request body must be an object")
    message = payload.get("message")
    if not isinstance(message, str) or not message.strip():
        raise ValueError("message is required")
    return ChannelTurnRequest(
        message=message,
        thinking=bool(payload.get("thinking", False)),
        show_thinking=bool(payload.get("show_thinking", False)),
    )
