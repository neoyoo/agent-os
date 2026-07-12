from __future__ import annotations

from collections.abc import Iterator

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
from agentos.runtime.provider_attempt import ProviderAttemptRunner
from agentos.runtime.provider_request_builder import (
    ProviderRequestBuild,
    ProviderRequestReceipt,
)
from agentos.runtime.retry import RetryPolicy


class CountingRequestFactory:
    def __init__(
        self,
        *,
        messages: MessageRuntime | None = None,
        temporary_id: str | None = None,
    ) -> None:
        self.calls = 0
        self.builds: list[ProviderRequestBuild] = []
        self.messages = messages
        self.temporary_id = temporary_id

    def __call__(self) -> ProviderRequestBuild:
        self.calls += 1
        items: tuple[ProviderInputItem, ...] = ()
        receipt_ids: tuple[str, ...] = ()
        if self.temporary_id is not None:
            items = (ProviderInputItem.recalled_user("remember this"),)
            receipt_ids = (self.temporary_id,)
        build = ProviderRequestBuild(
            request=ProviderRequest(
                system=f"system-{self.calls}",
                messages=items,
            ),
            receipt=ProviderRequestReceipt(receipt_ids),
        )
        self.builds.append(build)
        return build


class FailOnceStream:
    def __init__(self) -> None:
        self.calls = 0
        self.requests: list[ProviderRequest] = []

    def __call__(
        self,
        request: ProviderRequest,
        options: ProviderStreamOptions | None,
    ) -> Iterator[ProviderStreamEvent]:
        del options
        self.calls += 1
        self.requests.append(request)
        if self.calls == 1:
            yield ProviderStreamFailed("req-1", RuntimeError("retry me"))
            return
        yield ProviderStreamCompleted(
            request_id="req-2",
            response=ProviderResponse(content="done"),
        )


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
    on_retry: object | None = None,
) -> ProviderAttemptRunner:
    return ProviderAttemptRunner(
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
        on_retry=(lambda _attempt, _error: None)
        if on_retry is None
        else on_retry,  # type: ignore[arg-type]
    )


def test_sync_retry_rebuilds_request_and_reexecutes_before_hook() -> None:
    factory = CountingRequestFactory()
    provider = FailOnceStream()
    before_requests: list[ProviderRequest] = []
    retries: list[tuple[int, str]] = []
    runner = _runner(
        factory=factory,
        stream_provider=provider,
        before_call=lambda request: before_requests.append(request) or request,
        retry_policy=RetryPolicy(max_retries=1, backoff_base=0, jitter=0),
        on_retry=lambda attempt, error: retries.append((attempt, str(error))),
    )

    events = list(runner.run_stream(options=None))

    assert factory.calls == 2
    assert len(before_requests) == 2
    assert provider.requests[0] is not provider.requests[1]
    assert retries == [(1, "retry me")]
    assert isinstance(events[-1], ProviderStreamCompleted)
    assert events[-1].response.content == "done"


def test_sync_temporary_recall_survives_failed_attempt_and_consumes_after_success() -> None:
    messages = MessageRuntime()
    temporary = _temporary_message(messages, "msg_recalled")
    factory = CountingRequestFactory(
        messages=messages,
        temporary_id=temporary.id,
    )
    provider = FailOnceStream()
    runner = _runner(
        factory=factory,
        stream_provider=provider,
        messages=messages,
        retry_policy=RetryPolicy(max_retries=1, backoff_base=0, jitter=0),
    )

    list(runner.run_stream(options=None))

    assert len(provider.requests) == 2
    assert provider.requests[1].messages == (
        ProviderInputItem.recalled_user("remember this"),
    )
    assert not messages.has_temporary_recalled()


def test_sync_hook_replacement_invalidates_temporary_receipt() -> None:
    messages = MessageRuntime()
    temporary = _temporary_message(messages, "msg_recalled")
    factory = CountingRequestFactory(
        messages=messages,
        temporary_id=temporary.id,
    )
    replacement = ProviderRequest(system="replacement", messages=())
    runner = _runner(
        factory=factory,
        stream_provider=lambda _request, _options: iter(
            (
                ProviderStreamCompleted(
                    request_id="req-1",
                    response=ProviderResponse(content="done"),
                ),
            ),
        ),
        before_call=lambda _request: replacement,
        messages=messages,
    )

    list(runner.run_stream(options=None))

    assert messages.has_temporary_recalled()


def test_sync_retry_is_forbidden_after_visible_content_delta() -> None:
    factory = CountingRequestFactory()

    def stream(
        _request: ProviderRequest,
        _options: ProviderStreamOptions | None,
    ) -> Iterator[ProviderStreamEvent]:
        yield ProviderContentDelta("req-1", 0, "visible")
        yield ProviderStreamFailed("req-1", RuntimeError("after delta"))

    runner = _runner(
        factory=factory,
        stream_provider=stream,
        retry_policy=RetryPolicy(max_retries=1, backoff_base=0, jitter=0),
    )

    events: list[ProviderStreamEvent] = []
    with pytest.raises(RuntimeError, match="after delta"):
        events.extend(runner.run_stream(options=None))

    assert factory.calls == 1
    assert [type(event) for event in events] == [ProviderContentDelta]


