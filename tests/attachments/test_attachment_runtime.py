from dataclasses import replace
from pathlib import Path

import pytest

from agentos.attachments import (
    AttachmentError,
    AttachmentRuntime,
    BytesSource,
    ImagePart,
    TextPart,
)
from agentos.providers import ProviderInputItem


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
    assert f"handle: att:{attachment.handle}" in placeholder
    assert "load_attachment(handle=" not in placeholder
    assert "image-bytes" not in placeholder
    assert "base64" not in placeholder.lower()


def test_prepare_user_message_expands_attachment_for_rest_of_turn() -> None:
    runtime = AttachmentRuntime()
    attachment = runtime.upload_bytes(
        b"image-bytes",
        filename="diagram.png",
        mime_type="image/png",
    )
    user_text = "鍒嗘瀽鍥剧墖"
    content = runtime.prepare_user_message(user_text, [attachment])
    item = ProviderInputItem.business_user(content)

    first_request = runtime._project_provider_inputs_compat((item,))
    second_request = runtime._project_provider_inputs_compat((item,))

    initial_loaded = replace(
        ProviderInputItem.business_user(user_text),
        content=(
            TextPart(user_text),
            ImagePart(attachment),
        ),
    )
    turn_loaded = ProviderInputItem.context_mount(
        (
            TextPart(f"Loaded attachment {attachment.handle} for inspection."),
            ImagePart(attachment),
        ),
    )
    assert isinstance(first_request[0], ProviderInputItem)
    assert first_request == (initial_loaded,)
    assert second_request[-1] == turn_loaded


def test_load_attachment_handle_projects_for_rest_of_turn() -> None:
    runtime = AttachmentRuntime()
    attachment = runtime.upload_bytes(
        b"image-bytes",
        filename="diagram.png",
        mime_type="image/png",
    )

    runtime.load_attachment_handle(f"att:{attachment.handle}")
    item = ProviderInputItem.business_user("next")
    first_request = runtime._project_provider_inputs_compat((item,))
    second_request = runtime._project_provider_inputs_compat((item,))

    loaded = ProviderInputItem.context_mount(
        (
            TextPart(f"Loaded attachment {attachment.handle} for inspection."),
            ImagePart(attachment),
        ),
    )
    assert first_request[-1] == loaded
    assert second_request[-1] == loaded


def test_clear_turn_loaded_attachments_resets_loaded_attachment_state() -> None:
    runtime = AttachmentRuntime()
    attachment = runtime.upload_bytes(
        b"image-bytes",
        filename="diagram.png",
        mime_type="image/png",
    )

    runtime.load_attachment_handle(f"att:{attachment.handle}")
    runtime.clear_turn_loaded_attachments()

    item = ProviderInputItem.business_user("next")
    assert runtime._project_provider_inputs_compat((item,)) == (item,)


def test_load_attachment_unknown_attachment_handle_raises() -> None:
    runtime = AttachmentRuntime()

    with pytest.raises(AttachmentError, match="unknown attachment"):
        runtime.load_attachment_handle("att:missing")


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
