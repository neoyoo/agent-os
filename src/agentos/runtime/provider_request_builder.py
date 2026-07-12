from dataclasses import dataclass, field
from typing import Protocol

from agentos.context import ContextRuntime, ContextSnapshotRenderer, SystemEnvelope
from agentos.context.models import ContextSlotProjection
from agentos.messages import MessageRuntime
from agentos.providers import ProviderInputItem, ProviderRequest, ProviderToolSpec
from agentos.runtime.message_projection import project_stored_message


class SystemEnvelopeRenderer(Protocol):
    """渲染可信 SystemEnvelope 的结构化端口。"""

    def render(self) -> SystemEnvelope:
        """返回当前请求使用的可信 SystemEnvelope。"""


class ContextProjectionProvider(Protocol):
    """提供一次请求使用的 Context Slot 投影快照。"""

    def projections(self) -> tuple[ContextSlotProjection, ...]:
        """返回当前权威状态生成的不可变投影。"""


@dataclass(frozen=True, slots=True)
class ProviderRequestReceipt:
    """记录一次请求实际投影的临时消息。"""

    temporary_message_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """复制临时消息标识，避免保留调用方的可变别名。"""

        object.__setattr__(
            self,
            "temporary_message_ids",
            tuple(self.temporary_message_ids),
        )


@dataclass(frozen=True, slots=True)
class ProviderRequestBuild:
    """一次不可变 ProviderRequest 及其精确回执。"""

    request: ProviderRequest
    receipt: ProviderRequestReceipt


class ProviderRequestFactory(Protocol):
    """每次调用都重新构建 ProviderRequest 的工厂。"""

    def __call__(self) -> ProviderRequestBuild:
        """返回一次全新的请求构建结果。"""


@dataclass(slots=True)
class ProviderRequestBuilder:
    """把 context、active messages 和工具 schema 组装为 ProviderRequest。"""

    context_renderer: SystemEnvelopeRenderer
    message_runtime: MessageRuntime
    tools: list[ProviderToolSpec] = field(default_factory=list)
    attachment_runtime: object | None = None
    snapshot_renderer: ContextSnapshotRenderer | None = None
    context_projections: ContextProjectionProvider | None = None

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

    def build_with_receipt(self) -> ProviderRequestBuild:
        """从当前权威状态重新组装双平面请求及临时消息回执。"""

        if self.snapshot_renderer is None or self.context_projections is None:
            raise RuntimeError(
                "build_with_receipt requires snapshot renderer and context projections",
            )
        envelope = self.context_renderer.render()
        snapshot = self.snapshot_renderer.render(
            self.context_projections.projections(),
        )
        active_snapshot = self.message_runtime.snapshot_active_with_refs()
        messages = (
            ProviderInputItem.context_snapshot(snapshot.xml),
            *(
                project_stored_message(ref, message)
                for ref, message in active_snapshot
            ),
        )
        if self.attachment_runtime is not None:
            project_inputs = getattr(
                self.attachment_runtime,
                "_project_provider_inputs_compat",
                None,
            )
            if not callable(project_inputs):
                raise RuntimeError(
                    "attachment_runtime must define _project_provider_inputs_compat()",
                )
            messages = project_inputs(messages)
        return ProviderRequestBuild(
            request=ProviderRequest(
                system=envelope.text,
                messages=messages,
                tools=tuple(self.tools),
            ),
            receipt=ProviderRequestReceipt(
                temporary_message_ids=tuple(
                    ref.message_id for ref, _ in active_snapshot if ref.temporary
                ),
            ),
        )
