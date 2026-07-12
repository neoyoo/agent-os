import pytest

from agentos.attachments import AttachmentRuntime, BytesSource
from agentos.context import (
    ContextRuntime,
    ContextSnapshotRenderer,
    SystemEnvelope,
    WorkingStateField,
)
from agentos.context.projection import project_context_state
from agentos.messages import MessageRef, MessageRuntime, StoredMessage, ToolCall
from agentos.providers import ImagePart, TextPart
from agentos.runtime import ProviderRequestBuilder
from agentos.runtime.message_projection import project_stored_message
from agentos.runtime.provider_request_builder import ProviderRequestReceipt
from agentos.tokens import HeuristicTokenCounter
from tests._context_protocol_fixtures import default_context_renderer


class MutableProjectionProvider:
    def __init__(self, goal: str) -> None:
        self._runtime = ContextRuntime()
        self._runtime.declare_schema(
            [
                WorkingStateField(
                    name="task_goal",
                    type="string",
                    purpose="Current task goal.",
                ),
            ],
        )
        self.goal = goal

    @property
    def goal(self) -> str:
        return self._runtime.snapshot().working_state["task_goal"]  # type: ignore[return-value]

    @goal.setter
    def goal(self, value: str) -> None:
        self._runtime.update_state("task_goal", value)

    def projections(self):  # type: ignore[no-untyped-def]
        return project_context_state(self._runtime.snapshot())


class MutableSystemEnvelopeRenderer:
    def __init__(self, text: str) -> None:
        self.text = text

    def render(self) -> SystemEnvelope:
        return SystemEnvelope(self.text)


def configured_builder(
    messages: MessageRuntime,
    projections: MutableProjectionProvider,
    attachments: AttachmentRuntime | None = None,
) -> ProviderRequestBuilder:
    return ProviderRequestBuilder(
        context_renderer=default_context_renderer(),
        message_runtime=messages,
        snapshot_renderer=ContextSnapshotRenderer(HeuristicTokenCounter()),
        context_projections=projections,
        attachment_runtime=attachments,
    )


def test_builder_places_fresh_snapshot_before_active_messages() -> None:
    projections = MutableProjectionProvider(goal="first")
    messages = MessageRuntime()
    messages.append_user("hello")
    builder = configured_builder(messages, projections)

    first = builder.build_with_receipt()
    projections.goal = "second"
    second = builder.build_with_receipt()

    assert first is not second
    assert first.request is not second.request
    assert [item.kind for item in first.request.messages] == [
        "context_snapshot",
        "business_message",
    ]
    assert "first" in first.request.messages[0].content[0].text  # type: ignore[union-attr]
    assert "second" in second.request.messages[0].content[0].text  # type: ignore[union-attr]


def test_builder_renders_fresh_system_envelope_for_each_build() -> None:
    renderer = MutableSystemEnvelopeRenderer("first system")
    messages = MessageRuntime()
    messages.append_user("hello")
    builder = ProviderRequestBuilder(
        context_renderer=renderer,
        message_runtime=messages,
        snapshot_renderer=ContextSnapshotRenderer(HeuristicTokenCounter()),
        context_projections=MutableProjectionProvider(goal="stable"),
    )

    first = builder.build_with_receipt()
    renderer.text = "second system"
    second = builder.build_with_receipt()

    assert first.request.system == "first system"
    assert second.request.system == "second system"


def test_snapshot_never_interrupts_tool_pair() -> None:
    messages = MessageRuntime()
    messages.append_assistant(
        "",
        tool_calls=[ToolCall("call_1", "lookup", {"query": "x"})],
    )
    messages.append_tool_result("call_1", "done")

    build = configured_builder(
        messages,
        MutableProjectionProvider(goal="pair"),
    ).build_with_receipt()

    assert [item.kind for item in build.request.messages] == [
        "context_snapshot",
        "business_message",
        "tool_result",
    ]
    assert build.request.messages[1].tool_calls[0].id == "call_1"
    assert build.request.messages[2].tool_call_id == "call_1"


def test_build_does_not_consume_temporary_recall() -> None:
    messages = MessageRuntime()
    recalled = StoredMessage("msg_recalled", "user", "old evidence")
    messages.hydrate_messages([recalled])
    messages.active_window.prepend_temporary((recalled.id,))
    builder = configured_builder(
        messages,
        MutableProjectionProvider(goal="recall"),
    )

    first = builder.build_with_receipt()
    second = builder.build_with_receipt()

    assert first.receipt.temporary_message_ids == ("msg_recalled",)
    assert second.receipt.temporary_message_ids == ("msg_recalled",)
    assert first.request.messages[1].kind == "recalled_message"
    assert second.request.messages[1].kind == "recalled_message"
    assert messages.has_temporary_recalled()


