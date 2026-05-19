from pathlib import Path

import pytest

from agentos.attachments import (
    AttachmentError,
    AttachmentRuntime,
    BytesSource,
    ImagePart,
    TextPart,
)
from agentos.providers import UserMessage


def test_upload_bytes_creates_private_placeholder() -> None:
    runtime = AttachmentRuntime()
    attachment = runtime.upload_bytes(
        b"image-bytes",
        filename="diagram.png",
        mime_type="image/png",
    )

    placeholder = runtime.placeholder_text(attachment.handle)

    assert attachment.handle.startswith("att_")
    assert "diagram.png" in placeholder
    assert "image/png" in placeholder
    assert "load_image(handle=\"att:" in placeholder
    assert "image-bytes" not in placeholder
    assert "base64" not in placeholder.lower()


def test_prepare_user_message_expands_attachment_once() -> None:
    runtime = AttachmentRuntime()
    attachment = runtime.upload_bytes(
        b"image-bytes",
        filename="diagram.png",
        mime_type="image/png",
    )
    content = runtime.prepare_user_message("分析图片", [attachment])

    first_request = runtime.project_provider_messages([UserMessage(content=content)])
    second_request = runtime.project_provider_messages([UserMessage(content=content)])

    assert isinstance(first_request[0], UserMessage)
    assert first_request[0].content == (
        TextPart("分析图片"),
        ImagePart(attachment),
    )
    assert second_request == [UserMessage(content=content)]


def test_load_image_handle_persists_until_turn_clear() -> None:
    runtime = AttachmentRuntime()
    attachment = runtime.upload_bytes(
        b"image-bytes",
        filename="diagram.png",
        mime_type="image/png",
    )

    runtime.load_image_handle(f"att:{attachment.handle}")
    first_request = runtime.project_provider_messages([UserMessage(content="next")])
    second_request = runtime.project_provider_messages([UserMessage(content="next")])

    assert first_request[-1] == UserMessage(
        content=(
            TextPart(f"Loaded image {attachment.handle} for inspection."),
            ImagePart(attachment),
        ),
    )
    assert second_request[-1] == first_request[-1]


def test_clear_turn_loaded_images_resets_loaded_image_state() -> None:
    runtime = AttachmentRuntime()
    attachment = runtime.upload_bytes(
        b"image-bytes",
        filename="diagram.png",
        mime_type="image/png",
    )

    runtime.load_image_handle(f"att:{attachment.handle}")
    runtime.clear_turn_loaded_images()

    assert runtime.project_provider_messages([UserMessage(content="next")]) == [
        UserMessage(content="next"),
    ]


def test_load_image_unknown_attachment_handle_raises() -> None:
    runtime = AttachmentRuntime()

    with pytest.raises(AttachmentError, match="unknown attachment"):
        runtime.load_image_handle("att:missing")


def test_upload_rejects_non_image_attachment_mime() -> None:
    runtime = AttachmentRuntime()

    with pytest.raises(AttachmentError, match="unsupported attachment MIME"):
        runtime.upload_bytes(
            b"pdf data",
            filename="doc.pdf",
            mime_type="application/pdf",
        )


def test_upload_rejects_unsupported_mime_and_oversized_bytes() -> None:
    runtime = AttachmentRuntime(max_size_bytes=8)

    with pytest.raises(AttachmentError, match="unsupported attachment MIME"):
        runtime.upload_bytes(b"hello", filename="note.txt", mime_type="text/plain")
    with pytest.raises(AttachmentError, match="exceeds max attachment size"):
        runtime.upload_bytes(
            b"012345678",
            filename="diagram.png",
            mime_type="image/png",
        )


def test_upload_path_freezes_file_bytes_at_upload_time(tmp_path: Path) -> None:
    path = tmp_path / "diagram.png"
    path.write_bytes(b"first")
    runtime = AttachmentRuntime(max_size_bytes=6)

    attachment = runtime.upload(path, mime_type="image/png")
    path.write_bytes(b"changed-and-too-large")

    assert attachment.size_bytes == 5
    assert attachment.source == BytesSource(b"first")
