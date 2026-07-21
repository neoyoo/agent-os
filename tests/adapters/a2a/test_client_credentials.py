from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

from agentos.adapters.a2a import client as client_module
from agentos.adapters.a2a.client import A2APushHttpClient
from tests.adapters.a2a.test_client import (
    PUBLIC_IP,
    SEND_GATE,
    STREAM_RESPONSE,
    FakeWriter,
    _install_network,
    _response,
)
from tests.planning._async import async_test


@pytest.mark.parametrize(
    ("scheme", "credentials", "token"),
    (
        (None, "secret", None),
        ("bad scheme", "secret", None),
        ("Bearer", "", None),
        ("Bearer", "secret\r\nInjected: yes", None),
        ("Bearer", "secret\x7f", None),
        ("Bearer", "secrét", None),
        (None, None, ""),
        (None, None, "has space"),
        (None, None, "secret\nInjected: yes"),
        (None, None, "secret\x7f"),
        (None, None, "secrét"),
        ("B", "x" * 8191, None),
        (None, None, "x" * 8193),
    ),
)
@async_test
async def test_credentials_fail_closed_before_dns(
    monkeypatch: pytest.MonkeyPatch,
    scheme: str | None,
    credentials: str | None,
    token: str | None,
) -> None:
    resolved = False

    async def resolve(_: str, __: int) -> tuple[str, ...]:
        nonlocal resolved
        resolved = True
        return (PUBLIC_IP,)

    monkeypatch.setattr(client_module, "_resolve_public_addresses", resolve)

    with pytest.raises(ValueError):
        await A2APushHttpClient().deliver(
            url="https://push.example.test/hook",
            stream_response=STREAM_RESPONSE,
            send_gate=SEND_GATE,
            authentication_scheme=scheme,
            credentials=credentials,
            token=token,
        )

    assert resolved is False


def _successful_connection(
    writer: FakeWriter,
) -> Callable[..., Awaitable[tuple[object, FakeWriter]]]:
    async def connect(**_: object) -> tuple[object, FakeWriter]:
        return _response(), writer

    return connect


@async_test
async def test_scheme_without_credentials_omits_authorization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer = FakeWriter()
    _install_network(
        monkeypatch,
        addresses={"push.example.test": (PUBLIC_IP,)},
        connect=_successful_connection(writer),
    )

    await A2APushHttpClient().deliver(
        url="https://push.example.test/hook",
        stream_response=STREAM_RESPONSE,
        send_gate=SEND_GATE,
        authentication_scheme="Bearer",
    )

    assert b"Authorization:" not in writer.written


@async_test
async def test_credential_header_hard_limits_are_inclusive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer = FakeWriter()
    _install_network(
        monkeypatch,
        addresses={"push.example.test": (PUBLIC_IP,)},
        connect=_successful_connection(writer),
    )
    credentials = "x" * 8190
    token = "y" * 8192

    await A2APushHttpClient().deliver(
        url="https://push.example.test/hook",
        stream_response=STREAM_RESPONSE,
        send_gate=SEND_GATE,
        authentication_scheme="B",
        credentials=credentials,
        token=token,
    )

    assert f"Authorization: B {credentials}\r\n".encode("ascii") in writer.written
    assert (
        f"X-A2A-Notification-Token: {token}\r\n".encode("ascii")
        in writer.written
    )
