from dataclasses import dataclass
from typing import Literal

from agentos._json_values import FrozenJsonObject, freeze_json
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
        if type(self.content) is not str:
            raise TypeError("stored message content must be str")
        artifact_refs = tuple(self.artifact_refs)
        tool_calls = tuple(self.tool_calls)
        if any(type(item) is not ArtifactRef for item in artifact_refs):
            raise TypeError("stored message artifact_refs require ArtifactRef")
        if any(type(item) is not ToolCall for item in tool_calls):
            raise TypeError("stored message tool_calls require ToolCall")
        object.__setattr__(self, "artifact_refs", artifact_refs)
        object.__setattr__(self, "tool_calls", tool_calls)


@dataclass(frozen=True, slots=True)
class MessageRef:
    """ActiveWindow 中指向 MessageStore 原文的引用。"""

    message_id: str
    temporary: bool = False
