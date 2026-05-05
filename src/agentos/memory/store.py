from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, Sequence

from agentos.context import CompressedSegment
from agentos.memory.types import CompressedSegmentPackage, HotSessionState
from agentos.messages import Message, MessageRef

if TYPE_CHECKING:
    from agentos.runtime.session import SessionState


class HotSessionStore(Protocol):
    """活跃 session 热点工作集存储边界。"""

    def load_hot_state(self, session_id: str) -> HotSessionState | None:
        """读取热点 session state；未命中返回 None。"""

    def save_hot_state(self, state: HotSessionState) -> None:
        """保存热点 session state。"""

    def append_hot_message(self, session_id: str, message: Message) -> None:
        """追加一条热点原文消息。"""

    def get_hot_messages(
        self,
        session_id: str,
        message_ids: Sequence[str],
    ) -> list[Message] | None:
        """按 ids 读取热点消息；任一缺失返回 None。"""

    def save_segment_refs(
        self,
        session_id: str,
        segment_id: str,
        message_ids: Sequence[str],
    ) -> None:
        """保存热点 segment refs。"""

    def get_segment_refs(
        self,
        session_id: str,
        segment_id: str,
    ) -> tuple[str, ...] | None:
        """读取热点 segment refs；未命中返回 None。"""

    def set_temporary_recalled_refs(
        self,
        session_id: str,
        message_ids: Sequence[str],
    ) -> None:
        """设置一次性 recalled refs。"""

    def consume_temporary_recalled_refs(self, session_id: str) -> tuple[str, ...]:
        """消费并清空一次性 recalled refs。"""


class DurableSessionStore(Protocol):
    """长期 session 真值源存储边界。"""

    def save_session(self, session: "SessionState") -> None:
        """保存 session state。"""

    def load_session(self, session_id: str) -> "SessionState":
        """读取 session state。"""

    def append_message(self, session_id: str, message: Message) -> None:
        """追加原始消息。"""

    def get_messages(
        self,
        session_id: str,
        message_ids: Sequence[str],
    ) -> list[Message]:
        """按 ids 读取原始消息。"""

    def save_active_refs(
        self,
        session_id: str,
        refs: Sequence[MessageRef],
    ) -> None:
        """保存 active window refs checkpoint。"""

    def load_active_refs(self, session_id: str) -> tuple[MessageRef, ...]:
        """读取 active window refs checkpoint。"""

    def save_compressed_segment(
        self,
        session_id: str,
        package: CompressedSegmentPackage,
    ) -> None:
        """保存 compressed segment package。"""

    def get_segment_refs(self, session_id: str, segment_id: str) -> tuple[str, ...]:
        """读取 durable segment refs。"""

    def list_compressed_segments(
        self,
        session_id: str,
    ) -> tuple[CompressedSegment, ...]:
        """列出 session 下的 LLM 可见 compressed segments。"""
