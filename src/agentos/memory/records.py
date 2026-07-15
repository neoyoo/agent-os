from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal


MemoryKind = Literal["episodic", "semantic"]
SemanticCategory = Literal["preference", "reference", "fact", "procedure"]
EpisodicCategory = Literal["interaction", "outcome"]
MemoryCategory = SemanticCategory | EpisodicCategory

_SEMANTIC_CATEGORIES = frozenset({"preference", "reference", "fact", "procedure"})
_EPISODIC_CATEGORIES = frozenset({"interaction", "outcome"})


def _require_nonempty_text(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _require_aware_datetime(name: str, value: object) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    """Session-scoped episodic or semantic memory truth value."""

    handle: str
    session_id: str
    kind: MemoryKind
    category: MemoryCategory
    content: str
    artifact_handles: tuple[str, ...] = ()
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_nonempty_text("handle", self.handle)
        _require_nonempty_text("session_id", self.session_id)
        _require_nonempty_text("content", self.content)

        valid_categories = (
            _EPISODIC_CATEGORIES
            if self.kind == "episodic"
            else _SEMANTIC_CATEGORIES
            if self.kind == "semantic"
            else frozenset()
        )
        if self.category not in valid_categories:
            raise ValueError("invalid memory kind/category combination")

        if isinstance(self.artifact_handles, str):
            raise ValueError("artifact_handles must be a collection of handles")
        handles = tuple(self.artifact_handles)
        for handle in handles:
            _require_nonempty_text("artifact_handles", handle)
        if len(handles) != len(set(handles)):
            raise ValueError("artifact_handles must not contain duplicates")
        object.__setattr__(self, "artifact_handles", handles)

        if self.expires_at is not None:
            _require_aware_datetime("expires_at", self.expires_at)


@dataclass(frozen=True, slots=True, init=False)
class MemorySelectionContext:
    """Explicit request scope used for memory search and access checks."""

    session_id: str
    principal_id: str
    permissions: frozenset[str]
    query: str
    now: datetime

    def __init__(
        self,
        session_id: str,
        principal_id: str,
        permissions: Iterable[str],
        query: str,
        now: datetime,
    ) -> None:
        _require_nonempty_text("session_id", session_id)
        _require_nonempty_text("principal_id", principal_id)
        if not isinstance(query, str):
            raise ValueError("query must be a string")
        if isinstance(permissions, str):
            raise ValueError("permissions must be a collection of strings")
        frozen_permissions = frozenset(permissions)
        for permission in frozen_permissions:
            _require_nonempty_text("permissions", permission)
        _require_aware_datetime("now", now)

        object.__setattr__(self, "session_id", session_id)
        object.__setattr__(self, "principal_id", principal_id)
        object.__setattr__(self, "permissions", frozen_permissions)
        object.__setattr__(self, "query", query)
        object.__setattr__(self, "now", now)


@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    """MemoryStore search result with internal ranking evidence."""

    record: MemoryRecord
    score: float
    reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.record, MemoryRecord):
            raise TypeError("record must be a MemoryRecord")
        if isinstance(self.score, bool) or not isinstance(self.score, (int, float)):
            raise ValueError("score must be a number between 0 and 1")
        score = float(self.score)
        if not math.isfinite(score) or not 0.0 <= score <= 1.0:
            raise ValueError("score must be a number between 0 and 1")
        if self.reason is not None:
            _require_nonempty_text("reason", self.reason)
        object.__setattr__(self, "score", score)
