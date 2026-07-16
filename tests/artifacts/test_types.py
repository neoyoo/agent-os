from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime, timedelta, timezone

import pytest

from agentos.artifacts.types import (
    ArtifactNotFoundError,
    ArtifactPage,
    ArtifactRecord,
    ArtifactRef,
    ArtifactValidationError,
    ContextMount,
    new_artifact_id,
)


def artifact_record(**overrides: object) -> ArtifactRecord:
    values: dict[str, object] = {
        "id": "art_550e8400-e29b-41d4-a716-446655440000",
        "session_id": "session-1",
        "filename": "drawing.png",
        "media_type": "image/png",
        "size_bytes": 12,
        "created_at": datetime(2026, 7, 15, tzinfo=UTC),
    }
    values.update(overrides)
    return ArtifactRecord(**values)  # type: ignore[arg-type]


def test_artifact_values_are_frozen_slotted_and_copy_page_items() -> None:
    record = artifact_record()
    source = [record]
    page = ArtifactPage(items=source, next_cursor="next")
    mount = ContextMount(record.id, "user_upload")

    source.clear()

    assert page.items == (record,)
    assert not hasattr(record, "__dict__")
    assert not hasattr(page, "__dict__")
    assert not hasattr(mount, "__dict__")
    assert [item.name for item in fields(ArtifactRecord)] == [
        "id",
        "session_id",
        "filename",
        "media_type",
        "size_bytes",
        "created_at",
    ]
    with pytest.raises(FrozenInstanceError):
        record.filename = "changed.png"  # type: ignore[misc]


def test_new_artifact_id_uses_art_prefix_and_uuid4() -> None:
    first = new_artifact_id()
    second = new_artifact_id()

    assert first.startswith("art_")
    assert first != second
    assert artifact_record(id=first).id == first


@pytest.mark.parametrize(
    "artifact_id",
    [
        "",
        "att_550e8400-e29b-41d4-a716-446655440000",
        "art_550e8400-e29b-11d4-a716-446655440000",
        "art_not-a-uuid",
        "art_550E8400-E29B-41D4-A716-446655440000",
    ],
)
def test_artifact_values_reject_invalid_artifact_ids(artifact_id: str) -> None:
    with pytest.raises(ArtifactValidationError, match="artifact id is invalid"):
        artifact_record(id=artifact_id)
    with pytest.raises(ArtifactValidationError, match="artifact id is invalid"):
        ArtifactRef(artifact_id, "drawing.png", "image/png")
    with pytest.raises(ArtifactValidationError, match="artifact id is invalid"):
        ContextMount(artifact_id, "tool_result")


@pytest.mark.parametrize(
    "overrides",
    [
        {"session_id": ""},
        {"session_id": "session\n1"},
        {"media_type": ""},
        {"media_type": "IMAGE PNG"},
        {"media_type": "image/png\n"},
        {"size_bytes": -1},
        {"size_bytes": True},
        {"created_at": datetime(2026, 7, 15)},
        {
            "created_at": datetime(
                2026,
                7,
                15,
                tzinfo=timezone(timedelta(hours=8)),
            ),
        },
    ],
)
def test_artifact_record_rejects_invalid_metadata(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ArtifactValidationError):
        artifact_record(**overrides)


@pytest.mark.parametrize(
    "filename",
    ["a" * 256, "folder/drawing.png", "folder\\drawing.png", "bad\nname.png"],
)
def test_artifact_record_rejects_unsafe_filename(filename: str) -> None:
    with pytest.raises(ArtifactValidationError, match="artifact filename is invalid"):
        artifact_record(filename=filename)


def test_artifact_record_accepts_missing_filename_and_utc_alias() -> None:
    record = artifact_record(filename=None, created_at=datetime.now(UTC))

    assert record.filename is None


def test_artifact_ref_rejects_unsafe_filename_and_media_type() -> None:
    artifact_id = artifact_record().id
    with pytest.raises(ArtifactValidationError, match="artifact filename is invalid"):
        ArtifactRef(artifact_id, "folder/drawing.png", "image/png")
    with pytest.raises(ArtifactValidationError, match="artifact media type is invalid"):
        ArtifactRef(artifact_id, "drawing.png", "IMAGE PNG")


@pytest.mark.parametrize("reason", ["upload", "tool", ""])
def test_context_mount_rejects_unknown_reason(reason: str) -> None:
    with pytest.raises(ArtifactValidationError, match="artifact mount reason is invalid"):
        ContextMount(artifact_record().id, reason)  # type: ignore[arg-type]


def test_context_mount_scope_is_fixed_to_current_turn() -> None:
    mount = ContextMount(artifact_record().id, "tool_result")

    assert mount.scope == "current_turn"
    with pytest.raises(ArtifactValidationError, match="artifact mount scope is invalid"):
        ContextMount(
            artifact_record().id,
            "tool_result",
            scope="session",  # type: ignore[arg-type]
        )


def test_artifact_page_rejects_invalid_items_and_cursor() -> None:
    with pytest.raises(ArtifactValidationError, match="artifact page items are invalid"):
        ArtifactPage(items=(object(),), next_cursor=None)  # type: ignore[arg-type]
    with pytest.raises(ArtifactValidationError, match="artifact cursor is invalid"):
        ArtifactPage(items=(), next_cursor="")


def test_not_found_error_has_fixed_safe_message() -> None:
    assert str(ArtifactNotFoundError()) == "artifact not found"
