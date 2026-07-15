from datetime import UTC, datetime

from agentos.context.snapshot import ContextSnapshotRenderer
from agentos.memory import (
    BoundMemoryProjectionProvider,
    MemoryCandidate,
    MemoryRecord,
    MemoryRuntime,
    MemorySelectionContext,
)
from tests.context._snapshot_fixtures import RecordingTokenCounter


class StaticStore:
    def __init__(self, candidates: tuple[MemoryCandidate, ...]) -> None:
        self.candidates = candidates
        self.search_count = 0

    def search(
        self,
        context: MemorySelectionContext,
        candidate_limit: int,
    ) -> tuple[MemoryCandidate, ...]:
        self.search_count += 1
        return self.candidates[:candidate_limit]


class AllowAllMemoryAccess:
    def allows(
        self,
        record: MemoryRecord,
        context: MemorySelectionContext,
    ) -> bool:
        return True


def selection_context() -> MemorySelectionContext:
    return MemorySelectionContext(
        session_id="session_1",
        principal_id="user_1",
        permissions={"memory:read"},
        query="project",
        now=datetime(2026, 7, 15, 12, 0, tzinfo=UTC),
    )


def memory(
    handle: str,
    content: str,
    *,
    artifact_handles: tuple[str, ...] = (),
) -> MemoryCandidate:
    return MemoryCandidate(
        MemoryRecord(
            handle=handle,
            session_id="session_1",
            kind="semantic",
            category="fact",
            content=content,
            artifact_handles=artifact_handles,
        ),
        score=1.0,
        reason="internal ranking evidence",
    )


def test_memory_projection_uses_fixed_safe_xml_and_complete_variants() -> None:
    store = StaticStore(
        (
            memory(
                "mem_1",
                'Use <XML> & "quotes".',
                artifact_handles=("art_internal",),
            ),
            memory("mem_2", "Second memory."),
            memory("mem_3", "Third memory."),
        ),
    )
    runtime = MemoryRuntime(
        store,
        AllowAllMemoryAccess(),
        top_k=3,
        candidate_limit=3,
        min_score=0.0,
    )

    projections = runtime.projections(selection_context())

    projection = projections[0]
    assert projection.slot == "memory-context"
    assert projection.owner == "MemoryRuntime"
    assert [len(variant.element.children) for variant in projection.variants] == [
        3,
        2,
        1,
    ]
    assert [variant.omitted_count for variant in projection.variants] == [0, 1, 2]
    assert all(
        set(dict(item.attributes)) == {"handle", "kind", "category", "instructional"}
        for item in projection.variants[0].element.children
    )

    snapshot = ContextSnapshotRenderer(RecordingTokenCounter(1)).render(projections)
    assert (
        '<memory handle="mem_1" kind="semantic" category="fact" '
        'instructional="false">Use &lt;XML&gt; &amp; "quotes".</memory>' in snapshot.xml
    )
    for hidden in (
        "internal ranking evidence",
        "score",
        "session_1",
        "user_1",
        "memory:read",
        "expires_at",
        "art_internal",
    ):
        assert hidden not in snapshot.xml


def test_memory_projection_omits_empty_slot() -> None:
    runtime = MemoryRuntime(
        StaticStore(()),
        AllowAllMemoryAccess(),
        top_k=1,
        candidate_limit=1,
        min_score=0.0,
    )

    assert runtime.projections(selection_context()) == ()


def test_bound_memory_projection_provider_freezes_request_context() -> None:
    store = StaticStore((memory("mem_1", "Remember this."),))
    runtime = MemoryRuntime(
        store,
        AllowAllMemoryAccess(),
        top_k=1,
        candidate_limit=1,
        min_score=0.0,
    )
    provider = BoundMemoryProjectionProvider(runtime, selection_context())

    assert provider.projections()
    assert store.search_count == 1
