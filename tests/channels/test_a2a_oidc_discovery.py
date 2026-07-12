from __future__ import annotations

import pytest


class FakeClock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class FakeDiscoveryTransport:
    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, float, dict[str, str] | None]] = []

    def get_json(
        self,
        url: str,
        timeout_seconds: float,
        *,
        headers: dict[str, str] | None = None,
    ) -> object:
        self.calls.append((url, timeout_seconds, headers))
        if not self.responses:
            raise AssertionError("unexpected discovery fetch")
        return self.responses.pop(0)


def test_oidc_discovery_provider_fetches_and_caches_metadata() -> None:
    from agentos.channels.a2a import (
        OidcDiscoveryMetadata,
        OidcDiscoveryMetadataProvider,
    )

    clock = FakeClock(10.0)
    transport = FakeDiscoveryTransport(
        [
            {
                "issuer": "https://issuer.example/tenant",
                "jwks_uri": "https://issuer.example/tenant/jwks.json",
                "authorization_endpoint": "https://issuer.example/auth",
            },
        ],
    )
    provider = OidcDiscoveryMetadataProvider(
        issuer="https://issuer.example/tenant/",
        transport=transport,
        timeout_seconds=3.0,
        cache_ttl_seconds=60.0,
        clock=clock,
    )

    first = provider.metadata()
    second = provider.metadata()

    assert first == OidcDiscoveryMetadata(
        issuer="https://issuer.example/tenant",
        jwks_uri="https://issuer.example/tenant/jwks.json",
        raw={
            "issuer": "https://issuer.example/tenant",
            "jwks_uri": "https://issuer.example/tenant/jwks.json",
            "authorization_endpoint": "https://issuer.example/auth",
        },
    )
    assert second is first
    assert provider.jwks_uri() == "https://issuer.example/tenant/jwks.json"
    assert transport.calls == [
        (
            "https://issuer.example/tenant/.well-known/openid-configuration",
            3.0,
            None,
        ),
    ]


def test_oidc_discovery_provider_refreshes_after_ttl() -> None:
    from agentos.channels.a2a import OidcDiscoveryMetadataProvider

    clock = FakeClock(10.0)
    transport = FakeDiscoveryTransport(
        [
            {
                "issuer": "https://issuer.example",
                "jwks_uri": "https://issuer.example/jwks-v1.json",
            },
            {
                "issuer": "https://issuer.example",
                "jwks_uri": "https://issuer.example/jwks-v2.json",
            },
        ],
    )
    provider = OidcDiscoveryMetadataProvider(
        issuer="https://issuer.example",
        transport=transport,
        cache_ttl_seconds=5.0,
        clock=clock,
    )

    assert provider.jwks_uri() == "https://issuer.example/jwks-v1.json"
    clock.now = 14.0
    assert provider.jwks_uri() == "https://issuer.example/jwks-v1.json"
    clock.now = 15.1
    assert provider.jwks_uri() == "https://issuer.example/jwks-v2.json"
    assert len(transport.calls) == 2


def test_oidc_discovery_provider_rejects_mismatched_issuer() -> None:
    from agentos.channels.a2a import (
        A2AOidcDiscoveryError,
        OidcDiscoveryMetadataProvider,
    )

    provider = OidcDiscoveryMetadataProvider(
        issuer="https://issuer.example",
        transport=FakeDiscoveryTransport(
            [
                {
                    "issuer": "https://other-issuer.example",
                    "jwks_uri": "https://issuer.example/jwks.json",
                },
            ],
        ),
    )

    with pytest.raises(A2AOidcDiscoveryError, match="issuer"):
        provider.metadata()


def test_oidc_discovery_provider_requires_https_issuer_and_jwks_uri() -> None:
    from agentos.channels.a2a import (
        A2AOidcDiscoveryError,
        OidcDiscoveryMetadataProvider,
    )

    with pytest.raises(ValueError, match="https"):
        OidcDiscoveryMetadataProvider(issuer="http://issuer.example")

    provider = OidcDiscoveryMetadataProvider(
        issuer="https://issuer.example",
        transport=FakeDiscoveryTransport(
            [
                {
                    "issuer": "https://issuer.example",
                    "jwks_uri": "http://issuer.example/jwks.json",
                },
            ],
        ),
    )

    with pytest.raises(A2AOidcDiscoveryError, match="jwks_uri"):
        provider.metadata()


def test_oidc_discovery_provider_rejects_non_object_payload() -> None:
    from agentos.channels.a2a import (
        A2AOidcDiscoveryError,
        OidcDiscoveryMetadataProvider,
    )

    provider = OidcDiscoveryMetadataProvider(
        issuer="https://issuer.example",
        transport=FakeDiscoveryTransport([["not", "object"]]),
    )

    with pytest.raises(A2AOidcDiscoveryError, match="object"):
        provider.metadata()
