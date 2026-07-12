"""从业务消息和显式领域事件构建前端会话读模型。"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal, Protocol, TypeAlias, cast

from agentos._internal_transcript import InternalTranscriptValue
from agentos.artifacts import ArtifactRef
from agentos.messages.types import StoredMessage


class UserVisibleConversationEvent:
    """只有显式用户可见领域事件才能继承的标记基类。"""

    __slots__ = ()


@dataclass(frozen=True, slots=True)
class ConversationMessageItem:
    """前端可展示的业务消息。"""

    message_id: str
    role: Literal["user", "assistant"]
    content: str
    artifact_refs: tuple[ArtifactRef, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "artifact_refs", tuple(self.artifact_refs))


@dataclass(frozen=True, slots=True)
class ConversationEventItem:
    """由显式 projector 生成的前端领域事件。"""

    event_id: str
    kind: str
    content: str


ConversationItem: TypeAlias = ConversationMessageItem | ConversationEventItem


class ConversationEventProjector(Protocol):
    """把一种用户可见领域事件投影为前端条目。"""

    event_type: type[UserVisibleConversationEvent]

    def project(
        self,
        event: UserVisibleConversationEvent,
    ) -> ConversationEventItem | None:
        """投影事件；返回 None 表示本次不展示。"""


@dataclass(frozen=True, slots=True)
class ConversationReadModel:
    """从权威业务数据构建前端会话条目。"""

    projectors: tuple[ConversationEventProjector, ...] = ()

    def __post_init__(self) -> None:
        projectors = tuple(self.projectors)
        event_types: list[type[UserVisibleConversationEvent]] = []
        for projector in projectors:
            event_type = getattr(projector, "event_type", None)
            if not isinstance(event_type, type) or not issubclass(
                event_type,
                UserVisibleConversationEvent,
            ):
                raise TypeError(
                    "conversation projector event_type must inherit "
                    "UserVisibleConversationEvent",
                )
            if any(
                issubclass(event_type, registered)
                or issubclass(registered, event_type)
                for registered in event_types
            ):
                raise ValueError("conversation projectors have overlapping event_type")
            event_types.append(event_type)
        object.__setattr__(self, "projectors", projectors)

    def build(
        self,
        *,
        messages: Iterable[StoredMessage],
        events: Iterable[object] = (),
    ) -> tuple[ConversationItem, ...]:
        """按业务消息在前、显式领域事件在后的顺序构建读模型。"""

        items: list[ConversationItem] = []
        for message in messages:
            if not isinstance(message, StoredMessage):
                raise TypeError("conversation messages must contain StoredMessage")
            if message.role == "tool":
                continue
            items.append(
                ConversationMessageItem(
                    message_id=message.id,
                    role=cast(Literal["user", "assistant"], message.role),
                    content=message.content,
                    artifact_refs=message.artifact_refs,
                ),
            )
        for event in events:
            if isinstance(event, InternalTranscriptValue):
                raise TypeError("internal transcript value is not a domain event")
            if not isinstance(event, UserVisibleConversationEvent):
                continue
            projected = self._project_event(event)
            if projected is not None:
                items.append(projected)
        return tuple(items)

    def _project_event(
        self,
        event: UserVisibleConversationEvent,
    ) -> ConversationEventItem | None:
        for projector in self.projectors:
            if not isinstance(event, projector.event_type):
                continue
            projected = projector.project(event)
            if projected is not None and not isinstance(
                projected,
                ConversationEventItem,
            ):
                raise TypeError("conversation projector must return ConversationEventItem")
            return projected
        return None
