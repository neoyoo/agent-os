from typing import Protocol

from agentos.memory.records import (
    MemoryCandidate,
    MemoryRecord,
    MemorySelectionContext,
)


class MemoryStore(Protocol):
    """Episodic/Semantic Memory 的真值存储边界。"""

    async def put(self, record: MemoryRecord) -> None: ...

    async def get(self, handle: str) -> MemoryRecord: ...

    async def search(
        self,
        context: MemorySelectionContext,
        candidate_limit: int,
    ) -> tuple[MemoryCandidate, ...]: ...
