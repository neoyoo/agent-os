from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Literal, Protocol
from urllib import request as urllib_request
from urllib.parse import urlparse

from agentos.multi.types import AgentCard, TaskRequest, TaskResult


AgentHealthStatus = Literal["ok", "unhealthy"]
A2A_CURRENT_PROTOCOL_VERSION = "1.0"

_A2A_LEGACY_OPERATION_ALIASES: Mapping[str, str] = {
    "message/send": "SendMessage",
    "message/stream": "SendStreamingMessage",
    "tasks/resubscribe": "SubscribeToTask",
}


def _canonical_a2a_operation_name(operation: str) -> str:
    return _A2A_LEGACY_OPERATION_ALIASES.get(operation, operation)


@dataclass(frozen=True, slots=True)
class AgentHealth:
    """远程 agent health check 结果。"""

    status: AgentHealthStatus
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class A2AAgentSkill:
    """A2A 协议中的 skill 声明。"""

    id: str
    name: str
    description: str
    tags: tuple[str, ...] = ()
    examples: tuple[str, ...] = ()
    input_modes: tuple[str, ...] = ()
    output_modes: tuple[str, ...] = ()
    security: tuple[Mapping[str, tuple[str, ...]], ...] = ()


@dataclass(frozen=True, slots=True)
class A2AAgentExtension:
    """A2A Agent Card capability extension declaration."""

    uri: str
    description: str | None = None
    required: bool = False
    params: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class A2AAgentCapabilities:
    """A2A 协议中的能力标记。"""

    streaming: bool = False
    push_notifications: bool = False
    state_transition_history: bool = False
    extensions: tuple[A2AAgentExtension, ...] = ()


@dataclass(frozen=True, init=False, slots=True)
class A2AAgentInterface:
    """A2A Agent Card transport endpoint declaration."""

    protocol_binding: str
    url: str
    protocol_version: str = "1.0"
    tenant: str | None = None

    def __init__(
        self,
        *,
        protocol_binding: str | None = None,
        url: str,
        protocol_version: str = "1.0",
        tenant: str | None = None,
        transport: str | None = None,
    ) -> None:
        binding = protocol_binding or transport
        if not binding:
            raise ValueError("protocol_binding is required")
        object.__setattr__(self, "protocol_binding", binding)
        object.__setattr__(self, "url", url)
        object.__setattr__(self, "protocol_version", protocol_version)
        object.__setattr__(self, "tenant", tenant)

    @property
    def transport(self) -> str:
        """Return the legacy alias for the official protocol binding."""

        return self.protocol_binding


@dataclass(frozen=True, slots=True)
class A2AAgentProvider:
    """A2A 协议中的 provider 声明。"""

    organization: str
    url: str | None = None


@dataclass(frozen=True, slots=True)
class A2ACardSignature:
    """Detached JWS-like signature attached to an A2A Agent Card."""

    protected: str
    signature: str
    header: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class A2AAgentCard:
    """A2A 协议 Agent Card。"""

    name: str
    description: str
    url: str = ""
    version: str = ""
    protocol_version: str = A2A_CURRENT_PROTOCOL_VERSION
    provider: A2AAgentProvider | None = None
    capabilities: A2AAgentCapabilities = field(default_factory=A2AAgentCapabilities)
    skills: tuple[A2AAgentSkill, ...] = ()
    preferred_transport: str | None = None
    supported_interfaces: tuple[A2AAgentInterface, ...] = ()
    default_input_modes: tuple[str, ...] = ("text/plain",)
    default_output_modes: tuple[str, ...] = ("text/plain",)
    security_schemes: Mapping[str, Mapping[str, object]] = field(default_factory=dict)
    security: tuple[Mapping[str, tuple[str, ...]], ...] = ()
    signatures: tuple[A2ACardSignature, ...] = ()

    def __post_init__(self) -> None:
        if not self.url and self.supported_interfaces:
            object.__setattr__(self, "url", self.supported_interfaces[0].url)
        if self.preferred_transport is None and self.supported_interfaces:
            object.__setattr__(
                self,
                "preferred_transport",
                self.supported_interfaces[0].protocol_binding,
            )


class A2ACardTrustError(ValueError):
    """Raised when an A2A Agent Card fails trust verification."""


class A2ACardSigner(Protocol):
    """Boundary for signing A2A Agent Cards."""

    def sign(self, card: A2AAgentCard) -> A2AAgentCard:
        """Return a signed copy of the card."""


class A2ACardVerifier(Protocol):
    """Boundary for verifying A2A Agent Card trust."""

    def verify(self, card: A2AAgentCard) -> None:
        """Raise A2ACardTrustError when the card is not trusted."""


class A2ACardTrustStore(Protocol):
    """Boundary for looking up local card-signing trust material."""

    def secret_for_key_id(self, key_id: str) -> bytes | None:
        """Return the local HMAC secret for a key id, if trusted."""


class A2AEgressPolicyError(ValueError):
    """Raised when an outbound A2A URL is not allowed by policy."""


class A2AEgressUrlPolicy(Protocol):
    """Boundary for validating outbound A2A URLs before transport calls."""

    def validate_url(self, url: str) -> None:
        """Raise A2AEgressPolicyError when an outbound URL is not allowed."""


@dataclass(frozen=True, slots=True)
class PublicHttpsA2AEgressUrlPolicy:
    """Require HTTPS URLs with public, non-local literal IP hostnames."""

    def validate_url(self, url: str) -> None:
        """Validate the default public HTTPS A2A egress URL policy."""

        parsed = urlparse(url)
        if parsed.scheme.lower() != "https" or not parsed.hostname:
            raise A2AEgressPolicyError("https A2A egress URL is required")
        hostname = _normalized_a2a_egress_hostname(parsed.hostname)
        if hostname in {"localhost"} or hostname.endswith(".localhost"):
            raise A2AEgressPolicyError("public A2A egress hostname is required")
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            return
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_unspecified
            or address.is_reserved
        ):
            raise A2AEgressPolicyError("public A2A egress hostname is required")


@dataclass(frozen=True, slots=True)
class HostAllowListA2AEgressUrlPolicy:
    """Allow outbound A2A URLs only for explicitly trusted hosts or suffixes."""

    allowed_hosts: tuple[str, ...] = ()
    allowed_domain_suffixes: tuple[str, ...] = ()
    base_policy: A2AEgressUrlPolicy | None = None

    def __init__(
        self,
        *,
        allowed_hosts: Sequence[str] = (),
        allowed_domain_suffixes: Sequence[str] = (),
        base_policy: A2AEgressUrlPolicy | None = None,
    ) -> None:
        object.__setattr__(
            self,
            "allowed_hosts",
            tuple(
                _normalized_a2a_egress_hostname(host)
                for host in allowed_hosts
                if host
            ),
        )
        object.__setattr__(
            self,
            "allowed_domain_suffixes",
            tuple(
                _normalized_a2a_egress_domain_suffix(suffix)
                for suffix in allowed_domain_suffixes
                if suffix
            ),
        )
        object.__setattr__(
            self,
            "base_policy",
            base_policy or PublicHttpsA2AEgressUrlPolicy(),
        )

    def validate_url(self, url: str) -> None:
        """Validate public HTTPS rules plus exact-host or suffix allow-lists."""

        base_policy = self.base_policy or PublicHttpsA2AEgressUrlPolicy()
        base_policy.validate_url(url)
        parsed = urlparse(url)
        if parsed.hostname is None:
            raise A2AEgressPolicyError("https A2A egress URL is required")
        hostname = _normalized_a2a_egress_hostname(parsed.hostname)
        if hostname in self.allowed_hosts:
            return
        if any(
            _a2a_egress_hostname_matches_suffix(hostname, suffix)
            for suffix in self.allowed_domain_suffixes
        ):
            return
        raise A2AEgressPolicyError("allowed A2A egress hostname is required")


class StaticA2ACardTrustStore:
    """In-memory trust store for HMAC card-signing keys."""

    def __init__(self, secrets: Mapping[str, bytes | str]) -> None:
        self._secrets = {
            key_id: (
                secret.encode("utf-8")
                if isinstance(secret, str)
                else bytes(secret)
            )
            for key_id, secret in secrets.items()
        }

    def secret_for_key_id(self, key_id: str) -> bytes | None:
        """Return a trusted HMAC secret by key id."""

        return self._secrets.get(key_id)


