from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from agentos.compression._helpers import (
    clip_text,
    extract_keywords,
    extract_tool_hints,
)
from agentos.compression.compressor import Compressor, RuleBasedCompressor
from agentos.context import CompressedSegment
from agentos.memory import CompressedSegmentPackage, SegmentRecallDocument
from agentos.messages import Message
from agentos.providers import Provider, ProviderRequest, UserMessage


DEFAULT_COMPRESSION_PROMPT = """你是一个上下文压缩助手。将以下对话片段压缩为简洁摘要。

输出格式（严格遵循）：
TOPIC: 一句话主题（不超过 50 字）
SUMMARY: 摘要正文

摘要必须保留：
- 做出的决策和结论
- 代码变更的文件路径和内容要点
- 用户表达的偏好和约束
- 未解决的问题和待办事项
- 关键的技术细节和架构选择

摘要不需要保留：
- 寒暄和确认性回复
- 已被后续修正的中间方案
- 重复出现的信息（只保留最终版本）"""


@dataclass(slots=True)
class LlmCompressor:
    """用 provider 生成高质量压缩摘要的 opt-in compressor。"""

    provider: Provider
    prompt_template: str = DEFAULT_COMPRESSION_PROMPT
    max_output_tokens: int = 1024
    compression_ratio: float = 0.3

    def compress(
        self,
        segment_id: str,
        messages: Sequence[Message],
    ) -> CompressedSegment:
        """用 LLM 压缩消息序列为 topic + summary。"""

        if not messages:
            raise ValueError("cannot compress an empty message sequence")

        serialized = self._serialize_messages(messages)
        response = self.provider.complete(
            ProviderRequest(
                system=self._system_prompt(serialized),
                messages=[UserMessage(content=serialized)],
            ),
        )
        topic, summary = self._parse_llm_output(response.content)
        return CompressedSegment(id=segment_id, topic=topic, summary=summary)

    def compress_package(
        self,
        segment_id: str,
        session_id: str,
        messages: Sequence[Message],
    ) -> CompressedSegmentPackage:
        """生成完整 compression package，包含 recall document。"""

        segment = self.compress(segment_id, messages)
        helper = RuleBasedCompressor()
        return CompressedSegmentPackage(
            segment=segment,
            source_refs=tuple(message.id for message in messages),
            recall_document=SegmentRecallDocument(
                session_id=session_id,
                segment_id=segment.id,
                topic=segment.topic,
                summary=segment.summary,
                keywords=extract_keywords(messages),
                tool_hints=extract_tool_hints(messages),
                searchable_text=clip_text(
                    f"{segment.topic} {segment.summary} {self._serialize_messages(messages)}",
                    limit=helper.max_searchable_text_chars,
                ),
            ),
        )

    def _system_prompt(self, serialized_messages: str) -> str:
        """构造带目标输出预算的 system prompt。"""

        budget = self._target_output_tokens(serialized_messages)
        return f"{self.prompt_template}\n\n目标输出上限: {budget} tokens"

    def _target_output_tokens(self, serialized_messages: str) -> int:
        """按粗略 4 chars/token 估算输出 token 预算。"""

        estimated_input_tokens = max(1, len(serialized_messages) // 4)
        ratio_budget = max(1, int(estimated_input_tokens * self.compression_ratio))
        return min(self.max_output_tokens, ratio_budget)

    def _serialize_messages(self, messages: Sequence[Message]) -> str:
        """把原始消息序列化为 LLM 可读文本。"""

        return "\n".join(
            f"{message.role}: {message.content}"
            for message in messages
        )

    def _parse_llm_output(self, text: str) -> tuple[str, str]:
        """解析 TOPIC/SUMMARY 格式，失败时退化为整段摘要。"""

        cleaned = text.strip()
        topic_prefix = "TOPIC:"
        summary_prefix = "SUMMARY:"
        if topic_prefix in cleaned and summary_prefix in cleaned:
            topic_start = cleaned.index(topic_prefix) + len(topic_prefix)
            summary_start = cleaned.index(summary_prefix)
            topic = cleaned[topic_start:summary_start].strip()
            summary = cleaned[summary_start + len(summary_prefix):].strip()
            if topic and summary:
                return self._clip_topic(topic), summary
        return self._clip_topic(cleaned), cleaned

    def _clip_topic(self, topic: str) -> str:
        """限制 topic 长度。"""

        normalized = " ".join(topic.split())
        if len(normalized) <= 50:
            return normalized
        return normalized[:47] + "..."


class FallbackCompressor:
    """先尝试 primary compressor，失败后回退到 fallback compressor。"""

    def __init__(self, primary: Compressor, fallback: Compressor) -> None:
        """创建 fallback compressor。"""

        self.primary = primary
        self.fallback = fallback

    def compress(
        self,
        segment_id: str,
        messages: Sequence[Message],
    ) -> CompressedSegment:
        """压缩消息，primary 失败时使用 fallback。"""

        try:
            return self.primary.compress(segment_id, messages)
        except Exception:
            return self.fallback.compress(segment_id, messages)

    def compress_package(
        self,
        segment_id: str,
        session_id: str,
        messages: Sequence[Message],
    ) -> CompressedSegmentPackage:
        """生成 compression package，primary 失败时使用 fallback。"""

        try:
            primary_package = getattr(self.primary, "compress_package", None)
            if callable(primary_package):
                return primary_package(segment_id, session_id, messages)
            segment = self.primary.compress(segment_id, messages)
        except Exception:
            fallback_package = getattr(self.fallback, "compress_package", None)
            if callable(fallback_package):
                return fallback_package(segment_id, session_id, messages)
            segment = self.fallback.compress(segment_id, messages)

        helper = RuleBasedCompressor()
        return CompressedSegmentPackage(
            segment=segment,
            source_refs=tuple(message.id for message in messages),
            recall_document=SegmentRecallDocument(
                session_id=session_id,
                segment_id=segment.id,
                topic=segment.topic,
                summary=segment.summary,
                keywords=extract_keywords(messages),
                tool_hints=extract_tool_hints(messages),
                searchable_text=clip_text(
                    f"{segment.topic} {segment.summary}",
                    limit=helper.max_searchable_text_chars,
                ),
            ),
        )
