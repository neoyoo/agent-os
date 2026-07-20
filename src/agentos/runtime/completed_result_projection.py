from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agentos.capabilities.invocation import ToolInvocation
from agentos.context.runtime import ContextRuntime
from agentos.context.tool_mutations import (
    CONTEXT_MUTATION_TOOL_NAMES,
    apply_context_mutation,
)


COMPLETED_RESULT_PROJECTION_TOOL_NAMES = (
    CONTEXT_MUTATION_TOOL_NAMES | {"load_attachment"}
)


class _ArtifactReplayRuntime(Protocol):
    async def _restore_tool_result_mount(self, handle: str) -> None: ...


@dataclass(frozen=True, slots=True)
class CompletedResultProjector:
    """Rebuild closed SDK-owned ephemeral projections from completed results."""

    context_runtime: ContextRuntime | None = None
    artifact_runtime: _ArtifactReplayRuntime | None = None

    async def project(self, invocation: ToolInvocation) -> None:
        if type(invocation) is not ToolInvocation:
            raise TypeError("completed result projection requires ToolInvocation")
        if invocation.tool_name in CONTEXT_MUTATION_TOOL_NAMES:
            if self.context_runtime is None:
                raise RuntimeError("context replay requires ContextRuntime")
            apply_context_mutation(self.context_runtime, invocation)
            return
        if invocation.tool_name != "load_attachment":
            return
        if self.artifact_runtime is None:
            raise RuntimeError("attachment replay requires ArtifactRuntime")
        handle = invocation.arguments.get("handle")
        if type(handle) is not str:
            raise ValueError("load_attachment replay requires artifact handle")
        await self.artifact_runtime._restore_tool_result_mount(handle)

    async def project_after_tools(self, invocation: ToolInvocation) -> None:
        """Restore only projections excluded from the committed checkpoint."""

        if type(invocation) is not ToolInvocation:
            raise TypeError("completed result projection requires ToolInvocation")
        if invocation.tool_name != "load_attachment":
            return
        if self.artifact_runtime is None:
            raise RuntimeError("attachment replay requires ArtifactRuntime")
        handle = invocation.arguments.get("handle")
        if type(handle) is not str:
            raise ValueError("load_attachment replay requires artifact handle")
        await self.artifact_runtime._restore_tool_result_mount(handle)


__all__ = [
    "COMPLETED_RESULT_PROJECTION_TOOL_NAMES",
    "CompletedResultProjector",
]
