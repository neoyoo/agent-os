from __future__ import annotations

import pytest


class RecordingTransport:
    def __init__(self, response: dict[str, object] | None = None) -> None:
        self.response = response or {"result": {}}
        self.calls: list[tuple[str, str]] = []

    def get_json(
        self,
        url: str,
        timeout_seconds: float,
        *,
        headers: dict[str, str] | None = None,
    ) -> dict[str, object]:
        self.calls.append(("GET", url))
        return self.response

    def post_json(
        self,
        url: str,
        payload: dict[str, object],
        timeout_seconds: float,
        *,
        headers: dict[str, str] | None = None,
    ) -> dict[str, object]:
        self.calls.append(("POST", url))
        return self.response

    def delete_json(
        self,
        url: str,
        timeout_seconds: float,
        *,
        headers: dict[str, str] | None = None,
    ) -> dict[str, object]:
        self.calls.append(("DELETE", url))
        return self.response


class StaticDiscoveryProvider:
    def __init__(self, jwks_uri: str) -> None:
        self._jwks_uri = jwks_uri

    def jwks_uri(self) -> str:
        return self._jwks_uri


def test_public_https_a2a_egress_url_policy_rejects_non_public_targets() -> None:
    from agentos.channels.a2a import (
        A2AEgressPolicyError,
        PublicHttpsA2AEgressUrlPolicy,
    )

    policy = PublicHttpsA2AEgressUrlPolicy()

    policy.validate_url("https://issuer.example/.well-known/jwks.json")
    with pytest.raises(A2AEgressPolicyError, match="https"):
        policy.validate_url("http://issuer.example/.well-known/jwks.json")
    with pytest.raises(A2AEgressPolicyError, match="public"):
        policy.validate_url("https://localhost/.well-known/jwks.json")
    with pytest.raises(A2AEgressPolicyError, match="public"):
        policy.validate_url("https://127.0.0.1/.well-known/jwks.json")
    with pytest.raises(A2AEgressPolicyError, match="public"):
        policy.validate_url("https://10.0.0.10/.well-known/jwks.json")
    with pytest.raises(A2AEgressPolicyError, match="public"):
        policy.validate_url("https://[::1]/.well-known/jwks.json")


def test_host_allow_list_a2a_egress_url_policy_accepts_exact_and_suffix() -> None:
    from agentos.channels.a2a import (
        A2AEgressPolicyError,
        HostAllowListA2AEgressUrlPolicy,
    )

    policy = HostAllowListA2AEgressUrlPolicy(
        allowed_hosts=("issuer.example",),
        allowed_domain_suffixes=(".trusted.example",),
    )

    policy.validate_url("https://issuer.example/.well-known/jwks.json")
    policy.validate_url("https://trusted.example/.well-known/agent-card.json")
    policy.validate_url("https://team.trusted.example/a2a")
    with pytest.raises(A2AEgressPolicyError, match="allowed"):
        policy.validate_url("https://other.example/.well-known/jwks.json")
    with pytest.raises(A2AEgressPolicyError, match="allowed"):
        policy.validate_url("https://team.trusted.example.evil.com/a2a")


def test_a2a_card_resolver_enforces_egress_policy_before_transport() -> None:
    from agentos.channels.a2a import (
        A2ACardResolver,
        A2AEgressPolicyError,
        HostAllowListA2AEgressUrlPolicy,
    )

    transport = RecordingTransport()
    resolver = A2ACardResolver(
        well_known_urls={"Remote": "https://blocked.example"},
        transport=transport,
        egress_url_policy=HostAllowListA2AEgressUrlPolicy(
            allowed_hosts=("allowed.example",),
        ),
    )

    with pytest.raises(A2AEgressPolicyError, match="allowed"):
        resolver.resolve("Remote")

    assert transport.calls == []


def test_a2a_card_resolver_defaults_to_public_https_egress_policy() -> None:
    from agentos.channels.a2a import A2ACardResolver, A2AEgressPolicyError

    transport = RecordingTransport()
    resolver = A2ACardResolver(
        well_known_urls={"Local": "http://127.0.0.1:8080"},
        transport=transport,
    )

    with pytest.raises(A2AEgressPolicyError, match="https"):
        resolver.resolve("Local")

    assert transport.calls == []


def test_oidc_discovery_provider_enforces_egress_policy_before_transport() -> None:
    from agentos.channels.a2a import (
        A2AEgressPolicyError,
        HostAllowListA2AEgressUrlPolicy,
        OidcDiscoveryMetadataProvider,
    )

    transport = RecordingTransport()
    provider = OidcDiscoveryMetadataProvider(
        issuer="https://issuer.example",
        transport=transport,
        egress_url_policy=HostAllowListA2AEgressUrlPolicy(
            allowed_hosts=("allowed.example",),
        ),
    )

    with pytest.raises(A2AEgressPolicyError, match="allowed"):
        provider.metadata()

    assert transport.calls == []


