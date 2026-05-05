from __future__ import annotations


class QdrantRecallIndex:
    """Qdrant-backed RecallIndex adapter boundary."""

    def __init__(
        self,
        url: str,
        collection_name: str,
        embedding_provider: object,
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

    def __getattr__(self, name: str) -> object:
        """第一版只固定构造边界，具体 Qdrant schema 后续实现。"""

        raise NotImplementedError(
            f"QdrantRecallIndex.{name} is not implemented in this phase",
        )
