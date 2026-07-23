from __future__ import annotations

from typing import Protocol

from agentos.distributed.errors import SideEffectResolutionPermissionError
from agentos.distributed.models import RequestScope
from agentos.runtime.side_effect_types import SideEffectResolution


class SideEffectResolutionAuthorizer(Protocol):
    """授权一个主体对指定 Run 提交副作用 reconciliation 决议。"""

    async def authorize(
        self,
        *,
        scope: RequestScope,
        session_id: str,
        run_id: str,
        resolution: SideEffectResolution,
    ) -> None: ...


class DenySideEffectResolutionAuthorizer:
    """未配置部署授权策略时拒绝所有副作用 reconciliation。"""

    async def authorize(
        self,
        *,
        scope: RequestScope,
        session_id: str,
        run_id: str,
        resolution: SideEffectResolution,
    ) -> None:
        del scope, session_id, run_id, resolution
        raise SideEffectResolutionPermissionError()


__all__ = [
    "DenySideEffectResolutionAuthorizer",
    "SideEffectResolutionAuthorizer",
]