class JwksA2ACardTrustStore:
    """JWKS-backed trust store for HMAC A2A Agent Card signatures.

    This minimal trust boundary supports `oct` keys for local HS256 detached
    card signatures. Public-key JWT/OIDC validation remains deployment-owned.
    """

    def __init__(
        self,
        *,
        jwks_urls: Sequence[str],
        allowed_key_ids: Sequence[str] = (),
        transport: object | None = None,
        timeout_seconds: float = 5,
        cache_ttl_seconds: float = 300,
        clock: object | None = None,
        egress_url_policy: A2AEgressUrlPolicy | None = None,
    ) -> None:
        urls = tuple(str(url) for url in jwks_urls if str(url))
        if not urls:
            raise ValueError("jwks_urls must not be empty")
        for url in urls:
            if not url.lower().startswith("https://"):
                raise ValueError("jwks_urls must use https")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")
        if cache_ttl_seconds < 0:
            raise ValueError("cache_ttl_seconds must be >= 0")
        self._jwks_urls = urls
        self._allowed_key_ids = frozenset(str(key_id) for key_id in allowed_key_ids)
        self._transport = transport or UrllibA2ATransport()
        self._timeout_seconds = timeout_seconds
        self._cache_ttl_seconds = cache_ttl_seconds
        self._clock = clock if callable(clock) else time.time
        self._egress_url_policy = egress_url_policy
        self._cache: dict[str, bytes] = {}
        self._cache_loaded_at: float | None = None

    def secret_for_key_id(self, key_id: str) -> bytes | None:
        """Return a trusted HMAC secret by key id from configured JWKS URLs."""

        if not key_id:
            return None
        if self._allowed_key_ids and key_id not in self._allowed_key_ids:
            return None
        self._refresh_if_needed()
        return self._cache.get(key_id)

    def _refresh_if_needed(self) -> None:
        now = float(self._clock())
        if (
            self._cache_loaded_at is not None
            and now - self._cache_loaded_at <= self._cache_ttl_seconds
        ):
            return
        keys: dict[str, bytes] = {}
        for url in self._jwks_urls:
            payload = self._get_json(url)
            keys.update(self._keys_from_jwks(payload))
        self._cache = keys
        self._cache_loaded_at = now

    def _get_json(self, url: str) -> Mapping[str, object]:
        get_json = getattr(self._transport, "get_json", None)
        if not callable(get_json):
            raise A2ACardTrustError("JWKS transport must provide get_json()")
        _validate_a2a_egress_url(self._egress_url_policy, url)
        payload = get_json(url, self._timeout_seconds)
        if not isinstance(payload, Mapping):
            raise A2ACardTrustError("JWKS payload must be an object")
        return payload

    def _keys_from_jwks(self, payload: Mapping[str, object]) -> dict[str, bytes]:
        raw_keys = payload.get("keys", [])
        if not isinstance(raw_keys, Sequence) or isinstance(raw_keys, (str, bytes)):
            raise A2ACardTrustError("JWKS keys must be a list")
        keys: dict[str, bytes] = {}
        for raw_key in raw_keys:
            if not isinstance(raw_key, Mapping):
                continue
            key = self._secret_from_jwk(raw_key)
            if key is None:
                continue
            key_id, secret = key
            keys[key_id] = secret
        return keys

    def _secret_from_jwk(
        self,
        key: Mapping[str, object],
    ) -> tuple[str, bytes] | None:
        if key.get("kty") != "oct":
            return None
        if key.get("use", "sig") != "sig":
            return None
        if key.get("alg", "HS256") != "HS256":
            return None
        key_id = key.get("kid")
        if not isinstance(key_id, str) or not key_id:
            return None
        if self._allowed_key_ids and key_id not in self._allowed_key_ids:
            return None
        secret_value = key.get("k")
        if not isinstance(secret_value, str) or not secret_value:
            return None
        return key_id, _base64url_decode(secret_value)


@dataclass(frozen=True, repr=False, slots=True)
class RotatingA2ACardTrustKey:
    """One local HMAC card trust key with rollout metadata."""

    key_id: str
    secret: bytes | str
    not_before: float | None = None
    not_after: float | None = None
    revoked: bool = False

    def __post_init__(self) -> None:
        if not self.key_id:
            raise ValueError("key_id is required")
        secret = (
            self.secret.encode("utf-8")
            if isinstance(self.secret, str)
            else bytes(self.secret)
        )
        if not secret:
            raise ValueError("secret is required")
        if (
            self.not_before is not None
            and self.not_after is not None
            and self.not_after < self.not_before
        ):
            raise ValueError("not_after must be >= not_before")
        object.__setattr__(self, "secret", secret)

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}"
            f"(key_id={self.key_id!r}, secret=<redacted>, "
            f"not_before={self.not_before!r}, not_after={self.not_after!r}, "
            f"revoked={self.revoked!r})"
        )

    def is_active(self, now: float) -> bool:
        """Return whether this key is usable at the given timestamp."""

        if self.revoked:
            return False
        if self.not_before is not None and now < self.not_before:
            return False
        if self.not_after is not None and now > self.not_after:
            return False
        return True


class RotatingA2ACardTrustStore:
    """In-memory HMAC card trust store with key overlap, expiry, and revocation."""

    def __init__(
        self,
        *,
        keys: Sequence[RotatingA2ACardTrustKey],
        current_key_id: str,
        clock: object | None = None,
    ) -> None:
        if not current_key_id:
            raise ValueError("current_key_id is required")
        self._keys = {key.key_id: key for key in keys}
        if current_key_id not in self._keys:
            raise ValueError("current_key_id must reference a configured key")
        self._current_key_id = current_key_id
        self._clock = clock if callable(clock) else time.time

    def secret_for_key_id(self, key_id: str) -> bytes | None:
        """Return an active, trusted HMAC secret by key id."""

        key = self._keys.get(key_id)
        if key is None:
            return None
        now = float(self._clock())
        if not key.is_active(now):
            return None
        return bytes(key.secret)

    def signing_key(self) -> tuple[str, bytes]:
        """Return the active current signing key."""

        key = self._keys[self._current_key_id]
        now = float(self._clock())
        if not key.is_active(now):
            raise A2ACardTrustError("current card signing key is not active")
        return key.key_id, bytes(key.secret)

    def active_key_ids(self) -> tuple[str, ...]:
        """Return active key ids at the current timestamp."""

        now = float(self._clock())
        return tuple(
            key_id
            for key_id, key in sorted(self._keys.items())
            if key.is_active(now)
        )


