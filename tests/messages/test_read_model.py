from dataclasses import dataclass

import pytest

from agentos.artifacts import ArtifactRef
from agentos.context import ContextSnapshot
from agentos.messages import (
    ConversationEventItem,
    ConversationMessageItem,
    ConversationReadModel,
    StoredMessage,
    UserVisibleConversationEvent,
)
from agentos.providers import ProviderInputItem, ProviderRequest, ProviderResponse


class InternalTrace:
    pass


@dataclass(frozen=True, slots=True)
class ApprovalGranted(UserVisibleConversationEvent):
    id: str


class ApprovalProjector:
    event_type = ApprovalGranted

    def project(self, event: ApprovalGranted) -> ConversationEventItem | None:
        return ConversationEventItem(event.id, "approval", "已批准")


class InvalidProjector:
    event_type = InternalTrace

    def project(self, event: InternalTrace) -> ConversationEventItem | None:
        return ConversationEventItem("evt_invalid", "invalid", "不应显示")


class BroadVisibleProjector:
    event_type = UserVisibleConversationEvent

    def project(
        self,
        event: UserVisibleConversationEvent,
    ) -> ConversationEventItem | None:
        return ConversationEventItem("evt_broad", "broad", "不应绕过内部拒绝")


class SuppressingApprovalProjector:
    event_type = ApprovalGranted

    def project(self, event: ApprovalGranted) -> ConversationEventItem | None:
        return None


def test_read_model_uses_business_messages_and_registered_event_projectors() -> None:
    artifact = ArtifactRef("art_1", "drawing.png", "image/png")
    model = ConversationReadModel(projectors=(ApprovalProjector(),))

    result = model.build(
        messages=(
            StoredMessage("msg_1", "user", "你好", artifact_refs=(artifact,)),
            StoredMessage("msg_2", "tool", "internal", tool_call_id="call_1"),
            StoredMessage("msg_3", "assistant", "已处理"),
        ),
        events=(InternalTrace(), ApprovalGranted("evt_1")),
    )

    assert result == (
        ConversationMessageItem("msg_1", "user", "你好", (artifact,)),
        ConversationMessageItem("msg_3", "assistant", "已处理"),
        ConversationEventItem("evt_1", "approval", "已批准"),
    )


def test_conversation_message_item_copies_artifact_refs() -> None:
    artifact = ArtifactRef("art_1", "drawing.png", "image/png")
    artifact_refs = [artifact]

    item = ConversationMessageItem(
        "msg_1",
        "user",
        "你好",
        artifact_refs,  # type: ignore[arg-type]
    )
    artifact_refs.clear()

    assert item.artifact_refs == (artifact,)


@pytest.mark.parametrize(
    "internal",
    [
        ContextSnapshot(xml="<context-snapshot/>\n"),
        ProviderRequest(system="s", messages=(), tools=()),
        ProviderInputItem.context_snapshot("<context-snapshot/>\n"),
        ProviderResponse(content="internal"),
    ],
)
def test_read_model_rejects_internal_transcript_before_projector_dispatch(
    internal: object,
) -> None:
    model = ConversationReadModel(projectors=(BroadVisibleProjector(),))

    with pytest.raises(TypeError, match="domain event"):
        model.build(messages=(), events=(internal,))


def test_read_model_rejects_projectors_for_unmarked_events() -> None:
    with pytest.raises(TypeError, match="UserVisibleConversationEvent"):
        ConversationReadModel(projectors=(InvalidProjector(),))


def test_read_model_rejects_overlapping_projector_event_types() -> None:
    with pytest.raises(ValueError, match="overlapping event_type"):
        ConversationReadModel(
            projectors=(BroadVisibleProjector(), ApprovalProjector()),
        )


def test_read_model_skips_unregistered_visible_events() -> None:
    assert ConversationReadModel().build(
        messages=(),
        events=(ApprovalGranted("evt_1"),),
    ) == ()


def test_read_model_skips_events_suppressed_by_projector() -> None:
    model = ConversationReadModel(projectors=(SuppressingApprovalProjector(),))

    assert model.build(
        messages=(),
        events=(ApprovalGranted("evt_1"),),
    ) == ()