def test_oidc_discovery_provider_defaults_to_public_https_egress_policy() -> None:
    from agentos.channels.a2a import (
        A2AEgressPolicyError,
        OidcDiscoveryMetadataProvider,
    )

    transport = RecordingTransport()
    provider = OidcDiscoveryMetadataProvider(
        issuer="https://127.0.0.1:8080",
        transport=transport,
    )

    with pytest.raises(A2AEgressPolicyError, match="public"):
        provider.metadata()

    assert transport.calls == []


def test_jwks_card_trust_store_enforces_egress_policy_before_transport() -> None:
    from agentos.channels.a2a import (
        A2AEgressPolicyError,
        HostAllowListA2AEgressUrlPolicy,
        JwksA2ACardTrustStore,
    )

    transport = RecordingTransport()
    trust_store = JwksA2ACardTrustStore(
        jwks_urls=("https://issuer.example/jwks.json",),
        transport=transport,
        egress_url_policy=HostAllowListA2AEgressUrlPolicy(
            allowed_hosts=("allowed.example",),
        ),
    )

    with pytest.raises(A2AEgressPolicyError, match="allowed"):
        trust_store.secret_for_key_id("key-1")

    assert transport.calls == []


def test_jwks_card_trust_store_defaults_to_public_https_egress_policy() -> None:
    from agentos.channels.a2a import (
        A2AEgressPolicyError,
        JwksA2ACardTrustStore,
    )

    transport = RecordingTransport()
    trust_store = JwksA2ACardTrustStore(
        jwks_urls=("https://127.0.0.1:8080/jwks.json",),
        transport=transport,
    )

    with pytest.raises(A2AEgressPolicyError, match="public"):
        trust_store.secret_for_key_id("key-1")

    assert transport.calls == []


def test_jwks_jwt_verifier_enforces_egress_policy_before_transport() -> None:
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives.asymmetric import rsa

    from agentos.channels.a2a import (
        A2AEgressPolicyError,
        HostAllowListA2AEgressUrlPolicy,
        JwksA2AJwtVerifier,
    )

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = rs256_jwt(
        {
            "iss": "https://issuer.example",
            "aud": "agentos-a2a",
            "sub": "peer",
        },
        private_key=private_key,
        key_id="rsa-1",
    )
    transport = RecordingTransport(
        {"keys": [rsa_public_jwk(private_key, key_id="rsa-1")]},
    )
    verifier = JwksA2AJwtVerifier(
        discovery_provider=StaticDiscoveryProvider("https://issuer.example/jwks.json"),
        transport=transport,
        egress_url_policy=HostAllowListA2AEgressUrlPolicy(
            allowed_hosts=("allowed.example",),
        ),
    )

    with pytest.raises(A2AEgressPolicyError, match="allowed"):
        verifier.verify(token)

    assert transport.calls == []


