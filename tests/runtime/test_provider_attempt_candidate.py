from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import suppress
import inspect
import threading

import pytest

from agentos.providers import (
    ProviderContentDelta,
    ProviderRequest,
    ProviderResponse,
    ProviderStreamCompleted,
    ProviderStreamEvent,
    ProviderStreamFailed,
    ProviderStreamOptions,
)
from agentos.runtime._provider_attempt_async import (
    AsyncProviderAttemptCandidate,
    provider_stream_events,
)
from agentos.runtime.provider_request_builder import (
    ProviderRequestBuild,
    ProviderRequestReceipt,
)
from agentos.runtime.retry import RetryPolicy


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


def candidate_runner(
    provider: object,
    *,
    request_factory: CountingRequestFactory | None = None,
    before_call: Callable[[ProviderRequest], ProviderRequest] | None = None,
    consumed: list[tuple[str, ...]] | None = None,
    retry_policy: RetryPolicy | None = None,
    retries: list[tuple[int, str]] | None = None,
    request_id_factory: Callable[[], str] | None = None,
) -> AsyncProviderAttemptCandidate:
    factory = request_factory or CountingRequestFactory()
    receipt_consumer = consumed if consumed is not None else []
    make_request_id = request_id_factory or (lambda: "candidate-1")

    async def on_retry(attempt: int, error: Exception) -> None:
        if retries is not None:
            retries.append((attempt, str(error)))

    return AsyncProviderAttemptCandidate(
        request_factory=factory,
        stream_provider=lambda request, options: provider_stream_events(
            provider,  # type: ignore[arg-type]
            request,
            options,
            request_id_factory=make_request_id,
        ),
        before_call=before_call or (lambda request: request),
        after_call=lambda _request, response: response,
        ensure_usable=lambda _response: None,
        consume_temporary=receipt_consumer.append,
        retry_policy=retry_policy,
        on_retry=on_retry,
    )


async def collect_events(
    runner: AsyncProviderAttemptCandidate,
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
        runner = candidate_runner(provider)

        events = await collect_events(runner)

        assert provider.calls == [expected_capability]  # type: ignore[attr-defined]
        assert isinstance(events[-1], ProviderStreamCompleted)

    asyncio.run(scenario())


def test_candidate_retry_rebuilds_request() -> None:
    async def scenario() -> None:
        factory = CountingRequestFactory()
        retries: list[tuple[int, str]] = []

        class FailOnceProvider:
            def __init__(self) -> None:
                self.calls = 0
                self.requests: list[ProviderRequest] = []

            async def async_stream(
                self,
                request: ProviderRequest,
                _options: ProviderStreamOptions | None,
            ) -> AsyncIterator[ProviderStreamEvent]:
                self.calls += 1
                self.requests.append(request)
                if self.calls == 1:
                    yield ProviderStreamFailed("req-1", RuntimeError("retry me"))
                    return
                yield ProviderStreamCompleted(
                    "req-2",
                    ProviderResponse(content="done"),
                )

        provider = FailOnceProvider()
        runner = candidate_runner(
            provider,
            request_factory=factory,
            retry_policy=RetryPolicy(max_retries=1, backoff_base=0, jitter=0),
            retries=retries,
        )

        events = await collect_events(runner)

        assert factory.calls == 2
        assert provider.requests == factory.requests
        assert provider.requests[0] is not provider.requests[1]
        assert retries == [(1, "retry me")]
        assert isinstance(events[-1], ProviderStreamCompleted)

    asyncio.run(scenario())


def test_candidate_before_hook_replacement_does_not_consume_original_receipt() -> None:
    async def scenario() -> None:
        consumed: list[tuple[str, ...]] = []
        replacement = ProviderRequest(system="replacement", messages=())
        runner = candidate_runner(
            NativeAsyncStreamProvider(),
            request_factory=CountingRequestFactory("temporary-1"),
            before_call=lambda _request: replacement,
            consumed=consumed,
        )

        await collect_events(runner)

        assert consumed == [()]

    asyncio.run(scenario())


def test_candidate_does_not_retry_after_visible_delta() -> None:
    async def scenario() -> None:
        factory = CountingRequestFactory()

        class DeltaThenFailureProvider:
            def __init__(self) -> None:
                self.calls = 0

            async def async_stream(
                self,
                _request: ProviderRequest,
                _options: ProviderStreamOptions | None,
            ) -> AsyncIterator[ProviderStreamEvent]:
                self.calls += 1
                yield ProviderContentDelta("req-1", 0, "visible")
                yield ProviderStreamFailed("req-1", RuntimeError("after delta"))

        provider = DeltaThenFailureProvider()
        runner = candidate_runner(
            provider,
            request_factory=factory,
            retry_policy=RetryPolicy(max_retries=1, backoff_base=0, jitter=0),
        )

        with pytest.raises(RuntimeError, match="after delta"):
            await collect_events(runner)

        assert provider.calls == 1
        assert factory.calls == 1

    asyncio.run(scenario())


def test_candidate_missing_completion_does_not_consume_temporary_refs() -> None:
    async def scenario() -> None:
        consumed: list[tuple[str, ...]] = []

        class MissingCompletionProvider:
            async def async_stream(
                self,
                _request: ProviderRequest,
                _options: ProviderStreamOptions | None,
            ) -> AsyncIterator[ProviderStreamEvent]:
                if False:
                    yield ProviderStreamCompleted(
                        "never",
                        ProviderResponse(content=""),
                    )

        runner = candidate_runner(
            MissingCompletionProvider(),
            request_factory=CountingRequestFactory("temporary-1"),
            consumed=consumed,
        )

        with pytest.raises(RuntimeError, match="without completion"):
            await collect_events(runner)

        assert consumed == []

    asyncio.run(scenario())


def test_candidate_cancellation_waits_for_sync_bridge_cleanup() -> None:
    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        worker_blocked = asyncio.Event()
        release_worker = threading.Event()
        iterator_closed = threading.Event()

        class BlockingIterator:
            def __init__(self) -> None:
                self.index = 0

            def __iter__(self) -> Iterator[ProviderStreamEvent]:
                return self

            def __next__(self) -> ProviderStreamEvent:
                self.index += 1
                if self.index == 1:
                    return ProviderContentDelta("req-1", 0, "first")
                loop.call_soon_threadsafe(worker_blocked.set)
                release_worker.wait()
                raise StopIteration

            def close(self) -> None:
                iterator_closed.set()

        class BlockingSyncProvider:
            def stream(
                self,
                _request: ProviderRequest,
                _options: ProviderStreamOptions | None,
            ) -> Iterator[ProviderStreamEvent]:
                return BlockingIterator()

            def complete(self, _request: ProviderRequest) -> ProviderResponse:
                raise AssertionError("sync complete must not be selected")

        stream = provider_stream_events(
            BlockingSyncProvider(),  # type: ignore[arg-type]
            ProviderRequest(system="system", messages=()),
            None,
            request_id_factory=lambda: "candidate-1",
        )
        assert await anext(stream) == ProviderContentDelta("req-1", 0, "first")
        pending = asyncio.create_task(anext(stream))
        try:
            async with asyncio.timeout(2):
                await worker_blocked.wait()
                pending.cancel()
                loop.call_soon(release_worker.set)
                with pytest.raises(asyncio.CancelledError):
                    await pending

                assert iterator_closed.is_set()
        finally:
            release_worker.set()
            if not pending.done():
                pending.cancel()
            with suppress(asyncio.CancelledError):
                await pending

    asyncio.run(scenario())
