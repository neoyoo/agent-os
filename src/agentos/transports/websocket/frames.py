from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
import unicodedata

from agentos._json_values import (
    FrozenJsonObject,
    FrozenJsonValue,
)
from agentos.artifacts.types import validate_artifact_id
from agentos.distributed.models import RunSubmissionReceipt
from agentos.distributed._model_validation import require_identifier
from agentos.runtime.durable_commands import (
    DurableCommandReceipt,
    DurableRunCommand,
    DurableRunCommandKind,
)
from agentos.transports.run_stream import RunStreamGapProjection, RunStreamReplayProjection


@dataclass(frozen=True, slots=True)
class SubmitRunFrame:
    """首次 Run 提交的 WebSocket client frame。"""
    request_id: str
    session_id: str
    content: str
    artifact_handles: tuple[str, ...]

    def __post_init__(self) -> None:
        require_identifier(self.request_id, "request_id")
        require_identifier(self.session_id, "session_id")
        _require_text(self.content, "content", empty=True)
        if type(self.artifact_handles) is str:
            raise TypeError("artifact_handles must contain str values")
        handles = tuple(self.artifact_handles)
        for handle in handles:
            validate_artifact_id(handle)
        object.__setattr__(self, "artifact_handles", handles)

@dataclass(frozen=True, slots=True, init=False)
class SubmitCommandFrame:
    """使用 request_id 作为 command identity 的 client frame。"""
    request_id: str
    session_id: str
    run_id: str
    kind: DurableRunCommandKind
    payload: FrozenJsonObject

    def __init__(
        self,
        request_id: str,
        session_id: str,
        run_id: str,
        kind: DurableRunCommandKind,
        payload: dict[str, object] | FrozenJsonObject,
    ) -> None:
        require_identifier(request_id, "request_id")
        require_identifier(session_id, "session_id")
        command = DurableRunCommand(run_id, request_id, kind, payload)
        _validate_json_strings(command.payload)
        object.__setattr__(self, "request_id", request_id)
        object.__setattr__(self, "session_id", session_id)
        object.__setattr__(self, "run_id", command.run_id)
        object.__setattr__(self, "kind", command.kind)
        object.__setattr__(self, "payload", command.payload)

@dataclass(frozen=True, slots=True)
class SubscribeRunFrame:
    """创建一个 session/run scoped subscription。"""
    request_id: str
    session_id: str
    run_id: str
    cursor: str | None

    def __post_init__(self) -> None:
        _require_resource_frame(self.request_id, self.session_id, self.run_id)
        if self.cursor is not None:
            _require_cursor(self.cursor)

@dataclass(frozen=True, slots=True)
class UnsubscribeRunFrame:
    """幂等关闭一个 session/run scoped subscription。"""
    request_id: str
    session_id: str
    run_id: str

    def __post_init__(self) -> None:
        _require_resource_frame(self.request_id, self.session_id, self.run_id)

@dataclass(frozen=True, slots=True)
class SubmitRunReceiptData:
    """`submit_run` receipt 的固定 data schema。"""
    session_id: str
    run_id: str
    submission_id: str
    aggregate_version: int
    duplicate: bool

    def __post_init__(self) -> None:
        RunSubmissionReceipt(
            self.session_id,
            self.run_id,
            self.submission_id,
            self.aggregate_version,
            self.duplicate,
        )

@dataclass(frozen=True, slots=True)
class SubmitCommandReceiptData:
    """`submit_command` receipt 的固定 data schema。"""
    run_id: str
    command_id: str
    kind: DurableRunCommandKind
    aggregate_version: int
    duplicate: bool

    def __post_init__(self) -> None:
        DurableCommandReceipt(
            self.run_id,
            self.command_id,
            self.kind,
            self.aggregate_version,
            self.duplicate,
        )

@dataclass(frozen=True, slots=True)
class SubscriptionReceiptData:
    """subscribe/unsubscribe receipt 共用的 resource schema。"""
    session_id: str
    run_id: str

    def __post_init__(self) -> None:
        require_identifier(self.session_id, "session_id")
        require_identifier(self.run_id, "run_id")

@dataclass(frozen=True, slots=True)
class ReceiptFrame:
    """与具体 operation/data schema 绑定的 server receipt。"""
    request_id: str
    operation: Literal[
        "submit_run",
        "submit_command",
        "subscribe_run",
        "unsubscribe_run",
    ]
    data: SubmitRunReceiptData | SubmitCommandReceiptData | SubscriptionReceiptData

    def __post_init__(self) -> None:
        require_identifier(self.request_id, "request_id")
        expected_type = {
            "submit_run": SubmitRunReceiptData,
            "submit_command": SubmitCommandReceiptData,
            "subscribe_run": SubscriptionReceiptData,
            "unsubscribe_run": SubscriptionReceiptData,
        }.get(self.operation)
        if expected_type is None or type(self.data) is not expected_type:
            raise ValueError("receipt data does not match operation")
        identity = (
            self.data.submission_id
            if type(self.data) is SubmitRunReceiptData
            else self.data.command_id
            if type(self.data) is SubmitCommandReceiptData
            else self.request_id
        )
        if identity != self.request_id:
            raise ValueError("receipt request_id does not match write identity")

