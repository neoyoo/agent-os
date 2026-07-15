from __future__ import annotations

import asyncio
import inspect
import socket
from types import SimpleNamespace
from urllib.error import URLError

import pytest

from agentos.providers import (
    AnthropicProvider,
    OpenAICompatibleProvider,
    OpenAIProvider,
    Provider,
    ProviderRequest,
    ProviderTimeoutError,
)
from agentos.providers.openai_chat import OpenAIChatCompletionsProvider
from agentos.providers.openai_compatible_transport import (
    HttpxAsyncJSONTransport,
    UrlLibJSONTransport,
)


class RecordingTransport:
    def __init__(self) -> None:
        self.timeout: float | None = None

    def post_json(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, object],
        timeout: float,
    ) -> dict[str, object]:
        self.timeout = timeout
        return {"choices": [{"message": {"content": "ok"}}]}

    def post_json_stream(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, object],
        timeout: float,
    ):
        self.timeout = timeout
        return iter(())


class APITimeoutError(Exception):
    pass


class RaisingCall:
    def create(self, **kwargs: object) -> object:
        raise APITimeoutError("upstream timed out")


def test_openai_compatible_provider_forwards_timeout_seconds() -> None:
    transport = RecordingTransport()
    provider = OpenAICompatibleProvider(
        api_key="key",
        base_url="https://example.test",
        model="model",
        timeout_seconds=7,
        transport=transport,
    )

    provider.complete(ProviderRequest(system="", messages=[]))

    assert transport.timeout == 7


def test_openai_compatible_provider_has_no_legacy_timeout_parameter() -> None:
    parameters = inspect.signature(OpenAICompatibleProvider).parameters

    assert "timeout_seconds" in parameters
    assert "timeout" not in parameters


def test_openai_compatible_provider_defaults_to_timeout_seconds() -> None:
    provider = OpenAICompatibleProvider(
        api_key="key",
        base_url="https://example.test",
        model="model",
    )

    assert provider.timeout_seconds == 60


@pytest.mark.parametrize(
    "provider",
    [
        OpenAIProvider(
            client=SimpleNamespace(responses=RaisingCall()),
            model="model",
        ),
        OpenAIChatCompletionsProvider(
            client=SimpleNamespace(
                chat=SimpleNamespace(completions=RaisingCall()),
            ),
            model="model",
        ),
        AnthropicProvider(
            client=SimpleNamespace(messages=RaisingCall()),
            model="model",
        ),
    ],
    ids=["responses", "chat", "anthropic"],
)
def test_official_provider_clients_map_sdk_timeout(
    provider: Provider,
) -> None:
    with pytest.raises(ProviderTimeoutError):
        provider.complete(ProviderRequest(system="system", messages=()))


def test_openai_compatible_sync_and_async_complete_map_transport_timeout() -> None:
    class SyncTimeoutTransport:
        def post_json(self, **kwargs: object) -> dict[str, object]:
            raise TimeoutError("sync timed out")

    class AsyncTimeoutTransport:
        async def post_json(self, **kwargs: object) -> dict[str, object]:
            raise TimeoutError("async timed out")

    request = ProviderRequest(system="system", messages=())
    sync_provider = OpenAICompatibleProvider(
        api_key="key",
        base_url="https://example.test",
        model="model",
        transport=SyncTimeoutTransport(),  # type: ignore[arg-type]
    )
    async_provider = OpenAICompatibleProvider(
        api_key="key",
        base_url="https://example.test",
        model="model",
        async_transport=AsyncTimeoutTransport(),  # type: ignore[arg-type]
    )

    with pytest.raises(ProviderTimeoutError):
        sync_provider.complete(request)
    with pytest.raises(ProviderTimeoutError):
        asyncio.run(async_provider.async_complete(request))


def test_openai_compatible_sync_and_async_stream_map_transport_timeout() -> None:
    class SyncTimeoutTransport:
        def post_json_stream(self, **kwargs: object):
            yield from ()
            raise TimeoutError("sync stream timed out")

    class AsyncTimeoutTransport:
        async def post_json_stream(self, **kwargs: object):
            for item in ():
                yield item
            raise TimeoutError("async stream timed out")

    request = ProviderRequest(system="system", messages=())
    sync_provider = OpenAICompatibleProvider(
        api_key="key",
        base_url="https://example.test",
        model="model",
        transport=SyncTimeoutTransport(),  # type: ignore[arg-type]
    )
    async_provider = OpenAICompatibleProvider(
        api_key="key",
        base_url="https://example.test",
        model="model",
        async_transport=AsyncTimeoutTransport(),  # type: ignore[arg-type]
    )

    with pytest.raises(ProviderTimeoutError):
        list(sync_provider.stream(request))

    async def collect_async() -> None:
        async for _ in async_provider.async_stream(request):
            pass

    with pytest.raises(ProviderTimeoutError):
        asyncio.run(collect_async())


def test_httpx_complete_and_stream_map_timeout_exception(monkeypatch) -> None:
    class FakeTimeoutException(Exception):
        pass

    class FakeAsyncClient:
        def __init__(self, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self):
            raise FakeTimeoutException("timed out")

        async def __aexit__(self, *args: object) -> None:
            return None

    class FakeHTTPX:
        AsyncClient = FakeAsyncClient
        TimeoutException = FakeTimeoutException
        HTTPStatusError = RuntimeError
        HTTPError = RuntimeError

    monkeypatch.setattr(HttpxAsyncJSONTransport, "_httpx", lambda self: FakeHTTPX)
    transport = HttpxAsyncJSONTransport()

    with pytest.raises(ProviderTimeoutError):
        asyncio.run(
            transport.post_json(
                url="https://example.test",
                headers={},
                payload={},
                timeout=1,
            ),
        )

    async def collect_stream() -> None:
        async for _ in transport.post_json_stream(
            url="https://example.test",
            headers={},
            payload={},
            timeout=1,
        ):
            pass

    with pytest.raises(ProviderTimeoutError):
        asyncio.run(collect_stream())


def test_urllib_transport_maps_socket_timeout_to_provider_timeout_error() -> None:
    transport = UrlLibJSONTransport()

    with pytest.raises(ProviderTimeoutError):
        transport._map_transport_error(URLError(socket.timeout("timed out")))


def test_urllib_complete_and_stream_map_direct_socket_timeout(monkeypatch) -> None:
    def raise_timeout(*args: object, **kwargs: object) -> object:
        raise socket.timeout("timed out")

    monkeypatch.setattr(
        "agentos.providers.openai_compatible_transport.urlopen",
        raise_timeout,
    )
    transport = UrlLibJSONTransport()
    kwargs = {
        "url": "https://example.test",
        "headers": {},
        "payload": {},
        "timeout": 1,
    }

    with pytest.raises(ProviderTimeoutError):
        transport.post_json(**kwargs)
    with pytest.raises(ProviderTimeoutError):
        list(transport.post_json_stream(**kwargs))
