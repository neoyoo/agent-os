from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


ReadinessLevel = Literal[
    "direct",
    "primitives-ready",
    "future-extension",
    "not-applicable",
]

REQUIRED_READINESS_DIMENSIONS: tuple[str, ...] = (
    "session_state",
    "concurrency",
    "auth",
    "rate_limit",
    "timeout",
    "retry",
    "observability",
    "workspace",
    "protocol",
    "persistence",
    "schema_migration",
)

ReadinessEvidenceStatus = Literal["passed", "failed", "skipped", "unknown"]

WORKSPACE_BACKEND_EVIDENCE: tuple[str, ...] = (
    "WorkspaceExecutionBackend",
    "LocalWorkspaceExecutionBackend",
    "SandboxBackend",
    "WorkspaceExecutionRequest",
    "WorkspaceExecutionResult",
    "WorkspaceExecutionPolicy",
    "JSON-safe execution evidence",
)

_READINESS_EVIDENCE_STATUSES: tuple[str, ...] = (
    "passed",
    "failed",
    "skipped",
    "unknown",
)

_SECRET_KEY_PARTS = frozenset(
    {
        "api",
        "credential",
        "credentials",
        "key",
        "password",
        "secret",
        "token",
    },
)


@dataclass(frozen=True, slots=True)
class ReadinessEvidenceCheck:
    """One normalized release-readiness evidence check."""

    check_name: str
    status: ReadinessEvidenceStatus
    evidence: Mapping[str, object]
    required: bool = True
    source: str = "readiness"
    blocking_reason: str = ""
    metadata: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        if not self.check_name.strip():
            raise ValueError("check_name must not be empty")
        if self.status not in _READINESS_EVIDENCE_STATUSES:
            raise ValueError("status must be passed, failed, skipped, or unknown")
        if not self.source.strip():
            raise ValueError("source must not be empty")

    @classmethod
    def from_payload(
        cls,
        check_name: str,
        payload: object,
        *,
        required: bool | None = None,
        source: str | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> ReadinessEvidenceCheck:
        """Normalize an existing readiness/profile/gate payload."""

        payload_mapping = payload if isinstance(payload, Mapping) else {}
        if required is None:
            required = _required_from_payload(payload_mapping, default=True)
        status = _status_from_payload(payload)
        return cls(
            check_name=check_name,
            status=status,
            evidence=_evidence_mapping(payload),
            required=required,
            source=source or _source_from_payload(payload_mapping),
            blocking_reason=_blocking_reason_from_payload(
                payload_mapping,
                status=status,
                required=required,
            ),
            metadata=_json_safe_mapping(metadata or {}),
        )

    @property
    def blocks_production_readiness(self) -> bool:
        """Return whether this check should block a production release."""

        return self.required and self.status != "passed"

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-safe check payload."""

        return {
            "check_name": self.check_name,
            "status": self.status,
            "required": self.required,
            "source": self.source,
            "blocking_reason": self.blocking_reason,
            "block_production_readiness": self.blocks_production_readiness,
            "evidence": _json_safe_mapping(self.evidence),
            "metadata": _json_safe_mapping(self.metadata or {}),
        }


@dataclass(frozen=True, slots=True)
class ProductionReadinessEvidenceBundle:
    """Release gate over already-produced readiness evidence payloads."""

    checks: tuple[ReadinessEvidenceCheck, ...]
    required_checks: tuple[str, ...]
    bundle_name: str = "production_readiness"
    metadata: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        if not self.bundle_name.strip():
            raise ValueError("bundle_name must not be empty")
        _validate_names(self.required_checks, field_name="required_checks")
        _validate_names(
            tuple(check.check_name for check in self.checks),
            field_name="checks",
        )
        seen: set[str] = set()
        for check in self.checks:
            if check.check_name in seen:
                raise ValueError("checks must not contain duplicate check names")
            seen.add(check.check_name)

    @classmethod
    def from_sources(
        cls,
        sources: Mapping[str, object],
        *,
        required_checks: tuple[str, ...] = (),
        bundle_name: str = "production_readiness",
        metadata: Mapping[str, object] | None = None,
    ) -> ProductionReadinessEvidenceBundle:
        """Build a release evidence bundle from existing readiness sources."""

        if not sources:
            raise ValueError("sources must not be empty")
        _validate_names(tuple(sources), field_name="sources")
        required = required_checks or tuple(sources)
        _validate_names(required, field_name="required_checks")
        required_set = set(required)
        checks: list[ReadinessEvidenceCheck] = []
        for name, source in sources.items():
            payload = _resolve_readiness_source(source)
            payload_mapping = payload if isinstance(payload, Mapping) else {}
            checks.append(
                ReadinessEvidenceCheck.from_payload(
                    name,
                    payload,
                    required=_required_from_payload(
                        payload_mapping,
                        default=name in required_set,
                    ),
                    source=_readiness_source_name(source),
                ),
            )
        return cls(
            checks=tuple(checks),
            required_checks=tuple(required),
            bundle_name=bundle_name,
            metadata=metadata,
        )

    @property
    def checks_by_name(self) -> dict[str, ReadinessEvidenceCheck]:
        """Return checks keyed by stable check name."""

        return {check.check_name: check for check in self.checks}

    @property
    def missing_required_checks(self) -> tuple[str, ...]:
        """Return required check names with no evidence payload."""

        checks = self.checks_by_name
        return tuple(name for name in self.required_checks if name not in checks)

    @property
    def blocking_checks(self) -> tuple[str, ...]:
        """Return check names that block production readiness."""

        return tuple(
            check.check_name
            for check in self.checks
            if check.blocks_production_readiness
        )

    @property
    def accepted(self) -> bool:
        """Return whether all required release evidence is present and passing."""

        return not self.missing_required_checks and not self.blocking_checks

    @property
    def block_production_readiness(self) -> bool:
        """Return whether this bundle should block production readiness."""

        return not self.accepted

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-safe release gate evidence bundle."""

        return {
            "bundle_name": self.bundle_name,
            "status": "ok" if self.accepted else "failed",
            "accepted": self.accepted,
            "block_production_readiness": self.block_production_readiness,
            "required_checks": self.required_checks,
            "missing_required_checks": self.missing_required_checks,
            "blocking_checks": self.blocking_checks,
            "checks": tuple(check.as_dict() for check in self.checks),
            "metadata": _json_safe_mapping(self.metadata or {}),
            "sdk_owned": (
                "ProductionReadinessEvidenceBundle",
                "ReadinessEvidenceCheck",
                "ReadinessEvidenceStatus",
                "release gate evidence bundle",
                "JSON-safe evidence bundle",
                "blocking_checks",
                "missing_required_checks",
                "sdk_owned/deployment_owned boundary metadata",
            ),
            "deployment_owned": (
                "backend check execution",
                "provider and backend credentials",
                "network, TLS, gateway, and tenant policy",
                "migration execution",
                "CI matrix execution",
                "artifact retention",
                "release approval, rollout, rollback, alerting, and runbooks",
            ),
        }

    def readiness_metadata(self) -> dict[str, object]:
        """Return readiness-compatible bundle metadata."""

        return {
            "ready": self.accepted,
            **self.as_dict(),
        }

    def readiness_check(self) -> dict[str, object]:
        """Return an ASGI readiness-compatible check payload."""

        return {
            **self.readiness_metadata(),
            "ok": self.accepted,
        }


@dataclass(frozen=True, slots=True)
class ReadinessDimension:
    """One production-readiness dimension for an agent form."""

    name: str
    level: ReadinessLevel
    evidence: tuple[str, ...]
    gap: str = ""


@dataclass(frozen=True, slots=True)
class AgentFormReadiness:
    """Production-readiness record for one supported agent shape."""

    form_id: str
    name: str
    overall_level: ReadinessLevel
    summary: str
    dimensions: Mapping[str, ReadinessDimension]
    recommended_profile: str
    required_app_glue: tuple[str, ...] = ()


def list_agent_form_readiness() -> tuple[AgentFormReadiness, ...]:
    """Return all known agent-form readiness records."""

    return tuple(_FORMS.values())


def get_agent_form_readiness(form_id: str) -> AgentFormReadiness:
    """Return one readiness record by stable form id."""

    return _FORMS[form_id]


def _resolve_readiness_source(source: object) -> object:
    if callable(source):
        return source()
    for method_name in ("readiness_check", "readiness_metadata", "as_dict"):
        method = getattr(source, method_name, None)
        if callable(method):
            return method()
    return source


def _readiness_source_name(source: object) -> str:
    if callable(source):
        return getattr(source, "__name__", source.__class__.__name__)
    return source.__class__.__name__


def _required_from_payload(
    payload: Mapping[str, object],
    *,
    default: bool,
) -> bool:
    value = payload.get("required")
    if isinstance(value, bool):
        return value
    return default


def _status_from_payload(payload: object) -> ReadinessEvidenceStatus:
    if isinstance(payload, bool):
        return "passed" if payload else "failed"
    if not isinstance(payload, Mapping):
        return "unknown"
    if payload.get("block_production_readiness") is True:
        return "failed"
    if payload.get("block_plan_creation") is True:
        return "failed"
    for boolean_key in ("ok", "ready", "accepted"):
        value = payload.get(boolean_key)
        if isinstance(value, bool):
            return "passed" if value else "failed"
    status = payload.get("status")
    if isinstance(status, bool):
        return "passed" if status else "failed"
    if isinstance(status, str):
        normalized = status.lower()
        if normalized in {"ok", "ready", "pass", "passed", "success", "healthy"}:
            return "passed"
        if normalized in {
            "fail",
            "failed",
            "failure",
            "error",
            "blocked",
            "unhealthy",
        }:
            return "failed"
        if normalized in {"skip", "skipped"}:
            return "skipped"
        if normalized == "unknown":
            return "unknown"
    return "unknown"


def _evidence_mapping(payload: object) -> dict[str, object]:
    if isinstance(payload, Mapping):
        return _json_safe_mapping(payload)
    return {"value": _json_safe_value(payload)}


def _source_from_payload(payload: Mapping[str, object]) -> str:
    source = payload.get("source")
    if isinstance(source, str) and source.strip():
        return source
    return "readiness"


def _blocking_reason_from_payload(
    payload: Mapping[str, object],
    *,
    status: ReadinessEvidenceStatus,
    required: bool,
) -> str:
    reason = payload.get("blocking_reason")
    if isinstance(reason, str):
        return reason
    if not required or status == "passed":
        return ""
    if status == "skipped":
        return "required readiness evidence was skipped"
    if status == "unknown":
        return "required readiness evidence status is unknown"
    return "required readiness evidence failed"


def _validate_names(values: tuple[str, ...], *, field_name: str) -> None:
    if any(not value.strip() for value in values):
        raise ValueError(f"{field_name} must not contain empty names")


def _json_safe_mapping(values: Mapping[str, object]) -> dict[str, object]:
    return {
        str(key): _json_safe_value(value, key_hint=str(key))
        for key, value in values.items()
    }


def _json_safe_value(value: object, *, key_hint: str = "") -> object:
    if _is_secret_like_key(key_hint):
        return "<redacted>"
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple | list):
        return tuple(_json_safe_value(item) for item in value)
    if isinstance(value, Mapping):
        return _json_safe_mapping(value)
    return repr(value)


