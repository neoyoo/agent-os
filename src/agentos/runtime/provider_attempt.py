from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from agentos.providers import (
    Provider,
    ProviderRequest,
    ProviderResponse,
    ProviderStreamEvent,
    ProviderStreamOptions,
    complete_response_to_stream_events,
)
from agentos.runtime._async_provider_bridge import (
    complete_async_provider_from_thread,
    stream_async_provider_from_thread,
)
from agentos.runtime.provider_request_builder import ProviderRequestFactory
from agentos.runtime.provider_attempt_state import ProviderAttemptState
from agentos.runtime.retry import RetryPolicy


def ensure_provider_response_usable(response: ProviderResponse) -> None:
    """拒绝被 Provider 截断或拦截的响应。"""

    stop_reason = response.stop_reason
    if stop_reason in {"length", "max_tokens"}:
        raise RuntimeError(
            f"provider response was truncated before final answer: {stop_reason}",
        )
    if stop_reason == "content_filter":
        raise RuntimeError("provider response was blocked by content filter")


def provider_stream_events(
    provider: Provider,
    request: ProviderRequest,
    options: ProviderStreamOptions | None,
    *,
    async_event_loop: asyncio.AbstractEventLoop | None,
    request_id_factory: Callable[[], str],
    cancel_requested: Callable[[], bool],
) -> Iterator[ProviderStreamEvent]:
    """按同步 Loop 的能力优先级适配 Provider stream。"""

    if async_event_loop is not None:
        async_stream = getattr(provider, "async_stream", None)
        if callable(async_stream):
            yield from stream_async_provider_from_thread(
                loop=async_event_loop,
                async_stream_factory=lambda: async_stream(request, options),
                cancel_requested=cancel_requested,
            )
            return
        async_complete = getattr(provider, "async_complete", None)
        if callable(async_complete):
            yield from complete_async_provider_from_thread(
                loop=async_event_loop,
                async_complete_factory=lambda: async_complete(request),
                request_id=request_id_factory(),
                options=options,
                cancel_requested=cancel_requested,
            )
            return
    stream = getattr(provider, "stream", None)
    if callable(stream):
        yield from stream(request, options)
        return
    response = provider.complete(request)
    yield from complete_response_to_stream_events(
        request_id=request_id_factory(),
        response=response,
        options=options,
    )


@dataclass(slots=True)
class ProviderAttemptRunner:
    """协调一次或多次同步 Provider 物理调用。"""

    request_factory: ProviderRequestFactory
    stream_provider: Callable[
        [ProviderRequest, ProviderStreamOptions | None],
        Iterator[ProviderStreamEvent],
    ]
    before_call: Callable[[ProviderRequest], ProviderRequest]
    after_call: Callable[[ProviderRequest, ProviderResponse], ProviderResponse]
    ensure_usable: Callable[[ProviderResponse], None]
    consume_temporary: Callable[[tuple[str, ...]], None]
    retry_policy: RetryPolicy | None
    on_retry: Callable[[int, Exception], None]

    def run_stream(
        self,
        options: ProviderStreamOptions | None,
    ) -> Iterator[ProviderStreamEvent]:
        """运行 Provider attempt stream。"""

        policy = self.retry_policy
        if policy is not None:
            policy.raise_if_open()
        attempt = 0
        while True:
            state = ProviderAttemptState(options)
            try:
                build = self.request_factory()
                original_request = build.request
                request = self.before_call(original_request)
                temporary_ids = (
                    build.receipt.temporary_message_ids
                    if request is original_request
                    else ()
                )
                state.enter_provider_call()
                stream = iter(self.stream_provider(request, options))
                try:
                    for event in stream:
                        if not state.accept_stream_event(event):
                            continue
                        yield event
                finally:
                    close = getattr(stream, "close", None)
                    if callable(close):
                        close()
                completion = state.require_completion()
                state.enter_after_hook()
                response = self.after_call(request, completion.response)
                state.enter_validation()
                self.ensure_usable(response)
                state.enter_consumption()
                self.consume_temporary(temporary_ids)
                if policy is not None:
                    policy.record_success()
                yield state.completed_event(response)
                return
            except Exception as error:
                attempt += 1
                if not state.should_retry(policy, error, attempt):
                    state.record_terminal_failure(policy)
                    raise
                self.on_retry(attempt, error)
