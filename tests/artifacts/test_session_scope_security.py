import base64
import json

import pytest

from agentos.artifacts.in_memory import InMemoryArtifactStore
from agentos.artifacts.types import (
    ArtifactNotFoundError,
    ArtifactValidationError,
)


def _cursor(payload: dict[str, object]) -> str:
    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def put(store: InMemoryArtifactStore, session_id: str):
    return store.put(
        session_id=session_id,
        data=b"secret",
        filename="drawing.png",
        media_type="image/png",
    )


@pytest.mark.parametrize("operation", ["get", "read", "delete"])
def test_unknown_and_cross_session_access_have_identical_not_found(
    operation: str,
) -> None:
    store = InMemoryArtifactStore()
    record = put(store, "session-1")
    method = getattr(store, operation)

    messages = []
    for artifact_id in (
        record.id,
        "art_00000000-0000-4000-8000-000000000001",
    ):
        with pytest.raises(ArtifactNotFoundError) as error:
            method("session-2", artifact_id)
        messages.append(str(error.value))

    assert messages == ["artifact not found", "artifact not found"]


@pytest.mark.parametrize(
    "cursor",
    [
        "not-base64!",
        _cursor({"version": 1}),
        _cursor({"artifact_id": "secret", "version": 1}),
        _cursor(
            {
                "artifact_id": "art_00000000-0000-4000-8000-000000000001",
                "session_id": "session-1",
                "version": 1,
            }
        ),
        _cursor(
            {
                "artifact_id": "art_00000000-0000-4000-8000-000000000001",
                "version": 2,
            }
        ),
    ],
)
def test_invalid_cursor_has_fixed_safe_error(cursor: str) -> None:
    with pytest.raises(
        ArtifactValidationError,
        match="^invalid artifact cursor$",
    ):
        InMemoryArtifactStore().list("session-1", cursor=cursor)


def test_deleted_and_cross_session_cursor_anchor_have_same_error() -> None:
    store = InMemoryArtifactStore()
    cross = put(store, "session-2")
    deleted = put(store, "session-1")
    deleted_cursor = _cursor({"artifact_id": deleted.id, "version": 1})
    cross_cursor = _cursor({"artifact_id": cross.id, "version": 1})
    store.delete("session-1", deleted.id)

    for cursor in (deleted_cursor, cross_cursor):
        with pytest.raises(
            ArtifactValidationError,
            match="^invalid artifact cursor$",
        ):
            store.list("session-1", cursor=cursor)
