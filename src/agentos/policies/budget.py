from dataclasses import dataclass
from typing import Sequence

from agentos.messages import Message


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
