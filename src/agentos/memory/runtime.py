from __future__ import annotations

import math
from dataclasses import dataclass, field

from agentos.context.models import ContextSlotProjection
from agentos.memory.access import MemoryAccessPolicy
from agentos.memory.memory_store import MemoryStore
from agentos.memory.projection import project_memory_context
from agentos.memory.records import (
    MemoryCandidate,
    MemoryRecord,
    MemorySelectionContext,
)


class MemoryRuntime:
    """选择并投影 Session 范围的 Episodic/Semantic Memory。"""

    def __init__(
        self,
        store: MemoryStore,
        access_policy: MemoryAccessPolicy,
        top_k: int,
        candidate_limit: int,
        min_score: float = 0.0,
    ) -> None:
        if type(top_k) is not int or top_k <= 0:
            raise ValueError("top_k must be a positive integer")
        if type(candidate_limit) is not int or candidate_limit <= 0:
            raise ValueError("candidate_limit must be a positive integer")
        if candidate_limit < top_k:
            raise ValueError("candidate_limit must be at least top_k")
        if (
            isinstance(min_score, bool)
            or not isinstance(min_score, (int, float))
            or not math.isfinite(min_score)
            or not 0.0 <= min_score <= 1.0
        ):
            raise ValueError("min_score must be a number between 0 and 1")
        self._store = store
        self._access_policy = access_policy
        self._top_k = top_k
        self._candidate_limit = candidate_limit
        self._min_score = float(min_score)

    async def projections(
        self,
        context: MemorySelectionContext,
    ) -> tuple[ContextSlotProjection, ...]:
        """按本次显式请求上下文重新选择并投影 Memory。"""

        if not isinstance(context, MemorySelectionContext):
            raise TypeError("context must be a MemorySelectionContext")
        candidates = await self._store.search(context, self._candidate_limit)
        selected = self._select(candidates, context)
        return project_memory_context(selected)

    def _select(
        self,
        candidates: tuple[MemoryCandidate, ...],
        context: MemorySelectionContext,
    ) -> tuple[MemoryRecord, ...]:
        eligible: list[MemoryCandidate] = []
        for candidate in candidates:
            if not isinstance(candidate, MemoryCandidate):
                raise TypeError("memory search must return MemoryCandidate values")
            record = candidate.record
            if not isinstance(record, MemoryRecord):
                raise TypeError("memory candidate record must be a MemoryRecord")
            if record.session_id != context.session_id:
                continue
            allowed = self._access_policy.allows(record, context)
            if type(allowed) is not bool:
                raise TypeError("memory access policy must return a boolean")
            if not allowed:
                continue
            if record.expires_at is not None and record.expires_at <= context.now:
                continue
            if not _valid_score(candidate.score):
                raise ValueError("memory candidate score is invalid")
            if candidate.score < self._min_score:
                continue
            eligible.append(candidate)

        eligible.sort(key=lambda item: (-item.score, item.record.handle))
        selected: list[MemoryRecord] = []
        seen_handles: set[str] = set()
        for candidate in eligible:
            if candidate.record.handle in seen_handles:
                continue
            seen_handles.add(candidate.record.handle)
            selected.append(candidate.record)
            if len(selected) == self._top_k:
                break
        return tuple(selected)


def _valid_score(score: object) -> bool:
    return (
        not isinstance(score, bool)
        and isinstance(score, (int, float))
        and math.isfinite(score)
        and 0.0 <= score <= 1.0
    )


@dataclass(frozen=True, slots=True)
class BoundMemoryProjectionProvider:
    """把无参 Context Projection Port 绑定到一次 Memory 请求。"""

    runtime: MemoryRuntime
    context: MemorySelectionContext
    _cache: tuple[ContextSlotProjection, ...] | None = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )

    async def prepare_projection_cache(self) -> None:
        """异步选择当前请求的 Memory，并原子发布不可变投影。"""

        projection = await self.runtime.projections(self.context)
        object.__setattr__(self, "_cache", projection)

    def projections(self) -> tuple[ContextSlotProjection, ...]:
        """使用构造时冻结的选择上下文生成投影。"""

        if self._cache is None:
            raise RuntimeError("memory projection cache is not prepared")
        return self._cache
