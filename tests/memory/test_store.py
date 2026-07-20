import asyncio
from datetime import UTC, datetime
from threading import Barrier, Lock, Thread

import pytest

from agentos.memory import (
    InMemoryMemoryStore,
    MemoryRecord,
    MemorySelectionContext,
)


def selection_context(
    *,
    session_id: str = "session_1",
    query: str = "project python",
) -> MemorySelectionContext:
    return MemorySelectionContext(
        session_id=session_id,
        principal_id="user_1",
        permissions={"memory:read"},
        query=query,
        now=datetime(2026, 7, 15, 12, 0, tzinfo=UTC),
    )


def record(
    handle: str,
    *,
    session_id: str = "session_1",
    content: str = "Python project preference.",
) -> MemoryRecord:
    return MemoryRecord(
        handle=handle,
        session_id=session_id,
        kind="semantic",
        category="preference",
        content=content,
    )


def test_in_memory_store_put_get_and_replace_by_handle() -> None:
    async def scenario() -> None:
        store = InMemoryMemoryStore()
        await store.put(record("mem_1", content="First value"))
        replacement = record("mem_1", content="Current value")

        await store.put(replacement)

        assert await store.get("mem_1") is replacement
        with pytest.raises(KeyError, match="missing"):
            await store.get("missing")

    asyncio.run(scenario())


def test_in_memory_store_rejects_cross_session_handle_replacement() -> None:
    async def scenario() -> None:
        store = InMemoryMemoryStore()
        original = record("mem_1")
        await store.put(original)

        with pytest.raises(
            ValueError,
            match="memory handle already belongs to another session",
        ):
            await store.put(record("mem_1", session_id="session_2"))

        assert await store.get("mem_1") is original

    asyncio.run(scenario())


def test_in_memory_store_does_not_accept_preloaded_internal_records() -> None:
    with pytest.raises(TypeError, match="_records"):
        InMemoryMemoryStore(  # type: ignore[call-arg]
            _records={"mem_1": record("mem_1")},
        )


def test_in_memory_store_cross_session_handle_claim_is_atomic() -> None:
    store = InMemoryMemoryStore()
    barrier = Barrier(3)
    result_lock = Lock()
    results: list[str] = []

    def claim(memory_record: MemoryRecord) -> None:
        barrier.wait()
        try:
            asyncio.run(store.put(memory_record))
        except ValueError:
            result = "rejected"
        else:
            result = "stored"
        with result_lock:
            results.append(result)

    threads = (
        Thread(target=claim, args=(record("mem_1", session_id="session_1"),)),
        Thread(target=claim, args=(record("mem_1", session_id="session_2"),)),
    )
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=2)

    assert all(not thread.is_alive() for thread in threads)
    assert sorted(results) == ["rejected", "stored"]
    assert asyncio.run(store.get("mem_1")).session_id in {"session_1", "session_2"}


@pytest.mark.parametrize("handle", [None, "", "  "])
def test_in_memory_store_get_rejects_invalid_handle(handle: object) -> None:
    async def scenario() -> None:
        store = InMemoryMemoryStore()

        with pytest.raises(ValueError, match="handle must be a non-empty string"):
            await store.get(handle)  # type: ignore[arg-type]

    asyncio.run(scenario())


def test_in_memory_store_search_isolates_session_candidates() -> None:
    async def scenario() -> None:
        store = InMemoryMemoryStore()
        await store.put(record("mem_local"))
        await store.put(record("mem_other", session_id="session_2"))

        candidates = await store.search(selection_context(), candidate_limit=10)

        assert [candidate.record.handle for candidate in candidates] == ["mem_local"]
        assert all(
            candidate.record.session_id == "session_1" for candidate in candidates
        )

    asyncio.run(scenario())


def test_in_memory_store_search_orders_score_then_handle() -> None:
    async def scenario() -> None:
        store = InMemoryMemoryStore()
        await store.put(record("mem_b", content="Python project"))
        await store.put(record("mem_a", content="Python project"))
        await store.put(record("mem_low", content="Python only"))

        candidates = await store.search(selection_context(), candidate_limit=3)

        assert [candidate.record.handle for candidate in candidates] == [
            "mem_a",
            "mem_b",
            "mem_low",
        ]
        assert candidates[0].score == candidates[1].score
        assert candidates[1].score > candidates[2].score

    asyncio.run(scenario())


def test_in_memory_store_search_bounds_and_empty_query_are_deterministic() -> None:
    async def scenario() -> None:
        store = InMemoryMemoryStore()
        await store.put(record("mem_b", content="Second"))
        await store.put(record("mem_a", content="First"))

        assert await store.search(selection_context(query=""), candidate_limit=0) == ()
        candidates = await store.search(selection_context(query=""), candidate_limit=1)

        assert [candidate.record.handle for candidate in candidates] == ["mem_a"]
        assert candidates[0].score == 0.0

    asyncio.run(scenario())


@pytest.mark.parametrize("candidate_limit", [-1, True, 1.5])
def test_in_memory_store_rejects_invalid_candidate_limit(
    candidate_limit: object,
) -> None:
    async def scenario() -> None:
        store = InMemoryMemoryStore()

        with pytest.raises(
            ValueError,
            match="candidate_limit must be a non-negative integer",
        ):
            await store.search(
                selection_context(),
                candidate_limit=candidate_limit,  # type: ignore[arg-type]
            )

    asyncio.run(scenario())


def test_in_memory_store_validates_context_before_zero_limit_shortcut() -> None:
    async def scenario() -> None:
        store = InMemoryMemoryStore()

        with pytest.raises(TypeError, match="context must be a MemorySelectionContext"):
            await store.search(object(), candidate_limit=0)  # type: ignore[arg-type]

    asyncio.run(scenario())
