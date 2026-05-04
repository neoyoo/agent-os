from dataclasses import dataclass, field

from agentos.messages.store import MessageStore
from agentos.messages.types import Message, MessageRef


class ToolPairWindowError(ValueError):
    """ActiveWindow 操作会破坏 tool_use/tool_result 配对。"""


@dataclass(slots=True)
class ActiveWindow:
    """维护当前 provider request 可见的 active message refs。"""

    refs: list[MessageRef] = field(default_factory=list)

    def append(self, message_id: str, temporary: bool = False) -> None:
        """把消息引用加入 active window。"""

        self.refs.append(MessageRef(message_id=message_id, temporary=temporary))

    def prepend_temporary(self, message_ids: list[str]) -> None:
        """把召回消息作为一次性 refs 插入 active window 前部。"""

        existing_message_ids = {ref.message_id for ref in self.refs}
        deduplicated_message_ids: list[str] = []
        for message_id in message_ids:
            if message_id in existing_message_ids:
                continue
            existing_message_ids.add(message_id)
            deduplicated_message_ids.append(message_id)

        temporary_refs = [
            MessageRef(message_id=message_id, temporary=True)
            for message_id in deduplicated_message_ids
        ]
        self.refs = [*temporary_refs, *self.refs]

    def clear_temporary(self) -> None:
        """移除已经被下一次 provider request 消费的一次性 refs。"""

        self.refs = [ref for ref in self.refs if not ref.temporary]

    def has_temporary(self) -> bool:
        """判断 active window 是否包含等待注入的召回 refs。"""

        return any(ref.temporary for ref in self.refs)

    def remove_refs(self, message_ids: list[str], store: MessageStore) -> None:
        """从 active window 移除 refs，同时保护 tool pair 不被切半。"""

        selected_ids = set(message_ids)
        self._ensure_tool_pairs_remain_valid(selected_ids, store)
        self.refs = [ref for ref in self.refs if ref.message_id not in selected_ids]

    def materialize(self, store: MessageStore) -> list[Message]:
        """读取 active refs 对应的原始消息。"""

        return [store.get(ref.message_id) for ref in self.refs]

    @classmethod
    def from_refs(cls, refs: list[MessageRef]) -> "ActiveWindow":
        """从持久化 refs 恢复 active window。"""

        return cls(refs=list(refs))

    def snapshot_refs(self) -> tuple[MessageRef, ...]:
        """返回不可变 active refs 快照。"""

        return tuple(self.refs)

    def _ensure_tool_pairs_remain_valid(
        self,
        selected_ids: set[str],
        store: MessageStore,
    ) -> None:
        """检查移除操作不会只移除 tool pair 的一侧。"""

        active_messages = self._materialize_persistent(store)
        for assistant in active_messages:
            if assistant.role != "assistant" or not assistant.tool_calls:
                continue
            for tool_call in assistant.tool_calls:
                for tool_result in self._tool_results_for(active_messages, tool_call.id):
                    assistant_selected = assistant.id in selected_ids
                    result_selected = tool_result.id in selected_ids
                    if assistant_selected != result_selected:
                        raise ToolPairWindowError(
                            "cannot remove only one side of a tool pair",
                        )

    def _tool_results_for(
        self,
        active_messages: list[Message],
        tool_call_id: str,
    ) -> list[Message]:
        """查找 active window 中与工具调用配对的 tool result 消息。"""

        return [
            message
            for message in active_messages
            if message.role == "tool" and message.tool_call_id == tool_call_id
        ]

    def _materialize_persistent(self, store: MessageStore) -> list[Message]:
        """读取非 temporary refs 对应的原始消息。"""

        return [
            store.get(ref.message_id)
            for ref in self.refs
            if not ref.temporary
        ]
