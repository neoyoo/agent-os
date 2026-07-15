from typing import Protocol


class TextEmbeddingProvider(Protocol):
    """文本 embedding provider 边界。"""

    def embed_text(self, text: str) -> list[float]:
        """返回文本向量。"""
