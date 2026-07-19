from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from agentos._json_values import FrozenJsonObject, freeze_json_mapping
from agentos.context import ContextRuntime, WorkingStateField
from agentos.context.state import CompressedSegment, working_state_value_to_json
from agentos.artifacts import ArtifactRef
from agentos.messages import MessageRuntime
from agentos.runtime.session import SessionState, SessionStatus
from agentos.runtime.execution import RunExecutionCursor
from agentos.runtime.payloads import ProtectedPayloadRef

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentos.runtime.tool_payloads import ToolPayloadRuntime


CHECKPOINT_SCHEMA_VERSION = 2


@dataclass(frozen=True, slots=True)
class CheckpointToolCall:
    """持久消息中的 ToolCall，只保存受保护 arguments 引用。"""

    id: str
    name: str
    run_id: str
    turn_id: str
    invocation_id: str
    invocation_ref: ProtectedPayloadRef


@dataclass(frozen=True, slots=True)
class CheckpointStoredMessage:
    """持久化 Adapter 可安全保存的业务消息表示。"""

    id: str
    role: str
    content: str
    artifact_refs: tuple[ArtifactRef, ...] = ()
    tool_calls: tuple[CheckpointToolCall, ...] = ()
    tool_call_id: str | None = None


@dataclass(frozen=True, slots=True)
class ContextCheckpoint:
    """可以重建 ContextState 的持久数据，不包含临时投影。"""

    schema: tuple[WorkingStateField, ...]
    working_state: FrozenJsonObject
    compressed_history: tuple[CompressedSegment, ...]
    inherited_state: tuple[str, ...]

    @property
    def runtime_notices(self) -> tuple[()]:
        """Runtime notice 永不进入 checkpoint。"""

        return ()


@dataclass(frozen=True, slots=True)
class SessionCheckpoint:
    """一次 Durable 提交需要保存的 Session 恢复真值。"""

    session_id: str
    session_status: SessionStatus
    next_turn_number: int
    messages: tuple[CheckpointStoredMessage, ...]
    active_refs: tuple[str, ...]
    context: ContextCheckpoint
    execution_cursor: RunExecutionCursor | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.session_id, str) or not self.session_id.strip():
            raise ValueError("checkpoint session_id must not be empty")
        if self.session_status not in {"new", "running", "closed"}:
            raise ValueError("checkpoint session status is invalid")
        if type(self.next_turn_number) is not int or self.next_turn_number < 1:
            raise ValueError("checkpoint next turn number is invalid")
        if any(
            type(message) is not CheckpointStoredMessage
            for message in self.messages
        ):
            raise TypeError("checkpoint messages are invalid")
        if any(type(ref) is not str for ref in self.active_refs):
            raise TypeError("checkpoint active refs are invalid")
        if len(set(self.active_refs)) != len(self.active_refs):
            raise ValueError("checkpoint active refs are duplicated")
        message_ids = {message.id for message in self.messages}
        if any(ref not in message_ids for ref in self.active_refs):
            raise ValueError("checkpoint active ref is missing its message")
        if type(self.context) is not ContextCheckpoint:
            raise TypeError("checkpoint context is invalid")
        if (
            self.execution_cursor is not None
            and type(self.execution_cursor) is not RunExecutionCursor
        ):
            raise TypeError("checkpoint execution cursor is invalid")
        cursor = self.execution_cursor
        if cursor is not None and cursor.stage == "pending_tools":
            assistant = next(
                (
                    message
                    for message in self.messages
                    if message.id == cursor.assistant_message_id
                ),
                None,
            )
            if assistant is None or assistant.role != "assistant":
                raise ValueError(
                    "pending tool cursor is missing its assistant message",
                )
            if len(assistant.tool_calls) != len(cursor.pending_tools):
                raise ValueError(
                    "pending tool cursor does not match assistant tool calls",
                )
            for call, pending in zip(
                assistant.tool_calls,
                cursor.pending_tools,
                strict=True,
            ):
                if (
                    call.id != pending.provider_tool_call_id
                    or call.name != pending.tool_name
                    or call.turn_id != cursor.turn_id
                    or call.invocation_id != pending.invocation_id
                    or call.invocation_ref != pending.invocation_ref
                ):
                    raise ValueError(
                        "pending tool cursor does not match assistant tool calls",
                    )


