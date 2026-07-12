from __future__ import annotations

import base64
import json

import pytest

crypto = pytest.importorskip("cryptography")
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa


class FakeClock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class FakeJwksTransport:
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
            raise AssertionError("unexpected JWKS fetch")
        return self.responses.pop(0)


class StaticDiscoveryProvider:
    def __init__(self, jwks_uri: str) -> None:
        self._jwks_uri = jwks_uri

    def jwks_uri(self) -> str:
        return self._jwks_uri


def test_jwks_a2a_jwt_verifier_accepts_rs256_token_from_jwks() -> None:
    from agentos.channels.a2a import JwksA2AJwtVerifier

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = rsa_public_jwk(private_key, key_id="rsa-1")
    token = rs256_jwt(
        {
            "iss": "https://issuer.example",
            "aud": ["agentos-a2a"],
            "sub": "peer-subject",
            "azp": "researcher",
            "iat": 100.0,
            "nbf": 100.0,
            "exp": 200.0,
        },
        private_key=private_key,
        key_id="rsa-1",
    )
    transport = FakeJwksTransport([{"keys": [jwk]}])
    verifier = JwksA2AJwtVerifier(
        jwks_urls=("https://issuer.example/jwks.json",),
        transport=transport,
        timeout_seconds=3.0,
        cache_ttl_seconds=60.0,
        clock=lambda: 150.0,
    )

    claims = verifier.verify(token)

    assert claims.issuer == "https://issuer.example"
    assert claims.subject == "peer-subject"
    assert claims.audience == ("agentos-a2a",)
    assert claims.peer_id == "researcher"
    assert transport.calls == [
        ("https://issuer.example/jwks.json", 3.0, None),
    ]
    assert token not in repr(verifier)


def test_jwks_a2a_jwt_verifier_uses_oidc_discovery_provider_jwks_uri() -> None:
    from agentos.channels.a2a import JwksA2AJwtVerifier

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = rs256_jwt(
        {
            "iss": "https://issuer.example",
            "aud": "agentos-a2a",
            "sub": "peer-subject",
            "exp": 200.0,
        },
        private_key=private_key,
        key_id="rsa-oidc",
    )
    transport = FakeJwksTransport(
        [{"keys": [rsa_public_jwk(private_key, key_id="rsa-oidc")]}],
    )
    verifier = JwksA2AJwtVerifier(
        discovery_provider=StaticDiscoveryProvider(
            "https://issuer.example/.well-known/jwks.json",
        ),
        transport=transport,
        clock=lambda: 150.0,
    )

    assert verifier.verify(token).subject == "peer-subject"
    assert transport.calls[0][0] == "https://issuer.example/.well-known/jwks.json"


def test_jwks_a2a_jwt_verifier_caches_and_refreshes_jwks_keys() -> None:
    from agentos.channels.a2a import A2AInboundAuthError, JwksA2AJwtVerifier

    first_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    second_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    first_token = rs256_jwt(
        {"iss": "https://issuer.example", "aud": "agentos-a2a", "sub": "first"},
        private_key=first_key,
        key_id="rsa-1",
    )
    second_token = rs256_jwt(
        {"iss": "https://issuer.example", "aud": "agentos-a2a", "sub": "second"},
        private_key=second_key,
        key_id="rsa-2",
    )
    clock = FakeClock(10.0)
    transport = FakeJwksTransport(
        [
            {"keys": [rsa_public_jwk(first_key, key_id="rsa-1")]},
            {"keys": [rsa_public_jwk(second_key, key_id="rsa-2")]},
        ],
    )
    verifier = JwksA2AJwtVerifier(
        jwks_urls=("https://issuer.example/jwks.json",),
        transport=transport,
        cache_ttl_seconds=5.0,
        clock=clock,
    )

    assert verifier.verify(first_token).subject == "first"
    clock.now = 14.0
    with pytest.raises(A2AInboundAuthError, match="unauthorized peer"):
        verifier.verify(second_token)
    clock.now = 15.1
    assert verifier.verify(second_token).subject == "second"
    assert len(transport.calls) == 2


