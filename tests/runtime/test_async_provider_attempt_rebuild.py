from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import suppress
import threading

import pytest

from agentos.messages import MessageRuntime, StoredMessage
from agentos.providers import (
    ProviderContentDelta,
    ProviderInputItem,
    ProviderRequest,
    ProviderResponse,
    ProviderStreamCompleted,
    ProviderStreamEvent,
    ProviderStreamFailed,
    ProviderStreamOptions,
    ProviderThinkingDelta,
)
from agentos.runtime.async_provider_attempt import (
    AsyncProviderAttemptRunner,
    async_provider_stream_events,
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

    def __call__(self) -> ProviderRequestBuild:
        self.calls += 1
        messages: tuple[ProviderInputItem, ...] = ()
        receipt_ids: tuple[str, ...] = ()
        if self.temporary_id is not None:
            messages = (ProviderInputItem.recalled_user("remember this"),)
            receipt_ids = (self.temporary_id,)
        return ProviderRequestBuild(
            request=ProviderRequest(
                system=f"system-{self.calls}",
                messages=messages,
            ),
            receipt=ProviderRequestReceipt(receipt_ids),
        )


class FailOnceStream:
    def __init__(self) -> None:
        self.calls = 0
        self.requests: list[ProviderRequest] = []

    async def __call__(
        self,
        request: ProviderRequest,
        options: ProviderStreamOptions | None,
    ) -> AsyncIterator[ProviderStreamEvent]:
        del options
        self.calls += 1
        self.requests.append(request)
        if self.calls == 1:
            yield ProviderStreamFailed("req-1", RuntimeError("retry me"))
            return
        yield ProviderStreamCompleted("req-2", ProviderResponse(content="done"))


def _temporary_message(runtime: MessageRuntime, message_id: str) -> StoredMessage:
    message = StoredMessage(id=message_id, role="user", content="remember this")
    runtime.store.put(message)
    runtime.active_window.prepend_temporary((message.id,))
    return message


def _runner(
    *,
    factory: CountingRequestFactory,
    stream_provider: object,
    before_call: object | None = None,
    messages: MessageRuntime | None = None,
    retry_policy: RetryPolicy | None = None,
    retries: list[tuple[int, str]] | None = None,
) -> AsyncProviderAttemptRunner:
    async def on_retry(attempt: int, error: Exception) -> None:
        if retries is not None:
            retries.append((attempt, str(error)))

    return AsyncProviderAttemptRunner(
        request_factory=factory,
        stream_provider=stream_provider,  # type: ignore[arg-type]
        before_call=(lambda request: request)
        if before_call is None
        else before_call,  # type: ignore[arg-type]
        after_call=lambda _request, response: response,
        ensure_usable=lambda _response: None,
        consume_temporary=(
            lambda _message_ids: None
            if messages is None
            else messages.consume_temporary_refs(_message_ids)
        ),
        retry_policy=retry_policy,
        on_retry=on_retry,
    )


async def _collect(
    runner: AsyncProviderAttemptRunner,
    options: ProviderStreamOptions | None = None,
) -> list[ProviderStreamEvent]:
    return [event async for event in runner.run_stream(options)]


def test_async_retry_rebuilds_request_and_reexecutes_before_hook() -> None:
    async def scenario() -> None:
        factory = CountingRequestFactory()
        provider = FailOnceStream()
        before_requests: list[ProviderRequest] = []
        retries: list[tuple[int, str]] = []
        runner = _runner(
            factory=factory,
            stream_provider=provider,
            before_call=lambda request: before_requests.append(request) or request,
            retry_policy=RetryPolicy(max_retries=1, backoff_base=0, jitter=0),
            retries=retries,
        )

        events = await _collect(runner)

        assert factory.calls == 2
        assert len(before_requests) == 2
        assert provider.requests[0] is not provider.requests[1]
        assert retries == [(1, "retry me")]
        assert isinstance(events[-1], ProviderStreamCompleted)

    asyncio.run(scenario())


def test_async_temporary_recall_survives_failed_attempt_and_consumes_after_success() -> None:
    async def scenario() -> None:
        messages = MessageRuntime()
        temporary = _temporary_message(messages, "msg_recalled")
        factory = CountingRequestFactory(temporary.id)
        provider = FailOnceStream()
        runner = _runner(
            factory=factory,
            stream_provider=provider,
            messages=messages,
            retry_policy=RetryPolicy(max_retries=1, backoff_base=0, jitter=0),
        )

        await _collect(runner)

        assert provider.requests[1].messages == (
            ProviderInputItem.recalled_user("remember this"),
        )
        assert not messages.has_temporary_recalled()

    asyncio.run(scenario())


def test_async_hook_replacement_invalidates_temporary_receipt() -> None:
    async def scenario() -> None:
        messages = MessageRuntime()
        temporary = _temporary_message(messages, "msg_recalled")
        factory = CountingRequestFactory(temporary.id)
        replacement = ProviderRequest(system="replacement", messages=())

        async def stream(
            _request: ProviderRequest,
            _options: ProviderStreamOptions | None,
        ) -> AsyncIterator[ProviderStreamEvent]:
            yield ProviderStreamCompleted("req-1", ProviderResponse(content="done"))

        runner = _runner(
            factory=factory,
            stream_provider=stream,
            before_call=lambda _request: replacement,
            messages=messages,
        )

        await _collect(runner)

        assert messages.has_temporary_recalled()

    asyncio.run(scenario())


def test_async_retry_is_forbidden_after_visible_content_delta() -> None:
    async def scenario() -> None:
        factory = CountingRequestFactory()

        async def stream(
            _request: ProviderRequest,
            _options: ProviderStreamOptions | None,
        ) -> AsyncIterator[ProviderStreamEvent]:
            yield ProviderContentDelta("req-1", 0, "visible")
            yield ProviderStreamFailed("req-1", RuntimeError("after delta"))

        runner = _runner(
            factory=factory,
            stream_provider=stream,
            retry_policy=RetryPolicy(max_retries=1, backoff_base=0, jitter=0),
        )
        events: list[ProviderStreamEvent] = []

        with pytest.raises(RuntimeError, match="after delta"):
            async for event in runner.run_stream(None):
                events.append(event)

        assert factory.calls == 1
        assert [type(event) for event in events] == [ProviderContentDelta]

    asyncio.run(scenario())


def test_async_hidden_thinking_delta_does_not_block_retry() -> None:
    async def scenario() -> None:
        factory = CountingRequestFactory()
        calls = 0

        async def stream(
            _request: ProviderRequest,
            _options: ProviderStreamOptions | None,
        ) -> AsyncIterator[ProviderStreamEvent]:
            nonlocal calls
            calls += 1
            if calls == 1:
                yield ProviderThinkingDelta("req-1", 0, "hidden")
                yield ProviderStreamFailed("req-1", RuntimeError("retry me"))
                return
            yield ProviderStreamCompleted("req-2", ProviderResponse(content="done"))

        runner = _runner(
            factory=factory,
            stream_provider=stream,
            retry_policy=RetryPolicy(max_retries=1, backoff_base=0, jitter=0),
        )

        events = await _collect(
            runner,
            ProviderStreamOptions(show_thinking=False),
        )

        assert factory.calls == 2
        assert isinstance(events[-1], ProviderStreamCompleted)

    asyncio.run(scenario())


def test_async_completed_event_is_validated_and_emitted_last() -> None:
    async def scenario() -> None:
        factory = CountingRequestFactory()
        calls: list[str] = []
        raw_response = ProviderResponse(content="raw", stop_reason="raw-response")
        final_response = ProviderResponse(
            content="validated",
            stop_reason="after-hook",
        )

        async def stream(
            _request: ProviderRequest,
            _options: ProviderStreamOptions | None,
        ) -> AsyncIterator[ProviderStreamEvent]:
            yield ProviderContentDelta("req-1", 0, "chunk")
            yield ProviderStreamCompleted(
                "req-1",
                raw_response,
                stop_reason="raw-event",
            )

        async def on_retry(_attempt: int, _error: Exception) -> None:
            raise AssertionError("retry is not expected")

        runner = AsyncProviderAttemptRunner(
            request_factory=factory,
            stream_provider=stream,
            before_call=lambda request: calls.append("before") or request,
            after_call=lambda _request, _response: calls.append("after") or final_response,
            ensure_usable=lambda response: calls.append(f"usable:{response.content}"),
            consume_temporary=lambda ids: calls.append(f"consume:{ids!r}"),
            retry_policy=None,
            on_retry=on_retry,
        )

        events = await _collect(runner)

        assert calls == ["before", "after", "usable:validated", "consume:()"]
        assert [type(event) for event in events] == [
            ProviderContentDelta,
            ProviderStreamCompleted,
        ]
        assert events[-1].response is final_response  # type: ignore[union-attr]
        assert events[-1].stop_reason == "after-hook"  # type: ignore[union-attr]

    asyncio.run(scenario())


def test_async_missing_completion_fails_without_consuming_receipt() -> None:
    async def scenario() -> None:
        factory = CountingRequestFactory("msg_recalled")
        consumed: list[tuple[str, ...]] = []

        async def stream(
            _request: ProviderRequest,
            _options: ProviderStreamOptions | None,
        ) -> AsyncIterator[ProviderStreamEvent]:
            if False:
                yield ProviderStreamCompleted("never", ProviderResponse(content=""))

        async def on_retry(_attempt: int, _error: Exception) -> None:
            raise AssertionError("retry is not expected")

        runner = AsyncProviderAttemptRunner(
            request_factory=factory,
            stream_provider=stream,
            before_call=lambda request: request,
            after_call=lambda _request, response: response,
            ensure_usable=lambda _response: None,
            consume_temporary=consumed.append,
            retry_policy=None,
            on_retry=on_retry,
        )

        with pytest.raises(RuntimeError, match="without completion"):
            await _collect(runner)

        assert consumed == []

    asyncio.run(scenario())


@pytest.mark.parametrize("hook_stage", ["before", "after"])
def test_async_hook_failure_does_not_retry_or_open_provider_circuit(
    hook_stage: str,
) -> None:
    async def scenario() -> None:
        factory = CountingRequestFactory()
        provider_calls = 0
        retries: list[tuple[int, str]] = []
        policy = RetryPolicy(
            max_retries=1,
            backoff_base=0,
            jitter=0,
            circuit_failure_threshold=1,
        )

        async def stream(
            _request: ProviderRequest,
            _options: ProviderStreamOptions | None,
        ) -> AsyncIterator[ProviderStreamEvent]:
            nonlocal provider_calls
            provider_calls += 1
            yield ProviderStreamCompleted("req-1", ProviderResponse(content="done"))

        def before(request: ProviderRequest) -> ProviderRequest:
            if hook_stage == "before":
                raise RuntimeError("hook denied")
            return request

        def after(
            _request: ProviderRequest,
            response: ProviderResponse,
        ) -> ProviderResponse:
            if hook_stage == "after":
                raise RuntimeError("hook denied")
            return response

        async def on_retry(attempt: int, error: Exception) -> None:
            retries.append((attempt, str(error)))

        runner = AsyncProviderAttemptRunner(
            request_factory=factory,
            stream_provider=stream,
            before_call=before,
            after_call=after,
            ensure_usable=lambda _response: None,
            consume_temporary=lambda _ids: None,
            retry_policy=policy,
            on_retry=on_retry,
        )

        with pytest.raises(RuntimeError, match="hook denied"):
            await _collect(runner)

        assert factory.calls == 1
        assert provider_calls == (0 if hook_stage == "before" else 1)
        assert retries == []
        policy.raise_if_open()

    asyncio.run(scenario())


def test_async_raw_completion_rejects_late_events_and_closes_stream() -> None:
    async def scenario() -> None:
        factory = CountingRequestFactory()
        closed = False

        async def stream(
            _request: ProviderRequest,
            _options: ProviderStreamOptions | None,
        ) -> AsyncIterator[ProviderStreamEvent]:
            nonlocal closed
            try:
                yield ProviderStreamCompleted(
                    "req-1",
                    ProviderResponse(content="done"),
                )
                yield ProviderContentDelta("req-1", 1, "late")
            finally:
                closed = True

        runner = _runner(factory=factory, stream_provider=stream)

        with pytest.raises(RuntimeError, match="after completion"):
            await _collect(runner)

        assert closed is True

    asyncio.run(scenario())


def test_async_sync_provider_fallback_closes_iterator_before_late_event_error() -> None:
    async def scenario() -> None:
        factory = CountingRequestFactory()
        release_worker = threading.Event()
        iterator_closed = threading.Event()

        class LateEventIterator:
            def __init__(self) -> None:
                self.index = 0

            def __iter__(self) -> Iterator[ProviderStreamEvent]:
                return self

            def __next__(self) -> ProviderStreamEvent:
                self.index += 1
                if self.index == 1:
                    return ProviderStreamCompleted(
                        "req-1",
                        ProviderResponse(content="done"),
                    )
                if self.index == 2:
                    return ProviderContentDelta("req-1", 1, "late")
                release_worker.wait(timeout=1)
                raise StopIteration

            def close(self) -> None:
                iterator_closed.set()

        class SyncProvider:
            def stream(
                self,
                _request: ProviderRequest,
                _options: ProviderStreamOptions | None,
            ) -> Iterator[ProviderStreamEvent]:
                return LateEventIterator()

        provider = SyncProvider()
        runner = _runner(
            factory=factory,
            stream_provider=lambda request, options: async_provider_stream_events(
                provider,  # type: ignore[arg-type]
                request,
                options,
                request_id_factory=lambda: "fallback-1",
            ),
        )

        try:
            with pytest.raises(RuntimeError, match="after completion"):
                await _collect(runner)

            assert iterator_closed.is_set()
        finally:
            release_worker.set()

    asyncio.run(scenario())


def test_async_sync_provider_fallback_cancellation_closes_iterator() -> None:
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

        class SyncProvider:
            def stream(
                self,
                _request: ProviderRequest,
                _options: ProviderStreamOptions | None,
            ) -> Iterator[ProviderStreamEvent]:
                return BlockingIterator()

        stream = async_provider_stream_events(
            SyncProvider(),  # type: ignore[arg-type]
            ProviderRequest(system="system", messages=()),
            None,
            request_id_factory=lambda: "fallback-1",
        )

        first = await anext(stream)
        assert first == ProviderContentDelta("req-1", 0, "first")
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


def test_async_native_stream_double_cancel_waits_for_aclose() -> None:
    async def scenario() -> None:
        next_started = asyncio.Event()
        close_started = asyncio.Event()
        release_close = asyncio.Event()
        closed = False

        class BlockingAsyncIterator:
            def __init__(self) -> None:
                self.index = 0

            def __aiter__(self) -> AsyncIterator[ProviderStreamEvent]:
                return self

            async def __anext__(self) -> ProviderStreamEvent:
                self.index += 1
                if self.index == 1:
                    return ProviderContentDelta("req-1", 0, "first")
                next_started.set()
                await asyncio.Event().wait()
                raise StopAsyncIteration

            async def aclose(self) -> None:
                nonlocal closed
                close_started.set()
                await release_close.wait()
                closed = True

        class NativeProvider:
            def async_stream(
                self,
                _request: ProviderRequest,
                _options: ProviderStreamOptions | None,
            ) -> AsyncIterator[ProviderStreamEvent]:
                return BlockingAsyncIterator()

        stream = async_provider_stream_events(
            NativeProvider(),  # type: ignore[arg-type]
            ProviderRequest(system="system", messages=()),
            None,
            request_id_factory=lambda: "native-1",
        )

        first = await anext(stream)
        assert first == ProviderContentDelta("req-1", 0, "first")
        consumer = asyncio.create_task(anext(stream))
        try:
            async with asyncio.timeout(2):
                await next_started.wait()
                consumer.cancel()
                await close_started.wait()

                consumer.cancel()
                checkpoint = asyncio.Event()
                asyncio.get_running_loop().call_soon(checkpoint.set)
                await checkpoint.wait()

                assert not consumer.done()
                assert closed is False

                release_close.set()
                with pytest.raises(asyncio.CancelledError):
                    await consumer
                assert closed is True
                assert consumer.cancelling() == 2
        finally:
            release_close.set()
            if not consumer.done():
                consumer.cancel()
            with suppress(asyncio.CancelledError):
                await consumer

    asyncio.run(scenario())
