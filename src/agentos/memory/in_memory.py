from __future__ import annotations

import re
from dataclasses import dataclass, field
from threading import RLock

from agentos.memory.records import (
    MemoryCandidate,
    MemoryRecord,
    MemorySelectionContext,
)


@dataclass(slots=True)
class InMemoryMemoryStore:
    """Level 1 deterministic MemoryStore adapter."""

    _records: dict[str, MemoryRecord] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    _lock: RLock = field(default_factory=RLock, init=False, repr=False)

    async def put(self, record: MemoryRecord) -> None:
        if not isinstance(record, MemoryRecord):
            raise TypeError("record must be a MemoryRecord")
        with self._lock:
            existing = self._records.get(record.handle)
            if existing is not None and existing.session_id != record.session_id:
                raise ValueError("memory handle already belongs to another session")
            self._records[record.handle] = record

    async def get(self, handle: str) -> MemoryRecord:
        if not isinstance(handle, str) or not handle.strip():
            raise ValueError("handle must be a non-empty string")
        with self._lock:
            try:
                return self._records[handle]
            except KeyError as error:
                raise KeyError(handle) from error

    async def search(
        self,
        context: MemorySelectionContext,
        candidate_limit: int,
    ) -> tuple[MemoryCandidate, ...]:
        if not isinstance(context, MemorySelectionContext):
            raise TypeError("context must be a MemorySelectionContext")
        if type(candidate_limit) is not int or candidate_limit < 0:
            raise ValueError("candidate_limit must be a non-negative integer")
        if candidate_limit == 0:
            return ()

        query_tokens = self._tokens(context.query)
        candidates: list[MemoryCandidate] = []
        with self._lock:
            records = tuple(self._records.values())
        for record in records:
            if record.session_id != context.session_id:
                continue
            score, reason = self._score(record, query_tokens)
            if query_tokens and score == 0.0:
                continue
            candidates.append(
                MemoryCandidate(record=record, score=score, reason=reason),
            )

        candidates.sort(
            key=lambda candidate: (-candidate.score, candidate.record.handle),
        )
        return tuple(candidates[:candidate_limit])

    def _score(
        self,
        record: MemoryRecord,
        query_tokens: set[str],
    ) -> tuple[float, str]:
        if not query_tokens:
            return 0.0, "empty query"
        record_tokens = self._tokens(
            " ".join((record.handle, record.category, record.content)),
        )
        overlap = query_tokens & record_tokens
        if not overlap:
            return 0.0, "no lexical overlap"
        return (
            len(overlap) / len(query_tokens),
            "lexical overlap: " + ", ".join(sorted(overlap)),
        )

    def _tokens(self, value: str) -> set[str]:
        return {token.casefold() for token in re.findall(r"\w+", value)}
