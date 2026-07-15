"""OpenAI-compatible Chat Completions Provider facade。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass, field
from threading import Lock

from agentos._sync_work import run_sync
from agentos.providers._timeout import raise_for_provider_timeout
from agentos.providers.base import ProviderRequest, ProviderResponse
from agentos.providers.openai_compatible_parsing import (
    OpenAICompatibleStreamParser,
    parse_openai_compatible_response,
)
from agentos.providers.openai_compatible_transport import (
    AsyncOpenAICompatibleTransport,
    HttpxAsyncJSONTransport,
    OpenAICompatibleProviderError,
    OpenAICompatibleTransport,
    UrlLibJSONTransport,
)
from agentos.providers.openai_compatible_wire import (
    build_openai_compatible_payload,
)
from agentos.providers.stream import (
    ProviderStreamEvent,
    ProviderStreamOptions,
)


@dataclass(slots=True)
class OpenAICompatibleProvider:
    """使用 OpenAI-compatible Chat Completions 协议的 Provider。"""

    api_key: str
    base_url: str
    model: str
    timeout_seconds: float = 60.0
    transport: OpenAICompatibleTransport | None = None
    async_transport: AsyncOpenAICompatibleTransport | None = None
    thinking: dict[str, object] | None = None
    extra_body: dict[str, object] | None = None
    supports_parallel_tool_calls_parameter: bool = False
    _stream_invocation_ordinal: int = field(
        default=0,
        init=False,
        repr=False,
        compare=False,
    )
    _stream_seed_lock: Lock = field(
        default_factory=Lock,
        init=False,
        repr=False,
        compare=False,
    )

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        """调用 OpenAI-compatible `/chat/completions`。"""

        transport = self.transport or UrlLibJSONTransport()
        try:
            response = transport.post_json(
                url=self._chat_completions_url(),
                headers=self._headers(),
                payload=self._payload(request),
                timeout=self.timeout_seconds,
            )
        except Exception as error:
            raise_for_provider_timeout(
                error,
                provider_name="OpenAI-compatible",
            )
            raise
        return parse_openai_compatible_response(response)

    async def async_complete(self, request: ProviderRequest) -> ProviderResponse:
        """异步调用 OpenAI-compatible `/chat/completions`。"""

        if self.async_transport is None and self.transport is not None:
            return await run_sync(self.complete, request)
        transport = self.async_transport or HttpxAsyncJSONTransport()
        try:
            response = await transport.post_json(
                url=self._chat_completions_url(),
                headers=self._headers(),
                payload=self._payload(request),
                timeout=self.timeout_seconds,
            )
        except Exception as error:
            raise_for_provider_timeout(
                error,
                provider_name="OpenAI-compatible",
            )
            raise
        return parse_openai_compatible_response(response)

    def stream(
        self,
        request: ProviderRequest,
        options: ProviderStreamOptions | None = None,
    ) -> Iterator[ProviderStreamEvent]:
        """调用 OpenAI-compatible streaming Chat Completions。"""

        stream_options = options or ProviderStreamOptions()
        parser = OpenAICompatibleStreamParser(
            model=self.model,
            options=stream_options,
            stream_seed=self._next_stream_seed(),
        )
        payload = self._payload(request)
        payload["stream"] = True
        transport = self.transport or UrlLibJSONTransport()
        try:
            for chunk in transport.post_json_stream(
                url=self._chat_completions_url(),
                headers=self._headers(),
                payload=payload,
                timeout=self.timeout_seconds,
            ):
                yield from parser.feed(chunk)
        except Exception as error:
            raise_for_provider_timeout(
                error,
                provider_name="OpenAI-compatible",
            )
            raise
        yield from parser.finish()

    async def async_stream(
        self,
        request: ProviderRequest,
        options: ProviderStreamOptions | None = None,
    ) -> AsyncIterator[ProviderStreamEvent]:
        """异步调用 OpenAI-compatible streaming Chat Completions。"""

        if self.async_transport is None and self.transport is not None:
            async for event in _iterate_sync_stream(
                lambda: self.stream(request, options),
            ):
                yield event
            return
        stream_options = options or ProviderStreamOptions()
        parser = OpenAICompatibleStreamParser(
            model=self.model,
            options=stream_options,
            stream_seed=self._next_stream_seed(),
        )
        payload = self._payload(request)
        payload["stream"] = True
        transport = self.async_transport or HttpxAsyncJSONTransport()
        try:
            async for chunk in transport.post_json_stream(
                url=self._chat_completions_url(),
                headers=self._headers(),
                payload=payload,
                timeout=self.timeout_seconds,
            ):
                for event in parser.feed(chunk):
                    yield event
        except Exception as error:
            raise_for_provider_timeout(
                error,
                provider_name="OpenAI-compatible",
            )
            raise
        for event in parser.finish():
            yield event

    def _payload(self, request: ProviderRequest) -> dict[str, object]:
        return build_openai_compatible_payload(
            model=self.model,
            request=request,
            thinking=self.thinking,
            extra_body=self.extra_body,
            supports_parallel_tool_calls_parameter=(
                self.supports_parallel_tool_calls_parameter
            ),
            invalid_message_error=OpenAICompatibleProviderError,
        )

    def _chat_completions_url(self) -> str:
        base_url = self.base_url.rstrip("/")
        if base_url.endswith("/chat/completions"):
            return base_url
        return f"{base_url}/chat/completions"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _next_stream_seed(self) -> str:
        """冻结与 Provider chunk 元数据无关的调用级 seed。"""

        with self._stream_seed_lock:
            self._stream_invocation_ordinal += 1
            ordinal = self._stream_invocation_ordinal
        return f"stream_{ordinal}"


_SYNC_STREAM_DONE = object()


def _next_sync_stream_event(
    iterator: Iterator[ProviderStreamEvent],
) -> ProviderStreamEvent | object:
    try:
        return next(iterator)
    except StopIteration:
        return _SYNC_STREAM_DONE


async def _iterate_sync_stream(
    factory: Callable[[], Iterator[ProviderStreamEvent]],
) -> AsyncIterator[ProviderStreamEvent]:
    iterator = factory()
    while True:
        event = await run_sync(_next_sync_stream_event, iterator)
        if event is _SYNC_STREAM_DONE:
            return
        yield event