def test_provider_request_receipt_copies_temporary_ids() -> None:
    temporary_ids = ["msg_recalled"]

    receipt = ProviderRequestReceipt(temporary_ids)  # type: ignore[arg-type]
    temporary_ids.clear()

    assert receipt.temporary_message_ids == ("msg_recalled",)


def test_message_projection_rejects_unsupported_role() -> None:
    message = StoredMessage(
        "msg_invalid",
        "invalid",  # type: ignore[arg-type]
        "untrusted persisted value",
    )

    with pytest.raises(ValueError, match="unsupported message role"):
        project_stored_message(MessageRef(message.id), message)


@pytest.mark.parametrize("temporary", [False, True])
@pytest.mark.parametrize(
    "message",
    [
        StoredMessage(
            "msg_user_calls",
            "user",
            "invalid",
            tool_calls=(ToolCall("call_1", "lookup", {}),),
        ),
        StoredMessage(
            "msg_user_call_id",
            "user",
            "invalid",
            tool_call_id="call_1",
        ),
        StoredMessage(
            "msg_assistant_call_id",
            "assistant",
            "invalid",
            tool_call_id="call_1",
        ),
        StoredMessage(
            "msg_tool_calls",
            "tool",
            "invalid",
            tool_calls=(ToolCall("call_2", "lookup", {}),),
            tool_call_id="call_1",
        ),
    ],
    ids=[
        "user-tool-calls",
        "user-tool-call-id",
        "assistant-tool-call-id",
        "tool-tool-calls",
    ],
)
def test_message_projection_rejects_invalid_role_fields(
    temporary: bool,
    message: StoredMessage,
) -> None:
    with pytest.raises(ValueError, match="invalid stored message shape"):
        project_stored_message(
            MessageRef(message.id, temporary=temporary),
            message,
        )


def test_initial_uploaded_image_extends_the_business_user_item() -> None:
    messages = MessageRuntime()
    attachments = AttachmentRuntime()
    attachment = attachments.upload_bytes(
        b"image-bytes",
        filename="diagram.png",
        mime_type="image/png",
    )
    content = attachments.prepare_user_message("Analyze the image", [attachment])
    messages.append_user(content)

    build = configured_builder(
        messages,
        MutableProjectionProvider(goal="image"),
        attachments,
    ).build_with_receipt()

    user_item = build.request.messages[1]
    assert user_item.kind == "business_message"
    assert user_item.content == (
        TextPart("Analyze the image"),
        ImagePart(attachment),
    )


def test_uploaded_image_remains_mounted_on_later_builds_in_the_turn() -> None:
    messages = MessageRuntime()
    attachments = AttachmentRuntime()
    attachment = attachments.upload_bytes(
        b"image-bytes",
        filename="diagram.png",
        mime_type="image/png",
    )
    content = attachments.prepare_user_message("Analyze the image", [attachment])
    messages.append_user(content)
    builder = configured_builder(
        messages,
        MutableProjectionProvider(goal="image"),
        attachments,
    )

    builder.build_with_receipt()
    second = builder.build_with_receipt()

    assert [item.kind for item in second.request.messages] == [
        "context_snapshot",
        "business_message",
        "context_mount",
    ]
    assert second.request.messages[-1].content == (
        TextPart(f"Loaded attachment {attachment.handle} for inspection."),
        ImagePart(attachment),
    )


def test_loaded_image_is_appended_as_context_mount() -> None:
    messages = MessageRuntime()
    messages.append_user("Inspect the loaded image")
    attachments = AttachmentRuntime()
    attachment = attachments.upload_bytes(
        b"image-bytes",
        filename="diagram.png",
        mime_type="image/png",
    )
    attachments.load_attachment_handle(f"att:{attachment.handle}")

    build = configured_builder(
        messages,
        MutableProjectionProvider(goal="mount"),
        attachments,
    ).build_with_receipt()

    mount = build.request.messages[-1]
    assert mount.kind == "context_mount"
    assert mount.content[0] == TextPart(
        f"Loaded attachment {attachment.handle} for inspection.",
    )
    image = mount.content[1]
    assert isinstance(image, ImagePart)
    assert isinstance(image.attachment.source, BytesSource)
    assert image.attachment.source.data == b"image-bytes"