class HmacA2ACardSigner:
    """Sign A2A Agent Cards with a local detached HMAC-SHA256 signature."""

    def __init__(self, *, key_id: str, secret: bytes | str) -> None:
        if not key_id:
            raise ValueError("key_id is required")
        self._key_id = key_id
        self._secret = (
            secret.encode("utf-8")
            if isinstance(secret, str)
            else bytes(secret)
        )
        if not self._secret:
            raise ValueError("secret is required")

    def sign(self, card: A2AAgentCard) -> A2AAgentCard:
        """Return a copy of the card with a detached signature appended."""

        protected = _base64url_encode(
            json.dumps(
                {"alg": "HS256", "kid": self._key_id, "typ": "agent-card+jws"},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8"),
        )
        payload = _base64url_encode(_a2a_card_canonical_payload(card))
        digest = hmac.new(
            self._secret,
            f"{protected}.{payload}".encode("ascii"),
            hashlib.sha256,
        ).digest()
        signature = A2ACardSignature(
            protected=protected,
            signature=_base64url_encode(digest),
        )
        return replace(card, signatures=card.signatures + (signature,))


class RotatingHmacA2ACardSigner:
    """Sign A2A Agent Cards with the current key from a rotating trust store."""

    def __init__(self, trust_store: RotatingA2ACardTrustStore) -> None:
        self._trust_store = trust_store

    def sign(self, card: A2AAgentCard) -> A2AAgentCard:
        """Return a signed copy of the card using the active current key."""

        key_id, secret = self._trust_store.signing_key()
        return HmacA2ACardSigner(key_id=key_id, secret=secret).sign(card)


class HmacA2ACardVerifier:
    """Verify local detached HMAC-SHA256 A2A Agent Card signatures."""

    def __init__(self, trust_store: A2ACardTrustStore) -> None:
        self._trust_store = trust_store

    def verify(self, card: A2AAgentCard) -> None:
        """Raise A2ACardTrustError when no trusted signature validates."""

        if not card.signatures:
            raise A2ACardTrustError("no card signatures")
        errors: list[str] = []
        for signature in card.signatures:
            try:
                if self._verify_one(card, signature):
                    return
            except A2ACardTrustError as error:
                errors.append(str(error))
        if errors:
            raise A2ACardTrustError(errors[-1])
        raise A2ACardTrustError("signature verification failed")

    def _verify_one(
        self,
        card: A2AAgentCard,
        signature: A2ACardSignature,
    ) -> bool:
        protected = _decode_protected_header(signature.protected)
        if protected.get("alg") != "HS256":
            raise A2ACardTrustError("unsupported card signature algorithm")
        key_id = protected.get("kid")
        if not isinstance(key_id, str) or not key_id:
            raise A2ACardTrustError("card signature key id is required")
        secret = self._trust_store.secret_for_key_id(key_id)
        if secret is None:
            raise A2ACardTrustError("untrusted card signature key id")
        payload = _base64url_encode(_a2a_card_canonical_payload(card))
        digest = hmac.new(
            secret,
            f"{signature.protected}.{payload}".encode("ascii"),
            hashlib.sha256,
        ).digest()
        expected = _base64url_encode(digest)
        if not hmac.compare_digest(expected, signature.signature):
            raise A2ACardTrustError("signature verification failed")
        return True


class A2AAuthProvider(Protocol):
    """Boundary for outbound A2A peer-auth header injection."""

    def headers_for_card(self, card: A2AAgentCard) -> Mapping[str, str]:
        """Return headers to attach to outbound requests for a peer card."""


class A2AInboundAuthError(PermissionError):
    """Raised when an inbound A2A peer request is not authorized."""


class A2ACredentialRotationError(ValueError):
    """Raised when a rotating A2A bearer credential cannot be used."""


class A2AOidcDiscoveryError(ValueError):
    """Raised when OIDC discovery metadata is unavailable or invalid."""


class A2AInboundAuthPolicy(Protocol):
    """Boundary for inbound A2A peer authorization."""

    def authorize(self, headers: Mapping[str, str]) -> None:
        """Raise A2AInboundAuthError when a peer request is not authorized."""


class A2AOperationInboundAuthPolicy(Protocol):
    """Boundary for operation-aware inbound A2A peer authorization."""

    def authorize_operation(
        self,
        headers: Mapping[str, str],
        *,
        operation: str,
    ) -> None:
        """Raise A2AInboundAuthError when a peer cannot call an operation."""


class A2AResourceInboundAuthPolicy(Protocol):
    """Boundary for resource-aware inbound A2A peer authorization."""

    def authorize_resource(
        self,
        headers: Mapping[str, str],
        *,
        operation: str,
        task_id: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
    ) -> None:
        """Raise A2AInboundAuthError when a peer cannot access a resource."""


@dataclass(frozen=True, slots=True)
class OidcDiscoveryMetadata:
    """Validated OIDC discovery metadata needed by A2A JWT auth rollout."""

    issuer: str
    jwks_uri: str
    raw: Mapping[str, object] = field(default_factory=dict)


class OidcDiscoveryMetadataProvider:
    """Fetch and cache OIDC discovery metadata for one configured issuer."""

    def __init__(
        self,
        *,
        issuer: str,
        transport: object | None = None,
        timeout_seconds: float = 5,
        cache_ttl_seconds: float = 300,
        clock: object | None = None,
        egress_url_policy: A2AEgressUrlPolicy | None = None,
    ) -> None:
        normalized_issuer = str(issuer).rstrip("/")
        if not normalized_issuer:
            raise ValueError("issuer is required")
        if not _is_https_url(normalized_issuer):
            raise ValueError("issuer must use https")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")
        if cache_ttl_seconds < 0:
            raise ValueError("cache_ttl_seconds must be >= 0")
        self._issuer = normalized_issuer
        self._discovery_url = (
            normalized_issuer + "/.well-known/openid-configuration"
        )
        self._transport = transport or UrllibA2ATransport()
        self._timeout_seconds = timeout_seconds
        self._cache_ttl_seconds = cache_ttl_seconds
        self._clock = clock if callable(clock) else time.time
        self._egress_url_policy = egress_url_policy
        self._cache: OidcDiscoveryMetadata | None = None
        self._cache_loaded_at: float | None = None

    @property
    def issuer(self) -> str:
        """Return the normalized issuer URL."""

        return self._issuer

    @property
    def discovery_url(self) -> str:
        """Return the OpenID configuration URL for this issuer."""

        return self._discovery_url

    def metadata(self) -> OidcDiscoveryMetadata:
        """Return validated discovery metadata, refreshing cache when needed."""

        now = float(self._clock())
        if (
            self._cache is not None
            and self._cache_loaded_at is not None
            and now - self._cache_loaded_at <= self._cache_ttl_seconds
        ):
            return self._cache
        metadata = self._fetch_metadata()
        self._cache = metadata
        self._cache_loaded_at = now
        return metadata

    def jwks_uri(self) -> str:
        """Return the validated HTTPS JWKS URI from discovery metadata."""

        return self.metadata().jwks_uri

    def _fetch_metadata(self) -> OidcDiscoveryMetadata:
        get_json = getattr(self._transport, "get_json", None)
        if not callable(get_json):
            raise A2AOidcDiscoveryError(
                "OIDC discovery transport must provide get_json()",
            )
        _validate_a2a_egress_url(self._egress_url_policy, self._discovery_url)
        payload = get_json(self._discovery_url, self._timeout_seconds)
        if not isinstance(payload, Mapping):
            raise A2AOidcDiscoveryError(
                "OIDC discovery metadata must be an object",
            )
        issuer = payload.get("issuer")
        if issuer != self._issuer:
            raise A2AOidcDiscoveryError("OIDC discovery issuer mismatch")
        jwks_uri = payload.get("jwks_uri")
        if not isinstance(jwks_uri, str) or not _is_https_url(jwks_uri):
            raise A2AOidcDiscoveryError(
                "OIDC discovery jwks_uri must be an https URL",
            )
        return OidcDiscoveryMetadata(
            issuer=self._issuer,
            jwks_uri=jwks_uri,
            raw=dict(payload),
        )


@dataclass(frozen=True, slots=True)
class A2AJwtClaims:
    """Verified JWT claims used for inbound A2A peer authorization."""

    issuer: str
    subject: str
    audience: tuple[str, ...]
    expires_at: float | None = None
    not_before: float | None = None
    issued_at: float | None = None
    authorized_party: str | None = None
    raw: Mapping[str, object] = field(default_factory=dict)

    @property
    def peer_id(self) -> str:
        """Return the preferred peer id from azp, then sub."""

        return self.authorized_party or self.subject


class A2AJwtVerifier(Protocol):
    """Boundary for verifying JWT bearer tokens used by A2A peers."""

    def verify(self, token: str) -> A2AJwtClaims:
        """Return verified claims or raise A2AInboundAuthError."""


@dataclass(frozen=True, repr=False, slots=True)
class HmacA2AJwtVerifier:
    """Minimal HS256 JWT verifier for A2A inbound auth policies."""

    secret: bytes | str

    def __post_init__(self) -> None:
        secret = (
            self.secret.encode("utf-8")
            if isinstance(self.secret, str)
            else bytes(self.secret)
        )
        if not secret:
            raise ValueError("secret is required")
        object.__setattr__(self, "secret", secret)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(secret=<redacted>)"

    def verify(self, token: str) -> A2AJwtClaims:
        """Verify an HS256 compact JWT and return its claims."""

        parts = token.split(".")
        if len(parts) != 3 or not all(parts):
            raise A2AInboundAuthError("unauthorized peer")
        header = self._json_segment(parts[0])
        payload = self._json_segment(parts[1])
        if header.get("alg") != "HS256":
            raise A2AInboundAuthError("unauthorized peer")
        expected = _base64url_encode(
            hmac.new(
                bytes(self.secret),
                f"{parts[0]}.{parts[1]}".encode("ascii"),
                hashlib.sha256,
            ).digest(),
        )
        if not hmac.compare_digest(expected, parts[2]):
            raise A2AInboundAuthError("unauthorized peer")
        return _a2a_jwt_claims_from_payload(payload)

    def _json_segment(self, value: str) -> Mapping[str, object]:
        try:
            payload = json.loads(_base64url_decode(value).decode("utf-8"))
        except Exception as error:
            raise A2AInboundAuthError("unauthorized peer") from error
        if not isinstance(payload, Mapping):
            raise A2AInboundAuthError("unauthorized peer")
        return payload


class JwksA2AJwtVerifier:
    """RS256 JWT verifier backed by configured or discovered JWKS URLs."""

    def __init__(
        self,
        *,
        jwks_urls: Sequence[str] = (),
        discovery_provider: object | None = None,
        allowed_key_ids: Sequence[str] = (),
        transport: object | None = None,
        timeout_seconds: float = 5,
        cache_ttl_seconds: float = 300,
        clock: object | None = None,
        egress_url_policy: A2AEgressUrlPolicy | None = None,
    ) -> None:
        urls = tuple(str(url) for url in jwks_urls if str(url))
        if not urls and discovery_provider is None:
            raise ValueError("jwks_urls or discovery_provider is required")
        for url in urls:
            if not _is_https_url(url):
                raise ValueError("jwks_urls must use https")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")
        if cache_ttl_seconds < 0:
            raise ValueError("cache_ttl_seconds must be >= 0")
        self._jwks_urls = urls
        self._discovery_provider = discovery_provider
        self._allowed_key_ids = frozenset(str(key_id) for key_id in allowed_key_ids)
        self._transport = transport or UrllibA2ATransport()
        self._timeout_seconds = timeout_seconds
        self._cache_ttl_seconds = cache_ttl_seconds
        self._clock = clock if callable(clock) else time.time
        self._egress_url_policy = egress_url_policy
        self._cache: dict[str, object] = {}
        self._cache_loaded_at: float | None = None

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}"
            f"(jwks_urls={self._jwks_urls!r}, "
            f"allowed_key_ids={tuple(sorted(self._allowed_key_ids))!r}, "
            "keys=<redacted>)"
        )

    def verify(self, token: str) -> A2AJwtClaims:
        """Verify an RS256 compact JWT and return its claims."""

        parts = token.split(".")
        if len(parts) != 3 or not all(parts):
            raise A2AInboundAuthError("unauthorized peer")
        header = _jwt_json_segment(parts[0])
        if header.get("alg") != "RS256":
            raise A2AInboundAuthError("unauthorized peer")
        key_id = header.get("kid")
        if not isinstance(key_id, str) or not key_id:
            raise A2AInboundAuthError("unauthorized peer")
        if self._allowed_key_ids and key_id not in self._allowed_key_ids:
            raise A2AInboundAuthError("unauthorized peer")
        public_key = self._public_key_for_key_id(key_id)
        if public_key is None:
            raise A2AInboundAuthError("unauthorized peer")
        self._verify_signature(public_key, f"{parts[0]}.{parts[1]}", parts[2])
        payload = _jwt_json_segment(parts[1])
        return _a2a_jwt_claims_from_payload(payload)

    def _public_key_for_key_id(self, key_id: str) -> object | None:
        self._refresh_if_needed()
        return self._cache.get(key_id)

    def _refresh_if_needed(self) -> None:
        now = float(self._clock())
        if (
            self._cache_loaded_at is not None
            and now - self._cache_loaded_at <= self._cache_ttl_seconds
        ):
            return
        keys: dict[str, object] = {}
        for url in self._current_jwks_urls():
            payload = self._get_json(url)
            keys.update(self._keys_from_jwks(payload))
        self._cache = keys
        self._cache_loaded_at = now

    def _current_jwks_urls(self) -> tuple[str, ...]:
        urls = list(self._jwks_urls)
        if self._discovery_provider is not None:
            jwks_uri = self._discovery_jwks_uri()
            if not _is_https_url(jwks_uri):
                raise A2AInboundAuthError("unauthorized peer")
            urls.append(jwks_uri)
        return tuple(urls)

    def _discovery_jwks_uri(self) -> str:
        jwks_uri = getattr(self._discovery_provider, "jwks_uri", None)
        if not callable(jwks_uri):
            raise A2AInboundAuthError("unauthorized peer")
        value = jwks_uri()
        if not isinstance(value, str) or not value:
            raise A2AInboundAuthError("unauthorized peer")
        return value

    def _get_json(self, url: str) -> Mapping[str, object]:
        get_json = getattr(self._transport, "get_json", None)
        if not callable(get_json):
            raise A2AInboundAuthError("unauthorized peer")
        try:
            _validate_a2a_egress_url(self._egress_url_policy, url)
            payload = get_json(url, self._timeout_seconds)
        except Exception as error:
            if isinstance(error, A2AEgressPolicyError):
                raise
            raise A2AInboundAuthError("unauthorized peer") from error
        if not isinstance(payload, Mapping):
            raise A2AInboundAuthError("unauthorized peer")
        return payload

    def _keys_from_jwks(self, payload: Mapping[str, object]) -> dict[str, object]:
        raw_keys = payload.get("keys", [])
        if not isinstance(raw_keys, Sequence) or isinstance(raw_keys, (str, bytes)):
            raise A2AInboundAuthError("unauthorized peer")
        keys: dict[str, object] = {}
        for raw_key in raw_keys:
            if not isinstance(raw_key, Mapping):
                continue
            key = self._public_key_from_jwk(raw_key)
            if key is None:
                continue
            key_id, public_key = key
            keys[key_id] = public_key
        return keys

    def _public_key_from_jwk(
        self,
        key: Mapping[str, object],
    ) -> tuple[str, object] | None:
        if key.get("kty") != "RSA":
            return None
        if key.get("use", "sig") != "sig":
            return None
        if key.get("alg", "RS256") != "RS256":
            return None
        key_id = key.get("kid")
        if not isinstance(key_id, str) or not key_id:
            return None
        if self._allowed_key_ids and key_id not in self._allowed_key_ids:
            return None
        modulus = key.get("n")
        exponent = key.get("e")
        if not isinstance(modulus, str) or not isinstance(exponent, str):
            return None
        try:
            return key_id, _rsa_public_key_from_numbers(
                _base64url_uint(modulus),
                _base64url_uint(exponent),
            )
        except Exception:
            return None

    def _verify_signature(
        self,
        public_key: object,
        signing_input: str,
        signature: str,
    ) -> None:
        try:
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.asymmetric import padding

            public_key.verify(
                _base64url_decode(signature),
                signing_input.encode("ascii"),
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
        except Exception as error:
            raise A2AInboundAuthError("unauthorized peer") from error


@dataclass(frozen=True, repr=False, slots=True)
class OidcClaimsA2AInboundAuthPolicy:
    """Inbound A2A auth policy backed by verified JWT/OIDC claims."""

    verifier: A2AJwtVerifier
    issuer: str
    audience: str
    allowed_peer_ids: tuple[str, ...] = ()
    clock: object | None = None
    leeway_seconds: float = 60.0

    def __post_init__(self) -> None:
        if not self.issuer:
            raise ValueError("issuer is required")
        if not self.audience:
            raise ValueError("audience is required")
        if self.leeway_seconds < 0:
            raise ValueError("leeway_seconds must be >= 0")
        object.__setattr__(
            self,
            "allowed_peer_ids",
            tuple(str(peer_id) for peer_id in self.allowed_peer_ids if peer_id),
        )
        object.__setattr__(
            self,
            "clock",
            self.clock if callable(self.clock) else time.time,
        )

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}"
            f"(issuer={self.issuer!r}, audience={self.audience!r}, "
            f"allowed_peer_ids={self.allowed_peer_ids!r}, verifier=<redacted>)"
        )

    def authorize(self, headers: Mapping[str, str]) -> None:
        """Require a verified JWT bearer token with matching claims."""

        self._claims_for_headers(headers)

    def claims_for_headers(self, headers: Mapping[str, str]) -> A2AJwtClaims:
        """Return verified claims for callers that need peer identity."""

        return self._claims_for_headers(headers)

    def _claims_for_headers(self, headers: Mapping[str, str]) -> A2AJwtClaims:
        token = _bearer_token_from_headers(headers)
        if token is None:
            raise A2AInboundAuthError("unauthorized peer")
        claims = self.verifier.verify(token)
        now = float(self.clock())
        leeway = float(self.leeway_seconds)
        if claims.issuer != self.issuer:
            raise A2AInboundAuthError("unauthorized peer")
        if self.audience not in claims.audience:
            raise A2AInboundAuthError("unauthorized peer")
        if claims.expires_at is None or now > claims.expires_at + leeway:
            raise A2AInboundAuthError("unauthorized peer")
        if claims.not_before is not None and now + leeway < claims.not_before:
            raise A2AInboundAuthError("unauthorized peer")
        if self.allowed_peer_ids and claims.peer_id not in self.allowed_peer_ids:
            raise A2AInboundAuthError("unauthorized peer")
        return claims


