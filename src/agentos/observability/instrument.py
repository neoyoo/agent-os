from dataclasses import replace

from agentos.observability.config import ObservabilityConfig
from agentos.observability.instrumented import (
    InstrumentedCompressionRuntime,
    InstrumentedProvider,
    InstrumentedProviderRequestBuilder,
    InstrumentedQueryLoop,
    InstrumentedToolCallRouter,
)
from agentos.observability.logging import configure_structured_logger
from agentos.runtime import QueryLoop


def instrument_query_loop(
    loop: QueryLoop,
    config: ObservabilityConfig,
) -> InstrumentedQueryLoop:
    """返回带生产观测 wrapper 的 QueryLoop，不修改原始 loop。"""

    tracer = config.tracer
    capture_policy = config.capture_policy
    instrumented_provider = InstrumentedProvider(
        loop.provider,
        tracer=tracer,
        capture_policy=capture_policy,
    )
    instrumented_builder = InstrumentedProviderRequestBuilder(
        loop.request_builder,
        tracer=tracer,
        capture_policy=capture_policy,
    )
    instrumented_router = (
        None
        if loop.tool_call_router is None
        else InstrumentedToolCallRouter(
            loop.tool_call_router,  # type: ignore[arg-type]
            tracer=tracer,
            capture_policy=capture_policy,
        )
    )
    instrumented_compression = (
        None
        if loop.compression_runtime is None
        else InstrumentedCompressionRuntime(
            loop.compression_runtime,
            tracer=tracer,
            capture_policy=capture_policy,
        )
    )
    configured_loop = replace(
        loop,
        provider=instrumented_provider,
        request_builder=instrumented_builder,  # type: ignore[arg-type]
        tool_call_router=instrumented_router,
        compression_runtime=instrumented_compression,  # type: ignore[arg-type]
        structured_logger=configure_structured_logger(config),
    )
    return InstrumentedQueryLoop(
        configured_loop,
        tracer=tracer,
        capture_policy=capture_policy,
    )
