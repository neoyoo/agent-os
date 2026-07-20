from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from dataclasses import asdict
from time import monotonic

from agentos._sync_work import run_sync
from agentos.runtime._async_bridge import iterate_sync_in_executor
from agentos.observability.attributes import (
    apply_common_observability_attributes,
    metadata_identity_payload,
)
from agentos.observability.config import CapturePolicy, json_attribute
from agentos.observability.conventions import (
    AGENTOS_STREAM_CONTENT_DELTA,
    AGENTOS_STREAM_THINKING_DELTA,
    AGENTOS_STREAM_TOOL_CALL_DELTA,
    GEN_AI_OPERATION_NAME,
    GEN_AI_PROVIDER_NAME,
    GEN_AI_REQUEST_MODEL,
    GEN_AI_REQUEST_STREAM,
    GEN_AI_RESPONSE_FINISH_REASONS,
    GEN_AI_RESPONSE_ID,
    GEN_AI_RESPONSE_MODEL,
    GEN_AI_RESPONSE_TIME_TO_FIRST_CHUNK,
    GEN_AI_USAGE_INPUT_TOKENS,
    GEN_AI_USAGE_OUTPUT_TOKENS,
    GEN_AI_USAGE_TOTAL_TOKENS,
    LANGFUSE_OBSERVATION_INPUT,
    LANGFUSE_OBSERVATION_MODEL_NAME,
    LANGFUSE_OBSERVATION_OUTPUT,
    LANGFUSE_OBSERVATION_TYPE,
    LANGFUSE_OBSERVATION_USAGE_DETAILS,
)
from agentos.observability.snapshots import (
    ProviderRequestSnapshot,
    ProviderResponseSnapshot,
    build_provider_request_snapshot,
    build_provider_response_snapshot,
)
from agentos.observability.instrumented_tools import (
    InstrumentedToolCallRouter as InstrumentedToolCallRouter,
)
from agentos.observability.instrumented_request import (
    InstrumentedProviderRequestBuilder as InstrumentedProviderRequestBuilder,
)
from agentos.observability.tracer import Tracer
from agentos.providers import (
    Provider,
    ProviderContentDelta,
    ProviderRequest,
    ProviderResponse,
    ProviderStreamCompleted,
    ProviderStreamEvent,
    ProviderStreamOptions,
    ProviderStreamStarted,
    ProviderThinkingDelta,
    ProviderToolCallDelta,
    ProviderUsage,
    complete_response_to_stream_events,
)


