from __future__ import annotations

from typing import Protocol

from agentos.distributed.internal_models import (
    InternalRunInputReceipt,
    InternalRunSubmission,
    InternalSubmissionAuthority,
)
from agentos.distributed.models import RequestScope, RunSubmissionReceipt
from agentos.runtime.durable_commands import DurableCommandReceipt, DurableRunCommand


class InternalRunSubmissionPort(Protocol):
    """TeamDelivery authority 与 Run 创建的单事务内部边界。"""

    async def submit_internal(
        self,
        *,
        scope: RequestScope,
        submission: InternalRunSubmission,
        authority: InternalSubmissionAuthority,
    ) -> RunSubmissionReceipt: ...

    async def submit_wakeup(
        self,
        *,
        scope: RequestScope,
        session_id: str,
        command: DurableRunCommand,
        authority: InternalSubmissionAuthority,
    ) -> DurableCommandReceipt: ...

    async def get_applied_input(
        self,
        *,
        scope: RequestScope,
        authority: InternalSubmissionAuthority,
    ) -> InternalRunInputReceipt | None: ...


__all__ = ["InternalRunSubmissionPort"]