@dataclass(frozen=True, slots=True)
class RunCheckpoint:
    """一次 Run checkpoint 提交的稳定 metadata。"""

    checkpoint_id: str
    session_id: str
    run_id: str
    turn_id: str
    aggregate_version: int
    created_at: datetime
    schema_version: int = CHECKPOINT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for value in (
            self.checkpoint_id,
            self.session_id,
            self.run_id,
            self.turn_id,
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("run checkpoint identifier is invalid")
        if type(self.aggregate_version) is not int or self.aggregate_version < 0:
            raise ValueError("run checkpoint aggregate version is invalid")
        if not isinstance(self.created_at, datetime) or self.created_at.utcoffset() is None:
            raise ValueError("run checkpoint created_at must be timezone-aware")
        if self.schema_version != CHECKPOINT_SCHEMA_VERSION:
            raise ValueError("run checkpoint schema version is unsupported")


@dataclass(frozen=True, slots=True, weakref_slot=True)
class RuntimeCheckpointSource:
    """从当前 Session Runtime 采集确定性的 checkpoint 数据。"""

    session: SessionState
    messages: MessageRuntime
    context: ContextRuntime
    payloads: ToolPayloadRuntime | None = None

    def capture(
        self,
        *,
        execution_cursor: RunExecutionCursor | None = None,
        active_refs: tuple[str, ...] | None = None,
        run_id: str | None = None,
        turn_id: str | None = None,
    ) -> SessionCheckpoint:
        """采集持久真值，并剔除 temporary ref 与 runtime notice。"""

        from agentos.runtime.tool_payloads import ToolPayloadRuntime

        state = self.context.snapshot()
        payloads = self.payloads or ToolPayloadRuntime.for_session(self.session.id)
        if (
            execution_cursor is not None
            and turn_id is not None
            and execution_cursor.turn_id != turn_id
        ):
            raise ValueError("execution cursor does not match checkpoint turn")
        cursor_run_id = (
            None
            if execution_cursor is None
            else payloads.current_run_id(execution_cursor)
        )
        if run_id is not None and cursor_run_id not in {None, run_id}:
            raise ValueError("execution cursor does not match checkpoint run")
        working_state = {
            key: working_state_value_to_json(value)
            for key, value in state.working_state.items()
        }
        return SessionCheckpoint(
            session_id=self.session.id,
            session_status=self.session.status,
            next_turn_number=self.session.next_turn_number(),
            messages=payloads.checkpoint_messages(
                self.messages.store.all(),
                run_id=run_id or cursor_run_id,
                turn_id=turn_id or (
                    None if execution_cursor is None else execution_cursor.turn_id
                ),
            ),
            active_refs=(
                tuple(active_refs)
                if active_refs is not None
                else tuple(
                    ref.message_id
                    for ref in self.messages.active_window.snapshot_refs()
                    if not ref.temporary
                )
            ),
            context=ContextCheckpoint(
                schema=tuple(state.working_state_schema.fields),
                working_state=freeze_json_mapping(working_state),
                compressed_history=tuple(state.compressed_history),
                inherited_state=tuple(state.inherited_state),
            ),
            execution_cursor=execution_cursor,
        )


__all__ = [
    "CHECKPOINT_SCHEMA_VERSION",
    "CheckpointStoredMessage",
    "CheckpointToolCall",
    "ContextCheckpoint",
    "RuntimeCheckpointSource",
    "RunCheckpoint",
    "SessionCheckpoint",
]