class InstrumentedProvider:
    """在 provider boundary 上创建 generation span。"""

    def __init__(
        self,
        inner: Provider,
        *,
        tracer: Tracer,
        capture_policy: CapturePolicy,
    ) -> None:
        """保存被包装 provider 和观测配置。"""

        self._inner = inner
        self._tracer = tracer
        self._capture_policy = capture_policy

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        """调用 provider，并记录 provider.complete span。"""

        request_snapshot = build_provider_request_snapshot(
            request,
            self._capture_policy,
        )
        with self._tracer.start_span(
            "provider.complete",
            attributes={
                LANGFUSE_OBSERVATION_TYPE: "generation",
                GEN_AI_OPERATION_NAME: "chat",
                "agentos.provider_request.system.length": request_snapshot.system_length,
                "agentos.provider_request.messages.count": request_snapshot.message_count,
                "agentos.provider_request.tools.count": request_snapshot.tool_count,
                "agentos.provider_request.system.sha256": request_snapshot.system_sha256,
                "agentos.provider_request.messages.sha256": request_snapshot.messages_sha256,
                "agentos.provider_request.tools.sha256": request_snapshot.tools_sha256,
            },
        ) as span:
            apply_common_observability_attributes(
                span,
                tracer=self._tracer,
                capture_policy=self._capture_policy,
            )
            span.set_attribute(
                LANGFUSE_OBSERVATION_INPUT,
                json_attribute(
                    self._provider_input_payload(request_snapshot),
                    policy=self._capture_policy,
                ),
            )
            response = self._inner.complete(request)
            response_snapshot = build_provider_response_snapshot(
                response,
                self._capture_policy,
            )
            provider_name = response_snapshot.provider_name or "unknown"
            model = response_snapshot.model or "unknown"
            span.set_attributes(
                {
                    GEN_AI_PROVIDER_NAME: provider_name,
                    GEN_AI_REQUEST_MODEL: model,
                    GEN_AI_RESPONSE_MODEL: model,
                    LANGFUSE_OBSERVATION_MODEL_NAME: model,
                    GEN_AI_RESPONSE_FINISH_REASONS: (
                        []
                        if response_snapshot.stop_reason is None
                        else [response_snapshot.stop_reason]
                    ),
                    "agentos.provider.tool_call_count": len(
                        response_snapshot.tool_calls,
                    ),
                },
            )
            if response_snapshot.response_id is not None:
                span.set_attribute(GEN_AI_RESPONSE_ID, response_snapshot.response_id)
                span.set_attribute(
                    "agentos.provider.response_id",
                    response_snapshot.response_id,
                )
            if response_snapshot.usage is not None:
                self._set_usage_attributes(span, response_snapshot.usage)
            span.set_attribute(
                LANGFUSE_OBSERVATION_OUTPUT,
                json_attribute(
                    self._provider_output_payload(response_snapshot),
                    policy=self._capture_policy,
                ),
            )
            return response

    def stream(
        self,
        request: ProviderRequest,
        options: ProviderStreamOptions | None = None,
    ) -> Iterator[ProviderStreamEvent]:
        """调用 provider stream，并记录完整 streaming 生命周期 span。"""

        request_snapshot = build_provider_request_snapshot(
            request,
            self._capture_policy,
        )
        started_at = monotonic()
        first_chunk_at: float | None = None
        content_delta_count = 0
        content_char_count = 0
        thinking_delta_count = 0
        thinking_char_count = 0
        tool_delta_count = 0

        with self._tracer.start_span(
            "provider.stream",
            attributes={
                LANGFUSE_OBSERVATION_TYPE: "generation",
                GEN_AI_OPERATION_NAME: "chat",
                GEN_AI_REQUEST_STREAM: True,
                "agentos.provider_request.system.length": request_snapshot.system_length,
                "agentos.provider_request.messages.count": request_snapshot.message_count,
                "agentos.provider_request.tools.count": request_snapshot.tool_count,
                "agentos.provider_request.system.sha256": request_snapshot.system_sha256,
                "agentos.provider_request.messages.sha256": request_snapshot.messages_sha256,
                "agentos.provider_request.tools.sha256": request_snapshot.tools_sha256,
            },
        ) as span:
            apply_common_observability_attributes(
                span,
                tracer=self._tracer,
                capture_policy=self._capture_policy,
            )
            span.set_attribute(
                LANGFUSE_OBSERVATION_INPUT,
                json_attribute(
                    self._provider_input_payload(request_snapshot),
                    policy=self._capture_policy,
                ),
            )
            try:
                for event in self._inner_stream(request, options):
                    if (
                        first_chunk_at is None
                        and not isinstance(
                            event,
                            (ProviderStreamStarted, ProviderStreamCompleted),
                        )
                    ):
                        first_chunk_at = monotonic()
                    if isinstance(event, ProviderContentDelta):
                        content_delta_count += 1
                        content_char_count += len(event.text)
                        self._record_stream_event(
                            span,
                            AGENTOS_STREAM_CONTENT_DELTA,
                            sequence=event.index,
                            char_count=len(event.text),
                            text=event.text,
                        )
                    elif isinstance(event, ProviderThinkingDelta):
                        thinking_delta_count += 1
                        thinking_char_count += len(event.text)
                        self._record_stream_event(
                            span,
                            AGENTOS_STREAM_THINKING_DELTA,
                            sequence=event.index,
                            char_count=len(event.text),
                            text=event.text,
                        )
                    elif isinstance(event, ProviderToolCallDelta):
                        tool_delta_count += 1
                        self._record_stream_event(
                            span,
                            AGENTOS_STREAM_TOOL_CALL_DELTA,
                            sequence=event.index,
                            char_count=len(event.arguments_delta or ""),
                            text=event.arguments_delta or "",
                        )
                    elif isinstance(event, ProviderStreamCompleted):
                        response_snapshot = build_provider_response_snapshot(
                            event.response,
                            self._capture_policy,
                        )
                        self._set_stream_response_attributes(
                            span,
                            response_snapshot,
                            event.stop_reason,
                            first_chunk_at,
                            started_at,
                            content_delta_count,
                            content_char_count,
                            thinking_delta_count,
                            thinking_char_count,
                            tool_delta_count,
                        )
                    yield event
            except Exception as error:
                span.set_status("error", str(error))
                span.set_attribute("agentos.stream.partial", True)
                span.set_attribute(
                    "agentos.stream.content.char_count",
                    content_char_count,
                )
                raise

    async def async_complete(self, request: ProviderRequest) -> ProviderResponse:
        """Call async provider complete and record a generation span."""

        request_snapshot = build_provider_request_snapshot(
            request,
            self._capture_policy,
        )
        with self._tracer.start_span(
            "provider.complete",
            attributes={
                LANGFUSE_OBSERVATION_TYPE: "generation",
                GEN_AI_OPERATION_NAME: "chat",
                "agentos.provider_request.system.length": request_snapshot.system_length,
                "agentos.provider_request.messages.count": request_snapshot.message_count,
                "agentos.provider_request.tools.count": request_snapshot.tool_count,
                "agentos.provider_request.system.sha256": request_snapshot.system_sha256,
                "agentos.provider_request.messages.sha256": request_snapshot.messages_sha256,
                "agentos.provider_request.tools.sha256": request_snapshot.tools_sha256,
            },
        ) as span:
            apply_common_observability_attributes(
                span,
                tracer=self._tracer,
                capture_policy=self._capture_policy,
            )
            span.set_attribute(
                LANGFUSE_OBSERVATION_INPUT,
                json_attribute(
                    self._provider_input_payload(request_snapshot),
                    policy=self._capture_policy,
                ),
            )
            response = await self._inner_async_complete(request)
            response_snapshot = build_provider_response_snapshot(
                response,
                self._capture_policy,
            )
            provider_name = response_snapshot.provider_name or "unknown"
            model = response_snapshot.model or "unknown"
            span.set_attributes(
                {
                    GEN_AI_PROVIDER_NAME: provider_name,
                    GEN_AI_REQUEST_MODEL: model,
                    GEN_AI_RESPONSE_MODEL: model,
                    LANGFUSE_OBSERVATION_MODEL_NAME: model,
                    GEN_AI_RESPONSE_FINISH_REASONS: (
                        []
                        if response_snapshot.stop_reason is None
                        else [response_snapshot.stop_reason]
                    ),
                    "agentos.provider.tool_call_count": len(
                        response_snapshot.tool_calls,
                    ),
                },
            )
            if response_snapshot.response_id is not None:
                span.set_attribute(GEN_AI_RESPONSE_ID, response_snapshot.response_id)
                span.set_attribute(
                    "agentos.provider.response_id",
                    response_snapshot.response_id,
                )
            if response_snapshot.usage is not None:
                self._set_usage_attributes(span, response_snapshot.usage)
            span.set_attribute(
                LANGFUSE_OBSERVATION_OUTPUT,
                json_attribute(
                    self._provider_output_payload(response_snapshot),
                    policy=self._capture_policy,
                ),
            )
            return response

    async def async_stream(
        self,
        request: ProviderRequest,
        options: ProviderStreamOptions | None = None,
    ) -> AsyncIterator[ProviderStreamEvent]:
        """Call async provider stream and record a generation span."""

        request_snapshot = build_provider_request_snapshot(
            request,
            self._capture_policy,
        )
        started_at = monotonic()
        first_chunk_at: float | None = None
        content_delta_count = 0
        content_char_count = 0
        thinking_delta_count = 0
        thinking_char_count = 0
        tool_delta_count = 0

        with self._tracer.start_span(
            "provider.stream",
            attributes={
                LANGFUSE_OBSERVATION_TYPE: "generation",
                GEN_AI_OPERATION_NAME: "chat",
                GEN_AI_REQUEST_STREAM: True,
                "agentos.provider_request.system.length": request_snapshot.system_length,
                "agentos.provider_request.messages.count": request_snapshot.message_count,
                "agentos.provider_request.tools.count": request_snapshot.tool_count,
                "agentos.provider_request.system.sha256": request_snapshot.system_sha256,
                "agentos.provider_request.messages.sha256": request_snapshot.messages_sha256,
                "agentos.provider_request.tools.sha256": request_snapshot.tools_sha256,
            },
        ) as span:
            apply_common_observability_attributes(
                span,
                tracer=self._tracer,
                capture_policy=self._capture_policy,
            )
            span.set_attribute(
                LANGFUSE_OBSERVATION_INPUT,
                json_attribute(
                    self._provider_input_payload(request_snapshot),
                    policy=self._capture_policy,
                ),
            )
            try:
                async for event in self._inner_async_stream(request, options):
                    if (
                        first_chunk_at is None
                        and not isinstance(
                            event,
                            (ProviderStreamStarted, ProviderStreamCompleted),
                        )
                    ):
                        first_chunk_at = monotonic()
                    if isinstance(event, ProviderContentDelta):
                        content_delta_count += 1
                        content_char_count += len(event.text)
                        self._record_stream_event(
                            span,
                            AGENTOS_STREAM_CONTENT_DELTA,
                            sequence=event.index,
                            char_count=len(event.text),
                            text=event.text,
                        )
                    elif isinstance(event, ProviderThinkingDelta):
                        thinking_delta_count += 1
                        thinking_char_count += len(event.text)
                        self._record_stream_event(
                            span,
                            AGENTOS_STREAM_THINKING_DELTA,
                            sequence=event.index,
                            char_count=len(event.text),
                            text=event.text,
                        )
                    elif isinstance(event, ProviderToolCallDelta):
                        tool_delta_count += 1
                        self._record_stream_event(
                            span,
                            AGENTOS_STREAM_TOOL_CALL_DELTA,
                            sequence=event.index,
                            char_count=len(event.arguments_delta or ""),
                            text=event.arguments_delta or "",
                        )
                    elif isinstance(event, ProviderStreamCompleted):
                        response_snapshot = build_provider_response_snapshot(
                            event.response,
                            self._capture_policy,
                        )
                        self._set_stream_response_attributes(
                            span,
                            response_snapshot,
                            event.stop_reason,
                            first_chunk_at,
                            started_at,
                            content_delta_count,
                            content_char_count,
                            thinking_delta_count,
                            thinking_char_count,
                            tool_delta_count,
                        )
                    yield event
            except Exception as error:
                span.set_status("error", str(error))
                span.set_attribute("agentos.stream.partial", True)
                span.set_attribute(
                    "agentos.stream.content.char_count",
                    content_char_count,
                )
                raise

    def _provider_input_payload(
        self,
        snapshot: ProviderRequestSnapshot,
    ) -> dict[str, object]:
        """返回 provider span input payload。"""

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

    def _provider_output_payload(
        self,
        snapshot: ProviderResponseSnapshot,
    ) -> dict[str, object]:
        """返回 provider span output payload。"""

        if self._capture_policy.mode == "metadata":
            return {
                **metadata_identity_payload(capture_policy=self._capture_policy),
                "content_chars": snapshot.content_length,
                "tool_call_count": len(snapshot.tool_calls),
                "stop_reason": snapshot.stop_reason,
            }
        return {
            "content": snapshot.content,
            "thinking_content": snapshot.thinking_content,
            "tool_calls": [
                asdict(tool_call)
                for tool_call in snapshot.tool_calls
            ],
        }

    def _set_usage_attributes(self, span: object, usage: ProviderUsage) -> None:
        """把 ProviderUsage 写入 span attributes。"""

        values = asdict(usage)
        if values.get("input_tokens") is not None:
            span.set_attribute(GEN_AI_USAGE_INPUT_TOKENS, values["input_tokens"])
        if values.get("output_tokens") is not None:
            span.set_attribute(GEN_AI_USAGE_OUTPUT_TOKENS, values["output_tokens"])
        if values.get("total_tokens") is not None:
            span.set_attribute(GEN_AI_USAGE_TOTAL_TOKENS, values["total_tokens"])
        span.set_attribute(
            LANGFUSE_OBSERVATION_USAGE_DETAILS,
            json_attribute(values, policy=self._capture_policy),
        )

    def _inner_stream(
        self,
        request: ProviderRequest,
        options: ProviderStreamOptions | None,
    ) -> Iterator[ProviderStreamEvent]:
        """返回 inner provider stream，必要时使用 complete fallback。"""

        stream = getattr(self._inner, "stream", None)
        if callable(stream):
            yield from stream(request, options)
            return
        response = self._inner.complete(request)
        yield from complete_response_to_stream_events(
            request_id="provider_1",
            response=response,
            options=options,
        )

    async def _inner_async_complete(
        self,
        request: ProviderRequest,
    ) -> ProviderResponse:
        async_complete = getattr(self._inner, "async_complete", None)
        if callable(async_complete):
            return await async_complete(request)
        return await run_sync(self._inner.complete, request)

    async def _inner_async_stream(
        self,
        request: ProviderRequest,
        options: ProviderStreamOptions | None,
    ) -> AsyncIterator[ProviderStreamEvent]:
        async_stream = getattr(self._inner, "async_stream", None)
        if callable(async_stream):
            async for event in async_stream(request, options):
                yield event
            return
        stream = getattr(self._inner, "stream", None)
        if callable(stream):
            bridge = iterate_sync_in_executor(
                lambda: stream(request, options),
            )
            try:
                async for event in bridge:
                    yield event
            finally:
                await bridge._aclose_from_cancelled_task()
            return
        response = await self._inner_async_complete(request)
        for event in complete_response_to_stream_events(
            request_id="provider_1",
            response=response,
            options=options,
        ):
            yield event

    def _record_stream_event(
        self,
        span: object,
        name: str,
        *,
        sequence: int,
        char_count: int,
        text: str,
    ) -> None:
        """按 capture policy 记录低容量 stream span event。"""

        if not self._capture_policy.capture_stream_deltas:
            return
        attributes: dict[str, object] = {
            "sequence": sequence,
            "char_count": char_count,
        }
        if self._capture_policy.capture_stream_delta_text:
            attributes["text"] = text[: self._capture_policy.max_string_length]
        span.add_event(name, attributes)

    def _set_stream_response_attributes(
        self,
        span: object,
        snapshot: ProviderResponseSnapshot,
        stop_reason: str | None,
        first_chunk_at: float | None,
        started_at: float,
        content_delta_count: int,
        content_char_count: int,
        thinking_delta_count: int,
        thinking_char_count: int,
        tool_delta_count: int,
    ) -> None:
        """stream terminal event 后写 response attributes。"""

        provider_name = snapshot.provider_name or "unknown"
        model = snapshot.model or "unknown"
        span.set_attributes(
            {
                GEN_AI_PROVIDER_NAME: provider_name,
                GEN_AI_REQUEST_MODEL: model,
                GEN_AI_RESPONSE_MODEL: model,
                LANGFUSE_OBSERVATION_MODEL_NAME: model,
                GEN_AI_RESPONSE_FINISH_REASONS: (
                    [] if stop_reason is None else [stop_reason]
                ),
                "agentos.stream.content.delta_count": content_delta_count,
                "agentos.stream.content.char_count": content_char_count,
                "agentos.stream.thinking.delta_count": thinking_delta_count,
                "agentos.stream.thinking.char_count": thinking_char_count,
                "agentos.stream.tool_call.delta_count": tool_delta_count,
                "agentos.provider.tool_call_count": len(snapshot.tool_calls),
            },
        )
        if first_chunk_at is not None:
            span.set_attribute(
                GEN_AI_RESPONSE_TIME_TO_FIRST_CHUNK,
                first_chunk_at - started_at,
            )
        if snapshot.response_id is not None:
            span.set_attribute(GEN_AI_RESPONSE_ID, snapshot.response_id)
            span.set_attribute("agentos.provider.response_id", snapshot.response_id)
        if snapshot.usage is not None:
            self._set_usage_attributes(span, snapshot.usage)
        span.set_attribute(
            LANGFUSE_OBSERVATION_OUTPUT,
            json_attribute(
                self._provider_output_payload(snapshot),
                policy=self._capture_policy,
            ),
        )


class InstrumentedCompressionRuntime:
    """在 compression boundary 上创建 span。"""

    def __init__(
        self,
        inner: object,
        *,
        tracer: Tracer,
        capture_policy: CapturePolicy,
    ) -> None:
        """保存被包装 compression runtime。"""

        self._inner = inner
        self._tracer = tracer
        self._capture_policy = capture_policy

    def __getattr__(self, name: str) -> object:
        """透传 compression runtime 的状态和辅助方法。"""

        return getattr(self._inner, name)

    def maybe_compress(self) -> object:
        """执行压缩检查，并记录 compression span。"""

        with self._tracer.start_span(
            "compression.maybe_compress",
            attributes={LANGFUSE_OBSERVATION_TYPE: "span"},
        ) as span:
            apply_common_observability_attributes(
                span,
                tracer=self._tracer,
                capture_policy=self._capture_policy,
            )
            result = self._inner.maybe_compress()
            span.set_attribute("agentos.compression.executed", result is not None)
            if result is not None and getattr(result, "id", None) is not None:
                span.set_attribute("agentos.compression.segment_id", result.id)
            return result
