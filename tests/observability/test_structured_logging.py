from __future__ import annotations

import json
import logging
from io import StringIO

from agentos.context import ContextRenderer, ContextRuntime
from agentos.messages import MessageRuntime
from agentos.observability import CapturePolicy, ObservabilityConfig, StructuredLogFormatter, configure_structured_logger
from agentos.providers import FakeProvider
from agentos.runtime import ProviderRequestBuilder, QueryLoop


class DummyTracer:
    pass


def test_structured_logging_is_disabled_by_default() -> None:
    config = ObservabilityConfig(tracer=DummyTracer())

    assert config.logging_enabled is False


def test_query_loop_writes_structured_logs_when_enabled() -> None:
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(StructuredLogFormatter())
    logger = logging.getLogger("agentos.test.structured")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False

    config = ObservabilityConfig(
        tracer=DummyTracer(),
        capture_policy=CapturePolicy.metadata_only(),
        logging_enabled=True,
        logger_name="agentos.test.structured",
    )
    messages = MessageRuntime()
    loop = QueryLoop(
        context_runtime=ContextRuntime(),
        message_runtime=messages,
        request_builder=ProviderRequestBuilder(
            context_renderer=ContextRenderer(),
            message_runtime=messages,
        ),
        provider=FakeProvider(["done"]),
        structured_logger=configure_structured_logger(config),
    )

    loop.run_turn("hello")

    events = [json.loads(line)["event"] for line in stream.getvalue().splitlines()]
    assert events == ["turn_start", "provider_call", "turn_end"]