def _is_secret_like_key(key: str) -> bool:
    lowered = key.lower()
    parts = {
        part
        for part in lowered.replace("-", "_").replace(".", "_").split("_")
        if part
    }
    if lowered in _SECRET_KEY_PARTS:
        return True
    return bool(parts & _SECRET_KEY_PARTS)


def _dimensions(
    overrides: Mapping[str, tuple[ReadinessLevel, tuple[str, ...], str]],
    *,
    default_level: ReadinessLevel = "direct",
    default_evidence: tuple[str, ...] = ("AgentBuilder", "QueryLoop tests"),
    default_gap: str = "",
) -> dict[str, ReadinessDimension]:
    dimensions: dict[str, ReadinessDimension] = {}
    for name in REQUIRED_READINESS_DIMENSIONS:
        level, evidence, gap = overrides.get(
            name,
            (default_level, default_evidence, default_gap),
        )
        dimensions[name] = ReadinessDimension(
            name=name,
            level=level,
            evidence=evidence,
            gap=gap,
        )
    return dimensions


_FORMS: dict[str, AgentFormReadiness] = {
    "terminal-script": AgentFormReadiness(
        form_id="terminal-script",
        name="Terminal / Script Agent",
        overall_level="direct",
        summary=(
            "Single-process agent built with AgentBuilder and sync QueryLoop."
        ),
        recommended_profile="LocalRuntimeProfile",
        dimensions=_dimensions(
            {
                "auth": (
                    "not-applicable",
                    ("No network channel is exposed by default",),
                    "",
                ),
                "rate_limit": (
                    "not-applicable",
                    ("No network channel is exposed by default",),
                    "",
                ),
                "workspace": (
                    "primitives-ready",
                    (
                        "LocalWorkspaceProvider",
                        "WorkspacePolicy",
                        "WorkspaceExecutionIsolationProfile",
                        "WorkspaceToolSandboxPolicy",
                        *WORKSPACE_BACKEND_EVIDENCE,
                    ),
                    "OS/container sandboxing remains app/deployment-owned; Docker/E2B/enterprise runner adapters are deployment-owned.",
                ),
                "protocol": (
                    "not-applicable",
                    ("Programmatic Agent.run API",),
                    "",
                ),
                "schema_migration": (
                    "not-applicable",
                    ("No durable shared schema required by default",),
                    "",
                ),
            },
        ),
    ),
    "async-web-host": AgentFormReadiness(
        form_id="async-web-host",
        name="Async Web Host Agent",
        overall_level="direct",
        summary="AsyncQueryLoop and ASGI/SSE primitives for a single host.",
        recommended_profile="WebRuntimeProfile",
        dimensions=_dimensions(
            {
                "auth": (
                    "primitives-ready",
                    ("ChannelAuthPolicy", "AsgiAgentApp"),
                    "Production bearer/custom policy is application configured.",
                ),
                "rate_limit": (
                    "direct",
                    ("SlidingWindowRateLimiter", "ASGI channel tests"),
                    "",
                ),
                "workspace": (
                    "primitives-ready",
                    (
                        "WorkspaceHandle",
                        "WebRuntimeProfile metadata",
                        "WorkspaceExecutionIsolationProfile",
                        "WorkspaceToolSandboxPolicy",
                        *WORKSPACE_BACKEND_EVIDENCE,
                    ),
                    "OS/container sandboxing remains app/deployment-owned; Docker/E2B/enterprise runner adapters are deployment-owned.",
                ),
                "persistence": (
                    "primitives-ready",
                    ("SessionSnapshot", "SessionPersistence"),
                    "Single-host apps choose explicit restore/save policy.",
                ),
                "schema_migration": (
                    "primitives-ready",
                    ("SessionSnapshot serializers",),
                    "Snapshot migration policy is not yet versioned.",
                ),
            },
            default_evidence=("AsyncQueryLoop", "AsgiAgentApp"),
        ),
    ),
    "web-distributed-session": AgentFormReadiness(
        form_id="web-distributed-session",
        name="Web Distributed Session Agent",
        overall_level="primitives-ready",
        summary=(
            "Durable web session lifecycle plus Redis lease and Postgres "
            "snapshot adapters exist; production policy remains deployment-owned."
        ),
        recommended_profile="DistributedWebRuntimeProfile",
        required_app_glue=(
            "Redis-backed distributed lease configuration and TTL policy",
            "Postgres snapshot migration and credentials",
            "workspace policy",
            "failure recovery policy",
        ),
        dimensions=_dimensions(
            {
                "session_state": (
                    "primitives-ready",
                    (
                        "DistributedWebRuntimeProfile",
                        "DistributedWebSessionOperationsProfile",
                        "ProductionStatePlaneDeploymentProfile",
                        "DurableAgentSessionProvider",
                        "SessionSnapshot",
                        "PostgresSessionSnapshotPersistence",
                    ),
                    "Deployment must run snapshot migration and recovery policy.",
                ),
                "concurrency": (
                    "primitives-ready",
                    (
                        "ProductionStatePlaneDeploymentProfile",
                        "SessionLeaseStore",
                        "RedisSessionLeaseStore",
                    ),
                    "Deployment must configure Redis lease TTL and stale lease recovery policy.",
                ),
                "auth": (
                    "primitives-ready",
                    ("ChannelAuthPolicy",),
                    "Tenant/auth integration is application configured.",
                ),
                "rate_limit": (
                    "direct",
                    ("SlidingWindowRateLimiter",),
                    "",
                ),
                "workspace": (
                    "primitives-ready",
                    (
                        "WorkspacePolicy",
                        "DistributedWebRuntimeProfile",
                        "WorkspaceExecutionIsolationProfile",
                        "WorkspaceToolSandboxPolicy",
                        *WORKSPACE_BACKEND_EVIDENCE,
                    ),
                    "OS/container sandboxing remains app/deployment-owned; Docker/E2B/enterprise runner adapters are deployment-owned.",
                ),
                "persistence": (
                    "primitives-ready",
                    (
                        "SessionPersistence",
                        "SnapshotAgentFactory",
                        "PostgresSessionSnapshotPersistence",
                    ),
                    "Postgres schema migration, credentials, and live backend verification remain deployment-owned.",
                ),
                "schema_migration": (
                    "primitives-ready",
                    (
                        "SessionSnapshot serializers",
                        "2026-06-12-postgres-session-snapshots.sql",
                    ),
                    "Application rollout/version policy remains deployment-owned.",
                ),
            },
            default_evidence=("AsgiAgentApp", "AsyncQueryLoop"),
        ),
    ),
    "a2a-discovery": AgentFormReadiness(
        form_id="a2a-discovery",
        name="A2A Discovery Agent",
        overall_level="primitives-ready",
        summary=(
            "A2A Agent Card publication/discovery and a minimal "
            "message/send, message/stream, and task lookup/cancel operation "
            "boundaries exist, "
            "with SDK-level protocol version negotiation, signed-card, outbound "
            "auth, bearer credential rotation, and push notification config "
            "plus durable queued delivery/retry primitives; full operation parity "
            "is not claimed."
        ),
        recommended_profile="DistributedAgentProfile",
        required_app_glue=(
            "CA trust rollout policy",
            "deployment process supervision for webhook delivery workers",
            "worker alerting and restart supervision policy",
            "DNS pinning and egress proxy policy",
            "credential issuance and secret distribution policy",
            "tenant directory and role assignment lifecycle policy",
        ),
        dimensions=_dimensions(
            {
                "protocol": (
                    "primitives-ready",
                    (
                        "A2AAgentCard",
                        "A2AAgentSkill input/output modes",
                        "A2AAgentInterface",
                        "A2AAgentExtension",
                        "A2ACardResolver",
                        "A2AOperationServer",
                        "A2AOperationClient",
                        "A2AProtocolVersionPolicy",
                        "A2A-Version header",
                        "A2A 1.0 text part payload shape",
                        "A2A 1.0 file/data part payload shape",
                        "A2A artifact payload shape",
                        "A2A status/artifact event wrapper shape",
                        "A2AExtensionNegotiationPolicy",
                        "A2A-Extensions header",
                        "A2AConformanceHarness",
                        "SDK A2A self-conformance report",
                        "message/stream self-conformance check",
                        "message stream event self-conformance check",
                        "tasks/resubscribe self-conformance check",
                        "task resubscribe event self-conformance check",
                        "A2AExternalConformanceReportImporter",
                        "external A2A conformance result import",
                        "A2AExternalConformanceExecutionProfile",
                        "external A2A conformance execution profile",
                        "A2AExternalConformanceExecutionRecord",
                        "external conformance execution record",
                        "A2AExternalConformanceGateReport",
                        "external conformance gate report",
                        "A2AExternalConformanceInvocationPlan",
                        "external conformance invocation plan",
                        "A2AExternalConformanceInvocationGateReport",
                        "external conformance invocation gate report",
                        "PublicHttpsA2AEgressUrlPolicy",
                        "HostAllowListA2AEgressUrlPolicy",
                        "RejectAllA2AInboundAuthPolicy",
                        "default inbound A2A operation auth is fail-closed",
                        "AllowAllA2AInboundAuthPolicy is explicit local/dev opt-in",
                        "A2AOperationClient default public HTTPS egress policy",
                        "TaskStoreA2ATaskLifecycleRunner",
                        "well-known route",
                        "message/send route",
                        "message/stream route",
                        "message stream operation boundary",
                        "A2AOperationClient.stream_message",
                        "A2AOperationClient.stream_message_events",
                        "A2AMessageStreamEvent",
                        "parse_a2a_sse_events",
                        "task get/cancel routes",
                        "A2ATaskSubscriptionEvent",
                        "tasks/resubscribe operation",
                        "A2AOperationClient.task_resubscribe",
                        "task subscribe route",
                        "task subscribe operation boundary",
                        "A2APushNotificationConfig",
                        "InMemoryA2APushNotificationConfigStore",
                        "A2APushNotificationDispatcher",
                        "A2APushNotificationDeliveryWorker",
                        "A2APushNotificationDaemon",
                        "A2APushNotificationDeploymentProfile",
                        "A2AStreamLifecycleDeploymentProfile",
                        "A2APushNotificationHealthPolicy",
                        "A2APushNotificationHealthReport",
                        "A2APushNotificationRetryPolicy",
                        "HostAllowListA2APushNotificationUrlPolicy",
                        "PostgresA2APushNotificationDeliveryStore",
                        "push notification config routes",
                    ),
                    (
                        "Long-running stream reconnect, backpressure, durable "
                        "stream cursor storage, fan-out, deployment process "
                        "supervision, alerting/restart policy, credentials, "
                        "DNS pinning or egress proxy controls beyond SDK URL "
                        "policy, CA rollout, tenant directory and role "
                        "assignment lifecycle, external suite execution, and "
                        "certification attestation remain deployment-owned "
                        "around the external conformance invocation plan, "
                        "execution record, and gate report boundaries."
                    ),
                ),
                "auth": (
                    "primitives-ready",
                    (
                        "A2A Agent Card security fields",
                        "A2ACardSignature",
                        "HmacA2ACardSigner",
                        "HmacA2ACardVerifier",
                        "StaticA2ACardTrustStore",
                        "JwksA2ACardTrustStore",
                        "RotatingA2ACardTrustStore",
                        "RotatingHmacA2ACardSigner",
                        "StaticBearerA2AAuthProvider",
                        "StaticBearerA2AInboundAuthPolicy",
                        "A2ABearerCredential",
                        "RotatingBearerA2ACredentialStore",
                        "RotatingBearerA2AAuthProvider",
                        "RotatingBearerA2AInboundAuthPolicy",
                        "HmacA2AJwtVerifier",
                        "OidcDiscoveryMetadataProvider",
                        "OIDC discovery metadata validation",
                        "JwksA2AJwtVerifier",
                        "RS256/JWKS JWT verification",
                        "PublicHttpsA2AEgressUrlPolicy",
                        "HostAllowListA2AEgressUrlPolicy",
                        "RejectAllA2AInboundAuthPolicy",
                        "OidcClaimsA2AInboundAuthPolicy",
                        "JWT/OIDC claims validation",
                        "PeerAllowListA2AInboundAuthPolicy",
                        "OperationAllowListA2AInboundAuthPolicy",
                        "ResourceAllowListA2AInboundAuthPolicy",
                        "A2ATenantRbacRule",
                        "ClaimsTenantRbacA2AInboundAuthPolicy",
                        "claims-backed tenant RBAC policy",
                    ),
                    (
                        "CA trust rollout, DNS pinning or egress proxy controls "
                        "beyond SDK URL policy, credential issuance, secret "
                        "distribution, KMS/secret-manager governance, tenant "
                        "directory, role assignment lifecycle, and IdP "
                        "administration remain app/deployment-owned."
                    ),
                ),
                "rate_limit": (
                    "primitives-ready",
                    (
                        "ASGI rate limiter",
                        "A2AOperationRateLimitPolicy",
                        "PeerKeyA2AOperationRateLimitPolicy",
                        "A2APeerIdResolver",
                        "A2ARateLimitError",
                    ),
                    (
                        "Distributed/global quota storage, gateway enforcement, "
                        "billing tiers, and commercial entitlement policy "
                        "remain deployment-owned."
                    ),
                ),
                "workspace": (
                    "not-applicable",
                    ("Agent Card omits local workspace paths",),
                    "",
                ),
                "persistence": (
                    "primitives-ready",
                    (
                        "PersistentAgentRegistry",
                        "NacosAgentRegistryAdapter",
                        "NacosAgentCardResolver",
                        "NacosRegistryClient",
                        "NacosRegistryConfig",
                        "NacosRegistryEvidence",
                        "Nacos namespace_id evidence",
                        "discovery-only Nacos metadata",
                        "InMemoryA2APushNotificationConfigStore",
                        "InMemoryA2APushNotificationDeliveryStore",
                        "PostgresA2APushNotificationConfigStore",
                        "PostgresA2APushNotificationDeliveryStore",
                    ),
                    "Nacos is not task/plan/session/queue/worker runtime truth; registry credentials, live backend verification, and webhook worker scheduling remain app-owned.",
                ),
                "schema_migration": (
                    "primitives-ready",
                    (
                        "A2A card serialization tests",
                        "A2A protocol version negotiation tests",
                        "A2A text part payload tests",
                        "A2A file/data part payload tests",
                        "A2A artifact/event payload tests",
                        "A2A extension negotiation tests",
                        "SDK A2A self-conformance report",
                        "external A2A conformance result import",
                        "external A2A conformance execution profile",
                        "external conformance invocation plan",
                        "external conformance invocation gate report",
                        "external conformance execution record",
                        "external conformance gate report",
                        "2026-06-15-postgres-a2a-push-notifications.sql",
                    ),
                    "External A2A conformance suite execution, rollout policy, and certification attestation remain deployment-owned.",
                ),
            },
            default_level="primitives-ready",
            default_evidence=(
                "A2AAdapter",
                "A2AServerAdapter",
                "A2AOperationServer",
            ),
            default_gap="Production A2A service policy remains app-owned.",
        ),
    ),
    "team-discussion": AgentFormReadiness(
        form_id="team-discussion",
        name="Team Discussion Agent",
        overall_level="primitives-ready",
        summary=(
            "Team records/messages/wakeup primitives exist; worker sessions "
            "daemon worker polling, persistent retry/backoff, and cancellation "
            "intent primitives are SDK boundaries and can be assembled through "
            "the distributed team runtime profile preset."
        ),
        recommended_profile="DistributedTeamRuntimeProfile",
        required_app_glue=(),
        dimensions=_dimensions(
            {
                "session_state": (
                    "primitives-ready",
                    (
                        "DistributedTeamRuntimeProfile",
                        "TeamRuntime",
                        "TeamRecord",
                        "TeamMessage",
                        "TeamWorkerSessionProvider",
                    ),
                    "Worker session durability policy remains deployment-owned.",
                ),
                "concurrency": (
                    "primitives-ready",
                    (
                        "DistributedTeamRuntimeProfile",
                        "AgentMessageQueue",
                        "TeamNoticeStore",
                        "RedisAgentMessageQueue",
                        "ProductionStatePlaneDeploymentProfile",
                        "TeamWorkerRunner",
                        "TeamWorkerDaemon",
                        "WorkerProcessLifecycleDeploymentProfile",
                        "TeamWorkerRetryPolicy",
                        "InMemoryTeamWorkerRetryStore",
                        "PostgresTeamWorkerRetryStore",
                        "2026-06-12-postgres-team-worker-retries.sql",
                        "TeamWorkerCancellationStore",
                        "InMemoryTeamWorkerCancellationStore",
                        "PostgresTeamWorkerCancellationStore",
                        "2026-06-15-postgres-team-worker-cancellations.sql",
                    ),
                    "Worker process supervision and scaling policy remain deployment-owned.",
                ),
                "workspace": (
                    "primitives-ready",
                    (
                        "TeamMemberRecord.workspace",
                        "WorkspaceHandle",
                        "TeamWorkerPermissionPolicy",
                        "WorkspaceExecutionIsolationProfile",
                        "WorkspaceToolSandboxPolicy",
                        "ToolPathSandboxRule",
                        *WORKSPACE_BACKEND_EVIDENCE,
                    ),
                    "OS/container sandboxing remains deployment-owned; Docker/E2B/enterprise runner adapters are deployment-owned.",
                ),
                "protocol": (
                    "primitives-ready",
                    (
                        "TeamRuntime messages_for",
                        "TeamTools",
                        "DistributedTeamRuntimeProfile",
                        "TeamWorkerRunner",
                        "TeamUiEvent",
                        "InMemoryTeamUiStreamStore",
                        "PostgresTeamUiStreamStore",
                        "team UI JSON replay endpoint",
                        "team UI SSE/follow endpoint",
                    ),
                    "Team UI transport is available; production policy remains app-owned.",
                ),
                "persistence": (
                    "primitives-ready",
                    (
                        "InMemoryTeamStore",
                        "PostgresTeamStore",
                        "PostgresTeamUiStreamStore",
                    ),
                    "Production deployments must run the team-store migration and configure credentials.",
                ),
                "schema_migration": (
                    "primitives-ready",
                    (
                        "TeamRecord dataclasses",
                        "2026-06-12-postgres-team-store.sql",
                        "2026-06-12-postgres-team-worker-retries.sql",
                        "2026-06-15-postgres-team-ui-events.sql",
                    ),
                    "Application rollout/version policy remains deployment-owned.",
                ),
            },
            default_level="primitives-ready",
            default_evidence=("AgentCoordinator", "AgentMessageQueue"),
            default_gap="Production team orchestration policy remains app-owned.",
        ),
    ),
    "planner-intent-router": AgentFormReadiness(
        form_id="planner-intent-router",
        name="Planner / Intent Router Agent",
        overall_level="primitives-ready",
        summary=(
            "PlannerRuntime, PlannerTools, templates, and summary projection "
            "support intent-router, plan-and-execute, auditable step retry, "
            "ready-step dispatch, schedulable plan selection, plan "
            "claim/lease, claim-before-tick scheduler batches, one-shot "
            "scheduler tick, scheduler daemon polling, and structured "
            "decomposition validation, dispatch supervision, and stale-claim "
            "sweep patterns."
        ),
        recommended_profile="DistributedAgentProfile",
        required_app_glue=(
            "automatic LLM decomposition policy",
            "LLM prompt/model/approval/evaluation policy",
            "plan discovery and tenant filtering",
            "planner scheduler governance profile configuration",
            "distributed scheduler locks and leader election",
            "stale claim sweep scheduling policy",
            "worker dispatch loop execution",
            "process supervision and restart policy",
            "compensation orchestration",
        ),
        dimensions=_dimensions(
            {
                "session_state": (
                    "primitives-ready",
                    (
                        "PlannerRuntime",
                        "PlanDecomposition",
                        "PlanDecompositionGatePolicy",
                        "PlanDecompositionGateReport",
                        "PlanDecompositionValidationReport",
                        "PlannerRuntime.gate_decomposition_proposal",
                        "PlannerRuntime.validate_decomposition",
                        "PlanStepSpec",
                        "PlanStep.depends_on",
                        "PlannerDecompositionPolicyDeploymentProfile",
                        "PlannerLlmDecompositionGovernanceProfile",
                        "governance reference readiness payloads",
                        "PlannerOrchestrationDeploymentProfile",
                        "PlanStore",
                        "PostgresPlanStore",
                    ),
                    "LLM prompt/model/approval/evaluation policy, plan discovery, and distributed scheduler locks remain deployment-owned.",
                ),
                "concurrency": (
                    "primitives-ready",
                    (
                        "AgentCoordinator assignment boundary",
                        "PlanRetryPolicy",
                        "PlanDispatchReport",
                        "PlanDispatchSkip",
                        "PlanSchedulerTickReport",
                        "PlanClaimedSchedulerTickReport",
                        "PlanClaimedSchedulerTickSkip",
                        "PlannerClaimedSchedulerDaemon",
                        "PlannerClaimedSchedulerDaemonState",
                        "PlannerClaimedSchedulerDaemonError",
                        "PlannerSchedulerGovernanceDeploymentProfile",
                        "ProductionStatePlaneDeploymentProfile",
                        "planner scheduler governance profile",
                        "plan_discovery_policy",
                        "tenant_routing_policy",
                        "global_fairness_policy",
                        "scheduler_lock_policy",
                        "leader_election_policy",
                        "stale_lease_recovery_policy",
                        "live_backend_verification",
                        "PlannerWorkerDispatchSupervisionProfile",
                        "PlanClaimSweepReport",
                        "PlanClaimSweepSkip",
                        "PlanClaimSweepStore",
                        "PlannerStaleClaimSweepProfile",
                        "PlannerSchedulablePlan",
                        "PlanClaimStore",
                        "InMemoryPlanClaimStore",
                        "PostgresPlanClaimStore",
                        "PlanClaimRecord",
                        "plan claim/lease boundary",
                        "claim-before-tick scheduler boundary",
                        "PlannerSchedulerDaemon",
                        "PlannerSchedulerDaemonState",
                        "PlannerSchedulerDaemonError",
                        "WorkerProcessLifecycleDeploymentProfile",
                        "PlanStep attempts/next_retry_at/retry_status",
                    ),
                    "Schedulable-plan selection plus local and Postgres-backed plan claim/lease stores, claim-before-tick scheduler batches, claimed scheduler daemon polling, scheduler governance readiness metadata, dispatch supervision payloads, and exact stale-claim sweep reports/releases are SDK-owned, but tenant routing, global fairness, distributed scheduler locks, leader election, stale claim sweep scheduling policy, worker dispatch loop execution, process supervision, and compensation policy remain deployment-owned.",
                ),
                "workspace": (
                    "primitives-ready",
                    (
                        "SubAgentTemplate.workspace_scope",
                        "WorkspaceHandle",
                        "WorkspaceExecutionIsolationProfile",
                        *WORKSPACE_BACKEND_EVIDENCE,
                    ),
                    "Workspace narrowing enforcement exists as SDK metadata; OS/container sandboxing remains deployment-owned; Docker/E2B/enterprise runner adapters are deployment-owned.",
                ),
                "protocol": (
                    "primitives-ready",
                    (
                        "PlannerTools",
                        "plan_gate_decomposition_proposal",
                        "plan_create_from_decomposition",
                        "PlannerRuntime.validate_decomposition",
                        "plan_ready_steps",
                        "plan_fail_step",
                        "plan_retryable_steps",
                        "plan_retry_step",
                        "plan_dispatch_ready_steps",
                        "PlannerRuntime.schedulable_plans",
                        "plan_schedulable_plans",
                        "schedulable plan selection",
                        "PlannerRuntime.claim_schedulable_plans",
                        "plan_claim_schedulable_plans",
                        "PlannerRuntime.claimed_scheduler_tick",
                        "plan_claimed_scheduler_tick",
                        "PlannerClaimedSchedulerDaemon",
                        "PlannerRuntime.sweep_expired_claims",
                        "claim-before-tick scheduler boundary",
                        "planner worker dispatch supervision profile",
                        "stale claim sweep boundary",
                        "plan_scheduler_tick",
                        "explicit plan ids",
                    ),
                    "PlannerRuntime.schedulable_plans filters owner/status/ready/retryable work, PlannerRuntime.claim_schedulable_plans composes that selection with the injected PlanClaimStore, PlannerRuntime.claimed_scheduler_tick only ticks plans successfully claimed by the current worker, PlannerClaimedSchedulerDaemon polls that claim-before-tick boundary, PlannerRuntime.sweep_expired_claims reports and safely releases exact expired claim records, and PlannerWorkerDispatchSupervisionProfile plus PlannerStaleClaimSweepProfile project those reports into health/readiness payloads. PlannerSchedulerDaemon polls explicitly supplied plan ids, while tenant routing, global fairness, distributed scheduler locks, stale claim sweep scheduling policy, worker dispatch loop execution, process supervision, and compensation orchestration remain deployment-owned.",
                ),
                "persistence": (
                    "primitives-ready",
                    (
                        "InMemoryPlanStore",
                        "PostgresPlanStore",
                        "PostgresPlanClaimStore",
                        "PlanStore protocol",
                        "PlanClaimStore protocol",
                        "PlanClaimSweepStore protocol",
                    ),
                    "Deployment must run the plan-store and plan-claim migrations and configure credentials.",
                ),
                "schema_migration": (
                    "primitives-ready",
                    (
                        "PlanState dataclasses",
                        "2026-06-15-postgres-plan-store.sql",
                        "2026-06-16-postgres-plan-claims.sql",
                    ),
                    "Application rollout/version policy remains deployment-owned.",
                ),
            },
            default_level="primitives-ready",
            default_evidence=("PlannerRuntime", "PlannerTools"),
            default_gap="Production planner policy remains app-owned.",
        ),
    ),
}


__all__ = [
    "AgentFormReadiness",
    "ProductionReadinessEvidenceBundle",
    "REQUIRED_READINESS_DIMENSIONS",
    "ReadinessDimension",
    "ReadinessEvidenceCheck",
    "ReadinessEvidenceStatus",
    "ReadinessLevel",
    "get_agent_form_readiness",
    "list_agent_form_readiness",
]
