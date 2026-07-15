from __future__ import annotations

import re
from dataclasses import dataclass, field

from agentos.memory.records import (
    MemoryCandidate,
    MemoryRecord,
    MemorySelectionContext,
)


@dataclass(slots=True)
class InMemoryMemoryStore:
    """Level 1 deterministic MemoryStore adapter."""

    _records: dict[str, MemoryRecord] = field(default_factory=dict)

    def put(self, record: MemoryRecord) -> None:
        if not isinstance(record, MemoryRecord):
            raise TypeError("record must be a MemoryRecord")
        self._records[record.handle] = record

    def get(self, handle: str) -> MemoryRecord:
        try:
            return self._records[handle]
        except KeyError as error:
            raise KeyError(handle) from error

    def search(
        self,
        context: MemorySelectionContext,
        candidate_limit: int,
    ) -> tuple[MemoryCandidate, ...]:
        if candidate_limit <= 0:
            return ()

        query_tokens = self._tokens(context.query)
        candidates: list[MemoryCandidate] = []
        for record in self._records.values():
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
