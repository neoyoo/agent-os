from dataclasses import asdict, fields

import pytest

import agentos.events as public_events
from agentos.artifacts.in_memory import InMemoryArtifactStore
from agentos.artifacts.runtime import ArtifactRuntime
from agentos.artifacts.types import ArtifactNotFoundError, ArtifactValidationError
from agentos.events.artifacts import (
    ArtifactDeletedEvent,
    ArtifactLoadRequestedEvent,
    ArtifactMountedEvent,
    ArtifactUnmountedEvent,
    ArtifactUploadedEvent,
)
from agentos.events.bus import EventBus


def runtime() -> tuple[ArtifactRuntime, EventBus]:
    event_bus = EventBus()
    return (
        ArtifactRuntime(
            session_id="session-1",
            store=InMemoryArtifactStore(),
            event_bus=event_bus,
        ),
        event_bus,
    )


def upload(target: ArtifactRuntime, data: bytes = b"private-image"):
    return target.upload(
        data=data,
        filename="drawing.png",
        media_type="image/png",
    )


def test_artifact_events_stay_internal_and_never_define_content_fields() -> None:
    event_types = (
        ArtifactUploadedEvent,
        ArtifactLoadRequestedEvent,
        ArtifactMountedEvent,
        ArtifactUnmountedEvent,
        ArtifactDeletedEvent,
    )

    for event_type in event_types:
        assert not hasattr(public_events, event_type.__name__)
        assert {
            field.name for field in fields(event_type)
        } <= {
            "session_id",
            "turn_id",
            "handle",
            "filename",
            "media_type",
            "size_bytes",
            "reason",
        }
        assert not ({"data", "bytes", "path", "url", "provider_file_id"} & {
            field.name for field in fields(event_type)
        })


def test_upload_emits_safe_metadata_after_store_write() -> None:
    target, event_bus = runtime()

    record = upload(target)

    assert event_bus.events == [
        ArtifactUploadedEvent(
            session_id="session-1",
            handle=record.id,
            filename="drawing.png",
            media_type="image/png",
            size_bytes=len(b"private-image"),
        )
    ]
    assert b"private-image" not in repr(event_bus.events).encode()
    assert "data" not in asdict(event_bus.events[0])


def test_load_emits_requested_then_mounted_and_duplicate_is_idempotent() -> None:
    target, event_bus = runtime()
    record = upload(target)
    event_bus.events.clear()

    target.load_attachment(record.id)
    target.load_attachment(record.id)

    assert event_bus.events == [
        ArtifactLoadRequestedEvent(session_id="session-1", handle=record.id),
        ArtifactMountedEvent(
            session_id="session-1",
            handle=record.id,
            filename="drawing.png",
            media_type="image/png",
            reason="tool_result",
        ),
        ArtifactLoadRequestedEvent(session_id="session-1", handle=record.id),
    ]


def test_load_upgrades_user_mount_with_observable_unmount_and_mount() -> None:
    target, event_bus = runtime()
    record = upload(target)
    target.mount_user_upload(record.id)
    event_bus.events.clear()

    target.load_attachment(record.id)

    assert event_bus.events == [
        ArtifactLoadRequestedEvent(session_id="session-1", handle=record.id),
        ArtifactUnmountedEvent(
            session_id="session-1",
            handle=record.id,
            reason="user_upload",
        ),
        ArtifactMountedEvent(
            session_id="session-1",
            handle=record.id,
            filename="drawing.png",
            media_type="image/png",
            reason="tool_result",
        ),
    ]


def test_valid_unknown_load_emits_only_requested() -> None:
    target, event_bus = runtime()
    missing = "art_00000000-0000-4000-8000-000000000001"

    with pytest.raises(ArtifactNotFoundError, match="^artifact not found$"):
        target.load_attachment(missing)

    assert event_bus.events == [
        ArtifactLoadRequestedEvent(session_id="session-1", handle=missing)
    ]


@pytest.mark.parametrize("invalid", ["", "../../secret.png", "art_bad", "a" * 500])
def test_invalid_load_is_rejected_before_event_without_recording_input(
    invalid: str,
) -> None:
    target, event_bus = runtime()

    with pytest.raises(ArtifactValidationError, match="artifact id is invalid"):
        target.load_attachment(invalid)

    assert event_bus.events == []
    if invalid:
        assert invalid not in repr(event_bus.events)


def test_user_mount_clear_and_delete_emit_state_facts_in_order() -> None:
    target, event_bus = runtime()
    first = upload(target)
    second = upload(target)
    event_bus.events.clear()

    target.mount_user_upload(first.id)
    target.load_attachment(second.id)
    target.clear_mounts()
    target.delete(first.id)

    assert [type(event) for event in event_bus.events] == [
        ArtifactMountedEvent,
        ArtifactLoadRequestedEvent,
        ArtifactMountedEvent,
        ArtifactUnmountedEvent,
        ArtifactUnmountedEvent,
        ArtifactDeletedEvent,
    ]
    assert event_bus.events[0].reason == "user_upload"  # type: ignore[attr-defined]
    assert event_bus.events[4].handle == second.id  # type: ignore[attr-defined]
    assert event_bus.events[-1] == ArtifactDeletedEvent(
        session_id="session-1",
        handle=first.id,
        filename="drawing.png",
        media_type="image/png",
        size_bytes=len(b"private-image"),
    )


def test_delete_session_emits_unmounted_and_deleted_for_each_artifact() -> None:
    target, event_bus = runtime()
    first = upload(target)
    second = upload(target)
    target.mount_user_upload(first.id)
    event_bus.events.clear()

    target.delete_session()

    assert event_bus.events[0] == ArtifactUnmountedEvent(
        session_id="session-1",
        handle=first.id,
        reason="user_upload",
    )
    deleted = [
        event for event in event_bus.events if isinstance(event, ArtifactDeletedEvent)
    ]
    assert {event.handle for event in deleted} == {first.id, second.id}
