from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from agentos._json_values import FrozenJsonObject, freeze_json_mapping
from agentos.context import ContextRuntime, WorkingStateField
from agentos.context.state import CompressedSegment, working_state_value_to_json
from agentos.messages import MessageRuntime, StoredMessage
from agentos.runtime.session import SessionState, SessionStatus


CHECKPOINT_SCHEMA_VERSION = 1


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
    messages: tuple[StoredMessage, ...]
    active_refs: tuple[str, ...]
    context: ContextCheckpoint

    def __post_init__(self) -> None:
        if not isinstance(self.session_id, str) or not self.session_id.strip():
            raise ValueError("checkpoint session_id must not be empty")
        if self.session_status not in {"new", "running", "closed"}:
            raise ValueError("checkpoint session status is invalid")
        if type(self.next_turn_number) is not int or self.next_turn_number < 1:
            raise ValueError("checkpoint next turn number is invalid")
        if any(type(message) is not StoredMessage for message in self.messages):
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

    def capture(self) -> SessionCheckpoint:
        """采集持久真值，并剔除 temporary ref 与 runtime notice。"""

        state = self.context.snapshot()
        working_state = {
            key: working_state_value_to_json(value)
            for key, value in state.working_state.items()
        }
        return SessionCheckpoint(
            session_id=self.session.id,
            session_status=self.session.status,
            next_turn_number=self.session.next_turn_number(),
            messages=tuple(self.messages.store.all()),
            active_refs=tuple(
                ref.message_id
                for ref in self.messages.active_window.snapshot_refs()
                if not ref.temporary
            ),
            context=ContextCheckpoint(
                schema=tuple(state.working_state_schema.fields),
                working_state=freeze_json_mapping(working_state),
                compressed_history=tuple(state.compressed_history),
                inherited_state=tuple(state.inherited_state),
            ),
        )


__all__ = [
    "CHECKPOINT_SCHEMA_VERSION",
    "ContextCheckpoint",
    "RuntimeCheckpointSource",
    "RunCheckpoint",
    "SessionCheckpoint",
]