class RejectAllA2AInboundAuthPolicy:
    """Default inbound A2A auth policy for production fail-closed behavior."""

    def authorize(self, headers: Mapping[str, str]) -> None:
        """Reject inbound A2A peer requests without explicit auth policy."""

        raise A2AInboundAuthError("unauthorized peer")


class AllowAllA2AInboundAuthPolicy:
    """Opt-in inbound A2A auth policy for local/dev compatibility."""

    def authorize(self, headers: Mapping[str, str]) -> None:
        """Allow all inbound A2A peer requests."""


@dataclass(frozen=True, repr=False, slots=True)
class StaticBearerA2AInboundAuthPolicy:
    """Static bearer-token policy for inbound A2A peer requests."""

    token: str

    def __post_init__(self) -> None:
        if not self.token:
            raise ValueError("token is required")

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(token=<redacted>)"

    def authorize(self, headers: Mapping[str, str]) -> None:
        """Require Authorization: Bearer <token>."""

        authorization = _case_insensitive_header(headers, "authorization")
        expected = f"Bearer {self.token}"
        if authorization != expected:
            raise A2AInboundAuthError("unauthorized peer")


@dataclass(frozen=True, repr=False, slots=True)
class A2ABearerCredential:
    """One bearer credential with rollout metadata for A2A peer auth."""

    key_id: str
    peer_id: str
    token: str
    not_before: float | None = None
    not_after: float | None = None
    revoked: bool = False

    def __post_init__(self) -> None:
        key_id = str(self.key_id)
        peer_id = str(self.peer_id)
        token = str(self.token)
        if not key_id:
            raise ValueError("key_id is required")
        if not peer_id:
            raise ValueError("peer_id is required")
        if not token:
            raise ValueError("token is required")
        if (
            self.not_before is not None
            and self.not_after is not None
            and self.not_after < self.not_before
        ):
            raise ValueError("not_after must be >= not_before")
        object.__setattr__(self, "key_id", key_id)
        object.__setattr__(self, "peer_id", peer_id)
        object.__setattr__(self, "token", token)

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}"
            f"(key_id={self.key_id!r}, peer_id={self.peer_id!r}, "
            "token=<redacted>, "
            f"not_before={self.not_before!r}, not_after={self.not_after!r}, "
            f"revoked={self.revoked!r})"
        )

    def is_active(self, now: float) -> bool:
        """Return whether this bearer credential is usable now."""

        if self.revoked:
            return False
        if self.not_before is not None and now < self.not_before:
            return False
        if self.not_after is not None and now > self.not_after:
            return False
        return True


class RotatingBearerA2ACredentialStore:
    """In-memory A2A bearer credential store with overlap rotation semantics."""

    def __init__(
        self,
        *,
        credentials: Sequence[A2ABearerCredential],
        current_key_id: str,
        clock: object | None = None,
    ) -> None:
        if not current_key_id:
            raise ValueError("current_key_id is required")
        by_key_id: dict[str, A2ABearerCredential] = {}
        for credential in credentials:
            if credential.key_id in by_key_id:
                raise ValueError("credential key_id values must be unique")
            by_key_id[credential.key_id] = credential
        if not by_key_id:
            raise ValueError("credentials must not be empty")
        if current_key_id not in by_key_id:
            raise ValueError("current_key_id must reference a configured credential")
        self._credentials = by_key_id
        self._current_key_id = str(current_key_id)
        self._clock = clock if callable(clock) else time.time

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}"
            f"(current_key_id={self._current_key_id!r}, "
            f"active_key_ids={self.active_key_ids()!r}, "
            "credentials=<redacted>)"
        )

    def current_credential(self) -> A2ABearerCredential:
        """Return the active current outbound bearer credential."""

        credential = self._credentials[self._current_key_id]
        now = float(self._clock())
        if not credential.is_active(now):
            raise A2ACredentialRotationError(
                "current bearer credential is not active",
            )
        return credential

    def peer_id_for_token(self, token: str) -> str | None:
        """Return the active peer id for a bearer token, if any."""

        if not token:
            return None
        now = float(self._clock())
        for credential in self._credentials.values():
            if not credential.is_active(now):
                continue
            if hmac.compare_digest(str(token), credential.token):
                return credential.peer_id
        return None

    def active_key_ids(self) -> tuple[str, ...]:
        """Return active bearer credential key ids at the current timestamp."""

        now = float(self._clock())
        return tuple(
            key_id
            for key_id, credential in sorted(self._credentials.items())
            if credential.is_active(now)
        )


class RotatingBearerA2AAuthProvider:
    """Outbound A2A bearer auth provider backed by a rotating credential store."""

    def __init__(self, credential_store: RotatingBearerA2ACredentialStore) -> None:
        self._credential_store = credential_store

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}"
            "(credential_store=<redacted>)"
        )

    def headers_for_card(self, card: A2AAgentCard) -> Mapping[str, str]:
        """Return Authorization headers using the current active credential."""

        credential = self._credential_store.current_credential()
        return {"Authorization": f"Bearer {credential.token}"}


