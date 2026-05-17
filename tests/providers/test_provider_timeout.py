from __future__ import annotations

import socket
from urllib.error import URLError

import pytest

from agentos.providers import AnthropicProvider, OpenAICompatibleProvider, OpenAIProvider, ProviderTimeoutError
from agentos.providers.openai_compatible import UrlLibJSONTransport
from agentos.providers import ProviderRequest


class RecordingTransport:
    def __init__(self) -> None:
        self.timeout: float | None = None

    def post_json(self, url: str, headers: dict[str, str], payload: dict[str, object], timeout: float) -> dict[str, object]:
        self.timeout = timeout
        return {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}], "model": "m"}

    def post_json_stream(self, url: str, headers: dict[str, str], payload: dict[str, object], timeout: float):
        self.timeout = timeout
        return iter(())


def test_openai_compatible_provider_accepts_timeout_seconds_alias() -> None:
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


def test_openai_compatible_provider_deprecates_legacy_timeout() -> None:
    with pytest.warns(DeprecationWarning, match="timeout_seconds"):
        provider = OpenAICompatibleProvider(
            api_key="key",
            base_url="https://example.test",
            model="model",
            timeout=7,
        )

    assert provider.timeout_seconds == 7


def test_openai_compatible_provider_defaults_to_timeout_seconds() -> None:
    provider = OpenAICompatibleProvider(
        api_key="key",
        base_url="https://example.test",
        model="model",
    )

    assert provider.timeout_seconds == 60


def test_urllib_transport_maps_socket_timeout_to_provider_timeout_error() -> None:
    transport = UrlLibJSONTransport()

    with pytest.raises(ProviderTimeoutError):
        transport._map_transport_error(URLError(socket.timeout("timed out")))


def test_injected_openai_and_anthropic_clients_receive_timeout_when_supported() -> None:
    assert OpenAIProvider(client=object(), model="m", timeout_seconds=5).timeout_seconds == 5
    assert AnthropicProvider(client=object(), model="m", timeout_seconds=6).timeout_seconds == 6
