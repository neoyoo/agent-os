from copy import deepcopy
from dataclasses import dataclass, field
from typing import Literal


MessageRole = Literal["user", "assistant", "tool"]


@dataclass(frozen=True, slots=True)
class ToolCall:
    """assistant 消息中声明的工具调用。"""

    id: str
    name: str
    arguments: dict[str, object] = field(default_factory=dict)

    def to_provider_dict(self) -> dict[str, object]:
        """转换为 provider message 中可序列化的工具调用摘要。"""

        result: dict[str, object] = {"id": self.id, "name": self.name}
        if self.arguments:
            result["arguments"] = deepcopy(self.arguments)
        return result


@dataclass(frozen=True, slots=True)
class Message:
    """MessageStore 中 append-only 保存的原始消息。"""

    id: str
    role: MessageRole
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None

    def to_provider_dict(self) -> dict[str, object]:
        """转换为 provider 可接收的 active message 形态。"""

        message: dict[str, object] = {
            "role": self.role,
            "content": self.content,
        }
        if self.tool_calls:
            message["tool_calls"] = [
                tool_call.to_provider_dict() for tool_call in self.tool_calls
            ]
        if self.tool_call_id is not None:
            message["tool_call_id"] = self.tool_call_id
        return message


@dataclass(frozen=True, slots=True)
class MessageRef:
    """ActiveWindow 中指向 MessageStore 原文的引用。"""

    message_id: str
    temporary: bool = False
