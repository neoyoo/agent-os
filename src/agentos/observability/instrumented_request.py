from __future__ import annotations

from agentos.observability.attributes import (
    apply_common_observability_attributes,
    metadata_identity_payload,
)
from agentos.observability.config import CapturePolicy, json_attribute
from agentos.observability.conventions import (
    LANGFUSE_OBSERVATION_INPUT,
    LANGFUSE_OBSERVATION_TYPE,
)
from agentos.observability.snapshots import (
    ProviderRequestSnapshot,
    build_provider_request_snapshot,
)
from agentos.observability.tracer import Tracer
from agentos.runtime import ProviderRequestBuilder
from agentos.runtime.provider_request_builder import (
    ProviderInputProjectionProvider,
    ProviderRequestBuild,
)


class InstrumentedProviderRequestBuilder:
    """在 provider request build boundary 上创建 span。"""

    def __init__(
        self,
        inner: ProviderRequestBuilder,
        *,
        tracer: Tracer,
        capture_policy: CapturePolicy,
    ) -> None:
        """保存被包装 builder 和观测配置。"""

        self._inner = inner
        self._tracer = tracer
        self._capture_policy = capture_policy
        self.latest_request_snapshot: ProviderRequestSnapshot | None = None

    @property
    def input_projections(self) -> tuple[ProviderInputProjectionProvider, ...]:
        return self._inner.input_projections

    @input_projections.setter
    def input_projections(
        self,
        value: tuple[ProviderInputProjectionProvider, ...],
    ) -> None:
        self._inner.input_projections = value

    def _bind_context_source(self, context_runtime: object, token_counter: object) -> None:
        self._inner._bind_context_source(context_runtime, token_counter)  # type: ignore[arg-type]

    async def prepare_projection_cache(self) -> None:
        """在同步 request build 前准备被包装 builder 的投影缓存。"""

        await self._inner.prepare_projection_cache()

    def build(self) -> ProviderRequestBuild:
        """构造 provider request，并记录 provider.request.build span。"""

        with self._tracer.start_span(
            "provider.request.build",
            attributes={LANGFUSE_OBSERVATION_TYPE: "span"},
        ) as span:
            apply_common_observability_attributes(
                span,
                tracer=self._tracer,
                capture_policy=self._capture_policy,
            )
            build = self._inner.build()
            snapshot = build_provider_request_snapshot(
                build.request,
                self._capture_policy,
            )
            self.latest_request_snapshot = snapshot
            span.set_attributes(
                {
                    "agentos.provider_request.system.length": snapshot.system_length,
                    "agentos.provider_request.messages.count": snapshot.message_count,
                    "agentos.provider_request.tools.count": snapshot.tool_count,
                    "agentos.provider_request.system.sha256": snapshot.system_sha256,
                    "agentos.provider_request.messages.sha256": snapshot.messages_sha256,
                    "agentos.provider_request.tools.sha256": snapshot.tools_sha256,
                },
            )
            span.set_attribute(
                LANGFUSE_OBSERVATION_INPUT,
                json_attribute(
                    self._request_payload(snapshot),
                    policy=self._capture_policy,
                ),
            )
            return build

    def _request_payload(self, snapshot: ProviderRequestSnapshot) -> dict[str, object]:
        """返回 request build span input payload。"""

        if self._capture_policy.mode == "metadata":
            return {
                **metadata_identity_payload(capture_policy=self._capture_policy),
                "system_chars": snapshot.system_length,
                "message_count": snapshot.message_count,
                "tool_count": snapshot.tool_count,
            }
        return {
            "system": snapshot.system,
            "messages": snapshot.messages,
            "tools": snapshot.tools,
        }


__all__ = ["InstrumentedProviderRequestBuilder"]
