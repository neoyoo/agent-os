from dataclasses import dataclass, field

from agentos.messages.store import MessageStore
from agentos.messages.types import Message, MessageRole, ToolCall
from agentos.messages.window import ActiveWindow
from agentos.providers import (
    AssistantMessage,
    ProviderMessage,
    ProviderToolCall,
    ToolResultMessage,
    UserMessage,
)


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

    def hydrate_messages(self, messages: list[Message]) -> None:
        """把外部存储召回的原始消息水合进本地 MessageStore。"""

        for message in messages:
            self.store.put(message)

    def has_temporary_recalled(self) -> bool:
        """判断是否存在尚未被 provider request 消费的召回消息。"""

        return self.active_window.has_temporary()

    def materialize_active(self, consume_temporary: bool = False) -> list[Message]:
        """返回 active window 中的原始消息。"""

        messages = self.active_window.materialize(self.store)
        if consume_temporary:
            self.active_window.clear_temporary()
        return messages

    def materialize_provider_messages(self) -> list[ProviderMessage]:
        """返回 provider request 可直接使用的 active messages。"""

        return [
            self._to_provider_message(message)
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

    def _to_provider_message(self, message: Message) -> ProviderMessage:
        """把内部 Message 转为 provider 边界强类型消息。"""

        if message.role == "user":
            return UserMessage(content=message.content)
        if message.role == "assistant":
            return AssistantMessage(
                content=message.content,
                tool_calls=tuple(
                    ProviderToolCall(
                        id=tool_call.id,
                        name=tool_call.name,
                        arguments=dict(tool_call.arguments),
                    )
                    for tool_call in message.tool_calls
                ),
            )
        if message.role == "tool":
            if message.tool_call_id is None:
                raise ValueError("tool message requires tool_call_id")
            return ToolResultMessage(
                tool_call_id=message.tool_call_id,
                content=message.content,
            )
        raise ValueError(f"unsupported message role: {message.role}")
