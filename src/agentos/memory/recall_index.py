from typing import Protocol

from agentos.memory.types import RecallCandidate, SegmentRecallDocument


class RecallIndex(Protocol):
    """query 到 compressed segment candidate 的检索索引边界。"""

    def index_segment(self, document: SegmentRecallDocument) -> None:
        """写入一个 segment recall document。"""

    def search_segments(
        self,
        session_id: str,
        query: str,
        limit: int,
    ) -> tuple[RecallCandidate, ...]:
        """按 query 检索 candidate segments。"""

    def delete_session(self, session_id: str) -> None:
        """删除某个 session 的 recall index。"""
