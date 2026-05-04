from dataclasses import dataclass, field
from typing import Protocol

from agentos.compression.index import CompressionIndex
from agentos.context.state import ContextState
from agentos.messages.runtime import MessageRuntime
from agentos.observability.events import EventRecord
from agentos.runtime.session import SessionState


SNAPSHOT_VERSION = 1


class SnapshotVersionError(ValueError):
    """持久化 snapshot 版本不兼容。"""


class SnapshotLoadError(ValueError):
    """持久化 snapshot 数据损坏或无法反序列化。"""


@dataclass(frozen=True, slots=True)
class SessionSnapshot:
    """可持久化恢复的 agentos session 状态。"""

    session_state: SessionState
    context_state: ContextState
    message_runtime: MessageRuntime
    compression_index: CompressionIndex
    next_segment_number: int = 1
    event_records: tuple[EventRecord, ...] = field(default_factory=tuple)
    version: int = SNAPSHOT_VERSION


class SessionPersistence(Protocol):
    """session snapshot 存储边界。"""

    def save(self, snapshot: SessionSnapshot) -> None:
        """保存一个 session snapshot。"""

    def load(self, session_id: str) -> SessionSnapshot:
        """读取一个 session snapshot。"""

    def list_ids(self) -> list[str]:
        """列出已保存的 session ids。"""

    def delete(self, session_id: str) -> None:
        """删除一个 session snapshot。"""
