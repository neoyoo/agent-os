from dataclasses import dataclass, field

from agentos.messages._migration import (
    _LegacyProviderMessage,
    materialize_provider_messages,
)
from agentos.messages.store import MessageStore
from agentos.messages.types import MessageRef, MessageRole, StoredMessage, ToolCall
from agentos.messages.window import ActiveWindow


@dataclass(slots=True)
class MessageRuntime:
    """消息真值源与 active window 的门面。"""

    store: MessageStore = field(default_factory=MessageStore)
    active_window: ActiveWindow = field(default_factory=ActiveWindow)

    def append_user(self, content: str) -> StoredMessage:
        """追加 user 消息并加入 active window。"""

        return self._append_active(role="user", content=content)

    def append_assistant(
        self,
        content: str,
        tool_calls: list[ToolCall] | None = None,
    ) -> StoredMessage:
        """追加 assistant 消息并加入 active window。"""

        return self._append_active(
            role="assistant",
            content=content,
            tool_calls=tool_calls,
        )

    def append_tool_result(
        self,
        tool_call_id: str,
        content: str,
    ) -> StoredMessage:
        """追加 tool result 消息并加入 active window。"""

        return self._append_active(
            role="tool",
            content=content,
            tool_call_id=tool_call_id,
        )

    def hydrate_messages(self, messages: list[StoredMessage]) -> None:
        """把外部存储召回的原始消息水合进本地 MessageStore。"""

        for message in messages:
            self.store.put(message)

    def has_temporary_recalled(self) -> bool:
        """判断是否存在尚未被 provider request 消费的召回消息。"""

        return self.active_window.has_temporary()

    def snapshot_active_with_refs(
        self,
    ) -> tuple[tuple[MessageRef, StoredMessage], ...]:
        """返回同一时刻的 active ref 与消息真值快照。"""

        return tuple(
            (ref, self.store.get(ref.message_id))
            for ref in self.active_window.snapshot_refs()
        )

    def consume_temporary_refs(self, message_ids: tuple[str, ...]) -> None:
        """按成功请求回执精确消费 temporary refs。"""

        self.active_window.consume_temporary(message_ids)

    def materialize_active(self) -> list[StoredMessage]:
        """返回 active window 中的原始消息。"""

        return [message for _, message in self.snapshot_active_with_refs()]

    def materialize_provider_messages(self) -> list[_LegacyProviderMessage]:
        """返回 provider request 可直接使用的 active messages。"""

        return materialize_provider_messages(self.materialize_active())

    @classmethod
    def from_parts(
        cls,
        store: MessageStore,
        active_window: ActiveWindow,
    ) -> "MessageRuntime":
        """从持久化组件恢复 MessageRuntime。"""

        return cls(store=store, active_window=active_window)

    def _append_active(
        self,
        role: MessageRole,
        content: str,
        tool_calls: list[ToolCall] | None = None,
        tool_call_id: str | None = None,
    ) -> StoredMessage:
        """追加消息并同步 active ref。"""

        message = self.store.append(
            role=role,
            content=content,
            tool_calls=tool_calls,
            tool_call_id=tool_call_id,
        )
        self.active_window.append(message.id)
        return message
