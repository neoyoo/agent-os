from __future__ import annotations

import pytest

from agentos.adapters.a2a import client as client_module
from agentos.adapters.a2a.client import A2APushHttpClient, A2APushSecurityError
from tests.planning._async import async_test


PUBLIC_IP = "93.184.216.34"
STREAM_RESPONSE = b'{"statusUpdate":{"taskId":"run_1"}}'


@pytest.mark.parametrize(
    "url",
    (
        "http://push.example.test/hook",
        "https://user:secret@push.example.test/hook",
        "https://127.0.0.1/hook",
        "https://[::1]/hook",
        "https://localhost/hook",
        "https://service.localhost/hook",
        "https://" + chr(0xD800) + ".example/hook",
    ),
)
@async_test
async def test_delivery_rejects_unsafe_url_before_dns(
    monkeypatch: pytest.MonkeyPatch,
    url: str,
) -> None:
    resolved = False

    async def resolve(_: str, __: int) -> tuple[str, ...]:
        nonlocal resolved
        resolved = True
        return (PUBLIC_IP,)

    monkeypatch.setattr(client_module, "_resolve_public_addresses", resolve)
    with pytest.raises(A2APushSecurityError):
        await A2APushHttpClient().deliver(
            url=url,
            stream_response=STREAM_RESPONSE,
        )
    assert resolved is False


@pytest.mark.parametrize("timeout", (float("nan"), float("inf")))
def test_client_rejects_unbounded_timeout(timeout: float) -> None:
    with pytest.raises(ValueError, match="connect_timeout"):
        A2APushHttpClient(connect_timeout=timeout)


@async_test
async def test_request_and_authentication_limits_fail_before_dns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved = False

    async def resolve(_: str, __: int) -> tuple[str, ...]:
        nonlocal resolved
        resolved = True
        return (PUBLIC_IP,)

    monkeypatch.setattr(client_module, "_resolve_public_addresses", resolve)
    client = A2APushHttpClient()

    with pytest.raises(ValueError, match="StreamResponse"):
        await client.deliver(
            url="https://push.example.test/hook",
            stream_response=b"x" * (1024 * 1024 + 1),
        )
    with pytest.raises(ValueError, match="authentication"):
        await client.deliver(
            url="https://push.example.test/hook",
            stream_response=STREAM_RESPONSE,
            authentication_scheme="Bearer",
            credentials="secret\r\nInjected: yes",
        )
    assert resolved is False