@dataclass(frozen=True, slots=True)
class EventFrame:
    """由同一个 Shared Run Stream projection 生成的 server event。"""
    session_id: str
    run_id: str
    projection: RunStreamReplayProjection

    def __post_init__(self) -> None:
        require_identifier(self.session_id, "session_id")
        require_identifier(self.run_id, "run_id")
        if type(self.projection) is not RunStreamReplayProjection:
            raise TypeError("projection must be RunStreamReplayProjection")
        if (
            self.projection.data.get("session_id") != self.session_id
            or self.projection.data.get("run_id") != self.run_id
        ):
            raise ValueError("event projection scope does not match frame scope")

@dataclass(frozen=True, slots=True)
class StreamGapFrame:
    """不可连续 replay 的 scoped server frame。"""
    session_id: str
    run_id: str
    projection: RunStreamGapProjection

    def __post_init__(self) -> None:
        require_identifier(self.session_id, "session_id")
        require_identifier(self.run_id, "run_id")
        if type(self.projection) is not RunStreamGapProjection:
            raise TypeError("projection must be RunStreamGapProjection")
        if (
            self.projection.session_id != self.session_id
            or self.projection.run_id != self.run_id
        ):
            raise ValueError("gap projection scope does not match frame scope")

@dataclass(frozen=True, slots=True)
class ResumeCursor:
    """slow-consumer close 前返回的最后成功发送 cursor。"""
    session_id: str
    run_id: str
    cursor: str | None

    def __post_init__(self) -> None:
        require_identifier(self.session_id, "session_id")
        require_identifier(self.run_id, "run_id")
        if self.cursor is not None:
            _require_cursor(self.cursor)

@dataclass(frozen=True, slots=True, init=False)
class ErrorFrame:
    """遵守 request-bound/resource-bound 互斥规则的 server error。"""
    request_id: str | None
    code: str
    message: str
    session_id: str | None
    run_id: str | None
    resume_cursors: tuple[ResumeCursor, ...]

    def __init__(
        self,
        request_id: str | None,
        code: str,
        message: str,
        *,
        session_id: str | None = None,
        run_id: str | None = None,
        resume_cursors: tuple[ResumeCursor, ...] = (),
    ) -> None:
        if request_id is not None:
            require_identifier(request_id, "request_id")
        require_identifier(code, "code")
        _require_text(message, "message")
        if (session_id is None) != (run_id is None):
            raise ValueError("error resource fields must be complete")
        if session_id is not None:
            require_identifier(session_id, "session_id")
            require_identifier(run_id, "run_id")
        if request_id is not None and session_id is not None:
            raise ValueError("request-bound error cannot carry resource fields")
        cursors = tuple(resume_cursors)
        if any(type(item) is not ResumeCursor for item in cursors):
            raise TypeError("resume_cursors must contain ResumeCursor values")
        if cursors and code != "slow_consumer":
            raise ValueError("resume_cursors require slow_consumer error")
        if code == "slow_consumer" and (request_id is not None or session_id is not None):
            raise ValueError("slow_consumer error must be connection-scoped")
        cursors = tuple(sorted(cursors, key=lambda item: (item.session_id, item.run_id)))
        scopes = {(item.session_id, item.run_id) for item in cursors}
        if len(scopes) != len(cursors):
            raise ValueError("resume cursor scope must be unique")
        object.__setattr__(self, "request_id", request_id)
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "message", message)
        object.__setattr__(self, "session_id", session_id)
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "resume_cursors", cursors)

def _require_resource_frame(request_id: str, session_id: str, run_id: str) -> None:
    require_identifier(request_id, "request_id")
    require_identifier(session_id, "session_id")
    require_identifier(run_id, "run_id")

def _require_text(value: object, field_name: str, *, empty: bool = False) -> None:
    if (
        type(value) is not str
        or (not empty and not value)
        or any(unicodedata.category(char).startswith("C") for char in value)
    ):
        raise ValueError(f"{field_name} is invalid")

def _require_cursor(value: object) -> None:
    if (
        type(value) is not str
        or not value
        or len(value.encode("utf-8")) > 1024
        or any(unicodedata.category(char).startswith("C") for char in value)
    ):
        raise ValueError("cursor is invalid")

def _validate_json_strings(value: FrozenJsonValue) -> None:
    if type(value) is str:
        _require_text(value, "JSON string", empty=True)
    elif type(value) is tuple:
        for item in value:
            _validate_json_strings(item)
    elif type(value) is FrozenJsonObject:
        for key, item in value.items():
            _require_text(key, "JSON key", empty=True)
            _validate_json_strings(item)
