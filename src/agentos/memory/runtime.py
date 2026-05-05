from dataclasses import dataclass

from agentos.memory.recall_index import RecallIndex
from agentos.memory.store import DurableSessionStore, HotSessionStore
from agentos.memory.types import CompressedSegmentPackage
from agentos.messages import Message


@dataclass(slots=True)
class MemoryRuntime:
    """协调 compression 副产物记录和 context recall。"""

    hot_store: HotSessionStore
    durable_store: DurableSessionStore
    recall_index: RecallIndex

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

    def recall_by_handle(self, session_id: str, handle: str) -> list[Message]:
        """按 segment handle 恢复原文消息。"""

        source_refs = self.hot_store.get_segment_refs(session_id, handle)
        if source_refs is None:
            source_refs = self.durable_store.get_segment_refs(session_id, handle)

        hot_messages = self.hot_store.get_hot_messages(session_id, source_refs)
        if hot_messages is not None:
            return hot_messages
        return self.durable_store.get_messages(session_id, source_refs)

    def recall_by_query(
        self,
        session_id: str,
        query: str,
        limit: int,
    ) -> list[Message]:
        """按 query 检索相关 segment 并恢复原文消息。"""

        messages: list[Message] = []
        seen_message_ids: set[str] = set()
        candidates = self.recall_index.search_segments(session_id, query, limit)
        for candidate in candidates:
            for message in self.recall_by_handle(session_id, candidate.segment_id):
                if message.id in seen_message_ids:
                    continue
                seen_message_ids.add(message.id)
                messages.append(message)
        return messages
