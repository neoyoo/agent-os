from typing import Protocol

from agentos.artifacts.types import ArtifactPage, ArtifactRecord


class ArtifactStore(Protocol):
    """Artifact 内容与元数据的 Session-scoped 真值边界。"""

    def put(
        self,
        *,
        session_id: str,
        data: bytes,
        filename: str | None,
        media_type: str,
    ) -> ArtifactRecord:
        """保存独立 Artifact 并返回不含原始内容的元数据。"""

    def get(self, session_id: str, artifact_id: str) -> ArtifactRecord:
        """读取指定 Session 内的 Artifact 元数据。"""

    def read(self, session_id: str, artifact_id: str) -> bytes:
        """读取指定 Session 内的 Artifact 原始内容。"""

    def list(
        self,
        session_id: str,
        cursor: str | None = None,
        limit: int = 20,
    ) -> ArtifactPage:
        """按最新优先的稳定游标分页列出 Artifact。"""

    def delete(self, session_id: str, artifact_id: str) -> None:
        """删除指定 Session 内的单个 Artifact。"""

    def delete_session(self, session_id: str) -> None:
        """删除一个 Session 的全部 Artifact。"""
