from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
import inspect

import pytest

from agentos.providers import (
    ProviderRequest,
    ProviderResponse,
    ProviderStreamCompleted,
    ProviderStreamEvent,
    ProviderStreamOptions,
)
from agentos.runtime.provider_attempt import (
    ProviderAttemptRunner,
    provider_stream_events,
)
from agentos.runtime.provider_request_builder import (
    ProviderRequestBuild,
    ProviderRequestReceipt,
)


class CountingRequestFactory:
    def __init__(self, temporary_id: str | None = None) -> None:
        self.calls = 0
        self.temporary_id = temporary_id
        self.requests: list[ProviderRequest] = []

    def __call__(self) -> ProviderRequestBuild:
        self.calls += 1
        request = ProviderRequest(system=f"system-{self.calls}", messages=())
        self.requests.append(request)
        temporary_ids = () if self.temporary_id is None else (self.temporary_id,)
        return ProviderRequestBuild(
            request=request,
            receipt=ProviderRequestReceipt(temporary_ids),
        )


class SyncCompleteProvider:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def complete(self, _request: ProviderRequest) -> ProviderResponse:
        self.calls.append("sync_complete")
        return ProviderResponse(content="sync complete")


class SyncStreamProvider(SyncCompleteProvider):
    def stream(
        self,
        _request: ProviderRequest,
        _options: ProviderStreamOptions | None,
    ) -> Iterator[ProviderStreamEvent]:
        self.calls.append("sync_stream")

        def events() -> Iterator[ProviderStreamEvent]:
            yield ProviderStreamCompleted(
                "sync-stream-1",
                ProviderResponse(content="sync stream"),
            )

        return events()


class NativeAsyncCompleteProvider(SyncStreamProvider):
    def async_complete(
        self,
        _request: ProviderRequest,
    ) -> Awaitable[ProviderResponse]:
        self.calls.append("async_complete")

        async def complete() -> ProviderResponse:
            return ProviderResponse(content="async complete")

        return complete()


class NativeAsyncStreamProvider(NativeAsyncCompleteProvider):
    def async_stream(
        self,
        _request: ProviderRequest,
        _options: ProviderStreamOptions | None,
    ) -> AsyncIterator[ProviderStreamEvent]:
        self.calls.append("async_stream")

        async def events() -> AsyncIterator[ProviderStreamEvent]:
            yield ProviderStreamCompleted(
                "async-stream-1",
                ProviderResponse(content="async stream"),
            )

        return events()


def canonical_runner(
    provider: object,
    *,
    request_id_factory: Callable[[], str] | None = None,
) -> ProviderAttemptRunner:
    factory = CountingRequestFactory()
    make_request_id = request_id_factory or (lambda: "candidate-1")

    async def on_retry(_attempt: int, _error: Exception) -> None:
        raise AssertionError("retry is not expected")

    return ProviderAttemptRunner(
        request_factory=factory,
        stream_provider=lambda request, options: provider_stream_events(
            provider,  # type: ignore[arg-type]
            request,
            options,
            request_id_factory=make_request_id,
        ),
        before_call=lambda request: request,
        after_call=lambda _request, response: response,
        ensure_usable=lambda _response: None,
        consume_temporary=lambda _temporary_ids: None,
        retry_policy=None,
        on_retry=on_retry,
    )


async def collect_events(
    runner: ProviderAttemptRunner,
) -> list[ProviderStreamEvent]:
    return [event async for event in runner.run_stream(None)]


@pytest.mark.parametrize(
    ("provider", "capability", "arguments", "expected_capability"),
    [
        (
            NativeAsyncStreamProvider(),
            "async_stream",
            (None,),
            "async_stream",
        ),
        (
            NativeAsyncCompleteProvider(),
            "async_complete",
            (),
            "async_complete",
        ),
        (SyncStreamProvider(), "stream", (None,), "sync_stream"),
        (SyncCompleteProvider(), "complete", (), "sync_complete"),
    ],
)
def test_capability_spy_records_invocation_before_result_is_consumed(
    provider: object,
    capability: str,
    arguments: tuple[object, ...],
    expected_capability: str,
) -> None:
    request = ProviderRequest(system="system", messages=())
    result = getattr(provider, capability)(request, *arguments)
    try:
        assert provider.calls == [expected_capability]  # type: ignore[attr-defined]
    finally:
        if inspect.iscoroutine(result):
            result.close()
        elif inspect.isasyncgen(result):
            asyncio.run(result.aclose())
        else:
            close = getattr(result, "close", None)
            if callable(close):
                close()


@pytest.mark.parametrize(
    ("provider", "expected_capability"),
    [
        (NativeAsyncStreamProvider(), "async_stream"),
        (NativeAsyncCompleteProvider(), "async_complete"),
        (SyncStreamProvider(), "sync_stream"),
        (SyncCompleteProvider(), "sync_complete"),
    ],
)
def test_provider_capability_priority(
    provider: object,
    expected_capability: str,
) -> None:
    async def scenario() -> None:
        runner = canonical_runner(provider)

        events = await collect_events(runner)

        assert provider.calls == [expected_capability]  # type: ignore[attr-defined]
        assert isinstance(events[-1], ProviderStreamCompleted)

    asyncio.run(scenario())