@dataclass(frozen=True, repr=False, slots=True)
class RotatingBearerA2AInboundAuthPolicy:
    """Inbound A2A auth policy backed by active rotating bearer credentials."""

    credential_store: RotatingBearerA2ACredentialStore
    allowed_peer_ids: tuple[str, ...] = ()
    allowed_operations: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    allowed_task_ids: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    allowed_resources: Mapping[str, tuple[tuple[str, str], ...]] = field(
        default_factory=dict,
    )

    def __post_init__(self) -> None:
        if not callable(getattr(self.credential_store, "peer_id_for_token", None)):
            raise ValueError("credential_store must provide peer_id_for_token()")
        allowed_peer_ids = tuple(
            str(peer_id) for peer_id in self.allowed_peer_ids if peer_id
        )
        allowed_operations = {
            str(peer_id): tuple(
                _canonical_a2a_operation_name(str(operation))
                for operation in operations
                if operation
            )
            for peer_id, operations in self.allowed_operations.items()
            if str(peer_id)
        }
        allowed_task_ids = {
            str(peer_id): tuple(str(task_id) for task_id in task_ids if task_id)
            for peer_id, task_ids in self.allowed_task_ids.items()
            if str(peer_id)
        }
        allowed_resources = {
            str(peer_id): tuple(
                (str(resource_type), str(resource_id))
                for resource_type, resource_id in resources
                if resource_type and resource_id
            )
            for peer_id, resources in self.allowed_resources.items()
            if str(peer_id)
        }
        object.__setattr__(self, "allowed_peer_ids", allowed_peer_ids)
        object.__setattr__(self, "allowed_operations", allowed_operations)
        object.__setattr__(self, "allowed_task_ids", allowed_task_ids)
        object.__setattr__(self, "allowed_resources", allowed_resources)

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}"
            f"(allowed_peer_ids={self.allowed_peer_ids!r}, "
            f"allowed_operations={self.allowed_operations!r}, "
            f"allowed_task_ids={self.allowed_task_ids!r}, "
            f"allowed_resources={self.allowed_resources!r}, "
            "credential_store=<redacted>)"
        )

    def authorize(self, headers: Mapping[str, str]) -> None:
        """Require an active bearer credential from an allowed peer."""

        self._peer_id_for_headers(headers)

    def authorize_operation(
        self,
        headers: Mapping[str, str],
        *,
        operation: str,
    ) -> None:
        """Require active credentials and optional operation permission."""

        peer_id = self._peer_id_for_headers(headers)
        self._ensure_operation(peer_id, operation)

    def authorize_resource(
        self,
        headers: Mapping[str, str],
        *,
        operation: str,
        task_id: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
    ) -> None:
        """Require active credentials and optional task/resource permission."""

        peer_id = self._peer_id_for_headers(headers)
        self._ensure_operation(peer_id, operation)
        if task_id is not None and self.allowed_task_ids:
            allowed_tasks = set(self.allowed_task_ids.get(peer_id, ()))
            if task_id not in allowed_tasks:
                raise A2AInboundAuthError("unauthorized peer")
        if (
            resource_type is not None
            and resource_id is not None
            and self.allowed_resources
        ):
            allowed_resources = set(self.allowed_resources.get(peer_id, ()))
            if (resource_type, resource_id) not in allowed_resources:
                raise A2AInboundAuthError("unauthorized peer")

    def _peer_id_for_headers(self, headers: Mapping[str, str]) -> str:
        token = _bearer_token_from_headers(headers)
        if token is None:
            raise A2AInboundAuthError("unauthorized peer")
        peer_id = self.credential_store.peer_id_for_token(token)
        if peer_id is None:
            raise A2AInboundAuthError("unauthorized peer")
        allowed = set(self.allowed_peer_ids)
        if allowed and peer_id not in allowed:
            raise A2AInboundAuthError("unauthorized peer")
        return peer_id

    def _ensure_operation(self, peer_id: str, operation: str) -> None:
        if not self.allowed_operations:
            return
        allowed = set(self.allowed_operations.get(peer_id, ()))
        if _canonical_a2a_operation_name(operation) not in allowed:
            raise A2AInboundAuthError("unauthorized peer")


@dataclass(frozen=True, repr=False, slots=True)
class PeerAllowListA2AInboundAuthPolicy:
    """Bearer-token inbound auth policy with an explicit allowed peer list."""

    peer_tokens: Mapping[str, str]
    allowed_peer_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        normalized_tokens = {
            str(peer_id): str(token)
            for peer_id, token in self.peer_tokens.items()
            if str(peer_id) and str(token)
        }
        allowed = tuple(str(peer_id) for peer_id in self.allowed_peer_ids if peer_id)
        if not normalized_tokens:
            raise ValueError("peer_tokens must not be empty")
        if not allowed:
            raise ValueError("allowed_peer_ids must not be empty")
        object.__setattr__(self, "peer_tokens", normalized_tokens)
        object.__setattr__(self, "allowed_peer_ids", allowed)

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}"
            f"(allowed_peer_ids={self.allowed_peer_ids!r}, peer_tokens=<redacted>)"
        )

    def authorize(self, headers: Mapping[str, str]) -> None:
        """Require a known bearer token from an allowed peer id."""

        token = _bearer_token_from_headers(headers)
        if token is None:
            raise A2AInboundAuthError("unauthorized peer")
        allowed = set(self.allowed_peer_ids)
        for peer_id, expected_token in self.peer_tokens.items():
            if not hmac.compare_digest(token, expected_token):
                continue
            if peer_id not in allowed:
                raise A2AInboundAuthError("unauthorized peer")
            return
        raise A2AInboundAuthError("unauthorized peer")


@dataclass(frozen=True, repr=False, slots=True)
class OperationAllowListA2AInboundAuthPolicy:
    """Bearer-token inbound auth policy with per-peer operation allow-lists."""

    peer_tokens: Mapping[str, str]
    allowed_operations: Mapping[str, tuple[str, ...]]

    def __post_init__(self) -> None:
        normalized_tokens = {
            str(peer_id): str(token)
            for peer_id, token in self.peer_tokens.items()
            if str(peer_id) and str(token)
        }
        normalized_operations = {
            str(peer_id): tuple(
                _canonical_a2a_operation_name(str(operation))
                for operation in operations
                if operation
            )
            for peer_id, operations in self.allowed_operations.items()
            if str(peer_id)
        }
        if not normalized_tokens:
            raise ValueError("peer_tokens must not be empty")
        if not normalized_operations:
            raise ValueError("allowed_operations must not be empty")
        object.__setattr__(self, "peer_tokens", normalized_tokens)
        object.__setattr__(self, "allowed_operations", normalized_operations)

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}"
            f"(allowed_operations={self.allowed_operations!r}, "
            "peer_tokens=<redacted>)"
        )

    def authorize(self, headers: Mapping[str, str]) -> None:
        """Require a known bearer token for any configured operation."""

        self._peer_id_for_headers(headers)

    def authorize_operation(
        self,
        headers: Mapping[str, str],
        *,
        operation: str,
    ) -> None:
        """Require a known peer token authorized for the requested operation."""

        peer_id = self._peer_id_for_headers(headers)
        allowed = set(self.allowed_operations.get(peer_id, ()))
        if _canonical_a2a_operation_name(operation) not in allowed:
            raise A2AInboundAuthError("unauthorized peer")

    def _peer_id_for_headers(self, headers: Mapping[str, str]) -> str:
        token = _bearer_token_from_headers(headers)
        if token is None:
            raise A2AInboundAuthError("unauthorized peer")
        for peer_id, expected_token in self.peer_tokens.items():
            if hmac.compare_digest(token, expected_token):
                return peer_id
        raise A2AInboundAuthError("unauthorized peer")


@dataclass(frozen=True, repr=False, slots=True)
class ResourceAllowListA2AInboundAuthPolicy:
    """Bearer-token inbound auth policy with per-peer resource allow-lists."""

    peer_tokens: Mapping[str, str]
    allowed_operations: Mapping[str, tuple[str, ...]]
    allowed_task_ids: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    allowed_resources: Mapping[str, tuple[tuple[str, str], ...]] = field(
        default_factory=dict,
    )

    def __post_init__(self) -> None:
        normalized_tokens = {
            str(peer_id): str(token)
            for peer_id, token in self.peer_tokens.items()
            if str(peer_id) and str(token)
        }
        normalized_operations = {
            str(peer_id): tuple(
                _canonical_a2a_operation_name(str(operation))
                for operation in operations
                if operation
            )
            for peer_id, operations in self.allowed_operations.items()
            if str(peer_id)
        }
        normalized_tasks = {
            str(peer_id): tuple(str(task_id) for task_id in task_ids if task_id)
            for peer_id, task_ids in self.allowed_task_ids.items()
            if str(peer_id)
        }
        normalized_resources = {
            str(peer_id): tuple(
                (str(resource_type), str(resource_id))
                for resource_type, resource_id in resources
                if resource_type and resource_id
            )
            for peer_id, resources in self.allowed_resources.items()
            if str(peer_id)
        }
        if not normalized_tokens:
            raise ValueError("peer_tokens must not be empty")
        if not normalized_operations:
            raise ValueError("allowed_operations must not be empty")
        object.__setattr__(self, "peer_tokens", normalized_tokens)
        object.__setattr__(self, "allowed_operations", normalized_operations)
        object.__setattr__(self, "allowed_task_ids", normalized_tasks)
        object.__setattr__(self, "allowed_resources", normalized_resources)

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}"
            f"(allowed_operations={self.allowed_operations!r}, "
            f"allowed_task_ids={self.allowed_task_ids!r}, "
            f"allowed_resources={self.allowed_resources!r}, "
            "peer_tokens=<redacted>)"
        )

    def authorize(self, headers: Mapping[str, str]) -> None:
        """Require a known bearer token for any configured operation."""

        self._peer_id_for_headers(headers)

    def authorize_operation(
        self,
        headers: Mapping[str, str],
        *,
        operation: str,
    ) -> None:
        """Require a known peer token authorized for the requested operation."""

        peer_id = self._peer_id_for_headers(headers)
        self._ensure_operation(peer_id, operation)

    def authorize_resource(
        self,
        headers: Mapping[str, str],
        *,
        operation: str,
        task_id: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
    ) -> None:
        """Require operation and task/resource authorization for the peer."""

        peer_id = self._peer_id_for_headers(headers)
        self._ensure_operation(peer_id, operation)
        if task_id is not None:
            allowed_tasks = set(self.allowed_task_ids.get(peer_id, ()))
            if task_id not in allowed_tasks:
                raise A2AInboundAuthError("unauthorized peer")
        if resource_type is not None and resource_id is not None:
            allowed_resources = set(self.allowed_resources.get(peer_id, ()))
            if (resource_type, resource_id) not in allowed_resources:
                raise A2AInboundAuthError("unauthorized peer")

    def _ensure_operation(self, peer_id: str, operation: str) -> None:
        allowed = set(self.allowed_operations.get(peer_id, ()))
        if _canonical_a2a_operation_name(operation) not in allowed:
            raise A2AInboundAuthError("unauthorized peer")

    def _peer_id_for_headers(self, headers: Mapping[str, str]) -> str:
        token = _bearer_token_from_headers(headers)
        if token is None:
            raise A2AInboundAuthError("unauthorized peer")
        for peer_id, expected_token in self.peer_tokens.items():
            if hmac.compare_digest(token, expected_token):
                return peer_id
        raise A2AInboundAuthError("unauthorized peer")