@pytest.mark.parametrize(
    "bad_header",
    [
        {"alg": "HS256", "kid": "rsa-1", "typ": "JWT"},
        {"alg": "none", "kid": "rsa-1", "typ": "JWT"},
        {"alg": "RS256", "typ": "JWT"},
    ],
)
def test_jwks_a2a_jwt_verifier_rejects_unsupported_alg_or_missing_kid(
    bad_header: dict[str, object],
) -> None:
    from agentos.channels.a2a import A2AInboundAuthError, JwksA2AJwtVerifier

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = rs256_jwt(
        {"iss": "https://issuer.example", "aud": "agentos-a2a", "sub": "peer"},
        private_key=private_key,
        key_id="rsa-1",
        header=bad_header,
    )
    verifier = JwksA2AJwtVerifier(
        jwks_urls=("https://issuer.example/jwks.json",),
        transport=FakeJwksTransport(
            [{"keys": [rsa_public_jwk(private_key, key_id="rsa-1")]}],
        ),
    )

    with pytest.raises(A2AInboundAuthError, match="unauthorized peer"):
        verifier.verify(token)


def test_jwks_a2a_jwt_verifier_rejects_unknown_or_disallowed_keys() -> None:
    from agentos.channels.a2a import A2AInboundAuthError, JwksA2AJwtVerifier

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = rs256_jwt(
        {"iss": "https://issuer.example", "aud": "agentos-a2a", "sub": "peer"},
        private_key=private_key,
        key_id="rsa-blocked",
    )
    verifier = JwksA2AJwtVerifier(
        jwks_urls=("https://issuer.example/jwks.json",),
        allowed_key_ids=("rsa-allowed",),
        transport=FakeJwksTransport(
            [{"keys": [rsa_public_jwk(private_key, key_id="rsa-blocked")]}],
        ),
    )

    with pytest.raises(A2AInboundAuthError, match="unauthorized peer"):
        verifier.verify(token)


def test_jwks_a2a_jwt_verifier_rejects_wrong_signature() -> None:
    from agentos.channels.a2a import A2AInboundAuthError, JwksA2AJwtVerifier

    signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    trusted_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = rs256_jwt(
        {"iss": "https://issuer.example", "aud": "agentos-a2a", "sub": "peer"},
        private_key=signing_key,
        key_id="rsa-1",
    )
    verifier = JwksA2AJwtVerifier(
        jwks_urls=("https://issuer.example/jwks.json",),
        transport=FakeJwksTransport(
            [{"keys": [rsa_public_jwk(trusted_key, key_id="rsa-1")]}],
        ),
    )

    with pytest.raises(A2AInboundAuthError, match="unauthorized peer"):
        verifier.verify(token)


def test_jwks_a2a_jwt_verifier_ignores_non_rsa_or_non_signing_keys() -> None:
    from agentos.channels.a2a import A2AInboundAuthError, JwksA2AJwtVerifier

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = rs256_jwt(
        {"iss": "https://issuer.example", "aud": "agentos-a2a", "sub": "peer"},
        private_key=private_key,
        key_id="rsa-1",
    )
    verifier = JwksA2AJwtVerifier(
        jwks_urls=("https://issuer.example/jwks.json",),
        transport=FakeJwksTransport(
            [
                {
                    "keys": [
                        {"kty": "oct", "kid": "rsa-1", "k": _base64url(b"secret")},
                        {
                            **rsa_public_jwk(private_key, key_id="rsa-1"),
                            "use": "enc",
                        },
                    ],
                },
            ],
        ),
    )

    with pytest.raises(A2AInboundAuthError, match="unauthorized peer"):
        verifier.verify(token)


def rsa_public_jwk(
    private_key: rsa.RSAPrivateKey,
    *,
    key_id: str,
) -> dict[str, object]:
    public_numbers = private_key.public_key().public_numbers()
    return {
        "kty": "RSA",
        "kid": key_id,
        "use": "sig",
        "alg": "RS256",
        "n": _base64url_int(public_numbers.n),
        "e": _base64url_int(public_numbers.e),
    }


def rs256_jwt(
    claims: dict[str, object],
    *,
    private_key: rsa.RSAPrivateKey,
    key_id: str,
    header: dict[str, object] | None = None,
) -> str:
    jwt_header = header or {"alg": "RS256", "kid": key_id, "typ": "JWT"}
    signing_input = ".".join(
        [
            _base64url_json(jwt_header),
            _base64url_json(claims),
        ],
    )
    signature = private_key.sign(
        signing_input.encode("ascii"),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    return f"{signing_input}.{_base64url(signature)}"


def _base64url_json(payload: dict[str, object]) -> str:
    return _base64url(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"),
    )


def _base64url_int(value: int) -> str:
    length = max(1, (value.bit_length() + 7) // 8)
    return _base64url(value.to_bytes(length, "big"))


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")
