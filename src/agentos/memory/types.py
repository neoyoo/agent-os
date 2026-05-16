from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Sequence

from agentos.context import CompressedSegment
from agentos.messages import Message, MessageRef


@dataclass(frozen=True, slots=True)
class SegmentRecallDocument:
    """面向 recall index 的 segment 检索文档，不作为原文真值源。"""

    session_id: str
    segment_id: str
    topic: str
    summary: str
    keywords: tuple[str, ...] = ()
    tool_hints: tuple[str, ...] = ()
    searchable_text: str = ""

    def __init__(
        self,
        session_id: str,
        segment_id: str,
        topic: str,
        summary: str,
        keywords: Sequence[str] | None = None,
        tool_hints: Sequence[str] | None = None,
        searchable_text: str = "",
    ) -> None:
        object.__setattr__(self, "session_id", session_id)
        object.__setattr__(self, "segment_id", segment_id)
        object.__setattr__(self, "topic", topic)
        object.__setattr__(self, "summary", summary)
        object.__setattr__(self, "keywords", tuple(keywords or ()))
        object.__setattr__(self, "tool_hints", tuple(tool_hints or ()))
        object.__setattr__(self, "searchable_text", searchable_text)

    def to_text(self) -> str:
        """返回用于文本 embedding 或词法检索的稳定文本。"""

        parts = [
            f"topic: {self.topic}",
            f"summary: {self.summary}",
        ]
        if self.keywords:
            parts.append("keywords: " + ", ".join(self.keywords))
        if self.tool_hints:
            parts.append("tool_hints: " + ", ".join(self.tool_hints))
        if self.searchable_text:
            parts.append(f"searchable_text: {self.searchable_text}")
        return "\n".join(parts)


@dataclass(frozen=True, slots=True)
class CompressedSegmentPackage:
    """一次 compression 的完整副产物。"""

    segment: CompressedSegment
    source_refs: tuple[str, ...]
    recall_document: SegmentRecallDocument

    def __init__(
        self,
        segment: CompressedSegment,
        source_refs: Sequence[str],
        recall_document: SegmentRecallDocument,
    ) -> None:
        object.__setattr__(self, "segment", segment)
        object.__setattr__(self, "source_refs", tuple(source_refs))
        object.__setattr__(self, "recall_document", recall_document)


@dataclass(frozen=True, slots=True)
class RecallCandidate:
    """query recall 命中的候选 compressed segment。"""

    session_id: str
    segment_id: str
    score: float | None
    reason: str | None = None


@dataclass(frozen=True, slots=True, init=False)
class HotSessionState:
    """活跃 session 的热点工作集快照。"""

    session_id: str
    active_refs: tuple[MessageRef, ...]
    recent_messages: tuple[Message, ...]
    temporary_recalled_refs: tuple[str, ...]
    segment_refs: Mapping[str, tuple[str, ...]]
    metadata: Mapping[str, object]

    def __init__(
        self,
        session_id: str,
        active_refs: Sequence[MessageRef] | None = None,
        recent_messages: Sequence[Message] | None = None,
        temporary_recalled_refs: Sequence[str] | None = None,
        segment_refs: Mapping[str, Sequence[str]] | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> None:
        object.__setattr__(self, "session_id", session_id)
        object.__setattr__(self, "active_refs", tuple(active_refs or ()))
        object.__setattr__(self, "recent_messages", tuple(recent_messages or ()))
        object.__setattr__(
            self,
            "temporary_recalled_refs",
            tuple(temporary_recalled_refs or ()),
        )
        object.__setattr__(
            self,
            "segment_refs",
            MappingProxyType(
                {
                    segment_id: tuple(refs)
                    for segment_id, refs in (segment_refs or {}).items()
                },
            ),
        )
        object.__setattr__(
            self,
            "metadata",
            MappingProxyType(dict(metadata or {})),
        )