@dataclass(frozen=True, slots=True)
class A2ATenantRbacRule:
    """One tenant-scoped A2A operation/resource authorization rule."""

    tenant_id: str
    operations: tuple[str, ...]
    roles: tuple[str, ...] = ()
    scopes: tuple[str, ...] = ()
    resources: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        tenant_id = str(self.tenant_id)
        operations = tuple(str(operation) for operation in self.operations if operation)
        roles = tuple(str(role) for role in self.roles if role)
        scopes = tuple(str(scope) for scope in self.scopes if scope)
        resources = tuple(
            (str(resource_type), str(resource_id))
            for resource_type, resource_id in self.resources
            if resource_type and resource_id
        )
        if not tenant_id:
            raise ValueError("tenant_id is required")
        if not operations:
            raise ValueError("operations must not be empty")
        object.__setattr__(self, "tenant_id", tenant_id)
        object.__setattr__(
            self,
            "operations",
            tuple(_canonical_a2a_operation_name(operation) for operation in operations),
        )
        object.__setattr__(self, "roles", roles)
        object.__setattr__(self, "scopes", scopes)
        object.__setattr__(self, "resources", resources)


@dataclass(frozen=True, repr=False, slots=True)
class ClaimsTenantRbacA2AInboundAuthPolicy:
    """Claims-backed tenant RBAC policy for inbound A2A operations."""

    claims_policy: object
    rules: tuple[A2ATenantRbacRule, ...]
    tenant_claim_names: tuple[str, ...] = ("tenant_id", "tenant", "tenants")
    role_claim_names: tuple[str, ...] = ("roles", "role")
    scope_claim_names: tuple[str, ...] = ("scope", "scp", "scopes")

    def __post_init__(self) -> None:
        if not callable(getattr(self.claims_policy, "claims_for_headers", None)):
            raise ValueError("claims_policy must provide claims_for_headers()")
        rules = tuple(self.rules)
        tenant_claim_names = tuple(
            str(name) for name in self.tenant_claim_names if name
        )
        role_claim_names = tuple(str(name) for name in self.role_claim_names if name)
        scope_claim_names = tuple(
            str(name) for name in self.scope_claim_names if name
        )
        if not rules:
            raise ValueError("rules must not be empty")
        if not tenant_claim_names:
            raise ValueError("tenant_claim_names must not be empty")
        if not role_claim_names:
            raise ValueError("role_claim_names must not be empty")
        if not scope_claim_names:
            raise ValueError("scope_claim_names must not be empty")
        object.__setattr__(self, "rules", rules)
        object.__setattr__(self, "tenant_claim_names", tenant_claim_names)
        object.__setattr__(self, "role_claim_names", role_claim_names)
        object.__setattr__(self, "scope_claim_names", scope_claim_names)

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}"
            f"(rules={self.rules!r}, claims_policy=<redacted>)"
        )

    def authorize(self, headers: Mapping[str, str]) -> None:
        """Require verified claims that match at least one tenant rule."""

        self._authorize(headers, operation=None)

    def authorize_operation(
        self,
        headers: Mapping[str, str],
        *,
        operation: str,
    ) -> None:
        """Require tenant RBAC permission for one A2A operation."""

        self._authorize(headers, operation=operation)

    def authorize_resource(
        self,
        headers: Mapping[str, str],
        *,
        operation: str,
        task_id: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
    ) -> None:
        """Require tenant RBAC permission for one task/resource access."""

        resource_requests: list[tuple[str, str]] = []
        if task_id is not None:
            resource_requests.append(("task", task_id))
        if resource_type is not None and resource_id is not None:
            resource_requests.append((resource_type, resource_id))
        self._authorize(
            headers,
            operation=operation,
            resource_requests=tuple(resource_requests),
        )

    def _authorize(
        self,
        headers: Mapping[str, str],
        *,
        operation: str | None,
        resource_requests: tuple[tuple[str, str], ...] = (),
    ) -> None:
        claims = self.claims_policy.claims_for_headers(headers)
        raw = claims.raw
        tenant_ids = self._claim_values(raw, self.tenant_claim_names)
        roles = self._claim_values(raw, self.role_claim_names)
        scopes = self._claim_values(raw, self.scope_claim_names, split_strings=True)
        for rule in self.rules:
            if self._rule_matches(
                rule,
                tenant_ids=tenant_ids,
                roles=roles,
                scopes=scopes,
                operation=operation,
                resource_requests=resource_requests,
            ):
                return
        raise A2AInboundAuthError("unauthorized peer")

    def _rule_matches(
        self,
        rule: A2ATenantRbacRule,
        *,
        tenant_ids: tuple[str, ...],
        roles: tuple[str, ...],
        scopes: tuple[str, ...],
        operation: str | None,
        resource_requests: tuple[tuple[str, str], ...],
    ) -> bool:
        if rule.tenant_id not in tenant_ids:
            return False
        if operation is not None and not self._operation_matches(rule, operation):
            return False
        if rule.roles and not set(rule.roles).intersection(roles):
            return False
        if rule.scopes and not set(rule.scopes).intersection(scopes):
            return False
        if resource_requests and not all(
            self._resource_matches(rule, resource_type, resource_id)
            for resource_type, resource_id in resource_requests
        ):
            return False
        return True

    def _operation_matches(
        self,
        rule: A2ATenantRbacRule,
        operation: str,
    ) -> bool:
        return (
            "*" in rule.operations
            or _canonical_a2a_operation_name(operation) in rule.operations
        )

    def _resource_matches(
        self,
        rule: A2ATenantRbacRule,
        resource_type: str,
        resource_id: str,
    ) -> bool:
        if not rule.resources:
            return True
        for allowed_type, allowed_id in rule.resources:
            type_matches = allowed_type == "*" or allowed_type == resource_type
            id_matches = allowed_id == "*" or allowed_id == resource_id
            if type_matches and id_matches:
                return True
        return False

    def _claim_values(
        self,
        raw: Mapping[str, object],
        claim_names: tuple[str, ...],
        *,
        split_strings: bool = False,
    ) -> tuple[str, ...]:
        values: list[str] = []
        for name in claim_names:
            value = raw.get(name)
            if isinstance(value, str):
                if split_strings:
                    values.extend(part for part in value.split() if part)
                elif value:
                    values.append(value)
                continue
            if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
                values.extend(str(item) for item in value if isinstance(item, str) and item)
        return tuple(dict.fromkeys(values))


@dataclass(frozen=True, repr=False, slots=True)
class StaticBearerA2AAuthProvider:
    """Static bearer-token provider for outbound A2A peer calls."""

    token: str

    def __post_init__(self) -> None:
        if not self.token:
            raise ValueError("token is required")

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(token=<redacted>)"

    def headers_for_card(self, card: A2AAgentCard) -> Mapping[str, str]:
        """Return an Authorization header for the peer card."""

        return {"Authorization": f"Bearer {self.token}"}


def _case_insensitive_header(headers: Mapping[str, str], name: str) -> str | None:
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return value
    return None


def _bearer_token_from_headers(headers: Mapping[str, str]) -> str | None:
    authorization = _case_insensitive_header(headers, "authorization")
    if authorization is None:
        return None
    scheme, separator, token = authorization.partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not token:
        return None
    return token


def _is_https_url(value: str) -> bool:
    return value.lower().startswith("https://")


def _validate_a2a_egress_url(
    policy: A2AEgressUrlPolicy | None,
    url: str,
) -> None:
    (policy or PublicHttpsA2AEgressUrlPolicy()).validate_url(url)


def _normalized_a2a_egress_hostname(hostname: str) -> str:
    return hostname.strip().lower().rstrip(".").split("%", 1)[0]


def _normalized_a2a_egress_domain_suffix(suffix: str) -> str:
    return _normalized_a2a_egress_hostname(suffix).lstrip(".")


def _a2a_egress_hostname_matches_suffix(hostname: str, suffix: str) -> bool:
    return hostname == suffix or hostname.endswith(f".{suffix}")


