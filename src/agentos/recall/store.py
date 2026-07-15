from typing import Protocol, Sequence

from agentos.messages import StoredMessage
from agentos.recall.types import CompressedSegmentPackage


class SegmentHotStore(Protocol):
    """SegmentRepository 使用的热点数据最小边界。"""

    def save_segment_refs(
        self,
        session_id: str,
        segment_id: str,
        message_ids: Sequence[str],
    ) -> None: ...

    def get_segment_refs(
        self,
        session_id: str,
        segment_id: str,
    ) -> tuple[str, ...] | None: ...

    def get_hot_messages(
        self,
        session_id: str,
        message_ids: Sequence[str],
    ) -> list[StoredMessage] | None: ...


class SegmentDurableStore(Protocol):
    """SegmentRepository 使用的持久数据最小边界。"""

    def save_compressed_segment(
        self,
        session_id: str,
        package: CompressedSegmentPackage,
    ) -> None: ...

    def get_segment_refs(
        self,
        session_id: str,
        segment_id: str,
    ) -> tuple[str, ...]: ...

    def get_messages(
        self,
        session_id: str,
        message_ids: Sequence[str],
    ) -> list[StoredMessage]: ...
