from dataclasses import fields, replace

from agentos.observability.config import ObservabilityConfig
from agentos.observability.instrumented import (
    InstrumentedCompressionRuntime,
    InstrumentedProvider,
    InstrumentedProviderRequestBuilder,
    InstrumentedToolCallRouter,
)
from agentos.observability.logging import configure_structured_logger
from agentos.observability.query_loop import InstrumentedQueryLoop
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
    structured_logger = configure_structured_logger(config)
    changes: dict[str, object] = {
        "provider": instrumented_provider,
        "request_builder": instrumented_builder,
        "tool_call_router": instrumented_router,
        "compression_runtime": instrumented_compression,
        "structured_logger": structured_logger,
    }
    init_fields = {field.name for field in fields(loop) if field.init}
    configured_loop = replace(
        loop,
        **{name: value for name, value in changes.items() if name in init_fields},
    )
    return InstrumentedQueryLoop(
        configured_loop,  # type: ignore[arg-type]
        tracer=tracer,
        capture_policy=capture_policy,
    )
