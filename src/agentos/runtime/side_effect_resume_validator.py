from __future__ import annotations

from typing import Protocol

from agentos.runtime.run_runtime import RunWriteGuard
from agentos.runtime.errors import RunProtocolError
from agentos.runtime.execution import AcceptedTurnPreparation
from agentos.runtime.side_effect_resume import SideEffectResume


class SideEffectResumeValidator(Protocol):
    """校验分布式 reconciliation source 与当前 fence 的控制边界。"""

    async def validate(
        self,
        *,
        resume: SideEffectResume,
        guard: RunWriteGuard,
    ) -> None: ...


def require_side_effect_resume_validator(
    preparation: AcceptedTurnPreparation,
    validator: SideEffectResumeValidator | None,
) -> None:
    """在任何 execution 写入前拒绝缺失分布式 resume 校验边界。"""

    if type(preparation) is SideEffectResume and validator is None:
        raise RunProtocolError("side effect resume validator is required")


__all__ = [
    "SideEffectResumeValidator",
    "require_side_effect_resume_validator",
]
