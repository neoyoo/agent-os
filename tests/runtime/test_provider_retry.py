from __future__ import annotations

import pytest

from agentos.context import ContextRenderer, ContextRuntime
from agentos.events import EventBus, ProviderRetryEvent
from agentos.messages import MessageRuntime
from agentos.providers import ProviderRequest, ProviderResponse
from agentos.runtime import ProviderRequestBuilder, QueryLoop, RetryPolicy


class FlakyProvider:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("temporary provider failure")
        return ProviderResponse(content="recovered")


class AlwaysFailProvider:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        self.calls += 1
        raise RuntimeError("provider unavailable")


def _loop(provider: object, *, retry_policy: RetryPolicy, event_bus: EventBus | None = None) -> QueryLoop:
    messages = MessageRuntime()
    return QueryLoop(
        context_runtime=ContextRuntime(),
        message_runtime=messages,
        request_builder=ProviderRequestBuilder(
            context_renderer=ContextRenderer(),
            message_runtime=messages,
        ),
        provider=provider,  # type: ignore[arg-type]
        retry_policy=retry_policy,
        event_bus=event_bus,
    )


def test_query_loop_retries_provider_failures_and_emits_event() -> None:
    provider = FlakyProvider()
    bus = EventBus()
    loop = _loop(
        provider,
        retry_policy=RetryPolicy(max_retries=1, backoff_base=0, jitter=0),
        event_bus=bus,
    )

    assert loop.run_turn("hello") == "recovered"

    assert provider.calls == 2
    retry_events = [event for event in bus.events if isinstance(event, ProviderRetryEvent)]
    assert retry_events == [
        ProviderRetryEvent(
            attempt=1,
            max_retries=1,
            error="temporary provider failure",
            delay_seconds=0,
        ),
    ]


def test_query_loop_opens_circuit_after_consecutive_provider_failures() -> None:
    provider = AlwaysFailProvider()
    policy = RetryPolicy(
        max_retries=0,
        backoff_base=0,
        circuit_failure_threshold=1,
        circuit_open_seconds=30,
    )
    loop = _loop(provider, retry_policy=policy)

    with pytest.raises(RuntimeError, match="provider unavailable"):
        loop.run_turn("first")
    with pytest.raises(RuntimeError, match="provider circuit is open"):
        loop.run_turn("second")

    assert provider.calls == 1
