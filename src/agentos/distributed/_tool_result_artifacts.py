from __future__ import annotations

from dataclasses import dataclass

from agentos.artifacts import ArtifactRef
from agentos.capabilities.invocation import ToolInvocation
from agentos.capabilities.result_refs import (
    ArtifactToolResultRef,
    InlineToolResultRef,
    ToolResultRef,
)
from agentos.distributed.models import RequestScope
from agentos.distributed.protocols import DistributedArtifactPort
from agentos.policies import ToolResultBudget
from agentos.policies.tool_result_budget import cap_tool_result_content
from agentos.tokens import TokenCounter


@dataclass(frozen=True, slots=True)
class DistributedToolResultRefProjector:
    """Store oversized raw Tool results in the claim's shared ArtifactStore."""

    scope: RequestScope
    session_id: str
    artifacts: DistributedArtifactPort
    budget: ToolResultBudget
    token_counter: TokenCounter

    async def project(
        self,
        invocation: ToolInvocation,
        content: str,
    ) -> ToolResultRef:
        if type(invocation) is not ToolInvocation:
            raise TypeError("tool result projection requires ToolInvocation")
        if type(content) is not str:
            raise TypeError("tool result content must be str")
        context = invocation.context
        if (
            context.tenant_id != self.scope.tenant_id
            or context.session_id != self.session_id
        ):
            raise ValueError("tool result projection scope mismatch")
        capped = cap_tool_result_content(
            tool_name=invocation.tool_name,
            content=content,
            budget=self.budget,
            token_counter=self.token_counter,
        )
        if not capped.capped:
            return InlineToolResultRef(content)
        record = await self.artifacts.upload(
            scope=self.scope,
            session_id=self.session_id,
            upload_id=(
                f"tool-result:{context.operation_id}:{context.attempt}"
            ),
            data=content.encode("utf-8"),
            filename="tool-result.txt",
            media_type="text/plain",
        )
        if record.session_id != self.session_id:
            raise RuntimeError("artifact port returned another session artifact")
        return ArtifactToolResultRef(
            ArtifactRef(record.id, record.filename, record.media_type),
            capped.content,
        )


__all__ = ["DistributedToolResultRefProjector"]
