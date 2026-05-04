from dataclasses import dataclass, field

from agentos.messages.store import MessageStore
from agentos.messages.types import Message, MessageRole, ToolCall
from agentos.messages.window import ActiveWindow


@dataclass(slots=True)
class MessageRuntime:
    """消息真值源与 active window 的门面。"""

    store: MessageStore = field(default_factory=MessageStore)
    active_window: ActiveWindow = field(default_factory=ActiveWindow)

    def append_user(self, content: str) -> Message:
        """追加 user 消息并加入 active window。"""

        return self._append_active(role="user", content=content)

    def append_assistant(
        self,
        content: str,
        tool_calls: list[ToolCall] | None = None,
    ) -> Message:
        """追加 assistant 消息并加入 active window。"""

        return self._append_active(
            role="assistant",
            content=content,
            tool_calls=tool_calls,
        )

    def append_tool_result(self, tool_call_id: str, content: str) -> Message:
        """追加 tool result 消息并加入 active window。"""

        return self._append_active(
            role="tool",
            content=content,
            tool_call_id=tool_call_id,
        )

    def inject_temporary_recalled(self, message_ids: list[str]) -> None:
        """注入一次性召回 refs，供下一次 provider request 使用。"""

        for message_id in message_ids:
            self.store.get(message_id)
        self.active_window.prepend_temporary(message_ids)

    def has_temporary_recalled(self) -> bool:
        """判断是否存在尚未被 provider request 消费的召回消息。"""

        return self.active_window.has_temporary()

    def materialize_active(self, consume_temporary: bool = False) -> list[Message]:
        """返回 active window 中的原始消息。"""

        messages = self.active_window.materialize(self.store)
        if consume_temporary:
            self.active_window.clear_temporary()
        return messages

    def materialize_provider_messages(self) -> list[dict[str, object]]:
        """返回 provider request 可直接使用的 active messages。"""

        return [
            message.to_provider_dict()
            for message in self.materialize_active(consume_temporary=True)
        ]

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
    ) -> Message:
        """追加消息并同步 active ref。"""

        message = self.store.append(
            role=role,
            content=content,
            tool_calls=tool_calls,
            tool_call_id=tool_call_id,
        )
        self.active_window.append(message.id)
        return message
