from dataclasses import fields
from datetime import UTC, datetime

import pytest

from agentos.memory import MemoryCandidate, MemoryRecord, MemorySelectionContext


@pytest.mark.parametrize(
    ("kind", "category"),
    [
        ("episodic", "interaction"),
        ("episodic", "outcome"),
        ("semantic", "preference"),
        ("semantic", "reference"),
        ("semantic", "fact"),
        ("semantic", "procedure"),
    ],
)
def test_memory_record_accepts_valid_kind_category_pairs(
    kind: str,
    category: str,
) -> None:
    record = MemoryRecord(
        handle="mem_1",
        session_id="session_1",
        kind=kind,  # type: ignore[arg-type]
        category=category,  # type: ignore[arg-type]
        content="Remember this.",
    )

    assert record.kind == kind
    assert record.category == category


@pytest.mark.parametrize(
    ("kind", "category"),
    [
        ("episodic", "fact"),
        ("semantic", "interaction"),
        ("working", "fact"),
    ],
)
def test_memory_record_rejects_invalid_kind_category_pairs(
    kind: str,
    category: str,
) -> None:
    with pytest.raises(ValueError, match="kind/category"):
        MemoryRecord(
            handle="mem_1",
            session_id="session_1",
            kind=kind,  # type: ignore[arg-type]
            category=category,  # type: ignore[arg-type]
            content="Remember this.",
        )


@pytest.mark.parametrize("field_name", ["handle", "session_id", "content"])
def test_memory_record_rejects_empty_identity_and_content(field_name: str) -> None:
    values = {
        "handle": "mem_1",
        "session_id": "session_1",
        "kind": "semantic",
        "category": "fact",
        "content": "Remember this.",
    }
    values[field_name] = "  "

    with pytest.raises(ValueError, match=field_name):
        MemoryRecord(**values)  # type: ignore[arg-type]


def test_memory_record_freezes_artifact_handles_without_artifact_payload() -> None:
    handles = ["art_1", "art_2"]
    record = MemoryRecord(
        handle="mem_1",
        session_id="session_1",
        kind="semantic",
        category="reference",
        content="The report is available as an artifact.",
        artifact_handles=handles,
    )
    handles.append("art_3")

    assert record.artifact_handles == ("art_1", "art_2")
    assert {field.name for field in fields(MemoryRecord)}.isdisjoint(
        {"artifact_content", "artifact_data", "artifact_base64"},
    )


def test_memory_record_rejects_naive_expiry() -> None:
    with pytest.raises(ValueError, match="expires_at must be timezone-aware"):
        MemoryRecord(
            handle="mem_1",
            session_id="session_1",
            kind="episodic",
            category="outcome",
            content="Task completed.",
            expires_at=datetime(2026, 7, 15, 12, 0),
        )


@pytest.mark.parametrize("artifact_handles", ["art_1", [""], ["art_1", "art_1"]])
def test_memory_record_rejects_invalid_artifact_handle_collections(
    artifact_handles: object,
) -> None:
    with pytest.raises(ValueError, match="artifact_handles"):
        MemoryRecord(
            handle="mem_1",
            session_id="session_1",
            kind="semantic",
            category="reference",
            content="Artifact reference.",
            artifact_handles=artifact_handles,  # type: ignore[arg-type]
        )


def test_memory_selection_context_defensively_freezes_permissions() -> None:
    permissions = {"memory:read", "artifact:read"}
    context = MemorySelectionContext(
        session_id="session_1",
        principal_id="user_1",
        permissions=permissions,
        query="project preference",
        now=datetime(2026, 7, 15, 12, 0, tzinfo=UTC),
    )
    permissions.add("admin")

    assert context.permissions == frozenset({"memory:read", "artifact:read"})


def test_memory_selection_context_requires_explicit_scope_and_time() -> None:
    with pytest.raises(ValueError, match="session_id"):
        MemorySelectionContext(
            session_id="",
            principal_id="user_1",
            permissions=(),
            query="",
            now=datetime(2026, 7, 15, 12, 0, tzinfo=UTC),
        )

    with pytest.raises(ValueError, match="principal_id"):
        MemorySelectionContext(
            session_id="session_1",
            principal_id="",
            permissions=(),
            query="",
            now=datetime(2026, 7, 15, 12, 0, tzinfo=UTC),
        )

    with pytest.raises(ValueError, match="now must be timezone-aware"):
        MemorySelectionContext(
            session_id="session_1",
            principal_id="user_1",
            permissions=(),
            query="",
            now=datetime(2026, 7, 15, 12, 0),
        )


def test_memory_selection_context_rejects_string_permissions() -> None:
    with pytest.raises(ValueError, match="permissions must be a collection"):
        MemorySelectionContext(
            session_id="session_1",
            principal_id="user_1",
            permissions="memory:read",
            query="",
            now=datetime(2026, 7, 15, 12, 0, tzinfo=UTC),
        )


@pytest.mark.parametrize("score", [-0.1, 1.1, float("inf"), float("nan"), True])
def test_memory_candidate_rejects_invalid_scores(score: object) -> None:
    with pytest.raises(ValueError, match="score"):
        MemoryCandidate(
            record=MemoryRecord(
                handle="mem_1",
                session_id="session_1",
                kind="semantic",
                category="fact",
                content="Validated fact.",
            ),
            score=score,  # type: ignore[arg-type]
        )
