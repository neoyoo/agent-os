from collections.abc import Iterable
from dataclasses import dataclass, field

from agentos.messages.store import MessageStore
from agentos.messages.types import MessageRef, StoredMessage


class ToolPairWindowError(ValueError):
    """ActiveWindow 操作会破坏 tool_use/tool_result 配对。"""


@dataclass(slots=True, init=False)
class ActiveWindow:
    """维护当前 provider request 可见的 active message refs。"""

    _refs: list[MessageRef] = field(default_factory=list, repr=False)

    def __init__(self, refs: Iterable[MessageRef] = ()) -> None:
        """创建 message ID 全局唯一的 active window。"""

        copied_refs = list(refs)
        self._ensure_unique_message_ids(copied_refs)
        self._refs = copied_refs

    @property
    def refs(self) -> tuple[MessageRef, ...]:
        """返回不可变 active refs 快照。"""

        return tuple(self._refs)

    def append(self, message_id: str, temporary: bool = False) -> None:
        """把消息引用加入 active window。"""

        if any(ref.message_id == message_id for ref in self._refs):
            return
        self._refs.append(MessageRef(message_id=message_id, temporary=temporary))

    def prepend_temporary(self, message_ids: Iterable[str]) -> None:
        """把召回消息作为一次性 refs 插入 active window 前部。"""

        existing_message_ids = {ref.message_id for ref in self._refs}
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
        self._refs = [*temporary_refs, *self._refs]

    def consume_temporary(self, message_ids: tuple[str, ...]) -> None:
        """按请求回执精确移除已经成功投影的 temporary refs。"""

        consumed_ids = set(message_ids)
        self._refs = [
            ref
            for ref in self._refs
            if not (ref.temporary and ref.message_id in consumed_ids)
        ]

    def has_temporary(self) -> bool:
        """判断 active window 是否包含等待注入的召回 refs。"""

        return any(ref.temporary for ref in self._refs)

    def remove_refs(self, message_ids: list[str], store: MessageStore) -> None:
        """从 active window 移除 refs，同时保护 tool pair 不被切半。"""

        selected_ids = set(message_ids)
        self._ensure_tool_pairs_remain_valid(selected_ids, store)
        self._refs = [
            ref for ref in self._refs if ref.message_id not in selected_ids
        ]

    def materialize(self, store: MessageStore) -> list[StoredMessage]:
        """读取 active refs 对应的原始消息。"""

        return [store.get(ref.message_id) for ref in self._refs]

    @classmethod
    def from_refs(cls, refs: Iterable[MessageRef]) -> "ActiveWindow":
        """从持久化 refs 恢复 active window。"""

        return cls(refs)

    def snapshot_refs(self) -> tuple[MessageRef, ...]:
        """返回不可变 active refs 快照。"""

        return tuple(self._refs)

    @staticmethod
    def _ensure_unique_message_ids(refs: list[MessageRef]) -> None:
        """拒绝无法由 message ID receipt 精确消费的重复 refs。"""

        seen: set[str] = set()
        for ref in refs:
            if ref.message_id in seen:
                raise ValueError(f"duplicate active message ref: {ref.message_id}")
            seen.add(ref.message_id)

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
        active_messages: list[StoredMessage],
        tool_call_id: str,
    ) -> list[StoredMessage]:
        """查找 active window 中与工具调用配对的 tool result 消息。"""

        return [
            message
            for message in active_messages
            if message.role == "tool" and message.tool_call_id == tool_call_id
        ]

    def _materialize_persistent(
        self,
        store: MessageStore,
    ) -> list[StoredMessage]:
        """读取非 temporary refs 对应的原始消息。"""

        return [
            store.get(ref.message_id)
            for ref in self._refs
            if not ref.temporary
        ]