def rsa_public_jwk(
    private_key: object,
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
    private_key: object,
    key_id: str,
) -> str:
    import base64
    import json

    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    header = {"alg": "RS256", "kid": key_id, "typ": "JWT"}
    signing_input = ".".join(
        [
            _base64url(
                json.dumps(
                    header,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8"),
            ),
            _base64url(
                json.dumps(
                    claims,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8"),
            ),
        ],
    )
    signature = private_key.sign(
        signing_input.encode("ascii"),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    return (
        f"{signing_input}."
        f"{base64.urlsafe_b64encode(signature).decode('ascii').rstrip('=')}"
    )


def _base64url_int(value: int) -> str:
    length = max(1, (value.bit_length() + 7) // 8)
    return _base64url(value.to_bytes(length, "big"))


def _base64url(value: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def test_a2a_operation_client_enforces_egress_policy_before_transport() -> None:
    from agentos.channels.a2a import (
        A2AAgentCard,
        A2AEgressPolicyError,
        HostAllowListA2AEgressUrlPolicy,
    )
    from agentos.channels.a2a_operations import (
        A2AMessage,
        A2AMessagePart,
        A2AOperationClient,
    )

    transport = RecordingTransport()
    client = A2AOperationClient(
        transport=transport,
        egress_url_policy=HostAllowListA2AEgressUrlPolicy(
            allowed_hosts=("allowed.example",),
        ),
    )

    with pytest.raises(A2AEgressPolicyError, match="allowed"):
        client.send_message(
            A2AAgentCard(
                name="Blocked",
                description="Blocked peer.",
                url="https://blocked.example/a2a",
                version="1.0.0",
            ),
            A2AMessage(role="user", parts=(A2AMessagePart.from_text("hello"),)),
        )

    assert transport.calls == []


def test_a2a_operation_client_defaults_to_public_https_egress_policy() -> None:
    from agentos.channels.a2a import (
        A2AAgentCard,
        A2AEgressPolicyError,
    )
    from agentos.channels.a2a_operations import (
        A2AMessage,
        A2AMessagePart,
        A2AOperationClient,
    )

    transport = RecordingTransport()
    client = A2AOperationClient(transport=transport)

    with pytest.raises(A2AEgressPolicyError, match="https"):
        client.send_message(
            A2AAgentCard(
                name="LocalDev",
                description="Local peer.",
                url="http://127.0.0.1:8080/a2a",
                version="1.0.0",
            ),
            A2AMessage(role="user", parts=(A2AMessagePart.from_text("hello"),)),
        )

    assert transport.calls == []


def test_a2a_operation_client_stream_message_enforces_egress_policy_before_transport() -> None:
    from agentos.channels.a2a import (
        A2AAgentCard,
        A2AEgressPolicyError,
        HostAllowListA2AEgressUrlPolicy,
    )
    from agentos.channels.a2a_operations import (
        A2AMessage,
        A2AMessagePart,
        A2AOperationClient,
    )

    transport = RecordingTransport()
    client = A2AOperationClient(
        transport=transport,
        egress_url_policy=HostAllowListA2AEgressUrlPolicy(
            allowed_hosts=("allowed.example",),
        ),
    )

    with pytest.raises(A2AEgressPolicyError, match="allowed"):
        client.stream_message(
            A2AAgentCard(
                name="Blocked",
                description="Blocked peer.",
                url="https://blocked.example/a2a",
                version="1.0.0",
            ),
            A2AMessage(role="user", parts=(A2AMessagePart.from_text("hello"),)),
        )

    assert transport.calls == []


def test_a2a_operation_client_stream_message_events_enforces_egress_policy_before_transport() -> None:
    from agentos.channels.a2a import (
        A2AAgentCard,
        A2AEgressPolicyError,
        HostAllowListA2AEgressUrlPolicy,
    )
    from agentos.channels.a2a_operations import (
        A2AMessage,
        A2AMessagePart,
        A2AOperationClient,
    )

    class RecordingSseTransport(RecordingTransport):
        def post_sse(
            self,
            url: str,
            payload: dict[str, object],
            timeout_seconds: float,
            *,
            headers: dict[str, str] | None = None,
        ) -> tuple[str, ...]:
            self.calls.append((url, "POST_SSE", timeout_seconds, headers))
            return ()

    transport = RecordingSseTransport()
    client = A2AOperationClient(
        transport=transport,
        egress_url_policy=HostAllowListA2AEgressUrlPolicy(
            allowed_hosts=("allowed.example",),
        ),
    )

    with pytest.raises(A2AEgressPolicyError, match="allowed"):
        client.stream_message_events(
            A2AAgentCard(
                name="Blocked",
                description="Blocked peer.",
                url="https://blocked.example/a2a",
                version="1.0.0",
            ),
            A2AMessage(role="user", parts=(A2AMessagePart.from_text("hello"),)),
        )

    assert transport.calls == []


def test_a2a_operation_client_task_resubscribe_enforces_egress_policy_before_transport() -> None:
    from agentos.channels.a2a import (
        A2AAgentCard,
        A2AEgressPolicyError,
        HostAllowListA2AEgressUrlPolicy,
    )
    from agentos.channels.a2a_operations import A2AOperationClient

    transport = RecordingTransport()
    client = A2AOperationClient(
        transport=transport,
        egress_url_policy=HostAllowListA2AEgressUrlPolicy(
            allowed_hosts=("allowed.example",),
        ),
    )

    with pytest.raises(A2AEgressPolicyError, match="allowed"):
        client.task_resubscribe(
            A2AAgentCard(
                name="Blocked",
                description="Blocked peer.",
                url="https://blocked.example/a2a",
                version="1.0.0",
            ),
            "task_remote",
            after_event_id=4,
        )

    assert transport.calls == []


def test_internal_a2a_adapter_enforces_egress_policy_before_transport() -> None:
    from agentos.channels.a2a import (
        A2AAdapter,
        A2AEgressPolicyError,
        HostAllowListA2AEgressUrlPolicy,
    )
    from agentos.multi import AgentCard, TaskRequest

    transport = RecordingTransport()
    adapter = A2AAdapter(
        transport=transport,
        egress_url_policy=HostAllowListA2AEgressUrlPolicy(
            allowed_hosts=("allowed.example",),
        ),
    )
    card = AgentCard(
        agent_id="blocked",
        name="Blocked",
        description="Blocked internal peer.",
        capabilities=("research",),
        endpoint="https://blocked.example",
    )

    with pytest.raises(A2AEgressPolicyError, match="allowed"):
        adapter.send_task(
            card,
            TaskRequest(
                task_id="task_1",
                instruction="research",
            ),
        )

    assert transport.calls == []


def test_internal_a2a_adapter_defaults_to_public_https_egress_policy() -> None:
    from agentos.channels.a2a import A2AAdapter, A2AEgressPolicyError
    from agentos.multi import AgentCard, TaskRequest

    transport = RecordingTransport()
    adapter = A2AAdapter(transport=transport)
    card = AgentCard(
        agent_id="local",
        name="Local",
        description="Local internal peer.",
        capabilities=("research",),
        endpoint="http://127.0.0.1:8080",
    )

    with pytest.raises(A2AEgressPolicyError, match="https"):
        adapter.send_task(
            card,
            TaskRequest(task_id="task_1", instruction="research"),
        )

    assert transport.calls == []
