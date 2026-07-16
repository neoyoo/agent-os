from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from agentos.compression.index import CompressionIndex
from agentos.messages import MessageRuntime, StoredMessage
from agentos.recall.in_memory_index import InMemoryRecallIndex
from agentos.recall.index import RecallIndex
from agentos.recall.store import SegmentDurableStore, SegmentHotStore
from agentos.recall.types import CompressedSegmentPackage


class SegmentNotFoundError(KeyError):
    """请求的压缩片段不存在。"""


@dataclass(slots=True)
class _RuntimeSegmentRefs:
    saved: dict[tuple[str, str], tuple[str, ...]] = field(default_factory=dict)

    def save(
        self,
        session_id: str,
        segment_id: str,
        message_ids: Sequence[str],
    ) -> None:
        self.saved[(session_id, segment_id)] = tuple(message_ids)

    def load(self, session_id: str, segment_id: str) -> tuple[str, ...]:
        return self.saved[(session_id, segment_id)]


@dataclass(slots=True)
class _RuntimeSegmentHotStore:
    refs: _RuntimeSegmentRefs
    messages: MessageRuntime

    def save_segment_refs(
        self,
        session_id: str,
        segment_id: str,
        message_ids: Sequence[str],
    ) -> None:
        self.refs.save(session_id, segment_id, message_ids)

    def get_segment_refs(
        self,
        session_id: str,
        segment_id: str,
    ) -> tuple[str, ...] | None:
        try:
            return self.refs.load(session_id, segment_id)
        except KeyError:
            return None

    def get_hot_messages(
        self,
        session_id: str,
        message_ids: Sequence[str],
    ) -> list[StoredMessage] | None:
        try:
            return [self.messages.store.get(message_id) for message_id in message_ids]
        except KeyError:
            return None


@dataclass(slots=True)
class _RuntimeSegmentDurableStore:
    refs: _RuntimeSegmentRefs
    messages: MessageRuntime

    def save_compressed_segment(
        self,
        session_id: str,
        package: CompressedSegmentPackage,
    ) -> None:
        return None

    def get_segment_refs(
        self,
        session_id: str,
        segment_id: str,
    ) -> tuple[str, ...]:
        return self.refs.load(session_id, segment_id)

    def get_messages(
        self,
        session_id: str,
        message_ids: Sequence[str],
    ) -> list[StoredMessage]:
        return [self.messages.store.get(message_id) for message_id in message_ids]


@dataclass(slots=True)
class SegmentRepository:
    """持久化压缩片段，并按 handle 或 query 读取其原始消息。"""

    hot_store: SegmentHotStore
    durable_store: SegmentDurableStore
    recall_index: RecallIndex

    @classmethod
    def from_runtime(
        cls,
        compression_index: CompressionIndex,
        message_runtime: MessageRuntime,
        *,
        session_id: str,
    ) -> SegmentRepository:
        """把单 Session 的 Level 1 消息和压缩索引适配为统一 Repository。"""

        refs = _RuntimeSegmentRefs(
            saved={
                (session_id, segment_id): source_refs
                for segment_id, source_refs in compression_index.snapshot().items()
            },
        )
        return cls(
            hot_store=_RuntimeSegmentHotStore(refs, message_runtime),
            durable_store=_RuntimeSegmentDurableStore(refs, message_runtime),
            recall_index=InMemoryRecallIndex(),
        )

    def record_compressed_segment(self, package: CompressedSegmentPackage) -> None:
        """记录一次 compression package，建立 handle 和 query recall 能力。"""

        session_id = package.recall_document.session_id
        self.hot_store.save_segment_refs(
            session_id,
            package.segment.id,
            package.source_refs,
        )
        self.durable_store.save_compressed_segment(session_id, package)
        self.recall_index.index_segment(package.recall_document)

    def recall_by_handle(self, session_id: str, handle: str) -> list[StoredMessage]:
        """按 segment handle 恢复原文消息。"""

        source_refs = self.hot_store.get_segment_refs(session_id, handle)
        if source_refs is None:
            try:
                source_refs = self.durable_store.get_segment_refs(session_id, handle)
            except KeyError as error:
                raise SegmentNotFoundError(handle) from error

        hot_messages = self.hot_store.get_hot_messages(session_id, source_refs)
        if hot_messages is not None:
            return hot_messages
        return self.durable_store.get_messages(session_id, source_refs)

    def recall_by_query(
        self,
        session_id: str,
        query: str,
        limit: int,
    ) -> list[StoredMessage]:
        """按 query 检索相关 segment 并恢复原文消息。"""

        messages: list[StoredMessage] = []
        seen_message_ids: set[str] = set()
        candidates = self.recall_index.search_segments(session_id, query, limit)
        for candidate in candidates:
            for message in self.recall_by_handle(session_id, candidate.segment_id):
                if message.id in seen_message_ids:
                    continue
                seen_message_ids.add(message.id)
                messages.append(message)
        return messages
