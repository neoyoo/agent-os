from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

from agentos.providers import (
    Provider,
    ProviderRequest,
    ProviderResponse,
    ProviderStreamEvent,
    ProviderStreamOptions,
    complete_response_to_stream_events,
)
from agentos.runtime._async_bridge import (
    _await_cleanup_preserving_cancellation,
    iterate_sync_in_executor,
)
from agentos.runtime.provider_request_builder import ProviderRequestFactory
from agentos.runtime.provider_attempt_state import ProviderAttemptState
from agentos.runtime.retry import RetryPolicy


async def async_provider_stream_events(
    provider: Provider,
    request: ProviderRequest,
    options: ProviderStreamOptions | None,
    *,
    request_id_factory: Callable[[], str],
) -> AsyncIterator[ProviderStreamEvent]:
    """按异步 Loop 的能力优先级适配 Provider stream。"""

    async_stream = getattr(provider, "async_stream", None)
    if callable(async_stream):
        stream = async_stream(request, options)
        try:
            async for event in stream:
                yield event
        finally:
            close = getattr(stream, "aclose", None)
            if callable(close):
                await _await_cleanup_preserving_cancellation(close)
        return
    async_complete = getattr(provider, "async_complete", None)
    if callable(async_complete):
        response = await async_complete(request)
        for event in complete_response_to_stream_events(
            request_id=request_id_factory(),
            response=response,
            options=options,
        ):
            yield event
        return
    stream = getattr(provider, "stream", None)
    if callable(stream):
        bridge = iterate_sync_in_executor(lambda: stream(request, options))
        try:
            async for event in bridge:
                yield event
        finally:
            await bridge._aclose_from_cancelled_task()
        return
    response = await asyncio.to_thread(provider.complete, request)
    for event in complete_response_to_stream_events(
        request_id=request_id_factory(),
        response=response,
        options=options,
    ):
        yield event


@dataclass(slots=True)
class AsyncProviderAttemptRunner:
    """协调一次或多次异步 Provider 物理调用。"""

    request_factory: ProviderRequestFactory
    stream_provider: Callable[
        [ProviderRequest, ProviderStreamOptions | None],
        AsyncIterator[ProviderStreamEvent],
    ]
    before_call: Callable[[ProviderRequest], ProviderRequest]
    after_call: Callable[[ProviderRequest, ProviderResponse], ProviderResponse]
    ensure_usable: Callable[[ProviderResponse], None]
    consume_temporary: Callable[[tuple[str, ...]], None]
    retry_policy: RetryPolicy | None
    on_retry: Callable[[int, Exception], Awaitable[None]]

    async def run_stream(
        self,
        options: ProviderStreamOptions | None,
    ) -> AsyncIterator[ProviderStreamEvent]:
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
                stream = self.stream_provider(request, options)
                try:
                    async for event in stream:
                        if not state.accept_stream_event(event):
                            continue
                        yield event
                finally:
                    close = getattr(stream, "aclose", None)
                    if callable(close):
                        await close()
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
                await self.on_retry(attempt, error)
