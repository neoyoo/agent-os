from dataclasses import dataclass
from typing import cast

from agentos.artifacts.runtime import ArtifactRuntime
from agentos.artifacts.tools import (
    artifact_tool_specs,
    list_attachments,
    load_attachment,
)
from agentos.capabilities.invocation import ToolInvocation
from agentos.capabilities.tools import (
    RegisteredTool,
    SideEffectPolicy,
    ToolConcurrencyPolicy,
)


@dataclass(frozen=True, slots=True)
class ArtifactToolAdapter:
    """把 Session-scoped ArtifactRuntime 绑定为可注册工具。"""

    runtime: ArtifactRuntime

    def registered_tools(self) -> tuple[RegisteredTool, ...]:
        """返回唯一 schema owner 对应的 Artifact 工具。"""

        handlers = {
            "list_attachments": self._list_attachments,
            "load_attachment": self._load_attachment,
        }
        policies = {
            "list_attachments": SideEffectPolicy.PURE,
            "load_attachment": SideEffectPolicy.IDEMPOTENT,
        }
        return tuple(
            RegisteredTool(
                name=spec.function.name,
                description=spec.function.description,
                parameters=spec.function.parameters,
                handler=handlers[spec.function.name],
                side_effect_policy=policies[spec.function.name],
                concurrency_policy=ToolConcurrencyPolicy.EXCLUSIVE,
            )
            for spec in artifact_tool_specs()
        )

    async def _list_attachments(self, invocation: ToolInvocation) -> str:
        arguments = invocation.arguments
        return await list_attachments(
            self.runtime,
            cursor=cast(str | None, arguments.get("cursor")),
            limit=cast(int, arguments.get("limit", 20)),
        )

    async def _load_attachment(self, invocation: ToolInvocation) -> str:
        arguments = invocation.arguments
        return await load_attachment(
            self.runtime,
            handle=cast(str, arguments.get("handle")),
        )
