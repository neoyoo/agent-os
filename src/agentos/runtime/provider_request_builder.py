from dataclasses import dataclass, field
from typing import Protocol

from agentos.context import ContextRenderer, ContextState
from agentos.messages import MessageRuntime
from agentos.providers import ProviderRequest, ProviderToolSpec


class ContextSnapshotProvider(Protocol):
    """ProviderRequestBuilder 依赖的 context snapshot 边界。"""

    def snapshot(self) -> ContextState:
        """返回可渲染的 context snapshot。"""


@dataclass(slots=True)
class ProviderRequestBuilder:
    """把 context、active messages 和工具 schema 组装为 ProviderRequest。"""

    context_renderer: ContextRenderer
    message_runtime: MessageRuntime
    tools: list[ProviderToolSpec] = field(default_factory=list)
    attachment_runtime: object | None = None

    def build(self, context_runtime: ContextSnapshotProvider) -> ProviderRequest:
        """构造 provider 请求，不暴露 context 内部对象。"""

        context_state = context_runtime.snapshot()
        messages = self.message_runtime.materialize_provider_messages()
        if self.attachment_runtime is not None:
            project_provider_messages = getattr(
                self.attachment_runtime,
                "project_provider_messages",
                None,
            )
            if not callable(project_provider_messages):
                raise RuntimeError(
                    "attachment_runtime must define project_provider_messages()",
                )
            messages = project_provider_messages(messages)
        return ProviderRequest(
            system=self.context_renderer.render(context_state),
            messages=messages,
            tools=list(self.tools),
        )