def _jwt_json_segment(value: str) -> Mapping[str, object]:
    try:
        payload = json.loads(_base64url_decode(value).decode("utf-8"))
    except Exception as error:
        raise A2AInboundAuthError("unauthorized peer") from error
    if not isinstance(payload, Mapping):
        raise A2AInboundAuthError("unauthorized peer")
    return payload


def _base64url_uint(value: str) -> int:
    return int.from_bytes(_base64url_decode(value), "big")


def _rsa_public_key_from_numbers(modulus: int, exponent: int) -> object:
    try:
        from cryptography.hazmat.primitives.asymmetric import rsa

        return rsa.RSAPublicNumbers(exponent, modulus).public_key()
    except Exception as error:
        raise A2AInboundAuthError("unauthorized peer") from error


def _a2a_jwt_claims_from_payload(payload: Mapping[str, object]) -> A2AJwtClaims:
    issuer = payload.get("iss")
    subject = payload.get("sub")
    if not isinstance(issuer, str) or not issuer:
        raise A2AInboundAuthError("unauthorized peer")
    if not isinstance(subject, str) or not subject:
        raise A2AInboundAuthError("unauthorized peer")
    audience = _jwt_audience_tuple(payload.get("aud"))
    if not audience:
        raise A2AInboundAuthError("unauthorized peer")
    authorized_party = payload.get("azp")
    return A2AJwtClaims(
        issuer=issuer,
        subject=subject,
        audience=audience,
        expires_at=_jwt_numeric_date(payload.get("exp")),
        not_before=_jwt_numeric_date(payload.get("nbf")),
        issued_at=_jwt_numeric_date(payload.get("iat")),
        authorized_party=(
            authorized_party if isinstance(authorized_party, str) else None
        ),
        raw=dict(payload),
    )


def _jwt_audience_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, str) and value:
        return (value,)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return tuple(str(item) for item in value if isinstance(item, str) and item)
    return ()


