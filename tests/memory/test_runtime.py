from datetime import UTC, datetime, timedelta

import pytest

from agentos.memory import (
    MemoryCandidate,
    MemoryRecord,
    MemoryRuntime,
    MemorySelectionContext,
)


NOW = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)


def context(*, session_id: str = "session_1") -> MemorySelectionContext:
    return MemorySelectionContext(
        session_id=session_id,
        principal_id="user_1",
        permissions={"memory:read"},
        query="project preference",
        now=NOW,
    )


def record(
    handle: str,
    *,
    session_id: str = "session_1",
    expires_at: datetime | None = None,
) -> MemoryRecord:
    return MemoryRecord(
        handle=handle,
        session_id=session_id,
        kind="semantic",
        category="preference",
        content=f"Memory {handle}",
        expires_at=expires_at,
    )


class RecordingStore:
    def __init__(self, candidates: tuple[MemoryCandidate, ...]) -> None:
        self.candidates = candidates
        self.searches: list[tuple[MemorySelectionContext, int]] = []

    def search(
        self,
        selection_context: MemorySelectionContext,
        candidate_limit: int,
    ) -> tuple[MemoryCandidate, ...]:
        self.searches.append((selection_context, candidate_limit))
        return self.candidates


class HandleAccessPolicy:
    def __init__(self, denied: set[str] | None = None) -> None:
        self.denied = denied or set()
        self.checks: list[tuple[str, MemorySelectionContext]] = []

    def allows(
        self,
        memory_record: MemoryRecord,
        selection_context: MemorySelectionContext,
    ) -> bool:
        self.checks.append((memory_record.handle, selection_context))
        return memory_record.handle not in self.denied


def test_runtime_filters_and_deterministically_selects_top_k() -> None:
    candidates = (
        MemoryCandidate(record("mem_denied"), 1.0),
        MemoryCandidate(record("mem_other", session_id="session_2"), 1.0),
        MemoryCandidate(record("mem_expired", expires_at=NOW), 1.0),
        MemoryCandidate(record("mem_b"), 0.9),
        MemoryCandidate(record("mem_a"), 0.9),
        MemoryCandidate(record("mem_duplicate"), 0.8),
        MemoryCandidate(record("mem_duplicate"), 0.7),
        MemoryCandidate(record("mem_low"), 0.4),
    )
    store = RecordingStore(candidates)
    policy = HandleAccessPolicy({"mem_denied"})
    runtime = MemoryRuntime(
        store,
        policy,
        top_k=3,
        candidate_limit=10,
        min_score=0.5,
    )

    projections = runtime.projections(context())

    full = projections[0].variants[0].element
    assert [dict(child.attributes)["handle"] for child in full.children] == [
        "mem_a",
        "mem_b",
        "mem_duplicate",
    ]
    assert store.searches == [(context(), 10)]
    assert all(check_context == context() for _, check_context in policy.checks)


def test_runtime_searches_on_every_projection_request() -> None:
    store = RecordingStore((MemoryCandidate(record("mem_1"), 1.0),))
    runtime = MemoryRuntime(
        store,
        HandleAccessPolicy(),
        top_k=1,
        candidate_limit=2,
        min_score=0.0,
    )
    selection_context = context()

    first = runtime.projections(selection_context)
    store.candidates = (MemoryCandidate(record("mem_2"), 1.0),)
    second = runtime.projections(selection_context)

    assert len(store.searches) == 2
    assert first[0].variants[0].element.children[0].text == "Memory mem_1"
    assert second[0].variants[0].element.children[0].text == "Memory mem_2"


@pytest.mark.parametrize(
    ("top_k", "candidate_limit", "min_score", "message"),
    [
        (0, 1, 0.0, "top_k must be a positive integer"),
        (True, 1, 0.0, "top_k must be a positive integer"),
        (2, 1, 0.0, "candidate_limit must be at least top_k"),
        (1, 1.5, 0.0, "candidate_limit must be a positive integer"),
        (1, 1, -0.1, "min_score must be a number between 0 and 1"),
        (1, 1, float("nan"), "min_score must be a number between 0 and 1"),
        (1, 1, True, "min_score must be a number between 0 and 1"),
    ],
)
def test_runtime_rejects_invalid_selection_configuration(
    top_k: object,
    candidate_limit: object,
    min_score: object,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        MemoryRuntime(
            RecordingStore(()),
            HandleAccessPolicy(),
            top_k=top_k,  # type: ignore[arg-type]
            candidate_limit=candidate_limit,  # type: ignore[arg-type]
            min_score=min_score,  # type: ignore[arg-type]
        )


def test_runtime_rechecks_store_candidate_score() -> None:
    candidate = MemoryCandidate(record("mem_1"), 1.0)
    object.__setattr__(candidate, "score", float("nan"))
    runtime = MemoryRuntime(
        RecordingStore((candidate,)),
        HandleAccessPolicy(),
        top_k=1,
        candidate_limit=1,
        min_score=0.0,
    )

    with pytest.raises(ValueError, match="memory candidate score is invalid"):
        runtime.projections(context())


def test_runtime_rejects_non_boolean_access_policy_result() -> None:
    class InvalidAccessPolicy:
        def allows(
            self,
            memory_record: MemoryRecord,
            selection_context: MemorySelectionContext,
        ) -> object:
            return "allowed"

    runtime = MemoryRuntime(
        RecordingStore((MemoryCandidate(record("mem_1"), 1.0),)),
        InvalidAccessPolicy(),  # type: ignore[arg-type]
        top_k=1,
        candidate_limit=1,
        min_score=0.0,
    )

    with pytest.raises(
        TypeError,
        match="memory access policy must return a boolean",
    ):
        runtime.projections(context())


def test_expiry_after_explicit_request_time_remains_available() -> None:
    future = record("mem_future", expires_at=NOW + timedelta(seconds=1))
    runtime = MemoryRuntime(
        RecordingStore((MemoryCandidate(future, 1.0),)),
        HandleAccessPolicy(),
        top_k=1,
        candidate_limit=1,
        min_score=0.0,
    )

    assert runtime.projections(context())