def test_sync_hidden_thinking_delta_does_not_block_retry() -> None:
    factory = CountingRequestFactory()
    calls = 0

    def stream(
        _request: ProviderRequest,
        _options: ProviderStreamOptions | None,
    ) -> Iterator[ProviderStreamEvent]:
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

    events = list(
        runner.run_stream(
            options=ProviderStreamOptions(show_thinking=False),
        ),
    )

    assert factory.calls == 2
    assert isinstance(events[-1], ProviderStreamCompleted)


def test_sync_completed_event_is_validated_and_emitted_last() -> None:
    factory = CountingRequestFactory()
    calls: list[str] = []
    raw_response = ProviderResponse(content="raw", stop_reason="raw-response")
    final_response = ProviderResponse(
        content="validated",
        stop_reason="after-hook",
    )
    runner = ProviderAttemptRunner(
        request_factory=factory,
        stream_provider=lambda _request, _options: iter(
            (
                ProviderContentDelta("req-1", 0, "chunk"),
                ProviderStreamCompleted(
                    "req-1",
                    raw_response,
                    stop_reason="raw-event",
                ),
            ),
        ),
        before_call=lambda request: calls.append("before") or request,
        after_call=lambda _request, _response: calls.append("after") or final_response,
        ensure_usable=lambda response: calls.append(f"usable:{response.content}"),
        consume_temporary=lambda ids: calls.append(f"consume:{ids!r}"),
        retry_policy=None,
        on_retry=lambda _attempt, _error: None,
    )

    events = list(runner.run_stream(options=None))

    assert calls == ["before", "after", "usable:validated", "consume:()"]
    assert [type(event) for event in events] == [
        ProviderContentDelta,
        ProviderStreamCompleted,
    ]
    assert events[-1].response is final_response  # type: ignore[union-attr]
    assert events[-1].stop_reason == "after-hook"  # type: ignore[union-attr]


def test_sync_missing_completion_fails_without_consuming_receipt() -> None:
    factory = CountingRequestFactory(temporary_id="msg_recalled")
    consumed: list[tuple[str, ...]] = []
    runner = ProviderAttemptRunner(
        request_factory=factory,
        stream_provider=lambda _request, _options: iter(()),
        before_call=lambda request: request,
        after_call=lambda _request, response: response,
        ensure_usable=lambda _response: None,
        consume_temporary=consumed.append,
        retry_policy=None,
        on_retry=lambda _attempt, _error: None,
    )

    with pytest.raises(RuntimeError, match="without completion"):
        list(runner.run_stream(options=None))

    assert consumed == []


@pytest.mark.parametrize("hook_stage", ["before", "after"])
def test_sync_hook_failure_does_not_retry_or_open_provider_circuit(
    hook_stage: str,
) -> None:
    factory = CountingRequestFactory()
    provider_calls = 0
    retries: list[tuple[int, str]] = []
    policy = RetryPolicy(
        max_retries=1,
        backoff_base=0,
        jitter=0,
        circuit_failure_threshold=1,
    )

    def stream(
        _request: ProviderRequest,
        _options: ProviderStreamOptions | None,
    ) -> Iterator[ProviderStreamEvent]:
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

    runner = ProviderAttemptRunner(
        request_factory=factory,
        stream_provider=stream,
        before_call=before,
        after_call=after,
        ensure_usable=lambda _response: None,
        consume_temporary=lambda _ids: None,
        retry_policy=policy,
        on_retry=lambda attempt, error: retries.append((attempt, str(error))),
    )

    with pytest.raises(RuntimeError, match="hook denied"):
        list(runner.run_stream(options=None))

    assert factory.calls == 1
    assert provider_calls == (0 if hook_stage == "before" else 1)
    assert retries == []
    policy.raise_if_open()


def test_sync_raw_completion_rejects_late_events_and_closes_stream() -> None:
    factory = CountingRequestFactory()
    closed = False

    def stream(
        _request: ProviderRequest,
        _options: ProviderStreamOptions | None,
    ) -> Iterator[ProviderStreamEvent]:
        nonlocal closed
        try:
            yield ProviderStreamCompleted("req-1", ProviderResponse(content="done"))
            yield ProviderContentDelta("req-1", 1, "late")
        finally:
            closed = True

    runner = _runner(factory=factory, stream_provider=stream)

    with pytest.raises(RuntimeError, match="after completion"):
        list(runner.run_stream(options=None))

    assert closed is True
