from __future__ import annotations

from dataclasses import dataclass

from agentos.attachments import AttachmentRuntime
from agentos.capabilities import RegisteredTool, ToolCallRouter, ToolRegistry
from agentos.context import ContextRuntime
from agentos.providers import ProviderToolSpec
from agentos.recall import RecallRuntime
from agentos.runtime.tool_scheduler import ToolCallScheduler


@dataclass(frozen=True, slots=True)
class BuilderToolComponents:
    """封装 AgentBuilder 组装出的工具运行时组件。"""

    router: ToolCallRouter
    provider_tools: list[ProviderToolSpec]
    scheduler: ToolCallScheduler


def assemble_tool_components(
    *,
    tools: list[RegisteredTool] | None,
    tool_call_router: ToolCallRouter | None,
    context_runtime: ContextRuntime,
    recall_runtime: RecallRuntime,
    attachment_runtime: AttachmentRuntime,
    max_parallel_calls: int | None,
) -> BuilderToolComponents:
    """组装工具注册、路由、Provider schema 和批次调度器。"""

    if tools is not None and tool_call_router is not None:
        raise ValueError(
            "AgentBuilder cannot use both .tools() and .tool_call_router(). "
            "Choose one tool setup.",
        )

    registry = ToolRegistry()
    for tool in tools or ():
        registry.register(tool)
    router = tool_call_router or ToolCallRouter(
        tool_registry=registry,
        context_runtime=context_runtime,
        recall_runtime=recall_runtime,
        attachment_runtime=attachment_runtime,
    )
    if router.attachment_runtime is None:
        router.attachment_runtime = attachment_runtime

    return BuilderToolComponents(
        router=router,
        provider_tools=router.tool_specs(),
        scheduler=ToolCallScheduler(
            8 if max_parallel_calls is None else max_parallel_calls,
        ),
    )
