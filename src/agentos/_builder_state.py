from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentos.artifacts import ArtifactRuntime
    from agentos.context import ContextRuntime
    from agentos.runtime.run_runtime import RunRuntime
    from agentos.runtime.session import SessionState


@dataclass(frozen=True, slots=True)
class RuntimeStateComponents:
    context: ContextRuntime
    artifacts: ArtifactRuntime
    runs: RunRuntime
    session: SessionState


__all__ = ["RuntimeStateComponents"]
