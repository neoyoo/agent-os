"""WebSocket text JSON 的 typed frame 与确定性 serialization 边界。"""

from typing import Literal, TypeAlias

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
from agentos.transports.websocket.serialization import (
    MAX_CLIENT_FRAME_BYTES,
    MAX_CLIENT_JSON_DEPTH,
    WebSocketDecodeError,
    WebSocketFrameTooLargeError,
    decode_client_frame,
    encode_server_frame,
)


ClientFrame: TypeAlias = (
    SubmitRunFrame | SubmitCommandFrame | SubscribeRunFrame | UnsubscribeRunFrame
)
ReceiptData: TypeAlias = (
    SubmitRunReceiptData | SubmitCommandReceiptData | SubscriptionReceiptData
)
ReceiptOperation: TypeAlias = Literal[
    "submit_run",
    "submit_command",
    "subscribe_run",
    "unsubscribe_run",
]
ServerFrame: TypeAlias = ReceiptFrame | EventFrame | StreamGapFrame | ErrorFrame


__all__ = [
    "ClientFrame",
    "ErrorFrame",
    "EventFrame",
    "MAX_CLIENT_FRAME_BYTES",
    "MAX_CLIENT_JSON_DEPTH",
    "ReceiptData",
    "ReceiptFrame",
    "ReceiptOperation",
    "ResumeCursor",
    "ServerFrame",
    "StreamGapFrame",
    "SubmitCommandFrame",
    "SubmitCommandReceiptData",
    "SubmitRunFrame",
    "SubmitRunReceiptData",
    "SubscribeRunFrame",
    "SubscriptionReceiptData",
    "UnsubscribeRunFrame",
    "WebSocketDecodeError",
    "WebSocketFrameTooLargeError",
    "decode_client_frame",
    "encode_server_frame",
]
