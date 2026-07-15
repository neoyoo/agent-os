from dataclasses import dataclass, field
from typing import Protocol

from agentos.context import ContextSnapshotRenderer, ContextState, SystemEnvelope
from agentos.context.models import ContextSlotProjection
from agentos.context.projection import project_context_state
from agentos.messages import MessageRuntime
from agentos.providers import ProviderInputItem, ProviderRequest, ProviderToolSpec
from agentos.runtime.message_projection import project_stored_message
from agentos.tokens import TokenCounter


class SystemEnvelopeRenderer(Protocol):
    """渲染可信 SystemEnvelope 的结构化端口。"""

    def render(self) -> SystemEnvelope:
        """返回当前请求使用的可信 SystemEnvelope。"""


class ContextProjectionProvider(Protocol):
    """提供一次请求使用的 Context Slot 投影快照。"""

    def projections(self) -> tuple[ContextSlotProjection, ...]:
        """返回当前权威状态生成的不可变投影。"""


class ContextStateSource(Protocol):
    """提供 Provider request 所需的权威 context 快照。"""

    def snapshot(self) -> ContextState:
        """返回当前不可变 ContextState 快照。"""


@dataclass(frozen=True, slots=True)
class _ContextRuntimeProjectionProvider:
    source: ContextStateSource

    def projections(self) -> tuple[ContextSlotProjection, ...]:
        return project_context_state(self.source.snapshot())


@dataclass(frozen=True, slots=True)
class ProviderRequestReceipt:
    """记录一次请求实际投影的临时消息。"""

    temporary_message_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """复制临时消息标识，避免保留调用方的可变别名。"""

        if isinstance(self.temporary_message_ids, (str, bytes)):
            raise TypeError("temporary message IDs must be a sequence of str")
        temporary_message_ids = tuple(self.temporary_message_ids)
        if any(type(message_id) is not str for message_id in temporary_message_ids):
            raise TypeError("temporary message IDs must contain str values")
        object.__setattr__(
            self,
            "temporary_message_ids",
            temporary_message_ids,
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
    parallel_tool_calls: bool | None = True
    attachment_runtime: object | None = None
    snapshot_renderer: ContextSnapshotRenderer | None = None
    context_projections: ContextProjectionProvider | None = None
    _bound_context_source: ContextStateSource | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _bound_token_counter: TokenCounter | None = field(
        default=None,
        init=False,
        repr=False,
    )

    def _bind_context_source(
        self,
        source: ContextStateSource,
        token_counter: TokenCounter,
    ) -> None:
        """Task 13 前为未接线 builder 绑定唯一的 context 投影来源。"""

        if self._bound_context_source is not None:
            if (
                self._bound_context_source is not source
                or self._bound_token_counter is not token_counter
            ):
                raise RuntimeError(
                    "provider request builder is already bound to another context source",
                )
            return
        if self.snapshot_renderer is not None and self.context_projections is not None:
            return
        if self.snapshot_renderer is not None or self.context_projections is not None:
            raise RuntimeError(
                "provider request builder context projection is partially configured",
            )
        self.snapshot_renderer = ContextSnapshotRenderer(token_counter)
        self.context_projections = _ContextRuntimeProjectionProvider(source)
        self._bound_context_source = source
        self._bound_token_counter = token_counter

    def build(self) -> ProviderRequestBuild:
        """从当前权威状态重新组装双平面请求及临时消息回执。"""

        if self.snapshot_renderer is None or self.context_projections is None:
            raise RuntimeError(
                "build requires snapshot renderer and context projections",
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
                parallel_tool_calls=self.parallel_tool_calls,
            ),
            receipt=ProviderRequestReceipt(
                temporary_message_ids=tuple(
                    ref.message_id for ref, _ in active_snapshot if ref.temporary
                ),
            ),
        )
