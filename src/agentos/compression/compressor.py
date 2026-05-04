from dataclasses import dataclass
from typing import Protocol, Sequence

from agentos.context import CompressedSegment
from agentos.messages import Message


class Compressor(Protocol):
    """压缩器协议，允许后续替换为 LLMCompressor。"""

    def compress(self, segment_id: str, messages: Sequence[Message]) -> CompressedSegment:
        """把原始消息压缩为 LLM 可见摘要。"""


@dataclass(slots=True)
class RuleBasedCompressor:
    """测试和 fallback 使用的确定性压缩器。"""

    max_items: int = 4

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

        normalized = " ".join(value.split())
        if len(normalized) <= limit:
            return normalized
        return f"{normalized[: limit - 3]}..."
