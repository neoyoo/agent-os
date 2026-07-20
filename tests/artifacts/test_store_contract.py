import base64
import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime
from inspect import signature

import pytest

from agentos.artifacts.in_memory import InMemoryArtifactStore
from agentos.artifacts.store import ArtifactStore
from agentos.artifacts.types import (
    ArtifactNotFoundError,
    ArtifactValidationError,
)
from tests.artifacts._async import async_test


ArtifactStoreFactory = Callable[[], ArtifactStore]


@pytest.fixture
def store_factory() -> ArtifactStoreFactory:
    return InMemoryArtifactStore


async def put(
    store: ArtifactStore,
    *,
    session_id: str = "session-1",
    data: bytes = b"a",
):
    return await store.put(
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


@async_test
async def test_store_round_trip_and_duplicate_content_get_independent_ids(
    store_factory: ArtifactStoreFactory,
) -> None:
    store = store_factory()
    first = await put(store, data=b"same")
    second = await put(store, data=b"same")

    assert first.id != second.id
    assert await store.get("session-1", first.id) == first
    assert await store.read("session-1", first.id) == b"same"
    assert not hasattr(first, "data")


@async_test
async def test_store_list_defaults_to_latest_twenty_and_returns_stable_cursor() -> None:
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
    records = [
        await put(store, data=str(index).encode()) for index in range(21)
    ]

    first = await store.list("session-1")
    second = await store.list("session-1", cursor=first.next_cursor)

    assert first.items == tuple(reversed(records[1:]))
    assert first.next_cursor is not None
    assert second.items == (records[0],)
    assert second.next_cursor is None

    payload = _decode_cursor(first.next_cursor)
    assert payload == {"artifact_id": records[1].id, "version": 1}


@async_test
async def test_store_list_orders_created_at_then_artifact_id_descending() -> None:
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
    first, second, third = tuple([await put(store) for _ in range(3)])

    page = await store.list("session-1", limit=10)

    assert page.items == (third, second, first)


@pytest.mark.parametrize("limit", [0, 101, -1, True, 1.5, "20"])
@async_test
async def test_store_list_rejects_invalid_limit(
    store_factory: ArtifactStoreFactory,
    limit: object,
) -> None:
    with pytest.raises(ArtifactValidationError, match="artifact limit is invalid"):
        await store_factory().list(  # type: ignore[arg-type]
            "session-1",
            limit=limit,
        )


@async_test
async def test_store_delete_and_delete_session_remove_metadata_and_content(
    store_factory: ArtifactStoreFactory,
) -> None:
    store = store_factory()
    one = await put(store, data=b"one")
    two = await put(store, data=b"two")
    other = await put(store, session_id="session-2", data=b"other")

    await store.delete("session-1", one.id)
    with pytest.raises(ArtifactNotFoundError, match="^artifact not found$"):
        await store.read("session-1", one.id)

    await store.delete_session("session-1")
    assert (await store.list("session-1")).items == ()
    assert await store.read("session-2", other.id) == b"other"
    with pytest.raises(ArtifactNotFoundError, match="^artifact not found$"):
        await store.get("session-1", two.id)


@async_test
async def test_store_concurrent_puts_are_atomic_and_distinct(
    store_factory: ArtifactStoreFactory,
) -> None:
    store = store_factory()
    records = await asyncio.gather(
        *(put(store, data=str(index).encode()) for index in range(8))
    )
    ids = tuple(record.id for record in records)

    assert len(set(ids)) == 8
    assert len((await store.list("session-1", limit=100)).items) == 8


def _decode_cursor(cursor: str) -> object:
    padding = "=" * (-len(cursor) % 4)
    raw = base64.urlsafe_b64decode(cursor + padding)
    return json.loads(raw.decode("utf-8"))
