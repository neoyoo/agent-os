from dataclasses import dataclass
from typing import Literal

from agentos._frozen_json import FrozenJsonObject, freeze_json, thaw_json
from agentos.artifacts import ArtifactRef


MessageRole = Literal["user", "assistant", "tool"]


@dataclass(frozen=True, slots=True, init=False)
class ToolCall:
    """assistant 消息中声明的工具调用。"""

    id: str
    name: str
    arguments: FrozenJsonObject

    def __init__(
        self,
        id: str,
        name: str,
        arguments: dict[str, object] | FrozenJsonObject | None = None,
    ) -> None:
        frozen = freeze_json({} if arguments is None else arguments)
        if not isinstance(frozen, FrozenJsonObject):
            raise TypeError("tool call arguments must be a JSON object")
        object.__setattr__(self, "id", id)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "arguments", frozen)

    def to_provider_dict(self) -> dict[str, object]:
        """转换为 provider message 中可序列化的工具调用摘要。"""

        result: dict[str, object] = {"id": self.id, "name": self.name}
        if self.arguments:
            result["arguments"] = thaw_json(self.arguments)
        return result


@dataclass(frozen=True, slots=True)
class StoredMessage:
    """MessageStore 中 append-only 保存的业务消息真值。"""

    id: str
    role: MessageRole
    content: str
    artifact_refs: tuple[ArtifactRef, ...] = ()
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "artifact_refs", tuple(self.artifact_refs))
        object.__setattr__(self, "tool_calls", tuple(self.tool_calls))


# Phase 2 migration bridge; remove in Task 13.
Message = StoredMessage


@dataclass(frozen=True, slots=True)
class MessageRef:
    """ActiveWindow 中指向 MessageStore 原文的引用。"""

    message_id: str
    temporary: bool = False