def _jwt_numeric_date(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise A2AInboundAuthError("unauthorized peer")
    try:
        return float(value)
    except (TypeError, ValueError) as error:
        raise A2AInboundAuthError("unauthorized peer") from error


def a2a_card_signature_to_dict(
    signature: A2ACardSignature,
) -> dict[str, object]:
    """Serialize an A2A Agent Card signature."""

    payload: dict[str, object] = {
        "protected": signature.protected,
        "signature": signature.signature,
    }
    if signature.header:
        payload["header"] = dict(signature.header)
    return payload


def a2a_card_signature_from_dict(
    payload: Mapping[str, object],
) -> A2ACardSignature:
    """Deserialize an A2A Agent Card signature."""

    protected = payload.get("protected")
    signature = payload.get("signature")
    if not isinstance(protected, str) or not protected:
        raise ValueError("card signature protected header is required")
    if not isinstance(signature, str) or not signature:
        raise ValueError("card signature value is required")
    header_payload = payload.get("header", {})
    header = dict(header_payload) if isinstance(header_payload, Mapping) else {}
    return A2ACardSignature(
        protected=protected,
        signature=signature,
        header=header,
    )


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(str(item) for item in value if isinstance(item, str))


def _security_tuple(
    value: object,
) -> tuple[Mapping[str, tuple[str, ...]], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    items: list[Mapping[str, tuple[str, ...]]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        items.append(
            {
                str(name): tuple(str(scope) for scope in scopes)
                for name, scopes in item.items()
                if isinstance(scopes, list)
            },
        )
    return tuple(items)


def a2a_card_to_dict(
    card: A2AAgentCard,
    *,
    include_signatures: bool = True,
) -> dict[str, object]:
    """将 A2A Agent Card 转为协议 JSON 字典。"""

    payload: dict[str, object] = {
        "protocolVersion": card.protocol_version,
        "name": card.name,
        "description": card.description,
        "url": card.url,
        "version": card.version,
        "capabilities": {
            "streaming": card.capabilities.streaming,
            "pushNotifications": card.capabilities.push_notifications,
            "stateTransitionHistory": card.capabilities.state_transition_history,
        },
        "defaultInputModes": list(card.default_input_modes),
        "defaultOutputModes": list(card.default_output_modes),
        "skills": [
            {
                "id": skill.id,
                "name": skill.name,
                "description": skill.description,
                "tags": list(skill.tags),
                "examples": list(skill.examples),
                **(
                    {"inputModes": list(skill.input_modes)}
                    if skill.input_modes
                    else {}
                ),
                **(
                    {"outputModes": list(skill.output_modes)}
                    if skill.output_modes
                    else {}
                ),
                **(
                    {
                        "security": [
                            {
                                name: list(scopes)
                                for name, scopes in item.items()
                            }
                            for item in skill.security
                        ],
                    }
                    if skill.security
                    else {}
                ),
            }
            for skill in card.skills
        ],
    }
    if card.capabilities.extensions:
        payload["capabilities"]["extensions"] = [
            {
                "uri": extension.uri,
                **(
                    {"description": extension.description}
                    if extension.description is not None
                    else {}
                ),
                "required": extension.required,
                **(
                    {"params": dict(extension.params)}
                    if extension.params
                    else {}
                ),
            }
            for extension in card.capabilities.extensions
        ]
    if card.preferred_transport is not None:
        payload["preferredTransport"] = card.preferred_transport
    if card.supported_interfaces:
        payload["supportedInterfaces"] = [
            {
                "protocolBinding": interface.protocol_binding,
                "protocolVersion": interface.protocol_version,
                "url": interface.url,
                **(
                    {"tenant": interface.tenant}
                    if interface.tenant is not None
                    else {}
                ),
            }
            for interface in card.supported_interfaces
        ]
    if card.provider is not None:
        provider: dict[str, object] = {"organization": card.provider.organization}
        if card.provider.url is not None:
            provider["url"] = card.provider.url
        payload["provider"] = provider
    if card.security_schemes:
        payload["securitySchemes"] = {
            name: dict(spec)
            for name, spec in card.security_schemes.items()
        }
    if card.security:
        payload["security"] = [
            {name: list(scopes) for name, scopes in item.items()}
            for item in card.security
        ]
    if include_signatures and card.signatures:
        payload["signatures"] = [
            a2a_card_signature_to_dict(signature)
            for signature in card.signatures
        ]
    return payload


def a2a_card_from_dict(payload: Mapping[str, object]) -> A2AAgentCard:
    """从协议 JSON 字典还原 A2A Agent Card。"""

    capabilities_payload = payload.get("capabilities", {})
    capabilities = (
        capabilities_payload
        if isinstance(capabilities_payload, Mapping)
        else {}
    )
    provider_payload = payload.get("provider")
    provider = None
    if isinstance(provider_payload, Mapping):
        provider = A2AAgentProvider(
            organization=str(provider_payload.get("organization", "")),
            url=(
                None
                if provider_payload.get("url") is None
                else str(provider_payload.get("url"))
            ),
        )
    extensions = []
    for item in capabilities.get("extensions", []):
        if not isinstance(item, Mapping):
            continue
        uri = item.get("uri")
        if not isinstance(uri, str) or not uri:
            continue
        description = item.get("description")
        params = item.get("params", {})
        extensions.append(
            A2AAgentExtension(
                uri=uri,
                description=(
                    description
                    if isinstance(description, str)
                    else None
                ),
                required=bool(item.get("required", False)),
                params=dict(params) if isinstance(params, Mapping) else {},
            ),
        )
    interfaces = []
    for item in payload.get("supportedInterfaces", []):
        if not isinstance(item, Mapping):
            continue
        protocol_binding = item.get("protocolBinding", item.get("transport"))
        protocol_version = item.get("protocolVersion", "1.0")
        tenant = item.get("tenant")
        url = item.get("url")
        if not isinstance(protocol_binding, str) or not protocol_binding:
            continue
        if not isinstance(url, str) or not url:
            continue
        interfaces.append(
            A2AAgentInterface(
                protocol_binding=protocol_binding,
                protocol_version=(
                    protocol_version
                    if isinstance(protocol_version, str)
                    else str(protocol_version)
                ),
                url=url,
                tenant=tenant if isinstance(tenant, str) else None,
            ),
        )
    skills = []
    for item in payload.get("skills", []):
        if not isinstance(item, Mapping):
            continue
        tags = item.get("tags", [])
        examples = item.get("examples", [])
        skills.append(
            A2AAgentSkill(
                id=str(item.get("id", "")),
                name=str(item.get("name", "")),
                description=str(item.get("description", "")),
                tags=tuple(str(tag) for tag in tags if isinstance(tag, str)),
                examples=tuple(
                    str(example)
                    for example in examples
                    if isinstance(example, str)
                ),
                input_modes=_string_tuple(item.get("inputModes", ())),
                output_modes=_string_tuple(item.get("outputModes", ())),
                security=_security_tuple(item.get("security", ())),
            ),
        )
    security_schemes_payload = payload.get("securitySchemes", {})
    security_schemes = (
        {
            str(name): dict(spec)
            for name, spec in security_schemes_payload.items()
            if isinstance(spec, Mapping)
        }
        if isinstance(security_schemes_payload, Mapping)
        else {}
    )
    security_items = []
    for item in payload.get("security", []):
        if not isinstance(item, Mapping):
            continue
        security_items.append(
            {
                str(name): tuple(str(scope) for scope in scopes)
                for name, scopes in item.items()
                if isinstance(scopes, list)
            },
        )
    signatures_payload = payload.get("signatures", [])
    signatures = (
        tuple(
            a2a_card_signature_from_dict(signature)
            for signature in signatures_payload
            if isinstance(signature, Mapping)
        )
        if isinstance(signatures_payload, list)
        else ()
    )
    return A2AAgentCard(
        protocol_version=str(
            payload.get("protocolVersion", A2A_CURRENT_PROTOCOL_VERSION),
        ),
        name=str(payload["name"]),
        description=str(payload["description"]),
        url=str(
            payload.get(
                "url",
                interfaces[0].url if interfaces else "",
            ),
        ),
        version=str(payload["version"]),
        provider=provider,
        capabilities=A2AAgentCapabilities(
            streaming=bool(capabilities.get("streaming", False)),
            push_notifications=bool(capabilities.get("pushNotifications", False)),
            state_transition_history=bool(
                capabilities.get("stateTransitionHistory", False),
            ),
            extensions=tuple(extensions),
        ),
        skills=tuple(skills),
        preferred_transport=(
            str(payload["preferredTransport"])
            if payload.get("preferredTransport") is not None
            else None
        ),
        supported_interfaces=tuple(interfaces),
        default_input_modes=tuple(
            str(value)
            for value in payload.get("defaultInputModes", ["text/plain"])
        ),
        default_output_modes=tuple(
            str(value)
            for value in payload.get("defaultOutputModes", ["text/plain"])
        ),
        security_schemes=security_schemes,
        security=tuple(security_items),
        signatures=signatures,
    )


class A2ATransport(Protocol):
    """A2A JSON transport 边界。"""

    def post_json(
        self,
        url: str,
        payload: dict[str, object],
        timeout_seconds: float,
        *,
        headers: dict[str, str] | None = None,
    ) -> dict[str, object]:
        """POST JSON 并返回 JSON 对象。"""

    def post_sse(
        self,
        url: str,
        payload: dict[str, object],
        timeout_seconds: float,
        *,
        headers: dict[str, str] | None = None,
    ) -> object:
        """POST JSON and return text/event-stream chunks."""

    def get_json(
        self,
        url: str,
        timeout_seconds: float,
        *,
        headers: dict[str, str] | None = None,
    ) -> dict[str, object]:
        """GET JSON 并返回 JSON 对象。"""


    def delete_json(
        self,
        url: str,
        timeout_seconds: float,
        *,
        headers: dict[str, str] | None = None,
    ) -> dict[str, object]:
        """DELETE JSON and return a JSON object."""


class UrllibA2ATransport:
    """基于标准库 urllib 的最小 HTTP JSON transport。"""

    def post_json(
        self,
        url: str,
        payload: dict[str, object],
        timeout_seconds: float,
        *,
        headers: dict[str, str] | None = None,
    ) -> dict[str, object]:
        """POST JSON 并返回 JSON 对象。"""

        body = json.dumps(payload).encode("utf-8")
        request_headers = {"Content-Type": "application/json"}
        if headers:
            request_headers.update(headers)
        request = urllib_request.Request(
            url,
            data=body,
            headers=request_headers,
            method="POST",
        )
        with urllib_request.urlopen(request, timeout=timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))

    def post_sse(
        self,
        url: str,
        payload: dict[str, object],
        timeout_seconds: float,
        *,
        headers: dict[str, str] | None = None,
    ) -> tuple[bytes, ...]:
        """POST JSON and return text/event-stream response chunks."""

        body = json.dumps(payload).encode("utf-8")
        request_headers = {
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
        }
        if headers:
            request_headers.update(headers)
        request = urllib_request.Request(
            url,
            data=body,
            headers=request_headers,
            method="POST",
        )
        chunks: list[bytes] = []
        with urllib_request.urlopen(request, timeout=timeout_seconds) as response:
            while True:
                chunk = response.readline()
                if not chunk:
                    break
                chunks.append(chunk)
        return tuple(chunks)

    def get_json(
        self,
        url: str,
        timeout_seconds: float,
        *,
        headers: dict[str, str] | None = None,
    ) -> dict[str, object]:
        """GET JSON 并返回 JSON 对象。"""

        request = urllib_request.Request(
            url,
            headers=dict(headers or {}),
            method="GET",
        )
        with urllib_request.urlopen(request, timeout=timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))


    def delete_json(
        self,
        url: str,
        timeout_seconds: float,
        *,
        headers: dict[str, str] | None = None,
    ) -> dict[str, object]:
        """DELETE JSON and return a JSON object."""

        request = urllib_request.Request(
            url,
            headers=dict(headers or {}),
            method="DELETE",
        )
        with urllib_request.urlopen(request, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8")
            if not body:
                return {"result": {}}
            return json.loads(body)


def a2a_card_from_agent_card(
    card: AgentCard,
    *,
    url: str | None = None,
) -> A2AAgentCard:
    """将 agent-os 内部 AgentCard 转为 A2A 协议 card。"""

    target_url = url or card.endpoint
    if target_url is None:
        raise ValueError(f"agent card has no endpoint: {card.agent_id}")
    return A2AAgentCard(
        name=card.name,
        description=card.description,
        url=target_url,
        version=card.version,
        skills=tuple(
            A2AAgentSkill(
                id=capability,
                name=capability,
                description=f"Capability: {capability}",
                tags=(capability,),
            )
            for capability in card.capabilities
        ),
    )


class A2ACardResolver:
    """从直接配置和 well-known URL 解析 A2A Agent Card。"""

    def __init__(
        self,
        *,
        cards: Sequence[A2AAgentCard] = (),
        well_known_urls: Mapping[str, str] | None = None,
        transport: A2ATransport | None = None,
        card_verifier: A2ACardVerifier | None = None,
        timeout_seconds: float = 5,
        egress_url_policy: A2AEgressUrlPolicy | None = None,
    ) -> None:
        """创建 A2A card resolver。"""

        self._cards = {card.name: card for card in cards}
        self._well_known_urls = dict(well_known_urls or {})
        self._transport = transport or UrllibA2ATransport()
        self._card_verifier = card_verifier
        self._timeout_seconds = timeout_seconds
        self._egress_url_policy = egress_url_policy

    def resolve(self, name: str) -> A2AAgentCard | None:
        """按名称解析 A2A Agent Card。"""

        card = self._cards.get(name)
        if card is not None:
            return card
        url = self._well_known_urls.get(name)
        if url is None:
            return None
        well_known_url = self._well_known_card_url(url)
        _validate_a2a_egress_url(self._egress_url_policy, well_known_url)
        card = a2a_card_from_dict(
            self._transport.get_json(
                well_known_url,
                self._timeout_seconds,
            ),
        )
        if self._card_verifier is not None:
            self._card_verifier.verify(card)
        self._cards[name] = card
        return card

    def discover(self, required_skills: Sequence[str]) -> list[A2AAgentCard]:
        """按 skill id 发现 A2A Agent Card。"""

        for name in list(self._well_known_urls):
            self.resolve(name)
        required = set(required_skills)
        return [
            card
            for card in self._cards.values()
            if required.issubset({skill.id for skill in card.skills})
        ]

    def _well_known_card_url(self, value: str) -> str:
        if value.endswith("/.well-known/agent-card.json"):
            return value
        return value.rstrip("/") + "/.well-known/agent-card.json"


def _a2a_card_canonical_payload(card: A2AAgentCard) -> bytes:
    return json.dumps(
        a2a_card_to_dict(card, include_signatures=False),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode((value + padding).encode("ascii"))
    except Exception as error:
        raise A2ACardTrustError("invalid card signature encoding") from error


def _decode_protected_header(value: str) -> Mapping[str, object]:
    try:
        payload = json.loads(_base64url_decode(value).decode("utf-8"))
    except A2ACardTrustError:
        raise
    except Exception as error:
        raise A2ACardTrustError("invalid card signature header") from error
    if not isinstance(payload, Mapping):
        raise A2ACardTrustError("invalid card signature header")
    return payload


class A2AAdapter:
    """把 AgentCard endpoint 映射为 A2A JSON 调用。"""

    def __init__(
        self,
        transport: A2ATransport | None = None,
        *,
        egress_url_policy: A2AEgressUrlPolicy | None = None,
    ) -> None:
        self._transport = transport or UrllibA2ATransport()
        self._egress_url_policy = egress_url_policy

    def send_task(self, card: AgentCard, request: TaskRequest) -> TaskResult:
        """向远程 agent 发送任务请求并解析 TaskResult。"""

        payload = {
            "task_id": request.task_id,
            "instruction": request.instruction,
            "allowed_tool_names": list(request.allowed_tool_names),
            "timeout_seconds": request.timeout_seconds,
        }
        url = self._url(card, "/a2a/tasks")
        _validate_a2a_egress_url(self._egress_url_policy, url)
        response = self._transport.post_json(
            url,
            payload,
            request.timeout_seconds,
            headers=request.trace_context,
        )
        return TaskResult(
            task_id=str(response["task_id"]),
            status=response["status"],  # type: ignore[arg-type]
            summary=str(response.get("summary", "")),
            artifacts=dict(response.get("artifacts", {})),
            error=(
                None
                if response.get("error") is None
                else str(response.get("error"))
            ),
            elapsed_seconds=float(response.get("elapsed_seconds", 0)),
        )

    def check_health(
        self,
        card: AgentCard,
        *,
        timeout_seconds: float = 5,
    ) -> AgentHealth:
        """检查远程 agent health endpoint。"""

        try:
            url = self._url(card, "/a2a/health")
            _validate_a2a_egress_url(self._egress_url_policy, url)
            response = self._transport.get_json(
                url,
                timeout_seconds,
            )
        except Exception as error:
            return AgentHealth(
                status="unhealthy",
                detail=str(error),
            )
        status = response.get("status", "unhealthy")
        return AgentHealth(
            status="ok" if status == "ok" else "unhealthy",
            detail=(
                None
                if response.get("detail") is None
                else str(response.get("detail"))
            ),
        )

    def _url(self, card: AgentCard, path: str) -> str:
        if card.endpoint is None:
            raise ValueError(f"agent card has no endpoint: {card.agent_id}")
        return card.endpoint.rstrip("/") + path
