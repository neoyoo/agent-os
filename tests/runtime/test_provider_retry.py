from __future__ import annotations

import asyncio
from threading import Event as ThreadEvent

import pytest

from agentos.context import ContextRuntime
from agentos.events import EventBus, ProviderRetryEvent
from agentos.messages import MessageRuntime
from agentos.providers import ProviderRequest, ProviderResponse
from agentos.runtime import (
    Agent,
    AgentBusyError,
    ProviderRequestBuilder,
    QueryLoop,
    RetryPolicy,
)
from tests._context_protocol_fixtures import default_context_renderer


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
            context_renderer=default_context_renderer(),
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

    outcome = asyncio.run(Agent(loop).run("hello"))

    assert outcome.content == "recovered"

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

    async def scenario() -> None:
        agent = Agent(loop)
        with pytest.raises(RuntimeError, match="provider unavailable"):
            await agent.run("first")
        with pytest.raises(RuntimeError, match="provider circuit is open"):
            await agent.run("second")

    asyncio.run(scenario())

    assert provider.calls == 1


def test_external_close_waits_for_sync_retry_sleep_before_releasing_lease() -> None:
    async def scenario() -> None:
        sleep_started = ThreadEvent()
        release_sleep = ThreadEvent()
        sleep_finished = ThreadEvent()

        def blocking_sleep(_delay: float) -> None:
            sleep_started.set()
            release_sleep.wait()
            sleep_finished.set()

        provider = FlakyProvider()
        agent = Agent(
            _loop(
                provider,
                retry_policy=RetryPolicy(
                    max_retries=1,
                    backoff_base=0,
                    jitter=0,
                    sleep=blocking_sleep,
                ),
            ),
        )
        stream = await agent.run("hello", stream=True)

        async def consume() -> None:
            async for _event in stream:
                pass

        consumer = asyncio.create_task(consume())
        assert await asyncio.to_thread(sleep_started.wait, 5)
        close_task = asyncio.create_task(stream.aclose())
        results: list[object] = []
        try:
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(close_task), 0.05)
            with pytest.raises(AgentBusyError):
                await agent.run("replacement", stream=True)
        finally:
            release_sleep.set()
            results = await asyncio.gather(
                close_task,
                consumer,
                return_exceptions=True,
            )

        assert results[0] is None
        assert isinstance(results[1], asyncio.CancelledError)
        assert sleep_finished.is_set()
        replacement = await agent.run("replacement")
        assert replacement.content == "recovered"

    asyncio.run(scenario())
