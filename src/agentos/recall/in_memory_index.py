from __future__ import annotations

import re
from dataclasses import dataclass, field

from agentos.recall.types import RecallCandidate, SegmentRecallDocument


@dataclass(slots=True)
class InMemoryRecallIndex:
    """测试和 local profile 使用的词法 recall index。"""

    _documents: dict[str, dict[str, SegmentRecallDocument]] = field(
        default_factory=dict,
    )

    def index_segment(self, document: SegmentRecallDocument) -> None:
        self._documents.setdefault(document.session_id, {})[
            document.segment_id
        ] = document

    def search_segments(
        self,
        session_id: str,
        query: str,
        limit: int,
    ) -> tuple[RecallCandidate, ...]:
        if limit <= 0:
            return ()
        query_tokens = self._tokens(query)
        if not query_tokens:
            return ()

        candidates: list[RecallCandidate] = []
        for document in self._documents.get(session_id, {}).values():
            overlap = query_tokens & self._tokens(document.to_text())
            if not overlap:
                continue
            candidates.append(
                RecallCandidate(
                    session_id=session_id,
                    segment_id=document.segment_id,
                    score=len(overlap) / len(query_tokens),
                    reason="lexical overlap: " + ", ".join(sorted(overlap)),
                ),
            )

        candidates.sort(
            key=lambda candidate: (
                -(candidate.score or 0.0),
                candidate.segment_id,
            ),
        )
        return tuple(candidates[:limit])

    def delete_session(self, session_id: str) -> None:
        self._documents.pop(session_id, None)

    def _tokens(self, text: str) -> set[str]:
        tokens: set[str] = set()
        for token in re.findall(r"[A-Za-z0-9_./:-]+", text):
            lowered = token.lower()
            tokens.add(lowered)
            tokens.update(
                part for part in re.split(r"[._/:-]+", lowered) if part
            )
        return tokens
