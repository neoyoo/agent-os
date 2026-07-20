from __future__ import annotations

from typing import Protocol

from agentos.capabilities.invocation import ToolInvocation
from agentos.capabilities.result_refs import ToolResultRef


class ToolResultRefProjector(Protocol):
    """Persist raw Tool output and return its durable result reference."""

    async def project(
        self,
        invocation: ToolInvocation,
        content: str,
    ) -> ToolResultRef: ...


__all__ = ["ToolResultRefProjector"]
