from __future__ import annotations

from agentos.distributed.postgres._database import PostgresPool
from agentos.distributed.postgres._reconciliation import (
    validate_reconciliation_resume,
)
from agentos.runtime.run_runtime import RunWriteGuard
from agentos.runtime.side_effect_resume import SideEffectResume


class PostgresSideEffectResumeValidator:
    """使用 PostgreSQL 权威 source、command、cursor 与 fence 校验 resume。"""

    def __init__(self, database: PostgresPool) -> None:
        self._database = database

    async def validate(
        self,
        *,
        resume: SideEffectResume,
        guard: RunWriteGuard,
    ) -> None:
        async with self._database.transaction() as connection:
            await validate_reconciliation_resume(
                connection,
                resume=resume,
                guard=guard,
            )


__all__ = ["PostgresSideEffectResumeValidator"]
