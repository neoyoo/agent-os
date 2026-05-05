from agentos.messages.types import Message, MessageRole, ToolCall


class MessageStore:
    """append-only 的原始消息存储。"""

    def __init__(self) -> None:
        """创建空消息存储。"""

        self._messages: list[Message] = []
        self._next_id = 1

    def append(
        self,
        role: MessageRole,
        content: str,
        tool_calls: list[ToolCall] | None = None,
        tool_call_id: str | None = None,
    ) -> Message:
        """追加一条原始消息，并返回新消息。"""

        message = Message(
            id=self._new_id(),
            role=role,
            content=content,
            tool_calls=list(tool_calls or []),
            tool_call_id=tool_call_id,
        )
        self._messages.append(message)
        return message

    def get(self, message_id: str) -> Message:
        """按 id 读取原始消息。"""

        for message in self._messages:
            if message.id == message_id:
                return message
        raise KeyError(message_id)

    def put(self, message: Message) -> None:
        """按原始 id 水合一条已存在的消息。"""

        for existing in self._messages:
            if existing.id == message.id:
                if existing != message:
                    raise ValueError(f"message id conflict: {message.id}")
                return
        self._messages.append(message)
        self._advance_next_id(message.id)

    def all(self) -> list[Message]:
        """返回全部原始消息副本。"""

        return list(self._messages)

    @classmethod
    def from_messages(cls, messages: list[Message], next_id: int) -> "MessageStore":
        """从持久化 snapshot 恢复 MessageStore。"""

        store = cls()
        store._messages = list(messages)
        store._next_id = next_id
        return store

    def next_id_number(self) -> int:
        """返回下一条消息将使用的数字序号。"""

        return self._next_id

    def _new_id(self) -> str:
        """生成稳定递增的消息 id。"""

        message_id = f"msg_{self._next_id}"
        self._next_id += 1
        return message_id

    def _advance_next_id(self, message_id: str) -> None:
        """根据水合消息 id 推进下一个本地递增 id。"""

        if not message_id.startswith("msg_"):
            return
        try:
            number = int(message_id.removeprefix("msg_"))
        except ValueError:
            return
        self._next_id = max(self._next_id, number + 1)
