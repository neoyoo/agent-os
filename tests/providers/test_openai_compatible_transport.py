import asyncio
import inspect
from collections.abc import AsyncIterator, Iterator
from io import BytesIO
from threading import Event as ThreadEvent
from urllib.error import HTTPError

import pytest

from agentos import Agent
from agentos.context import ContextRuntime
from agentos.messages import MessageRuntime
from agentos.providers import (
    HttpxAsyncJSONTransport,
    OpenAICompatibleProvider,
    OpenAICompatibleProviderError,
    ProviderRequest,
    UrlLibJSONTransport,
)
from agentos.runtime import AgentBusyError, ProviderRequestBuilder, QueryLoop
from tests._context_protocol_fixtures import default_context_renderer

def test_openai_compatible_provider_async_complete_uses_async_transport() -> None:
    class FakeAsyncTransport:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def post_json(
            self,
            url: str,
            headers: dict[str, str],
            payload: dict[str, object],
            timeout: float,
        ) -> dict[str, object]:
            self.calls.append(
                {
                    "url": url,
                    "headers": headers,
                    "payload": payload,
                    "timeout": timeout,
                },
            )
            return {"choices": [{"message": {"content": "async done"}}]}

    async def run() -> tuple[str, list[dict[str, object]]]:
        transport = FakeAsyncTransport()
        provider = OpenAICompatibleProvider(
            api_key="test-key",
            base_url="https://api.deepseek.example",
            model="deepseek-chat",
            async_transport=transport,
        )
        response = await provider.async_complete(
            ProviderRequest(system="system", messages=[]),
        )
        return response.content, transport.calls

    content, calls = asyncio.run(run())

    assert content == "async done"
    assert calls[0]["url"] == "https://api.deepseek.example/chat/completions"


def test_openai_compatible_sync_transport_converges_with_bound_run_tracker() -> None:
    from agentos._sync_work import SyncWorkTracker, bind_sync_work_tracker

    class BlockingTransport:
        def __init__(self) -> None:
            self.started = ThreadEvent()
            self.release = ThreadEvent()
            self.finished = ThreadEvent()

        def post_json(self, **_kwargs: object) -> dict[str, object]:
            self.started.set()
            self.release.wait()
            self.finished.set()
            return {"choices": [{"message": {"content": "done"}}]}

    async def scenario() -> None:
        transport = BlockingTransport()
        provider = OpenAICompatibleProvider(
            api_key="test-key",
            base_url="https://api.deepseek.example",
            model="deepseek-chat",
            transport=transport,  # type: ignore[arg-type]
        )
        tracker = SyncWorkTracker()

        async def events() -> AsyncIterator[object]:
            yield await provider.async_complete(
                ProviderRequest(system="system", messages=[]),
            )

        source = bind_sync_work_tracker(events(), tracker)
        consumer = asyncio.create_task(anext(source))
        try:
            assert await asyncio.to_thread(transport.started.wait, 5)
            consumer.cancel("transport cancellation")
            with pytest.raises(asyncio.CancelledError):
                await consumer

            waiter = asyncio.create_task(tracker.wait_until_idle())
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(waiter), 0.05)
        finally:
            transport.release.set()
            await asyncio.gather(consumer, return_exceptions=True)
            await source.aclose()

        await waiter
        assert transport.finished.is_set()
        assert tracker.exceptions == ()

    asyncio.run(scenario())


def test_query_loop_close_waits_for_sync_stream_transport_next() -> None:
    class BlockingStreamTransport:
        def __init__(self) -> None:
            self.blocked = ThreadEvent()
            self.release = ThreadEvent()
            self.finished = ThreadEvent()
            self.closed = ThreadEvent()

        def post_json_stream(
            self,
            **_kwargs: object,
        ) -> Iterator[dict[str, object]]:
            try:
                yield {
                    "id": "stream_1",
                    "model": "test-model",
                    "choices": [
                        {"finish_reason": None, "delta": {"content": "first"}},
                    ],
                }
                self.blocked.set()
                self.release.wait()
                self.finished.set()
                yield {
                    "id": "stream_1",
                    "model": "test-model",
                    "choices": [
                        {"finish_reason": "stop", "delta": {}},
                    ],
                }
            finally:
                self.closed.set()

    async def scenario() -> None:
        transport = BlockingStreamTransport()
        messages = MessageRuntime()
        provider = OpenAICompatibleProvider(
            api_key="test-key",
            base_url="https://api.example.test",
            model="test-model",
            transport=transport,  # type: ignore[arg-type]
        )
        agent = Agent(
            QueryLoop(
                context_runtime=ContextRuntime(),
                message_runtime=messages,
                request_builder=ProviderRequestBuilder(
                    context_renderer=default_context_renderer(),
                    message_runtime=messages,
                ),
                provider=provider,
            ),
        )
        stream = await agent.run("hello", stream=True)

        async def consume() -> None:
            async for _event in stream:
                pass

        consumer = asyncio.create_task(consume())
        assert await asyncio.to_thread(transport.blocked.wait, 5)
        close_task = asyncio.create_task(stream.aclose())
        results: list[object] = []
        try:
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(close_task), 0.05)
            with pytest.raises(AgentBusyError):
                await agent.run("replacement", stream=True)
        finally:
            transport.release.set()
            results = await asyncio.gather(
                close_task,
                consumer,
                return_exceptions=True,
            )

        assert results[0] is None
        assert isinstance(results[1], asyncio.CancelledError)
        assert transport.finished.is_set()
        assert transport.closed.is_set()
        replacement = await agent.run("replacement")
        assert replacement.content == "first"

    asyncio.run(scenario())


