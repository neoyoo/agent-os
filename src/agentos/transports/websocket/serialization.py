from __future__ import annotations

from dataclasses import asdict
import json
import math
import unicodedata
from typing import Any, TypeAlias

from agentos._json_values import thaw_json_value
from agentos.transports.websocket.frames import (
    ErrorFrame,
    EventFrame,
    ReceiptFrame,
    ResumeCursor,
    StreamGapFrame,
    SubmitCommandFrame,
    SubmitCommandReceiptData,
    SubmitRunFrame,
    SubmitRunReceiptData,
    SubscribeRunFrame,
    SubscriptionReceiptData,
    UnsubscribeRunFrame,
)


MAX_CLIENT_FRAME_BYTES = 256 * 1024
MAX_CLIENT_JSON_DEPTH = 32
ClientFrame: TypeAlias = (
    SubmitRunFrame | SubmitCommandFrame | SubscribeRunFrame | UnsubscribeRunFrame
)
ServerFrame: TypeAlias = ReceiptFrame | EventFrame | StreamGapFrame | ErrorFrame


class WebSocketDecodeError(ValueError):
    """固定脱敏的 WebSocket client frame 解码失败。"""

    def __init__(self, request_id: str | None = None) -> None:
        self.request_id = request_id
        super().__init__("invalid request")


class WebSocketFrameTooLargeError(WebSocketDecodeError):
    """Client frame 超过协议 hard max。"""

    def __init__(self) -> None:
        self.request_id = None
        ValueError.__init__(self, "frame is too large")


def decode_client_frame(text: str) -> ClientFrame:
    """严格解析一个 UTF-8 text JSON client frame。"""

    if type(text) is not str:
        raise TypeError("text must be str")
    if len(text) > MAX_CLIENT_FRAME_BYTES:
        raise WebSocketFrameTooLargeError()
    try:
        raw = text.encode("utf-8")
    except UnicodeEncodeError as error:
        raise WebSocketDecodeError() from error
    if len(raw) > MAX_CLIENT_FRAME_BYTES:
        raise WebSocketFrameTooLargeError()
    try:
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (json.JSONDecodeError, RecursionError, ValueError) as error:
        raise WebSocketDecodeError() from error
    if type(value) is not dict:
        raise WebSocketDecodeError()
    request_id = _trusted_request_id(value.get("request_id"))
    try:
        _validate_json_value(value, depth=1)
        return _decode_client_object(value)
    except (TypeError, ValueError, RecursionError) as error:
        raise WebSocketDecodeError(request_id) from error


def encode_server_frame(frame: ServerFrame) -> str:
    """输出 compact、sorted 且可编码为 UTF-8 的 server frame JSON。"""

    payload = _server_frame_to_dict(frame)
    try:
        text = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
        return text.encode("utf-8").decode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise ValueError("server frame is not valid UTF-8 JSON") from error


def _decode_client_object(value: dict[str, Any]) -> ClientFrame:
    frame_type = value.get("type")
    if frame_type == "submit_run":
        _require_fields(
            value,
            {"type", "request_id", "session_id", "content", "artifact_handles"},
        )
        handles = value["artifact_handles"]
        if type(handles) is not list:
            raise ValueError("artifact_handles is invalid")
        return SubmitRunFrame(
            value["request_id"],
            value["session_id"],
            value["content"],
            tuple(handles),
        )
    if frame_type == "submit_command":
        _require_fields(
            value,
            {"type", "request_id", "session_id", "run_id", "kind", "payload"},
        )
        if type(value["payload"]) is not dict:
            raise ValueError("payload is invalid")
        return SubmitCommandFrame(
            value["request_id"],
            value["session_id"],
            value["run_id"],
            value["kind"],
            value["payload"],
        )
    if frame_type == "subscribe_run":
        _require_fields(
            value,
            {"type", "request_id", "session_id", "run_id", "cursor"},
        )
        return SubscribeRunFrame(
            value["request_id"],
            value["session_id"],
            value["run_id"],
            value["cursor"],
        )
    if frame_type == "unsubscribe_run":
        _require_fields(
            value,
            {"type", "request_id", "session_id", "run_id"},
        )
        return UnsubscribeRunFrame(
            value["request_id"],
            value["session_id"],
            value["run_id"],
        )
    raise ValueError("client frame type is invalid")


def _server_frame_to_dict(frame: ServerFrame) -> dict[str, object]:
    if type(frame) is ReceiptFrame:
        return {
            "type": "receipt",
            "request_id": frame.request_id,
            "operation": frame.operation,
            "data": _receipt_data(frame),
        }
    if type(frame) is EventFrame:
        return {
            "type": "event",
            "session_id": frame.session_id,
            "run_id": frame.run_id,
            "cursor": frame.projection.cursor,
            "event_kind": frame.projection.event.kind,
            "data": thaw_json_value(frame.projection.data),
        }
    if type(frame) is StreamGapFrame:
        return {
            "type": "stream_gap",
            "session_id": frame.session_id,
            "run_id": frame.run_id,
            "reason": frame.projection.reason,
        }
    if type(frame) is ErrorFrame:
        payload: dict[str, object] = {
            "type": "error",
            "request_id": frame.request_id,
            "code": frame.code,
            "message": frame.message,
        }
        if frame.session_id is not None:
            payload.update(session_id=frame.session_id, run_id=frame.run_id)
        if frame.code == "slow_consumer":
            payload["resume_cursors"] = [
                _resume_cursor_to_dict(item) for item in frame.resume_cursors
            ]
        return payload
    raise TypeError("frame must be a typed WebSocket server frame")


def _receipt_data(frame: ReceiptFrame) -> dict[str, object]:
    data = frame.data
    if type(data) is SubmitRunReceiptData:
        return asdict(data)
    if type(data) is SubmitCommandReceiptData:
        return asdict(data)
    if type(data) is SubscriptionReceiptData:
        return asdict(data)
    raise TypeError("receipt data is invalid")


def _resume_cursor_to_dict(item: ResumeCursor) -> dict[str, object]:
    return {"session_id": item.session_id, "run_id": item.run_id, "cursor": item.cursor}


def _require_fields(value: dict[str, Any], expected: set[str]) -> None:
    if set(value) != expected:
        raise ValueError("client frame fields are invalid")


def _trusted_request_id(value: object) -> str | None:
    if (
        type(value) is str
        and 1 <= len(value) <= 255
        and value == value.strip()
        and not any(
            char.isspace() or unicodedata.category(char).startswith("C")
            for char in value
        )
    ):
        return value
    return None


def _validate_json_value(value: object, *, depth: int) -> None:
    if depth > MAX_CLIENT_JSON_DEPTH:
        raise ValueError("JSON nesting exceeds limit")
    if type(value) is str:
        if any(unicodedata.category(char).startswith("C") for char in value):
            raise ValueError("JSON string contains a control character")
        return
    if type(value) is float and not math.isfinite(value):
        raise ValueError("JSON number must be finite")
    if type(value) is dict:
        for key, item in value.items():
            _validate_json_value(key, depth=depth + 1)
            _validate_json_value(item, depth=depth + 1)
    elif type(value) is list:
        for item in value:
            _validate_json_value(item, depth=depth + 1)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _reject_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


__all__ = [
    "MAX_CLIENT_FRAME_BYTES",
    "MAX_CLIENT_JSON_DEPTH",
    "WebSocketDecodeError",
    "WebSocketFrameTooLargeError",
    "decode_client_frame",
    "encode_server_frame",
]
