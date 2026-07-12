from dataclasses import dataclass, field
from typing import Protocol

from agentos.context import ContextRuntime, SystemEnvelope
from agentos.messages import MessageRuntime
from agentos.providers import ProviderRequest, ProviderToolSpec


class SystemEnvelopeRenderer(Protocol):
    """渲染可信 SystemEnvelope 的结构化端口。"""

    def render(self) -> SystemEnvelope:
        """返回当前请求使用的可信 SystemEnvelope。"""


@dataclass(slots=True)
class ProviderRequestBuilder:
    """把 context、active messages 和工具 schema 组装为 ProviderRequest。"""

    context_renderer: SystemEnvelopeRenderer
    message_runtime: MessageRuntime
    tools: list[ProviderToolSpec] = field(default_factory=list)
    attachment_runtime: object | None = None

    def build(self, context_runtime: ContextRuntime) -> ProviderRequest:
        """构造请求；Phase 1 保留参数形状，Phase 2 再消费动态 context。"""

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
            system=self.context_renderer.render().text,
            messages=messages,
            tools=list(self.tools),
        )