def test_openai_compatible_transport_includes_error_body(monkeypatch) -> None:
    def _raise_http_error(*args: object, **kwargs: object) -> object:
        raise HTTPError(
            url="https://api.deepseek.example/chat/completions",
            code=400,
            msg="Bad Request",
            hdrs={},
            fp=BytesIO(b'{"error":{"message":"invalid model"}}'),
        )

    monkeypatch.setattr(
        "agentos.providers.openai_compatible_transport.urlopen",
        _raise_http_error,
    )

    try:
        UrlLibJSONTransport().post_json(
            url="https://api.deepseek.example/chat/completions",
            headers={},
            payload={},
            timeout=1.0,
        )
    except OpenAICompatibleProviderError as error:
        assert "HTTP 400" in str(error)
        assert "invalid model" in str(error)
    else:
        raise AssertionError("Expected OpenAICompatibleProviderError")


def test_openai_compatible_stream_transport_ignores_sse_metadata(monkeypatch) -> None:
    class FakeStreamResponse:
        def __iter__(self):
            return iter(
                [
                    b": keep-alive\n",
                    b"event: message\n",
                    b"id: chunk_1\n",
                    b"retry: 1000\n",
                    b"data:\n",
                    b'data: {"id":"chatcmpl_1","choices":[]}\n',
                    b'{"id":"chatcmpl_2","choices":[]}\n',
                    b"data: [DONE]\n",
                ],
            )

        def __enter__(self):
            return self

        def __exit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(
        "agentos.providers.openai_compatible_transport.urlopen",
        lambda *args, **kwargs: FakeStreamResponse(),
    )

    chunks = list(
        UrlLibJSONTransport().post_json_stream(
            url="https://api.deepseek.example/chat/completions",
            headers={},
            payload={},
            timeout=1.0,
        ),
    )

    assert [chunk["id"] for chunk in chunks] == ["chatcmpl_1", "chatcmpl_2"]


def test_openai_compatible_async_stream_transport_ignores_sse_metadata(
    monkeypatch,
) -> None:
    class FakeStreamResponse:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        def raise_for_status(self) -> None:
            return None

        async def aiter_lines(self):
            for line in [
                ": keep-alive",
                "event: message",
                "id: chunk_1",
                "retry: 1000",
                "data:",
                'data: {"id":"chatcmpl_1","choices":[]}',
                '{"id":"chatcmpl_2","choices":[]}',
                "data: [DONE]",
            ]:
                yield line

    class FakeAsyncClient:
        def __init__(self, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        def stream(self, *args: object, **kwargs: object) -> FakeStreamResponse:
            return FakeStreamResponse()

    class FakeHTTPX:
        AsyncClient = FakeAsyncClient
        HTTPStatusError = RuntimeError
        HTTPError = RuntimeError

    monkeypatch.setattr(HttpxAsyncJSONTransport, "_httpx", lambda self: FakeHTTPX)

    async def collect() -> list[dict[str, object]]:
        return [
            chunk
            async for chunk in HttpxAsyncJSONTransport().post_json_stream(
                url="https://api.deepseek.example/chat/completions",
                headers={},
                payload={},
                timeout=1.0,
            )
        ]

    chunks = asyncio.run(collect())

    assert [chunk["id"] for chunk in chunks] == ["chatcmpl_1", "chatcmpl_2"]


def test_openai_compatible_async_stream_transport_reads_error_body(
    monkeypatch,
) -> None:
    class ResponseNotRead(RuntimeError):
        pass

    class FakeHTTPStatusError(RuntimeError):
        def __init__(self, response: object) -> None:
            super().__init__("401 Unauthorized")
            self.response = response

    class FakeStreamResponse:
        status_code = 401
        _content: bytes | None = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def aread(self) -> bytes:
            self._content = b'{"error":"invalid api key"}'
            return self._content

        @property
        def text(self) -> str:
            if self._content is None:
                raise ResponseNotRead("streaming response has not been read")
            return self._content.decode("utf-8")

        def raise_for_status(self) -> None:
            raise FakeHTTPStatusError(self)

        async def aiter_lines(self):
            yield "data: [DONE]"

    class FakeAsyncClient:
        def __init__(self, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        def stream(self, *args: object, **kwargs: object) -> FakeStreamResponse:
            return FakeStreamResponse()

    class FakeHTTPX:
        AsyncClient = FakeAsyncClient
        HTTPStatusError = FakeHTTPStatusError
        HTTPError = RuntimeError

    monkeypatch.setattr(HttpxAsyncJSONTransport, "_httpx", lambda self: FakeHTTPX)

    async def collect() -> None:
        async for _ in HttpxAsyncJSONTransport().post_json_stream(
            url="https://api.example.test/chat/completions",
            headers={},
            payload={},
            timeout=1.0,
        ):
            pass

    with pytest.raises(OpenAICompatibleProviderError) as error:
        asyncio.run(collect())

    message = str(error.value)
    assert "HTTP 401" in message
    assert "invalid api key" in message


def test_openai_compatible_provider_leaves_http_error_translation_to_transport() -> None:
    source = inspect.getsource(OpenAICompatibleProvider.complete)

    assert "except HTTPError" not in source
