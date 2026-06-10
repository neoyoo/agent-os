from dataclasses import dataclass
from typing import Protocol, Sequence

from agentos.messages import Message
from agentos.tokens import TokenCounter


class CompressionBudget(Protocol):
    """Compression evictor 依赖的预算协议。"""

    def should_compress(self, messages: Sequence[Message]) -> bool:
        """判断 active window 是否超过预算。"""

    def oldest_prefix_size(self, messages: Sequence[Message]) -> int:
        """返回应优先压缩的最旧消息前缀长度。"""


@dataclass(frozen=True, slots=True)
class BudgetPolicy:
    """根据 active message 数量决定是否需要压缩。"""

    max_active_messages: int
    retain_latest_messages: int = 2

    def __post_init__(self) -> None:
        """校验预算参数，避免压缩策略进入不可执行状态。"""

        if self.max_active_messages < 1:
            raise ValueError("max_active_messages must be at least 1")
        if self.retain_latest_messages < 1:
            raise ValueError("retain_latest_messages must be at least 1")
        if self.retain_latest_messages > self.max_active_messages:
            raise ValueError(
                "retain_latest_messages must not exceed max_active_messages",
            )

    def should_compress(self, messages: Sequence[Message]) -> bool:
        """判断 active window 是否超过预算。"""

        return len(messages) > self.max_active_messages

    def oldest_prefix_size(self, messages: Sequence[Message]) -> int:
        """返回应优先压缩的最旧消息前缀长度。"""

        if not self.should_compress(messages):
            return 0
        return max(1, len(messages) - self.retain_latest_messages)


@dataclass(frozen=True, slots=True)
class TokenBudgetPolicy:
    """按 token 预算触发压缩，并按 token 保留最新后缀。"""

    token_counter: TokenCounter
    context_window: int
    reserve_output_tokens: int = 4096
    retain_latest_tokens: int = 8000
    static_overhead_tokens: int = 0

    def __post_init__(self) -> None:
        """校验 token 预算参数。"""

        if self.context_window < 1:
            raise ValueError("context_window must be at least 1")
        if self.reserve_output_tokens < 0:
            raise ValueError("reserve_output_tokens must not be negative")
        if self.retain_latest_tokens < 1:
            raise ValueError("retain_latest_tokens must be at least 1")
        if self.static_overhead_tokens < 0:
            raise ValueError("static_overhead_tokens must not be negative")
        if self.effective_window < 1:
            raise ValueError("effective_window must be at least 1")

    @property
    def effective_window(self) -> int:
        """扣除输出 headroom 后的可用输入窗口。"""

        return self.context_window - self.reserve_output_tokens

    def should_compress(self, messages: Sequence[Message]) -> bool:
        """判断 active window 是否超过 token 预算。"""

        return self._message_tokens(messages) + self.static_overhead_tokens > self.effective_window

    def oldest_prefix_size(self, messages: Sequence[Message]) -> int:
        """返回应优先压缩的最旧消息前缀长度。"""

        if not self.should_compress(messages):
            return 0
        retained_tokens = 0
        retained_count = 0
        for message in reversed(messages):
            message_tokens = self._single_message_tokens(message)
            if retained_count > 0 and retained_tokens + message_tokens > self.retain_latest_tokens:
                break
            retained_tokens += message_tokens
            retained_count += 1
        return max(0, len(messages) - retained_count)

    def _message_tokens(self, messages: Sequence[Message]) -> int:
        return sum(self._single_message_tokens(message) for message in messages)

    def _single_message_tokens(self, message: Message) -> int:
        return self.token_counter.count_text(message.content)
