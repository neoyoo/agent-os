import base64
import json
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from inspect import signature

import pytest

from agentos.artifacts.in_memory import InMemoryArtifactStore
from agentos.artifacts.store import ArtifactStore
from agentos.artifacts.types import (
    ArtifactNotFoundError,
    ArtifactValidationError,
)


ArtifactStoreFactory = Callable[[], ArtifactStore]


@pytest.fixture
def store_factory() -> ArtifactStoreFactory:
    return InMemoryArtifactStore


def put(store: ArtifactStore, *, session_id: str = "session-1", data: bytes = b"a"):
    return store.put(
        session_id=session_id,
        data=data,
        filename="drawing.png",
        media_type="image/png",
    )


def test_store_protocol_freezes_six_session_scoped_methods() -> None:
    assert {
        name
        for name, value in ArtifactStore.__dict__.items()
        if callable(value) and not name.startswith("_")
    } == {"put", "get", "read", "list", "delete", "delete_session"}
    assert list(signature(ArtifactStore.list).parameters) == [
        "self",
        "session_id",
        "cursor",
        "limit",
    ]


def test_store_round_trip_and_duplicate_content_get_independent_ids(
    store_factory: ArtifactStoreFactory,
) -> None:
    store = store_factory()
    first = put(store, data=b"same")
    second = put(store, data=b"same")

    assert first.id != second.id
    assert store.get("session-1", first.id) == first
    assert store.read("session-1", first.id) == b"same"
    assert not hasattr(first, "data")


def test_store_list_defaults_to_latest_twenty_and_returns_stable_cursor() -> None:
    created_at = datetime(2026, 7, 15, tzinfo=UTC)
    ids = iter(
        [
            f"art_00000000-0000-4000-8000-{index:012x}"
            for index in range(21)
        ]
    )
    store = InMemoryArtifactStore(
        clock=lambda: created_at,
        id_factory=lambda: next(ids),
    )
    records = [put(store, data=str(index).encode()) for index in range(21)]

    first = store.list("session-1")
    second = store.list("session-1", cursor=first.next_cursor)

    assert first.items == tuple(reversed(records[1:]))
    assert first.next_cursor is not None
    assert second.items == (records[0],)
    assert second.next_cursor is None

    payload = _decode_cursor(first.next_cursor)
    assert payload == {"artifact_id": records[1].id, "version": 1}


def test_store_list_orders_created_at_then_artifact_id_descending() -> None:
    timestamps = iter(
        [
            datetime(2026, 7, 15, 10, tzinfo=UTC),
            datetime(2026, 7, 15, 11, tzinfo=UTC),
            datetime(2026, 7, 15, 11, tzinfo=UTC),
        ]
    )
    ids = iter(
        [
            "art_00000000-0000-4000-8000-000000000001",
            "art_00000000-0000-4000-8000-000000000002",
            "art_00000000-0000-4000-8000-000000000003",
        ]
    )
    store = InMemoryArtifactStore(
        clock=lambda: next(timestamps),
        id_factory=lambda: next(ids),
    )
    first, second, third = (put(store) for _ in range(3))

    page = store.list("session-1", limit=10)

    assert page.items == (third, second, first)


@pytest.mark.parametrize("limit", [0, 101, -1, True, 1.5, "20"])
def test_store_list_rejects_invalid_limit(
    store_factory: ArtifactStoreFactory,
    limit: object,
) -> None:
    with pytest.raises(ArtifactValidationError, match="artifact limit is invalid"):
        store_factory().list("session-1", limit=limit)  # type: ignore[arg-type]


def test_store_delete_and_delete_session_remove_metadata_and_content(
    store_factory: ArtifactStoreFactory,
) -> None:
    store = store_factory()
    one = put(store, data=b"one")
    two = put(store, data=b"two")
    other = put(store, session_id="session-2", data=b"other")

    store.delete("session-1", one.id)
    with pytest.raises(ArtifactNotFoundError, match="^artifact not found$"):
        store.read("session-1", one.id)

    store.delete_session("session-1")
    assert store.list("session-1").items == ()
    assert store.read("session-2", other.id) == b"other"
    with pytest.raises(ArtifactNotFoundError, match="^artifact not found$"):
        store.get("session-1", two.id)


def test_store_concurrent_puts_are_atomic_and_distinct(
    store_factory: ArtifactStoreFactory,
) -> None:
    store = store_factory()
    barrier = threading.Barrier(8)

    def upload(index: int) -> str:
        barrier.wait()
        return put(store, data=str(index).encode()).id

    with ThreadPoolExecutor(max_workers=8) as executor:
        ids = tuple(executor.map(upload, range(8)))

    assert len(set(ids)) == 8
    assert len(store.list("session-1", limit=100).items) == 8


def _decode_cursor(cursor: str) -> object:
    padding = "=" * (-len(cursor) % 4)
    raw = base64.urlsafe_b64decode(cursor + padding)
    return json.loads(raw.decode("utf-8"))
