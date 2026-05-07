from __future__ import annotations

from uuid import NAMESPACE_URL, uuid5

from agentos.memory.embeddings import TextEmbeddingProvider
from agentos.memory.types import RecallCandidate, SegmentRecallDocument


class QdrantRecallIndex:
    """Qdrant-backed RecallIndex adapter。"""

    def __init__(
        self,
        url: str,
        collection_name: str,
        embedding_provider: TextEmbeddingProvider,
        client: object | None = None,
    ) -> None:
        """创建 Qdrant recall index；未安装 qdrant extra 时给出清晰错误。"""

        if client is not None:
            self._client = client
            self._url = url
            self._collection_name = collection_name
            self._embedding_provider = embedding_provider
            return
        try:
            from qdrant_client import QdrantClient
        except ImportError as error:
            raise RuntimeError(
                "QdrantRecallIndex requires the optional dependency "
                "`agentos[qdrant]`.",
            ) from error
        self._client = QdrantClient(url=url)
        self._url = url
        self._collection_name = collection_name
        self._embedding_provider = embedding_provider

    def index_segment(self, document: SegmentRecallDocument) -> None:
        """写入一个 segment recall document。"""

        self._client.upsert(
            collection_name=self._collection_name,
            points=[
                {
                    "id": self._point_id(document.session_id, document.segment_id),
                    "vector": self._embedding_provider.embed_text(document.to_text()),
                    "payload": self._payload(document),
                },
            ],
        )

    def search_segments(
        self,
        session_id: str,
        query: str,
        limit: int,
    ) -> tuple[RecallCandidate, ...]:
        """按 query 检索 candidate segments。"""

        if limit <= 0:
            return ()
        results = self._client.search(
            collection_name=self._collection_name,
            query_vector=self._embedding_provider.embed_text(query),
            query_filter=self._session_filter(session_id),
            limit=limit,
        )
        candidates: list[RecallCandidate] = []
        for result in results:
            payload = self._result_payload(result)
            candidates.append(
                RecallCandidate(
                    session_id=str(payload["session_id"]),
                    segment_id=str(payload["segment_id"]),
                    score=self._result_score(result),
                    reason="qdrant",
                ),
            )
        return tuple(candidates)

    def delete_session(self, session_id: str) -> None:
        """删除某个 session 的 recall index。"""

        self._client.delete(
            collection_name=self._collection_name,
            points_selector={"filter": self._session_filter(session_id)},
        )

    def _point_id(self, session_id: str, segment_id: str) -> str:
        """生成 Qdrant 可接受的稳定 UUID point id。"""

        return str(uuid5(NAMESPACE_URL, f"agentos:{session_id}:{segment_id}"))

    def _payload(self, document: SegmentRecallDocument) -> dict[str, object]:
        """生成 Qdrant payload；payload 只保存 pointer 和检索摘要。"""

        return {
            "session_id": document.session_id,
            "segment_id": document.segment_id,
            "topic": document.topic,
            "summary": document.summary,
            "keywords": list(document.keywords),
            "tool_hints": list(document.tool_hints),
            "searchable_text": document.searchable_text,
        }

    def _session_filter(self, session_id: str) -> dict[str, object]:
        """生成 session-scoped Qdrant filter。"""

        return {
            "must": [
                {
                    "key": "session_id",
                    "match": {"value": session_id},
                },
            ],
        }

    def _result_payload(self, result: object) -> dict[str, object]:
        if isinstance(result, dict):
            return dict(result.get("payload", {}))
        return dict(getattr(result, "payload"))

    def _result_score(self, result: object) -> float | None:
        if isinstance(result, dict):
            score = result.get("score")
        else:
            score = getattr(result, "score", None)
        return None if score is None else float(score)
