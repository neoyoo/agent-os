"""压缩片段召回。"""

from typing import TYPE_CHECKING

from agentos.recall.embeddings import TextEmbeddingProvider
from agentos.recall.in_memory_index import InMemoryRecallIndex
from agentos.recall.index import RecallIndex
from agentos.recall.runtime import RecallContextError, RecallRuntime
from agentos.recall.segment_repository import SegmentRepository
from agentos.recall.store import SegmentDurableStore, SegmentHotStore
from agentos.recall.types import (
    CompressedSegmentPackage,
    RecallCandidate,
    SegmentRecallDocument,
)

if TYPE_CHECKING:
    from agentos.recall.qdrant_index import QdrantRecallIndex

__all__ = [
    "CompressedSegmentPackage",
    "InMemoryRecallIndex",
    "QdrantRecallIndex",
    "RecallCandidate",
    "RecallContextError",
    "RecallIndex",
    "RecallRuntime",
    "SegmentDurableStore",
    "SegmentHotStore",
    "SegmentRecallDocument",
    "SegmentRepository",
    "TextEmbeddingProvider",
]


def __getattr__(name: str) -> object:
    """仅在使用可选 Qdrant Adapter 时加载其实现模块。"""

    if name != "QdrantRecallIndex":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from agentos.recall.qdrant_index import QdrantRecallIndex

    return QdrantRecallIndex
