from __future__ import annotations

from agentos.channels.a2a import (
    A2AAgentCapabilities,
    A2AAgentCard,
    A2AAgentExtension,
    A2AAgentInterface,
    A2AAgentProvider,
    A2AAgentSkill,
    A2ACardSignature,
    A2ACardResolver,
    A2ACardTrustError,
    HmacA2ACardSigner,
    HmacA2ACardVerifier,
    JwksA2ACardTrustStore,
    RotatingA2ACardTrustKey,
    RotatingA2ACardTrustStore,
    RotatingHmacA2ACardSigner,
    StaticA2ACardTrustStore,
    a2a_card_from_agent_card,
    a2a_card_from_dict,
    a2a_card_to_dict,
)
from agentos.multi import AgentCard


def _base64url(value: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def test_a2a_agent_card_defaults_to_current_protocol_version() -> None:
    card = A2AAgentCard(
        name="Research Agent",
        description="Researches documents.",
        url="https://agents.example/a2a",
        version="1.0.0",
    )

    payload = a2a_card_to_dict(card)

    assert card.protocol_version == "1.0"
    assert payload["protocolVersion"] == "1.0"
    assert a2a_card_from_dict(payload).protocol_version == "1.0"


def test_a2a_agent_card_preserves_explicit_legacy_protocol_version() -> None:
    card = A2AAgentCard(
        name="Legacy Agent",
        description="Uses the legacy A2A binding.",
        url="https://agents.example/a2a",
        version="1.0.0",
        protocol_version="0.3.0",
    )

    payload = a2a_card_to_dict(card)

    assert payload["protocolVersion"] == "0.3.0"
    assert a2a_card_from_dict(payload).protocol_version == "0.3.0"


def test_a2a_card_from_dict_defaults_missing_protocol_to_current_version() -> None:
    card = a2a_card_from_dict(
        {
            "name": "No Protocol Field",
            "description": "Relies on SDK default.",
            "url": "https://agents.example/a2a",
            "version": "1.0.0",
            "skills": [],
        },
    )

    assert card.protocol_version == "1.0"


def test_a2a_agent_card_serializes_protocol_keys() -> None:
    card = A2AAgentCard(
        name="Research Agent",
        description="Researches documents.",
        url="https://agents.example/a2a",
        version="1.0.0",
        provider=A2AAgentProvider(
            organization="Example Inc.",
            url="https://example.com",
        ),
        capabilities=A2AAgentCapabilities(
            streaming=True,
            push_notifications=False,
            state_transition_history=True,
        ),
        skills=(
            A2AAgentSkill(
                id="research",
                name="Research",
                description="Find and summarize sources.",
                tags=("search", "summarize"),
                examples=("Find recent papers about agent protocols.",),
            ),
        ),
        security_schemes={
            "apiKey": {"type": "apiKey", "in": "header", "name": "x-api-key"},
        },
        security=({"apiKey": ()},),
        signatures=(
            A2ACardSignature(
                protected="eyJhbGciOiJIUzI1NiIsImtpZCI6ImtleS0xIn0",
                signature="signed",
                header={"jku": "https://example.com/jwks.json"},
            ),
        ),
    )

    payload = a2a_card_to_dict(card)

    assert payload["protocolVersion"] == "1.0"
    assert payload["defaultInputModes"] == ["text/plain"]
    assert payload["defaultOutputModes"] == ["text/plain"]
    assert payload["capabilities"] == {
        "streaming": True,
        "pushNotifications": False,
        "stateTransitionHistory": True,
    }
    assert payload["provider"] == {
        "organization": "Example Inc.",
        "url": "https://example.com",
    }
    assert payload["skills"] == [
        {
            "id": "research",
            "name": "Research",
            "description": "Find and summarize sources.",
            "tags": ["search", "summarize"],
            "examples": ["Find recent papers about agent protocols."],
        },
    ]
    assert payload["signatures"] == [
        {
            "protected": "eyJhbGciOiJIUzI1NiIsImtpZCI6ImtleS0xIn0",
            "signature": "signed",
            "header": {"jku": "https://example.com/jwks.json"},
        },
    ]
    assert a2a_card_from_dict(payload) == card


def test_a2a_agent_card_serializes_discovery_metadata() -> None:
    card = A2AAgentCard(
        name="Research Agent",
        description="Researches documents.",
        version="1.0.0",
        supported_interfaces=(
            A2AAgentInterface(
                transport="JSONRPC",
                url="https://agents.example/a2a/jsonrpc",
            ),
            A2AAgentInterface(
                transport="GRPC",
                url="https://agents.example/a2a/grpc",
            ),
        ),
        capabilities=A2AAgentCapabilities(
            streaming=True,
            push_notifications=True,
            state_transition_history=True,
            extensions=(
                A2AAgentExtension(
                    uri="https://agentos.dev/extensions/team-discussion",
                    description="Team discussion event projection.",
                    required=False,
                    params={"eventKinds": ["message.created"]},
                ),
            ),
        ),
        skills=(
            A2AAgentSkill(
                id="research",
                name="Research",
                description="Find and summarize sources.",
                tags=("search", "summarize"),
                examples=("Find recent papers about agent protocols.",),
                input_modes=("text/plain", "application/pdf"),
                output_modes=("text/plain", "application/json"),
                security=({"apiKey": ()},),
            ),
        ),
        default_input_modes=("text/plain", "application/pdf"),
        default_output_modes=("text/plain", "application/json"),
    )

    payload = a2a_card_to_dict(card)

    assert payload["url"] == "https://agents.example/a2a/jsonrpc"
    assert payload["preferredTransport"] == "JSONRPC"
    assert payload["supportedInterfaces"] == [
        {
            "protocolBinding": "JSONRPC",
            "protocolVersion": "1.0",
            "url": "https://agents.example/a2a/jsonrpc",
        },
        {
            "protocolBinding": "GRPC",
            "protocolVersion": "1.0",
            "url": "https://agents.example/a2a/grpc",
        },
    ]
    assert payload["capabilities"]["extensions"] == [
        {
            "uri": "https://agentos.dev/extensions/team-discussion",
            "description": "Team discussion event projection.",
            "required": False,
            "params": {"eventKinds": ["message.created"]},
        },
    ]
    assert payload["skills"][0]["inputModes"] == [
        "text/plain",
        "application/pdf",
    ]
    assert payload["skills"][0]["outputModes"] == [
        "text/plain",
        "application/json",
    ]
    assert payload["skills"][0]["security"] == [{"apiKey": []}]
    assert a2a_card_from_dict(payload) == card


def test_a2a_agent_card_serializes_v1_supported_interfaces() -> None:
    card = A2AAgentCard(
        name="Tenant Agent",
        description="Publishes an official A2A v1 JSON-RPC interface.",
        version="1.0.0",
        supported_interfaces=(
            A2AAgentInterface(
                protocol_binding="JSONRPC",
                protocol_version="1.0",
                url="https://agents.example/a2a/jsonrpc",
                tenant="tenant-a",
            ),
        ),
    )

    payload = a2a_card_to_dict(card)

    assert payload["supportedInterfaces"] == [
        {
            "protocolBinding": "JSONRPC",
            "protocolVersion": "1.0",
            "url": "https://agents.example/a2a/jsonrpc",
            "tenant": "tenant-a",
        },
    ]
    assert "transport" not in payload["supportedInterfaces"][0]
    assert a2a_card_from_dict(payload) == card


def test_a2a_card_from_dict_accepts_interface_only_endpoint() -> None:
    card = a2a_card_from_dict(
        {
            "protocolVersion": "0.3.0",
            "name": "Interface Only",
            "description": "Publishes endpoint through supportedInterfaces.",
            "version": "1.0.0",
            "preferredTransport": "JSONRPC",
            "supportedInterfaces": [
                {
                    "protocolBinding": "JSONRPC",
                    "protocolVersion": "0.3",
                    "url": "https://agents.example/a2a",
                },
            ],
            "skills": [],
        },
    )

    assert card.url == "https://agents.example/a2a"
    assert card.supported_interfaces == (
        A2AAgentInterface(
            protocol_binding="JSONRPC",
            protocol_version="0.3",
            url="https://agents.example/a2a",
        ),
    )
    assert a2a_card_to_dict(card)["supportedInterfaces"] == [
        {
            "protocolBinding": "JSONRPC",
            "protocolVersion": "0.3",
            "url": "https://agents.example/a2a",
        },
    ]


def test_a2a_card_from_dict_accepts_legacy_transport_interface_alias() -> None:
    card = a2a_card_from_dict(
        {
            "protocolVersion": "1.0.0",
            "name": "Legacy Interface Alias",
            "description": "Uses the old transport field shape.",
            "version": "1.0.0",
            "supportedInterfaces": [
                {
                    "transport": "JSONRPC",
                    "url": "https://agents.example/a2a",
                },
            ],
            "skills": [],
        },
    )

    assert card.supported_interfaces == (
        A2AAgentInterface(
            protocol_binding="JSONRPC",
            protocol_version="1.0",
            url="https://agents.example/a2a",
        ),
    )
    assert a2a_card_to_dict(card)["supportedInterfaces"] == [
        {
            "protocolBinding": "JSONRPC",
            "protocolVersion": "1.0",
            "url": "https://agents.example/a2a",
        },
    ]


def test_a2a_card_from_internal_agent_card_maps_capabilities_to_skills() -> None:
    internal = AgentCard(
        agent_id="researcher",
        name="Researcher",
        description="Research specialist.",
        capabilities=("research", "summarize"),
        version="2.0.0",
        endpoint="https://agents.example/researcher",
    )

    card = a2a_card_from_agent_card(internal)

    assert card.name == "Researcher"
    assert card.url == "https://agents.example/researcher"
    assert card.version == "2.0.0"
    assert [skill.id for skill in card.skills] == ["research", "summarize"]


def test_a2a_card_resolver_loads_direct_and_well_known_cards() -> None:
    direct = A2AAgentCard(
        name="Direct",
        description="Direct card.",
        url="https://direct.example/a2a",
        version="1.0.0",
        skills=(A2AAgentSkill(id="direct", name="Direct", description="Direct."),),
    )
    remote = A2AAgentCard(
        name="Remote",
        description="Remote card.",
        url="https://remote.example/a2a",
        version="1.0.0",
        skills=(A2AAgentSkill(id="search", name="Search", description="Search."),),
    )

    class FakeTransport:
        def get_json(self, url: str, timeout_seconds: float) -> dict[str, object]:
            assert url == "https://remote.example/.well-known/agent-card.json"
            return a2a_card_to_dict(remote)

        def post_json(self, *args: object, **kwargs: object) -> dict[str, object]:
            raise AssertionError("not used")

    resolver = A2ACardResolver(
        cards=(direct,),
        well_known_urls={"Remote": "https://remote.example"},
        transport=FakeTransport(),
    )

    assert resolver.resolve("Direct") == direct
    assert resolver.resolve("Remote") == remote
    assert resolver.discover(("search",)) == [remote]


def test_hmac_a2a_card_signer_adds_jws_signature_and_verifier_trusts_it() -> None:
    card = A2AAgentCard(
        name="Signed",
        description="Signed card.",
        url="https://signed.example/a2a",
        version="1.0.0",
        skills=(A2AAgentSkill(id="search", name="Search", description="Search."),),
    )
    signer = HmacA2ACardSigner(key_id="key-1", secret=b"test-secret")
    verifier = HmacA2ACardVerifier(
        StaticA2ACardTrustStore({"key-1": b"test-secret"}),
    )

    signed = signer.sign(card)

    assert signed.signatures
    assert signed.signatures[0].header == {}
    verifier.verify(signed)


def test_hmac_a2a_card_verifier_rejects_tampered_card() -> None:
    card = A2AAgentCard(
        name="Signed",
        description="Signed card.",
        url="https://signed.example/a2a",
        version="1.0.0",
    )
    signer = HmacA2ACardSigner(key_id="key-1", secret=b"test-secret")
    verifier = HmacA2ACardVerifier(
        StaticA2ACardTrustStore({"key-1": b"test-secret"}),
    )

    signed = signer.sign(card)
    tampered = A2AAgentCard(
        name=signed.name,
        description="Changed after signing.",
        url=signed.url,
        version=signed.version,
        signatures=signed.signatures,
    )

    try:
        verifier.verify(tampered)
    except A2ACardTrustError as error:
        assert "signature verification failed" in str(error)
    else:
        raise AssertionError("tampered card should not verify")


def test_rotating_a2a_card_trust_store_supports_overlap_and_expiry() -> None:
    now = 100.0
    store = RotatingA2ACardTrustStore(
        keys=(
            RotatingA2ACardTrustKey(
                key_id="old-key",
                secret=b"old-secret",
                not_before=0.0,
                not_after=110.0,
            ),
            RotatingA2ACardTrustKey(
                key_id="new-key",
                secret=b"new-secret",
                not_before=90.0,
            ),
        ),
        current_key_id="new-key",
        clock=lambda: now,
    )

    assert store.secret_for_key_id("old-key") == b"old-secret"
    assert store.secret_for_key_id("new-key") == b"new-secret"
    assert store.signing_key() == (
        "new-key",
        b"new-secret",
    )

    expired = RotatingA2ACardTrustStore(
        keys=(
            RotatingA2ACardTrustKey(
                key_id="old-key",
                secret=b"old-secret",
                not_before=0.0,
                not_after=50.0,
            ),
        ),
        current_key_id="old-key",
        clock=lambda: now,
    )

    assert expired.secret_for_key_id("old-key") is None


def test_rotating_a2a_card_trust_store_rejects_revoked_keys() -> None:
    store = RotatingA2ACardTrustStore(
        keys=(
            RotatingA2ACardTrustKey(
                key_id="revoked-key",
                secret=b"revoked-secret",
                revoked=True,
            ),
        ),
        current_key_id="revoked-key",
        clock=lambda: 10.0,
    )

    assert store.secret_for_key_id("revoked-key") is None

    try:
        store.signing_key()
    except A2ACardTrustError as error:
        assert "current card signing key is not active" in str(error)
    else:
        raise AssertionError("revoked signing key should not be active")


def test_rotating_hmac_a2a_card_signer_uses_current_key() -> None:
    card = A2AAgentCard(
        name="Signed",
        description="Signed card.",
        url="https://signed.example/a2a",
        version="1.0.0",
    )
    store = RotatingA2ACardTrustStore(
        keys=(
            RotatingA2ACardTrustKey(
                key_id="old-key",
                secret=b"old-secret",
                not_before=0.0,
                not_after=110.0,
            ),
            RotatingA2ACardTrustKey(
                key_id="new-key",
                secret=b"new-secret",
                not_before=90.0,
            ),
        ),
        current_key_id="new-key",
        clock=lambda: 100.0,
    )
    signer = RotatingHmacA2ACardSigner(store)
    verifier = HmacA2ACardVerifier(store)

    signed = signer.sign(card)

    assert signed.signatures
    verifier.verify(signed)

    old_only_verifier = HmacA2ACardVerifier(
        StaticA2ACardTrustStore({"old-key": b"old-secret"}),
    )
    try:
        old_only_verifier.verify(signed)
    except A2ACardTrustError as error:
        assert "untrusted card signature key id" in str(error)
    else:
        raise AssertionError("current signing key should be new-key")


def test_a2a_card_resolver_rejects_untrusted_remote_card() -> None:
    remote = A2AAgentCard(
        name="Remote",
        description="Unsigned remote card.",
        url="https://remote.example/a2a",
        version="1.0.0",
        skills=(A2AAgentSkill(id="search", name="Search", description="Search."),),
    )

    class FakeTransport:
        def get_json(
            self,
            url: str,
            timeout_seconds: float,
            *,
            headers: dict[str, str] | None = None,
        ) -> dict[str, object]:
            assert headers is None
            assert url == "https://remote.example/.well-known/agent-card.json"
            return a2a_card_to_dict(remote)

        def post_json(self, *args: object, **kwargs: object) -> dict[str, object]:
            raise AssertionError("not used")

    resolver = A2ACardResolver(
        well_known_urls={"Remote": "https://remote.example"},
        transport=FakeTransport(),
        card_verifier=HmacA2ACardVerifier(
            StaticA2ACardTrustStore({"key-1": b"test-secret"}),
        ),
    )

    try:
        resolver.resolve("Remote")
    except A2ACardTrustError as error:
        assert "no card signatures" in str(error)
    else:
        raise AssertionError("untrusted remote card should be rejected")


def test_jwks_a2a_card_trust_store_fetches_oct_key_for_hmac_verifier() -> None:
    card = A2AAgentCard(
        name="Signed",
        description="Signed card.",
        url="https://signed.example/a2a",
        version="1.0.0",
    )
    signed = HmacA2ACardSigner(key_id="key-1", secret=b"jwks-secret").sign(card)
    calls: list[str] = []

    class FakeTransport:
        def get_json(
            self,
            url: str,
            timeout_seconds: float,
            *,
            headers: dict[str, str] | None = None,
        ) -> dict[str, object]:
            assert headers is None
            assert timeout_seconds == 3.0
            calls.append(url)
            return {
                "keys": [
                    {
                        "kty": "oct",
                        "kid": "key-1",
                        "use": "sig",
                        "alg": "HS256",
                        "k": _base64url(b"jwks-secret"),
                    },
                ],
            }

        def post_json(self, *args: object, **kwargs: object) -> dict[str, object]:
            raise AssertionError("not used")

    trust_store = JwksA2ACardTrustStore(
        jwks_urls=("https://issuer.example/.well-known/jwks.json",),
        transport=FakeTransport(),
        timeout_seconds=3.0,
        cache_ttl_seconds=60.0,
        clock=lambda: 10.0,
    )
    verifier = HmacA2ACardVerifier(trust_store)

    verifier.verify(signed)
    verifier.verify(signed)

    assert calls == ["https://issuer.example/.well-known/jwks.json"]


def test_jwks_a2a_card_trust_store_requires_https_urls() -> None:
    try:
        JwksA2ACardTrustStore(jwks_urls=("http://issuer.example/jwks.json",))
    except ValueError as error:
        assert "https" in str(error).lower()
    else:
        raise AssertionError("JWKS trust URLs must require HTTPS")


def test_jwks_a2a_card_trust_store_ignores_untrusted_or_non_signing_keys() -> None:
    class FakeTransport:
        def get_json(
            self,
            url: str,
            timeout_seconds: float,
            *,
            headers: dict[str, str] | None = None,
        ) -> dict[str, object]:
            return {
                "keys": [
                    {
                        "kty": "oct",
                        "kid": "key-1",
                        "use": "enc",
                        "alg": "HS256",
                        "k": _base64url(b"enc-secret"),
                    },
                    {
                        "kty": "oct",
                        "kid": "key-2",
                        "use": "sig",
                        "alg": "HS512",
                        "k": _base64url(b"wrong-alg-secret"),
                    },
                    {
                        "kty": "oct",
                        "kid": "key-3",
                        "use": "sig",
                        "alg": "HS256",
                        "k": _base64url(b"disallowed-secret"),
                    },
                ],
            }

        def post_json(self, *args: object, **kwargs: object) -> dict[str, object]:
            raise AssertionError("not used")

    trust_store = JwksA2ACardTrustStore(
        jwks_urls=("https://issuer.example/jwks.json",),
        allowed_key_ids=("key-1", "key-2"),
        transport=FakeTransport(),
    )

    assert trust_store.secret_for_key_id("key-1") is None
    assert trust_store.secret_for_key_id("key-2") is None
    assert trust_store.secret_for_key_id("key-3") is None


def test_a2a_card_resolver_verifies_remote_card_with_jwks_trust_store() -> None:
    remote = A2AAgentCard(
        name="Remote",
        description="Remote signed card.",
        url="https://remote.example/a2a",
        version="1.0.0",
        skills=(A2AAgentSkill(id="search", name="Search", description="Search."),),
    )
    signed = HmacA2ACardSigner(key_id="remote-key", secret=b"remote-secret").sign(
        remote,
    )

    class FakeTransport:
        def get_json(
            self,
            url: str,
            timeout_seconds: float,
            *,
            headers: dict[str, str] | None = None,
        ) -> dict[str, object]:
            if url == "https://remote.example/.well-known/agent-card.json":
                return a2a_card_to_dict(signed)
            if url == "https://issuer.example/jwks.json":
                return {
                    "keys": [
                        {
                            "kty": "oct",
                            "kid": "remote-key",
                            "use": "sig",
                            "alg": "HS256",
                            "k": _base64url(b"remote-secret"),
                        },
                    ],
                }
            raise AssertionError(f"unexpected URL: {url}")

        def post_json(self, *args: object, **kwargs: object) -> dict[str, object]:
            raise AssertionError("not used")

    transport = FakeTransport()
    resolver = A2ACardResolver(
        well_known_urls={"Remote": "https://remote.example"},
        transport=transport,
        card_verifier=HmacA2ACardVerifier(
            JwksA2ACardTrustStore(
                jwks_urls=("https://issuer.example/jwks.json",),
                allowed_key_ids=("remote-key",),
                transport=transport,
            ),
        ),
    )

    assert resolver.resolve("Remote") == signed
