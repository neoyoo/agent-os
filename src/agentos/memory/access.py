from typing import Protocol

from agentos.memory.records import MemoryRecord, MemorySelectionContext


class MemoryAccessPolicy(Protocol):
    """决定请求主体是否可以读取一条 MemoryRecord。"""

    def allows(
        self,
        record: MemoryRecord,
        context: MemorySelectionContext,
    ) -> bool: ...
