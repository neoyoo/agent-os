from dataclasses import dataclass
from typing import Sequence

from agentos.messages import Message
from agentos.policies import CompressionBudget


@dataclass(frozen=True, slots=True)
class Evictor:
    """选择可压缩的 active message refs。"""

    budget_policy: CompressionBudget

    def select_message_ids(self, messages: Sequence[Message]) -> list[str]:
        """选择最旧的连续前缀，并扩展边界以保护 tool pair。"""

        selected_until = self.budget_policy.oldest_prefix_size(messages) - 1
        if selected_until < 0:
            return []

        selected_until = self._expand_for_tool_pairs(messages, selected_until)
        return [message.id for message in messages[: selected_until + 1]]

    def _expand_for_tool_pairs(
        self,
        messages: Sequence[Message],
        selected_until: int,
    ) -> int:
        """如果选择边界切到 tool pair 中间，就扩展到 pair 结束。"""

        changed = True
        while changed:
            changed = False
            for index, message in enumerate(messages):
                if message.role != "assistant" or not message.tool_calls:
                    continue

                result_indexes = self._tool_result_indexes(messages, message)
                assistant_selected = index <= selected_until
                result_selected = any(item <= selected_until for item in result_indexes)

                if assistant_selected and result_indexes:
                    new_boundary = max(selected_until, *result_indexes)
                elif result_selected:
                    new_boundary = max(selected_until, index)
                else:
                    new_boundary = selected_until

                if new_boundary != selected_until:
                    selected_until = new_boundary
                    changed = True

        return selected_until

    def _tool_result_indexes(
        self,
        messages: Sequence[Message],
        assistant: Message,
    ) -> list[int]:
        """查找 assistant tool calls 对应的 tool result 位置。"""

        tool_call_ids = {tool_call.id for tool_call in assistant.tool_calls}
        return [
            index
            for index, message in enumerate(messages)
            if message.role == "tool" and message.tool_call_id in tool_call_ids
        ]
