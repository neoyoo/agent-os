from dataclasses import dataclass
from typing import Protocol, Sequence

from agentos.compression._helpers import (
    build_searchable_text,
    clip_text,
    extract_keywords,
    extract_tool_hints,
)
from agentos.context import CompressedSegment
from agentos.memory import CompressedSegmentPackage, SegmentRecallDocument
from agentos.messages import Message


class Compressor(Protocol):
    """压缩器协议，允许后续替换为 LLMCompressor。"""

    def compress(self, segment_id: str, messages: Sequence[Message]) -> CompressedSegment:
        """把原始消息压缩为 LLM 可见摘要。"""


class PackageCompressor(Protocol):
    """生成 compression 完整副产物的压缩器协议。"""

    def compress_package(
        self,
        segment_id: str,
        session_id: str,
        messages: Sequence[Message],
    ) -> CompressedSegmentPackage:
        """把原始消息压缩为 segment package。"""


@dataclass(slots=True)
class RuleBasedCompressor:
    """测试和 fallback 使用的确定性压缩器。"""

    max_items: int = 4
    max_searchable_text_chars: int = 500

    def compress(self, segment_id: str, messages: Sequence[Message]) -> CompressedSegment:
        """把原始消息压缩为 LLM 可见摘要，不携带内部元数据。"""

        if not messages:
            raise ValueError("cannot compress an empty message sequence")

        topic = self._topic(messages)
        snippets = [self._snippet(message) for message in messages[: self.max_items]]
        remaining_count = len(messages) - len(snippets)
        if remaining_count > 0:
            snippets.append(f"另有 {remaining_count} 条历史消息。")

        return CompressedSegment(
            id=segment_id,
            topic=topic,
            summary=f"压缩了 {len(messages)} 条历史消息：" + " ".join(snippets),
        )

    def compress_package(
        self,
        segment_id: str,
        session_id: str,
        messages: Sequence[Message],
    ) -> CompressedSegmentPackage:
        """生成 LLM 摘要、source refs 和 recall document。"""

        segment = self.compress(segment_id, messages)
        return CompressedSegmentPackage(
            segment=segment,
            source_refs=tuple(message.id for message in messages),
            recall_document=SegmentRecallDocument(
                session_id=session_id,
                segment_id=segment.id,
                topic=segment.topic,
                summary=segment.summary,
                keywords=self._keywords(messages),
                tool_hints=self._tool_hints(messages),
                searchable_text=self._searchable_text(messages),
            ),
        )

    def _topic(self, messages: Sequence[Message]) -> str:
        """从第一条 user 消息提取稳定主题。"""

        for message in messages:
            if message.role == "user" and message.content:
                return self._clip(message.content, limit=48)
        return "historical context"

    def _snippet(self, message: Message) -> str:
        """生成单条消息的短摘要片段。"""

        return f"{message.role}: {self._clip(message.content, limit=80)}"

    def _clip(self, value: str, limit: int) -> str:
        """限制摘要片段长度，避免 fallback 摘要过长。"""

        return clip_text(value, limit=limit)

    def _keywords(self, messages: Sequence[Message]) -> tuple[str, ...]:
        """从源消息中提取适合词法检索的稳定关键词。"""

        return extract_keywords(messages)

    def _tool_hints(self, messages: Sequence[Message]) -> tuple[str, ...]:
        """提取工具调用名称和关键参数摘要。"""

        return extract_tool_hints(messages)

    def _searchable_text(self, messages: Sequence[Message]) -> str:
        """生成 recall index 使用的短检索文本。"""

        return build_searchable_text(
            messages,
            max_items=self.max_items,
            limit=self.max_searchable_text_chars,
        )

    def _is_keyword(self, token: str) -> bool:
        """判断 token 是否值得进入 recall document。"""

        return "." in token or "-" in token or "_" in token or len(token) >= 4

    def _dedupe(self, values: Sequence[str]) -> tuple[str, ...]:
        """保留顺序地去重空值。"""

        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            normalized = value.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            result.append(normalized)
        return tuple(result)
