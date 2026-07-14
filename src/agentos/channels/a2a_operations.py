from __future__ import annotations

import ipaddress
import base64
import json
import time
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from threading import Event, RLock, Thread
from typing import Literal, Protocol, cast
from urllib.parse import urlparse

from agentos.channels.a2a import (
    A2AAgentCard,
    A2AAgentExtension,
    A2AAuthProvider,
    A2AEgressUrlPolicy,
    A2AInboundAuthError,
    A2AInboundAuthPolicy,
    A2AOperationInboundAuthPolicy,
    A2AResourceInboundAuthPolicy,
    PublicHttpsA2AEgressUrlPolicy,
    RejectAllA2AInboundAuthPolicy,
    A2ATransport,
    UrllibA2ATransport,
    _validate_a2a_egress_url,
)
from agentos.channels.a2a_execution import execute_a2a_agent, project_a2a_task
from agentos.channels.rate_limit import RateLimiter
from agentos.multi.task_store import TaskStore
from agentos.multi.types import TaskRecord, TaskStatus
from agentos.observability import use_incoming_trace_headers
from agentos.persistence.postgres import BackendUnavailableError
from agentos.persistence.protocols import PostgresConnection, PostgresCursor
from agentos.runtime import Agent


A2A_PROTOCOL_VERSION_HEADER = "A2A-Version"
A2A_EXTENSIONS_HEADER = "A2A-Extensions"
A2A_DEFAULT_PROTOCOL_VERSION = "1.0"
A2A_LEGACY_EMPTY_PROTOCOL_VERSION = "0.3"
A2A_DEFAULT_SUPPORTED_PROTOCOL_VERSIONS = (
    A2A_LEGACY_EMPTY_PROTOCOL_VERSION,
    A2A_DEFAULT_PROTOCOL_VERSION,
)

A2AMessageRole = Literal["user", "agent"]
A2AMessagePartKind = Literal["text", "file", "data"]
A2AOfficialOperationName = Literal[
    "SendMessage",
    "SendStreamingMessage",
    "SubscribeToTask",
]
A2A_OFFICIAL_OPERATION_TO_JSONRPC_METHOD: Mapping[
    A2AOfficialOperationName,
    str,
] = {
    "SendMessage": "SendMessage",
    "SendStreamingMessage": "SendStreamingMessage",
    "SubscribeToTask": "SubscribeToTask",
}
A2A_LEGACY_JSONRPC_METHOD_TO_OFFICIAL_OPERATION: Mapping[
    str,
    A2AOfficialOperationName,
] = {
    "message/send": "SendMessage",
    "message/stream": "SendStreamingMessage",
    "tasks/resubscribe": "SubscribeToTask",
}
A2A_JSONRPC_METHOD_TO_OFFICIAL_OPERATION: Mapping[
    str,
    A2AOfficialOperationName,
] = {
    method: operation
    for operation, method in A2A_OFFICIAL_OPERATION_TO_JSONRPC_METHOD.items()
} | dict(A2A_LEGACY_JSONRPC_METHOD_TO_OFFICIAL_OPERATION)
A2ATaskState = Literal[
    "submitted",
    "working",
    "input-required",
    "completed",
    "canceled",
    "failed",
    "rejected",
    "auth-required",
    "unknown",
]


@dataclass(frozen=True, slots=True)
class A2AMessagePart:
    """Protocol-facing A2A message part."""

    kind: A2AMessagePartKind
    text: str | None = None
    raw: str | None = None
    url: str | None = None
    filename: str | None = None
    media_type: str | None = None
    data: Mapping[str, object] | None = None

    @classmethod
    def from_text(cls, value: str) -> A2AMessagePart:
        """Create a text part."""

        return cls(kind="text", text=value)

    @classmethod
    def from_file_bytes(
        cls,
        value: str,
        *,
        filename: str | None = None,
        media_type: str | None = None,
    ) -> A2AMessagePart:
        """Create a file part backed by inline base64/raw bytes."""

        return cls(
            kind="file",
            raw=value,
            filename=filename,
            media_type=media_type,
        )

    @classmethod
    def from_file_url(
        cls,
        value: str,
        *,
        filename: str | None = None,
        media_type: str | None = None,
    ) -> A2AMessagePart:
        """Create a file part backed by a URL."""

        return cls(
            kind="file",
            url=value,
            filename=filename,
            media_type=media_type,
        )

    @classmethod
    def from_data(
        cls,
        value: Mapping[str, object],
        *,
        media_type: str | None = "application/json",
    ) -> A2AMessagePart:
        """Create a structured data part."""

        return cls(
            kind="data",
            data=dict(value),
            media_type=media_type,
        )


@dataclass(frozen=True, slots=True)
class A2AMessage:
    """Protocol-facing A2A message."""

    role: A2AMessageRole
    parts: tuple[A2AMessagePart, ...]
    message_id: str | None = None
    context_id: str | None = None
    task_id: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def text_content(self) -> str:
        """Return text parts joined for Agent.run input."""

        return "\n".join(
            part.text for part in self.parts
            if part.kind == "text" and part.text is not None
        )


@dataclass(frozen=True, slots=True)
class A2AArtifact:
    """Protocol-facing A2A artifact projection."""

    artifact_id: str
    parts: tuple[A2AMessagePart, ...]
    name: str | None = None
    description: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class A2ATask:
    """Protocol-facing A2A task projection."""

    task_id: str
    context_id: str | None
    state: A2ATaskState
    messages: tuple[A2AMessage, ...] = ()
    artifacts: tuple[A2AArtifact, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class A2ATaskSubscriptionEvent:
    """Protocol-facing task status update event for A2A SSE subscriptions."""

    event_id: str
    task: A2ATask
    final: bool = False


@dataclass(frozen=True, slots=True)
class A2ATaskArtifactUpdateEvent:
    """Protocol-facing A2A task artifact update event."""

    event_id: str
    task_id: str
    artifact: A2AArtifact
    context_id: str | None = None
    append: bool = False
    last_chunk: bool = False
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class A2AMessageStreamEvent:
    """Typed event parsed from an A2A message/stream SSE response."""

    event: str | None
    raw: Mapping[str, object]
    task: A2ATask | None = None
    message: A2AMessage | None = None
    task_event: A2ATaskSubscriptionEvent | None = None
    artifact_event: A2ATaskArtifactUpdateEvent | None = None


class A2APushNotificationConfigError(ValueError):
    """Raised when a push notification config is invalid."""


@dataclass(frozen=True, repr=False, slots=True)
class A2APushNotificationAuthentication:
    """Authentication settings for outbound webhook notifications."""

    schemes: tuple[str, ...]
    credentials: str | None = None

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}"
            f"(schemes={self.schemes!r}, credentials=<redacted>)"
        )


@dataclass(frozen=True, repr=False, slots=True)
class A2APushNotificationConfig:
    """Webhook configuration for A2A task push notifications."""

    url: str
    authentication: A2APushNotificationAuthentication | None = None
    config_id: str | None = None
    token: str | None = None

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}"
            f"(url={self.url!r}, config_id={self.config_id!r}, "
            "authentication=<redacted>, token=<redacted>)"
        )


class A2APushNotificationUrlPolicy(Protocol):
    """Policy boundary for validating outbound push notification webhook URLs."""

    def validate(self, config: A2APushNotificationConfig) -> None:
        """Raise when a push notification webhook URL is not allowed."""


@dataclass(frozen=True, slots=True)
class PublicHttpsA2APushNotificationUrlPolicy:
    """Require HTTPS URLs with public, non-local literal IP hostnames."""

    def validate(self, config: A2APushNotificationConfig) -> None:
        """Validate the default public HTTPS webhook URL policy."""

        parsed = urlparse(config.url)
        if parsed.scheme.lower() != "https" or not parsed.hostname:
            raise A2APushNotificationConfigError("https webhook url is required")
        hostname = _normalized_push_notification_hostname(parsed.hostname)
        if hostname in {"localhost"} or hostname.endswith(".localhost"):
            raise A2APushNotificationConfigError("public webhook hostname is required")
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
            raise A2APushNotificationConfigError("public webhook hostname is required")


@dataclass(frozen=True, slots=True)
class HostAllowListA2APushNotificationUrlPolicy:
    """Allow webhook URLs only for explicitly trusted hosts or domain suffixes."""

    allowed_hosts: tuple[str, ...] = ()
    allowed_domain_suffixes: tuple[str, ...] = ()
    base_policy: A2APushNotificationUrlPolicy | None = None

    def __init__(
        self,
        *,
        allowed_hosts: tuple[str, ...] = (),
        allowed_domain_suffixes: tuple[str, ...] = (),
        base_policy: A2APushNotificationUrlPolicy | None = None,
    ) -> None:
        object.__setattr__(
            self,
            "allowed_hosts",
            tuple(
                _normalized_push_notification_hostname(host)
                for host in allowed_hosts
                if host
            ),
        )
        object.__setattr__(
            self,
            "allowed_domain_suffixes",
            tuple(
                _normalized_push_notification_domain_suffix(suffix)
                for suffix in allowed_domain_suffixes
                if suffix
            ),
        )
        object.__setattr__(
            self,
            "base_policy",
            base_policy or PublicHttpsA2APushNotificationUrlPolicy(),
        )

    def validate(self, config: A2APushNotificationConfig) -> None:
        """Validate HTTPS/public-host rules plus an exact or suffix allow-list."""

        base_policy = self.base_policy or PublicHttpsA2APushNotificationUrlPolicy()
        base_policy.validate(config)
        parsed = urlparse(config.url)
        if parsed.hostname is None:
            raise A2APushNotificationConfigError("https webhook url is required")
        hostname = _normalized_push_notification_hostname(parsed.hostname)
        if hostname in self.allowed_hosts:
            return
        if any(
            _push_notification_hostname_matches_suffix(hostname, suffix)
            for suffix in self.allowed_domain_suffixes
        ):
            return
        raise A2APushNotificationConfigError("allowed webhook hostname is required")


class A2APushNotificationConfigStore(Protocol):
    """Boundary for persistent A2A task push notification configs."""

    def create(
        self,
        task_id: str,
        config: A2APushNotificationConfig,
    ) -> A2APushNotificationConfig:
        """Create or replace a push notification config for a task."""

    def get(
        self,
        task_id: str,
        config_id: str,
    ) -> A2APushNotificationConfig | None:
        """Return a push notification config by id."""

    def list(self, task_id: str) -> tuple[A2APushNotificationConfig, ...]:
        """Return push notification configs for a task."""

    def delete(self, task_id: str, config_id: str) -> bool:
        """Delete a push notification config if present."""


class InMemoryA2APushNotificationConfigStore:
    """In-memory A2A push notification config store."""

    def __init__(
        self,
        *,
        url_policy: A2APushNotificationUrlPolicy | None = None,
    ) -> None:
        self._configs: dict[tuple[str, str], A2APushNotificationConfig] = {}
        self._url_policy = url_policy or PublicHttpsA2APushNotificationUrlPolicy()

    def create(
        self,
        task_id: str,
        config: A2APushNotificationConfig,
    ) -> A2APushNotificationConfig:
        """Create or replace a push notification config for a task."""

        config_id = config.config_id or f"push_{int(time.time() * 1000)}"
        stored = A2APushNotificationConfig(
            url=config.url,
            authentication=config.authentication,
            config_id=config_id,
            token=config.token,
        )
        self._url_policy.validate(stored)
        self._configs[(task_id, config_id)] = stored
        return stored

    def get(
        self,
        task_id: str,
        config_id: str,
    ) -> A2APushNotificationConfig | None:
        """Return a push notification config by id."""

        return self._configs.get((task_id, config_id))

    def list(self, task_id: str) -> tuple[A2APushNotificationConfig, ...]:
        """Return push notification configs for a task."""

        return tuple(
            config
            for (stored_task_id, _config_id), config in sorted(
                self._configs.items(),
            )
            if stored_task_id == task_id
        )

    def delete(self, task_id: str, config_id: str) -> bool:
        """Delete a push notification config if present."""

        return self._configs.pop((task_id, config_id), None) is not None


A2APushNotificationDeliveryStatus = Literal["delivered", "failed"]
A2APushNotificationDeliveryRecordStatus = Literal[
    "queued",
    "running",
    "delivered",
    "retry_scheduled",
    "dead_letter",
]
A2APushNotificationDaemonStatus = Literal[
    "idle",
    "running",
    "stopping",
    "stopped",
]
A2APushNotificationHealthStatus = Literal[
    "healthy",
    "degraded",
    "unhealthy",
    "stopped",
    "unstarted",
]


@dataclass(frozen=True, repr=False, slots=True)
class A2APushNotificationDelivery:
    """Result of a single webhook push notification delivery attempt."""

    config_id: str | None
    url: str
    status: A2APushNotificationDeliveryStatus
    response: Mapping[str, object] | None = None
    error: str | None = None

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}"
            f"(config_id={self.config_id!r}, url={self.url!r}, "
            f"status={self.status!r}, error={self.error!r})"
        )


@dataclass(frozen=True, slots=True)
class A2APushNotificationRetryPolicy:
    """Retry/backoff policy for queued webhook push deliveries."""

    max_attempts: int = 1
    backoff_seconds: float = 0.0
    backoff_multiplier: float = 1.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.backoff_seconds < 0:
            raise ValueError("backoff_seconds must be >= 0")
        if self.backoff_multiplier < 1:
            raise ValueError("backoff_multiplier must be >= 1")

    def delay_for_attempt(self, attempt: int) -> float:
        """Return retry delay after the given failed attempt."""

        return self.backoff_seconds * (
            self.backoff_multiplier ** max(0, attempt - 1)
        )


@dataclass(frozen=True, repr=False, slots=True)
class A2APushNotificationDeliveryRecord:
    """Queued state for one webhook push notification delivery."""

    delivery_id: str
    task_id: str
    config: A2APushNotificationConfig
    event: A2ATaskSubscriptionEvent
    created_at: float
    next_run_at: float
    status: A2APushNotificationDeliveryRecordStatus = "queued"
    attempts: int = 0
    last_error: str | None = None
    response: Mapping[str, object] | None = None
    worker_id: str | None = None
    lease_expires_at: float | None = None
    delivered_at: float | None = None
    dead_lettered_at: float | None = None

    def __repr__(self) -> str:
        last_error = _redact_push_notification_error(self.config, self.last_error)
        return (
            f"{self.__class__.__name__}"
            f"(delivery_id={self.delivery_id!r}, task_id={self.task_id!r}, "
            f"config_id={self.config.config_id!r}, url={self.config.url!r}, "
            f"event_id={self.event.event_id!r}, status={self.status!r}, "
            f"attempts={self.attempts!r}, next_run_at={self.next_run_at!r}, "
            f"last_error={last_error!r})"
        )


@dataclass(frozen=True, slots=True)
class A2APushNotificationDaemonError:
    """Diagnostic record for one failed daemon polling iteration."""

    worker_id: str
    error: str
    raised_at: float


@dataclass(frozen=True, slots=True)
class A2APushNotificationDaemonState:
    """Snapshot of an A2A push notification daemon lifecycle."""

    status: A2APushNotificationDaemonStatus
    worker_id: str
    poll_interval_seconds: float
    batch_limit: int
    iterations: int = 0
    started_at: float | None = None
    stopped_at: float | None = None
    last_run_at: float | None = None
    last_results: tuple[A2APushNotificationDeliveryRecord, ...] = ()
    errors: tuple[A2APushNotificationDaemonError, ...] = ()


@dataclass(frozen=True, slots=True)
class A2APushNotificationHealthReport:
    """Health projection for one A2A push notification daemon."""

    status: A2APushNotificationHealthStatus
    reason: str
    worker_id: str
    daemon_status: A2APushNotificationDaemonStatus
    iterations: int
    last_run_at: float | None = None
    seconds_since_last_run: float | None = None
    recent_error_count: int = 0
    last_error: str | None = None


@dataclass(frozen=True, slots=True)
class A2APushNotificationHealthPolicy:
    """Classify A2A push daemon state for supervisors and health checks."""

    max_stale_seconds: float = 60.0
    error_window_seconds: float = 300.0
    degraded_error_threshold: int = 1
    unhealthy_error_threshold: int = 3

    def __post_init__(self) -> None:
        if self.max_stale_seconds < 0:
            raise ValueError("max_stale_seconds must be >= 0")
        if self.error_window_seconds < 0:
            raise ValueError("error_window_seconds must be >= 0")
        if self.degraded_error_threshold < 1:
            raise ValueError("degraded_error_threshold must be >= 1")
        if self.unhealthy_error_threshold < self.degraded_error_threshold:
            raise ValueError(
                "unhealthy_error_threshold must be >= degraded_error_threshold",
            )

    def evaluate(
        self,
        state: A2APushNotificationDaemonState,
        *,
        now: float | None = None,
    ) -> A2APushNotificationHealthReport:
        """Return a health report for an immutable daemon state snapshot."""

        effective_now = time.time() if now is None else float(now)
        seconds_since_last_run = (
            None
            if state.last_run_at is None
            else max(0.0, effective_now - state.last_run_at)
        )
        recent_errors = self._recent_errors(state, now=effective_now)
        last_error = recent_errors[-1].error if recent_errors else None
        status: A2APushNotificationHealthStatus
        reason: str
        if state.iterations == 0 and state.last_run_at is None:
            status = "unstarted"
            reason = "worker has not run yet"
        elif (
            seconds_since_last_run is not None
            and seconds_since_last_run > self.max_stale_seconds
        ):
            status = "unhealthy"
            reason = "worker polling is stale"
        elif len(recent_errors) >= self.unhealthy_error_threshold:
            status = "unhealthy"
            reason = "worker has too many recent polling errors"
        elif len(recent_errors) >= self.degraded_error_threshold:
            status = "degraded"
            reason = "worker has recent polling errors"
        elif state.status == "stopped":
            status = "stopped"
            reason = "worker is stopped"
        else:
            status = "healthy"
            reason = "worker is polling"
        return A2APushNotificationHealthReport(
            status=status,
            reason=reason,
            worker_id=state.worker_id,
            daemon_status=state.status,
            iterations=state.iterations,
            last_run_at=state.last_run_at,
            seconds_since_last_run=seconds_since_last_run,
            recent_error_count=len(recent_errors),
            last_error=last_error,
        )

    def _recent_errors(
        self,
        state: A2APushNotificationDaemonState,
        *,
        now: float,
    ) -> tuple[A2APushNotificationDaemonError, ...]:
        cutoff = now - self.error_window_seconds
        return tuple(error for error in state.errors if error.raised_at >= cutoff)


A2A_STREAM_LIFECYCLE_REQUIRED_COMPONENTS: tuple[str, ...] = (
    "reconnect_policy",
    "durable_cursor_store",
    "fanout_broker",
    "backpressure_policy",
    "stream_supervision",
)


@dataclass(frozen=True, slots=True)
class A2AStreamLifecycleDeploymentProfile:
    """Deployment-facing readiness contract for A2A stream lifecycle policy."""

    configured_components: tuple[str, ...] = ()
    required_components: tuple[str, ...] = (
        A2A_STREAM_LIFECYCLE_REQUIRED_COMPONENTS
    )
    probe_name: str = "a2a_stream_lifecycle"

    def __post_init__(self) -> None:
        if not self.probe_name.strip():
            raise ValueError("probe_name must not be empty")
        if not self.required_components:
            raise ValueError("required_components must not be empty")
        self._validate_component_names(
            self.required_components,
            field_name="required_components",
        )
        self._validate_component_names(
            self.configured_components,
            field_name="configured_components",
        )

    def missing_components(self) -> tuple[str, ...]:
        """Return required lifecycle components not configured by deployment."""

        configured = set(self.configured_components)
        return tuple(
            component
            for component in self.required_components
            if component not in configured
        )

    def readiness_metadata(self) -> dict[str, object]:
        """Return JSON-safe deployment guidance for A2A stream lifecycle."""

        missing = self.missing_components()
        return {
            "profile": self.__class__.__name__,
            "probe_name": self.probe_name,
            "ready": not missing,
            "required_components": self.required_components,
            "configured_components": self.configured_components,
            "missing_components": missing,
            "sdk_owned": (
                "message/stream operation boundary",
                "typed message stream event parser",
                "tasks/resubscribe operation boundary",
                "one-shot task resubscribe client",
                "push notification delivery primitives",
            ),
            "deployment_owned": (
                "automatic reconnect loops",
                "durable cursor storage",
                "fan-out",
                "backpressure",
                "gateway quota",
                "billing",
                "credential issuance",
                "process supervision",
                "DNS pinning",
                "enterprise egress proxy",
                "CA rollout",
                "tenant directory lifecycle",
                "external conformance execution",
            ),
        }

    def readiness_check(self) -> dict[str, object]:
        """Return an ASGI readiness-compatible check payload."""

        metadata = self.readiness_metadata()
        ok = bool(metadata["ready"])
        return {
            **metadata,
            "status": "ok" if ok else "failed",
            "ok": ok,
        }

    def _validate_component_names(
        self,
        components: tuple[str, ...],
        *,
        field_name: str,
    ) -> None:
        if any(not component.strip() for component in components):
            raise ValueError(f"{field_name} must not contain empty names")


@dataclass(frozen=True, slots=True)
class A2APushNotificationDeploymentProfile:
    """Deployment-facing checks for one A2A push notification daemon."""

    daemon: object
    policy: A2APushNotificationHealthPolicy = field(
        default_factory=A2APushNotificationHealthPolicy,
    )
    probe_name: str = "a2a_push_worker"
    ready_statuses: tuple[A2APushNotificationHealthStatus, ...] = ("healthy",)

    def __post_init__(self) -> None:
        if not self.probe_name.strip():
            raise ValueError("probe_name must not be empty")
        if not self.ready_statuses:
            raise ValueError("ready_statuses must not be empty")

    def health_payload(self, *, now: float | None = None) -> dict[str, object]:
        """Return a JSON-safe health payload for deployment probes."""

        report = self._health_report(now=now)
        ok = report.status in self.ready_statuses
        return {
            "status": report.status,
            "ok": ok,
            "reason": report.reason,
            "worker_id": report.worker_id,
            "daemon_status": report.daemon_status,
            "iterations": report.iterations,
            "last_run_at": report.last_run_at,
            "seconds_since_last_run": report.seconds_since_last_run,
            "recent_error_count": report.recent_error_count,
            "last_error": report.last_error,
        }

    def health_check(self, *, now: float | None = None) -> dict[str, object]:
        """Return the raw SDK health status for liveness-style inspection."""

        return self.health_payload(now=now)

    def readiness_check(self, *, now: float | None = None) -> dict[str, object]:
        """Return an ASGI readiness-compatible check payload."""

        payload = self.health_payload(now=now)
        health_status = payload["status"]
        ok = bool(payload["ok"])
        return {
            **payload,
            "status": "ok" if ok else "failed",
            "health_status": health_status,
            "ok": ok,
        }

    def readiness_metadata(self) -> dict[str, object]:
        """Return deployment guidance for this worker probe."""

        return {
            "profile": self.__class__.__name__,
            "probe_name": self.probe_name,
            "worker_id": self._daemon_worker_id(),
            "ready_statuses": self.ready_statuses,
            "deployment_owned": (
                "process supervision",
                "restart policy",
                "alerting",
                "credentials",
                "DNS pinning",
                "enterprise egress proxy",
                "CA rollout",
                "tenant RBAC",
                "external conformance execution",
            ),
        }

    def _health_report(
        self,
        *,
        now: float | None,
    ) -> A2APushNotificationHealthReport:
        health = getattr(self.daemon, "health", None)
        if callable(health):
            return health(policy=self.policy, now=now)
        state = self.daemon.state()
        return self.policy.evaluate(state, now=now)

    def _daemon_worker_id(self) -> str:
        worker_id = getattr(self.daemon, "worker_id", None)
        if isinstance(worker_id, str):
            return worker_id
        state = getattr(self.daemon, "state", None)
        if callable(state):
            return state().worker_id
        return ""


class A2APushNotificationDeliveryStore(Protocol):
    """Boundary for queued A2A push notification delivery state."""

    def enqueue(
        self,
        record: A2APushNotificationDeliveryRecord,
    ) -> A2APushNotificationDeliveryRecord:
        """Store a delivery record for later processing."""

    def get(self, delivery_id: str) -> A2APushNotificationDeliveryRecord | None:
        """Return one delivery record by id."""

    def claim_due(
        self,
        *,
        now: float,
        worker_id: str,
        lease_seconds: float,
        limit: int,
    ) -> tuple[A2APushNotificationDeliveryRecord, ...]:
        """Claim due queued deliveries for one worker."""

    def mark_delivered(
        self,
        delivery_id: str,
        *,
        delivery: A2APushNotificationDelivery,
        now: float,
    ) -> A2APushNotificationDeliveryRecord | None:
        """Mark a delivery as delivered."""

    def record_failure(
        self,
        delivery_id: str,
        *,
        delivery: A2APushNotificationDelivery,
        now: float,
        retry_policy: A2APushNotificationRetryPolicy,
    ) -> A2APushNotificationDeliveryRecord | None:
        """Store retry or dead-letter state after a failed delivery."""

    def list_records(
        self,
        *,
        status: A2APushNotificationDeliveryRecordStatus | None = None,
    ) -> tuple[A2APushNotificationDeliveryRecord, ...]:
        """Return delivery records, optionally filtered by status."""


class InMemoryA2APushNotificationDeliveryStore:
    """Thread-safe in-memory queue for A2A push notification deliveries."""

    def __init__(self) -> None:
        self._records: dict[str, A2APushNotificationDeliveryRecord] = {}
        self._lock = RLock()

    def enqueue(
        self,
        record: A2APushNotificationDeliveryRecord,
    ) -> A2APushNotificationDeliveryRecord:
        """Store a delivery record for later processing."""

        stored = A2APushNotificationDeliveryRecord(
            delivery_id=record.delivery_id,
            task_id=record.task_id,
            config=record.config,
            event=record.event,
            created_at=record.created_at,
            next_run_at=record.next_run_at,
            status=record.status,
            attempts=record.attempts,
            last_error=record.last_error,
            response=record.response,
            worker_id=record.worker_id,
            lease_expires_at=record.lease_expires_at,
            delivered_at=record.delivered_at,
            dead_lettered_at=record.dead_lettered_at,
        )
        with self._lock:
            self._records[stored.delivery_id] = stored
        return stored

    def get(self, delivery_id: str) -> A2APushNotificationDeliveryRecord | None:
        """Return one delivery record by id."""

        with self._lock:
            return self._records.get(delivery_id)

    def claim_due(
        self,
        *,
        now: float,
        worker_id: str,
        lease_seconds: float,
        limit: int,
    ) -> tuple[A2APushNotificationDeliveryRecord, ...]:
        """Claim due queued deliveries for one worker."""

        if limit <= 0:
            return ()
        claimed: list[A2APushNotificationDeliveryRecord] = []
        with self._lock:
            for record in sorted(
                self._records.values(),
                key=lambda item: (item.next_run_at, item.created_at, item.delivery_id),
            ):
                if len(claimed) >= limit:
                    break
                if record.status not in {"queued", "retry_scheduled", "running"}:
                    continue
                if record.status == "running" and (
                    record.lease_expires_at is None
                    or record.lease_expires_at > now
                ):
                    continue
                if record.next_run_at > now:
                    continue
                updated = A2APushNotificationDeliveryRecord(
                    delivery_id=record.delivery_id,
                    task_id=record.task_id,
                    config=record.config,
                    event=record.event,
                    created_at=record.created_at,
                    next_run_at=record.next_run_at,
                    status="running",
                    attempts=record.attempts,
                    last_error=record.last_error,
                    response=record.response,
                    worker_id=worker_id,
                    lease_expires_at=now + lease_seconds,
                    delivered_at=record.delivered_at,
                    dead_lettered_at=record.dead_lettered_at,
                )
                self._records[record.delivery_id] = updated
                claimed.append(updated)
        return tuple(claimed)

    def mark_delivered(
        self,
        delivery_id: str,
        *,
        delivery: A2APushNotificationDelivery,
        now: float,
    ) -> A2APushNotificationDeliveryRecord | None:
        """Mark a delivery as delivered."""

        with self._lock:
            record = self._records.get(delivery_id)
            if record is None:
                return None
            updated = A2APushNotificationDeliveryRecord(
                delivery_id=record.delivery_id,
                task_id=record.task_id,
                config=record.config,
                event=record.event,
                created_at=record.created_at,
                next_run_at=record.next_run_at,
                status="delivered",
                attempts=record.attempts + 1,
                last_error=None,
                response=delivery.response,
                worker_id=record.worker_id,
                lease_expires_at=None,
                delivered_at=now,
                dead_lettered_at=None,
            )
            self._records[delivery_id] = updated
            return updated

    def record_failure(
        self,
        delivery_id: str,
        *,
        delivery: A2APushNotificationDelivery,
        now: float,
        retry_policy: A2APushNotificationRetryPolicy,
    ) -> A2APushNotificationDeliveryRecord | None:
        """Store retry or dead-letter state after a failed delivery."""

        with self._lock:
            record = self._records.get(delivery_id)
            if record is None:
                return None
            attempts = record.attempts + 1
            exhausted = attempts >= retry_policy.max_attempts
            next_run_at = (
                record.next_run_at
                if exhausted
                else now + retry_policy.delay_for_attempt(attempts)
            )
            updated = A2APushNotificationDeliveryRecord(
                delivery_id=record.delivery_id,
                task_id=record.task_id,
                config=record.config,
                event=record.event,
                created_at=record.created_at,
                next_run_at=next_run_at,
                status="dead_letter" if exhausted else "retry_scheduled",
                attempts=attempts,
                last_error=delivery.error or "push notification delivery failed",
                response=delivery.response,
                worker_id=record.worker_id,
                lease_expires_at=None,
                delivered_at=None,
                dead_lettered_at=now if exhausted else None,
            )
            self._records[delivery_id] = updated
            return updated

    def list_records(
        self,
        *,
        status: A2APushNotificationDeliveryRecordStatus | None = None,
    ) -> tuple[A2APushNotificationDeliveryRecord, ...]:
        """Return delivery records, optionally filtered by status."""

        with self._lock:
            records = tuple(
                self._records[key]
                for key in sorted(self._records)
            )
        if status is None:
            return records
        return tuple(record for record in records if record.status == status)


class PostgresA2APushNotificationConfigStore(A2APushNotificationConfigStore):
    """Postgres-backed A2A push notification config store."""

    def __init__(
        self,
        dsn: str,
        connection: object | None = None,
        pool: object | None = None,
        *,
        url_policy: A2APushNotificationUrlPolicy | None = None,
    ) -> None:
        """Create a Postgres push notification config store."""

        self._url_policy = url_policy or PublicHttpsA2APushNotificationUrlPolicy()
        self._pool = pool
        if connection is not None:
            self._connection = connection
            self._dsn = dsn
            return
        if pool is not None:
            getconn = getattr(pool, "getconn", None)
            connection_method = getattr(pool, "connection", None)
            if callable(getconn):
                self._connection = getconn()
            elif callable(connection_method):
                context = connection_method()
                self._connection = context.__enter__()
                self._pool_context = context
            else:
                raise RuntimeError("Postgres pool must provide getconn() or connection()")
            self._dsn = dsn
            return
        try:
            import psycopg
        except ImportError as error:
            raise RuntimeError(
                "PostgresA2APushNotificationConfigStore requires the optional "
                "dependency `agentos[postgres]`.",
            ) from error
        self._connection = psycopg.connect(dsn)
        self._dsn = dsn

    @classmethod
    def from_pool(
        cls,
        dsn: str,
        pool: object | None = None,
        *,
        url_policy: A2APushNotificationUrlPolicy | None = None,
    ) -> "PostgresA2APushNotificationConfigStore":
        """Create a config store from a psycopg pool."""

        if pool is None:
            try:
                from psycopg_pool import ConnectionPool
            except ImportError as error:
                raise RuntimeError(
                    "PostgresA2APushNotificationConfigStore pool support "
                    "requires `agentos[postgres]`.",
                ) from error
            pool = ConnectionPool(dsn)
        return cls(dsn, pool=pool, url_policy=url_policy)

    def create(
        self,
        task_id: str,
        config: A2APushNotificationConfig,
    ) -> A2APushNotificationConfig:
        """Create or replace a push notification config for a task."""

        config_id = config.config_id or f"push_{int(time.time() * 1000)}"
        stored = A2APushNotificationConfig(
            url=config.url,
            authentication=config.authentication,
            config_id=config_id,
            token=config.token,
        )
        self._url_policy.validate(stored)
        self._execute(
            """
            INSERT INTO agentos_a2a_push_notification_configs (
              task_id, config_id, url, authentication_schemes, payload
            )
            VALUES (%s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (task_id, config_id) DO UPDATE SET
                url = EXCLUDED.url,
                authentication_schemes = EXCLUDED.authentication_schemes,
                payload = EXCLUDED.payload,
                updated_at = now()
            """,
            (
                task_id,
                config_id,
                stored.url,
                list(stored.authentication.schemes)
                if stored.authentication is not None
                else [],
                self._json_dump(a2a_push_notification_config_to_dict(stored)),
            ),
        )
        self._commit()
        return stored

    def get(
        self,
        task_id: str,
        config_id: str,
    ) -> A2APushNotificationConfig | None:
        """Return a push notification config by id."""

        row = self._execute(
            """
            SELECT payload FROM agentos_a2a_push_notification_configs
            WHERE task_id = %s AND config_id = %s
            """,
            (task_id, config_id),
        ).fetchone()
        if row is None:
            return None
        return a2a_push_notification_config_from_dict(self._json_value(row[0]))

    def list(self, task_id: str) -> tuple[A2APushNotificationConfig, ...]:
        """Return push notification configs for a task."""

        rows = self._execute(
            """
            SELECT payload FROM agentos_a2a_push_notification_configs
            WHERE task_id = %s
            ORDER BY config_id
            """,
            (task_id,),
        ).fetchall()
        return tuple(
            a2a_push_notification_config_from_dict(self._json_value(row[0]))
            for row in rows
        )

    def delete(self, task_id: str, config_id: str) -> bool:
        """Delete a push notification config if present."""

        row = self._execute(
            """
            DELETE FROM agentos_a2a_push_notification_configs
            WHERE task_id = %s AND config_id = %s
            RETURNING config_id
            """,
            (task_id, config_id),
        ).fetchone()
        self._commit()
        return row is not None

    def close(self) -> None:
        """Close or return the current Postgres connection."""

        _close_postgres_connection(self)

    def _execute(
        self,
        sql: str,
        params: tuple[object, ...] = (),
    ) -> PostgresCursor:
        try:
            return cast(PostgresConnection, self._connection).execute(sql, params)
        except Exception as error:
            raise BackendUnavailableError("Postgres backend unavailable") from error

    def _commit(self) -> None:
        _commit_postgres_connection(self._connection)

    def _json_dump(self, value: dict[str, object]) -> str:
        return json.dumps(value, ensure_ascii=False, allow_nan=False)

    def _json_value(self, value: object) -> dict[str, object]:
        return _json_object(value, "A2A push notification config payload")


class PostgresA2APushNotificationDeliveryStore(A2APushNotificationDeliveryStore):
    """Postgres-backed queue for A2A push notification deliveries."""

    def __init__(
        self,
        dsn: str,
        connection: object | None = None,
        pool: object | None = None,
    ) -> None:
        """Create a Postgres push notification delivery store."""

        self._pool = pool
        if connection is not None:
            self._connection = connection
            self._dsn = dsn
            return
        if pool is not None:
            getconn = getattr(pool, "getconn", None)
            connection_method = getattr(pool, "connection", None)
            if callable(getconn):
                self._connection = getconn()
            elif callable(connection_method):
                context = connection_method()
                self._connection = context.__enter__()
                self._pool_context = context
            else:
                raise RuntimeError("Postgres pool must provide getconn() or connection()")
            self._dsn = dsn
            return
        try:
            import psycopg
        except ImportError as error:
            raise RuntimeError(
                "PostgresA2APushNotificationDeliveryStore requires the optional "
                "dependency `agentos[postgres]`.",
            ) from error
        self._connection = psycopg.connect(dsn)
        self._dsn = dsn

    @classmethod
    def from_pool(
        cls,
        dsn: str,
        pool: object | None = None,
    ) -> "PostgresA2APushNotificationDeliveryStore":
        """Create a delivery store from a psycopg pool."""

        if pool is None:
            try:
                from psycopg_pool import ConnectionPool
            except ImportError as error:
                raise RuntimeError(
                    "PostgresA2APushNotificationDeliveryStore pool support "
                    "requires `agentos[postgres]`.",
                ) from error
            pool = ConnectionPool(dsn)
        return cls(dsn, pool=pool)

    def enqueue(
        self,
        record: A2APushNotificationDeliveryRecord,
    ) -> A2APushNotificationDeliveryRecord:
        """Store a delivery record for later processing."""

        self._upsert_record(record)
        self._commit()
        return record

    def get(self, delivery_id: str) -> A2APushNotificationDeliveryRecord | None:
        """Return one delivery record by id."""

        row = self._execute(
            """
            SELECT payload FROM agentos_a2a_push_notification_deliveries
            WHERE delivery_id = %s
            """,
            (delivery_id,),
        ).fetchone()
        if row is None:
            return None
        return a2a_push_notification_delivery_record_from_dict(
            self._json_value(row[0]),
        )

    def claim_due(
        self,
        *,
        now: float,
        worker_id: str,
        lease_seconds: float,
        limit: int,
    ) -> tuple[A2APushNotificationDeliveryRecord, ...]:
        """Claim due queued deliveries for one worker."""

        if limit <= 0:
            return ()
        rows = self._execute(
            """
            SELECT payload FROM agentos_a2a_push_notification_deliveries
            WHERE (
                status IN ('queued', 'retry_scheduled')
                AND next_run_at <= %s
              )
              OR (
                status = 'running'
                AND lease_expires_at IS NOT NULL
                AND lease_expires_at <= %s
              )
            ORDER BY next_run_at, created_at, delivery_id
            LIMIT %s
            FOR UPDATE SKIP LOCKED
            """,
            (now, now, limit),
        ).fetchall()
        claimed: list[A2APushNotificationDeliveryRecord] = []
        for row in rows:
            record = a2a_push_notification_delivery_record_from_dict(
                self._json_value(row[0]),
            )
            updated = A2APushNotificationDeliveryRecord(
                delivery_id=record.delivery_id,
                task_id=record.task_id,
                config=record.config,
                event=record.event,
                created_at=record.created_at,
                next_run_at=record.next_run_at,
                status="running",
                attempts=record.attempts,
                last_error=record.last_error,
                response=record.response,
                worker_id=worker_id,
                lease_expires_at=now + lease_seconds,
                delivered_at=record.delivered_at,
                dead_lettered_at=record.dead_lettered_at,
            )
            self._update_record(updated)
            claimed.append(updated)
        self._commit()
        return tuple(claimed)

    def mark_delivered(
        self,
        delivery_id: str,
        *,
        delivery: A2APushNotificationDelivery,
        now: float,
    ) -> A2APushNotificationDeliveryRecord | None:
        """Mark a delivery as delivered."""

        record = self.get(delivery_id)
        if record is None:
            return None
        updated = A2APushNotificationDeliveryRecord(
            delivery_id=record.delivery_id,
            task_id=record.task_id,
            config=record.config,
            event=record.event,
            created_at=record.created_at,
            next_run_at=record.next_run_at,
            status="delivered",
            attempts=record.attempts + 1,
            last_error=None,
            response=delivery.response,
            worker_id=record.worker_id,
            lease_expires_at=None,
            delivered_at=now,
            dead_lettered_at=None,
        )
        self._update_record(updated)
        self._commit()
        return updated

    def record_failure(
        self,
        delivery_id: str,
        *,
        delivery: A2APushNotificationDelivery,
        now: float,
        retry_policy: A2APushNotificationRetryPolicy,
    ) -> A2APushNotificationDeliveryRecord | None:
        """Store retry or dead-letter state after a failed delivery."""

        record = self.get(delivery_id)
        if record is None:
            return None
        attempts = record.attempts + 1
        exhausted = attempts >= retry_policy.max_attempts
        updated = A2APushNotificationDeliveryRecord(
            delivery_id=record.delivery_id,
            task_id=record.task_id,
            config=record.config,
            event=record.event,
            created_at=record.created_at,
            next_run_at=(
                record.next_run_at
                if exhausted
                else now + retry_policy.delay_for_attempt(attempts)
            ),
            status="dead_letter" if exhausted else "retry_scheduled",
            attempts=attempts,
            last_error=delivery.error or "push notification delivery failed",
            response=delivery.response,
            worker_id=record.worker_id,
            lease_expires_at=None,
            delivered_at=None,
            dead_lettered_at=now if exhausted else None,
        )
        self._update_record(updated)
        self._commit()
        return updated

    def list_records(
        self,
        *,
        status: A2APushNotificationDeliveryRecordStatus | None = None,
    ) -> tuple[A2APushNotificationDeliveryRecord, ...]:
        """Return delivery records, optionally filtered by status."""

        if status is None:
            rows = self._execute(
                """
                SELECT payload FROM agentos_a2a_push_notification_deliveries
                ORDER BY next_run_at, created_at, delivery_id
                """,
            ).fetchall()
        else:
            rows = self._execute(
                """
                SELECT payload FROM agentos_a2a_push_notification_deliveries
                WHERE status = %s
                ORDER BY next_run_at, created_at, delivery_id
                """,
                (status,),
            ).fetchall()
        return tuple(
            a2a_push_notification_delivery_record_from_dict(
                self._json_value(row[0]),
            )
            for row in rows
        )

    def close(self) -> None:
        """Close or return the current Postgres connection."""

        _close_postgres_connection(self)

    def _upsert_record(self, record: A2APushNotificationDeliveryRecord) -> None:
        self._execute(
            """
            INSERT INTO agentos_a2a_push_notification_deliveries (
              delivery_id, task_id, config_id, status, next_run_at, created_at,
              attempts, worker_id, lease_expires_at, delivered_at,
              dead_lettered_at, payload
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (delivery_id) DO UPDATE SET
                task_id = EXCLUDED.task_id,
                config_id = EXCLUDED.config_id,
                status = EXCLUDED.status,
                next_run_at = EXCLUDED.next_run_at,
                attempts = EXCLUDED.attempts,
                worker_id = EXCLUDED.worker_id,
                lease_expires_at = EXCLUDED.lease_expires_at,
                delivered_at = EXCLUDED.delivered_at,
                dead_lettered_at = EXCLUDED.dead_lettered_at,
                payload = EXCLUDED.payload,
                updated_at = now()
            """,
            _delivery_record_params(record),
        )

    def _update_record(
        self,
        record: A2APushNotificationDeliveryRecord,
    ) -> A2APushNotificationDeliveryRecord | None:
        row = self._execute(
            """
            UPDATE agentos_a2a_push_notification_deliveries
            SET status = %s,
                next_run_at = %s,
                attempts = %s,
                worker_id = %s,
                lease_expires_at = %s,
                delivered_at = %s,
                dead_lettered_at = %s,
                payload = %s::jsonb,
                updated_at = now()
            WHERE delivery_id = %s
            RETURNING payload
            """,
            (
                record.status,
                record.next_run_at,
                record.attempts,
                record.worker_id,
                record.lease_expires_at,
                record.delivered_at,
                record.dead_lettered_at,
                self._json_dump(a2a_push_notification_delivery_record_to_dict(record)),
                record.delivery_id,
            ),
        ).fetchone()
        if row is None:
            return None
        return a2a_push_notification_delivery_record_from_dict(
            self._json_value(row[0]),
        )

    def _execute(
        self,
        sql: str,
        params: tuple[object, ...] = (),
    ) -> PostgresCursor:
        try:
            return cast(PostgresConnection, self._connection).execute(sql, params)
        except Exception as error:
            raise BackendUnavailableError("Postgres backend unavailable") from error

    def _commit(self) -> None:
        _commit_postgres_connection(self._connection)

    def _json_dump(self, value: dict[str, object]) -> str:
        return json.dumps(value, ensure_ascii=False, allow_nan=False)

    def _json_value(self, value: object) -> dict[str, object]:
        return _json_object(value, "A2A push notification delivery payload")


class A2APushNotificationDispatcher:
    """Deliver A2A task update events to configured webhook endpoints."""

    def __init__(
        self,
        transport: A2ATransport | None = None,
        *,
        timeout_seconds: float = 10,
        url_policy: A2APushNotificationUrlPolicy | None = None,
    ) -> None:
        self._transport = transport or UrllibA2ATransport()
        self._timeout_seconds = timeout_seconds
        self._url_policy = url_policy or PublicHttpsA2APushNotificationUrlPolicy()

    def deliver(
        self,
        config: A2APushNotificationConfig,
        event: A2ATaskSubscriptionEvent,
    ) -> A2APushNotificationDelivery:
        """POST a task status update event to one configured webhook."""

        try:
            self._url_policy.validate(config)
            response = self._transport.post_json(
                config.url,
                a2a_push_notification_payload_to_dict(event),
                self._timeout_seconds,
                headers=_push_notification_headers(config),
            )
        except Exception as error:
            return A2APushNotificationDelivery(
                config_id=config.config_id,
                url=config.url,
                status="failed",
                error=str(error),
            )
        return A2APushNotificationDelivery(
            config_id=config.config_id,
            url=config.url,
            status="delivered",
            response=response,
        )


class A2APushNotificationDeliveryWorker:
    """Run due queued A2A push notification deliveries."""

    def __init__(
        self,
        store: A2APushNotificationDeliveryStore,
        dispatcher: A2APushNotificationDispatcher,
        *,
        retry_policy: A2APushNotificationRetryPolicy | None = None,
        lease_seconds: float = 30.0,
    ) -> None:
        self._store = store
        self._dispatcher = dispatcher
        self._retry_policy = retry_policy or A2APushNotificationRetryPolicy()
        self._lease_seconds = lease_seconds

    def run_pending(
        self,
        *,
        now: float | None = None,
        worker_id: str = "a2a-push-worker",
        limit: int = 10,
    ) -> tuple[A2APushNotificationDeliveryRecord, ...]:
        """Claim due deliveries, deliver them, and persist their outcome."""

        effective_now = time.time() if now is None else now
        claimed = self._store.claim_due(
            now=effective_now,
            worker_id=worker_id,
            lease_seconds=self._lease_seconds,
            limit=limit,
        )
        results: list[A2APushNotificationDeliveryRecord] = []
        for record in claimed:
            delivery = self._dispatcher.deliver(record.config, record.event)
            if delivery.status == "delivered":
                updated = self._store.mark_delivered(
                    record.delivery_id,
                    delivery=delivery,
                    now=effective_now,
                )
            else:
                updated = self._store.record_failure(
                    record.delivery_id,
                    delivery=delivery,
                    now=effective_now,
                    retry_policy=self._retry_policy,
                )
            if updated is not None:
                results.append(updated)
        return tuple(results)


class A2APushNotificationDaemon:
    """Host loop that repeatedly runs due A2A push notification deliveries."""

    def __init__(
        self,
        *,
        worker: A2APushNotificationDeliveryWorker,
        worker_id: str = "a2a-push-worker",
        poll_interval_seconds: float = 0.5,
        batch_limit: int = 10,
        clock: object | None = None,
    ) -> None:
        if poll_interval_seconds < 0:
            raise ValueError("poll_interval_seconds must be >= 0")
        if batch_limit < 1:
            raise ValueError("batch_limit must be >= 1")
        self.worker = worker
        self.worker_id = worker_id
        self.poll_interval_seconds = poll_interval_seconds
        self.batch_limit = batch_limit
        self._clock = clock if callable(clock) else time.time
        self._lock = RLock()
        self._stop_event = Event()
        self._thread: Thread | None = None
        self._state = A2APushNotificationDaemonState(
            status="idle",
            worker_id=worker_id,
            poll_interval_seconds=poll_interval_seconds,
            batch_limit=batch_limit,
        )

    def run_once(self) -> tuple[A2APushNotificationDeliveryRecord, ...]:
        """Process one daemon iteration without starting a background thread."""

        now = float(self._clock())
        results = self.worker.run_pending(
            now=now,
            worker_id=self.worker_id,
            limit=self.batch_limit,
        )
        self._record_run(results, now=now)
        return results

    def start(self) -> None:
        """Start the background polling loop if it is not already running."""

        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            now = float(self._clock())
            self._state = replace(
                self._state,
                status="running",
                started_at=now,
                stopped_at=None,
            )
            self._thread = Thread(
                target=self._run_loop,
                name=f"agentos-a2a-push-daemon-{self.worker_id}",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        """Request the background polling loop to stop."""

        self._stop_event.set()
        with self._lock:
            if self._state.status == "running":
                self._state = replace(self._state, status="stopping")

    def join(self, timeout: float | None = None) -> bool:
        """Wait for the background loop to exit."""

        thread = self._thread
        if thread is None:
            return True
        thread.join(timeout=timeout)
        return not thread.is_alive()

    def is_running(self) -> bool:
        """Return whether the daemon thread is currently alive."""

        thread = self._thread
        return thread is not None and thread.is_alive()

    def state(self) -> A2APushNotificationDaemonState:
        """Return an immutable daemon state snapshot."""

        with self._lock:
            return self._state

    def health(
        self,
        *,
        policy: A2APushNotificationHealthPolicy | None = None,
        now: float | None = None,
    ) -> A2APushNotificationHealthReport:
        """Return a supervisor-friendly health report for this daemon."""

        health_policy = policy or A2APushNotificationHealthPolicy()
        effective_now = float(self._clock()) if now is None else now
        return health_policy.evaluate(self.state(), now=effective_now)

    def _run_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                try:
                    self.run_once()
                except Exception as error:
                    self._record_error(error)
                self._stop_event.wait(self.poll_interval_seconds)
        finally:
            with self._lock:
                self._state = replace(
                    self._state,
                    status="stopped",
                    stopped_at=float(self._clock()),
                )

    def _record_run(
        self,
        results: tuple[A2APushNotificationDeliveryRecord, ...],
        *,
        now: float,
    ) -> None:
        with self._lock:
            self._state = replace(
                self._state,
                iterations=self._state.iterations + 1,
                last_run_at=now,
                last_results=tuple(results),
            )

    def _record_error(self, error: Exception) -> None:
        now = float(self._clock())
        record = A2APushNotificationDaemonError(
            worker_id=self.worker_id,
            error=str(error),
            raised_at=now,
        )
        with self._lock:
            self._state = replace(
                self._state,
                iterations=self._state.iterations + 1,
                last_run_at=now,
                errors=self._state.errors + (record,),
            )


@dataclass(frozen=True, slots=True)
class A2AOperationError:
    """JSON-RPC-like A2A operation error."""

    code: int
    message: str
    data: Mapping[str, object] | None = None


class A2ARateLimitError(RuntimeError):
    """Raised when an inbound A2A operation exceeds a configured rate policy."""

    def __init__(self, retry_after_seconds: int = 0) -> None:
        self.retry_after_seconds = max(0, int(retry_after_seconds))
        super().__init__("rate limit exceeded")


class A2APeerIdResolver(Protocol):
    """Boundary for resolving an authenticated inbound A2A peer id."""

    def peer_id_for_headers(self, headers: Mapping[str, str]) -> str | None:
        """Return the peer id for request headers, or None when unknown."""


class A2AOperationRateLimitPolicy(Protocol):
    """Boundary for inbound A2A operation rate limiting."""

    def check_operation(
        self,
        headers: Mapping[str, str],
        *,
        operation: str,
        task_id: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
    ) -> None:
        """Raise A2ARateLimitError when an operation should be throttled."""


@dataclass(frozen=True, slots=True)
class PeerKeyA2AOperationRateLimitPolicy:
    """Rate-limit inbound A2A operations by peer and operation context."""

    peer_id_resolver: A2APeerIdResolver
    rate_limiter: RateLimiter
    key_prefix: str = "a2a"

    def check_operation(
        self,
        headers: Mapping[str, str],
        *,
        operation: str,
        task_id: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
    ) -> None:
        """Check the configured limiter for one peer-scoped A2A operation."""

        peer_id = self.peer_id_resolver.peer_id_for_headers(headers)
        if peer_id is None or not str(peer_id).strip():
            raise A2ARateLimitError()
        key = self.key_for_operation(
            str(peer_id),
            operation=operation,
            task_id=task_id,
            resource_type=resource_type,
            resource_id=resource_id,
        )
        decision = self.rate_limiter.check(key)
        if not decision.allowed:
            raise A2ARateLimitError(decision.retry_after_seconds)

    def key_for_operation(
        self,
        peer_id: str,
        *,
        operation: str,
        task_id: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
    ) -> str:
        """Build the stable local limiter key for one operation context."""

        parts = [
            self._key_part(str(self.key_prefix).strip() or "a2a"),
            f"peer={self._key_part(peer_id)}",
            f"operation={self._key_part(operation)}",
        ]
        if task_id:
            parts.append(f"task={self._key_part(task_id)}")
        if resource_type:
            resource = self._key_part(resource_type)
            if resource_id:
                resource = f"{resource}/{self._key_part(resource_id)}"
            parts.append(f"resource={resource}")
        return ":".join(parts)

    @staticmethod
    def _key_part(value: object) -> str:
        text = str(value).strip()
        if not text:
            return "_"
        return (
            text.replace("\\", "_")
            .replace("/", "_")
            .replace(":", "_")
            .replace(" ", "_")
        )


@dataclass(frozen=True, slots=True)
class A2AOperationRequest:
    """JSON-RPC-like A2A operation request."""

    method: str
    params: Mapping[str, object]
    request_id: str | int | None = None
    jsonrpc: str = "2.0"

    @property
    def operation_name(self) -> A2AOfficialOperationName | None:
        """Return the official A2A abstract operation name for this method."""

        return a2a_official_operation_for_method(self.method)

    @classmethod
    def message_send(
        cls,
        message: A2AMessage,
        *,
        request_id: str | int | None = None,
    ) -> A2AOperationRequest:
        """Create a message/send request."""

        return cls(
            method=A2A_OFFICIAL_OPERATION_TO_JSONRPC_METHOD["SendMessage"],
            params={"message": a2a_message_to_dict(message)},
            request_id=request_id,
        )

    @classmethod
    def message_stream(
        cls,
        message: A2AMessage,
        *,
        request_id: str | int | None = None,
    ) -> A2AOperationRequest:
        """Create a message/stream request."""

        return cls(
            method=A2A_OFFICIAL_OPERATION_TO_JSONRPC_METHOD["SendStreamingMessage"],
            params={"message": a2a_message_to_dict(message)},
            request_id=request_id,
        )

    @classmethod
    def task_resubscribe(
        cls,
        task_id: str,
        *,
        after_event_id: int | None = None,
        request_id: str | int | None = None,
    ) -> A2AOperationRequest:
        """Create a tasks/resubscribe request."""

        params: dict[str, object] = {"id": task_id}
        if after_event_id is not None:
            params["afterEventId"] = after_event_id
        return cls(
            method=A2A_OFFICIAL_OPERATION_TO_JSONRPC_METHOD["SubscribeToTask"],
            params=params,
            request_id=request_id,
        )


@dataclass(frozen=True, slots=True)
class A2AOperationResponse:
    """JSON-RPC-like A2A operation response."""

    request_id: str | int | None
    task: A2ATask | None = None
    task_event: A2ATaskSubscriptionEvent | None = None
    error: A2AOperationError | None = None
    jsonrpc: str = "2.0"


def a2a_official_operation_for_method(
    method: str,
) -> A2AOfficialOperationName | None:
    """Map a JSON-RPC method name to the official A2A operation name."""

    return A2A_JSONRPC_METHOD_TO_OFFICIAL_OPERATION.get(method)


@dataclass(frozen=True, slots=True)
class A2AProtocolVersionPolicy:
    """Protocol-version negotiation policy for A2A operation calls."""

    supported_versions: tuple[str, ...] = A2A_DEFAULT_SUPPORTED_PROTOCOL_VERSIONS
    default_client_version: str = A2A_DEFAULT_PROTOCOL_VERSION

    def __init__(
        self,
        *,
        supported_versions: tuple[str, ...] = A2A_DEFAULT_SUPPORTED_PROTOCOL_VERSIONS,
        default_client_version: str = A2A_DEFAULT_PROTOCOL_VERSION,
    ) -> None:
        normalized = tuple(
            self.normalize(version)
            for version in supported_versions
            if str(version).strip()
        )
        if not normalized:
            raise ValueError("supported_versions must not be empty")
        object.__setattr__(self, "supported_versions", normalized)
        object.__setattr__(
            self,
            "default_client_version",
            self.normalize(default_client_version),
        )

    def requested_version(self, headers: Mapping[str, str] | None) -> str:
        """Return the normalized requested version from A2A service headers."""

        raw = _header_value(headers, A2A_PROTOCOL_VERSION_HEADER)
        if raw is None or not raw.strip():
            return A2A_LEGACY_EMPTY_PROTOCOL_VERSION
        return self.normalize(raw)

    def ensure_supported(self, headers: Mapping[str, str] | None) -> None:
        """Raise when inbound headers request an unsupported version."""

        requested = self.requested_version(headers)
        if requested not in self.supported_versions:
            raise A2AProtocolVersionError(requested, self.supported_versions)

    @staticmethod
    def normalize(version: object) -> str:
        """Normalize protocol versions to Major.Minor for negotiation."""

        text = str(version).strip()
        parts = text.split(".")
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            return f"{int(parts[0])}.{int(parts[1])}"
        return text


class A2AProtocolVersionError(ValueError):
    """Raised when an A2A peer requests an unsupported protocol version."""

    def __init__(
        self,
        requested_version: str,
        supported_versions: tuple[str, ...],
    ) -> None:
        self.requested_version = requested_version
        self.supported_versions = supported_versions
        super().__init__(
            "The requested A2A protocol version "
            f"{requested_version} is not supported by this agent",
        )


@dataclass(frozen=True, slots=True)
class A2AExtensionNegotiationResult:
    """Result of comparing requested and locally supported A2A extensions."""

    requested_extensions: tuple[str, ...]
    supported_extensions: tuple[str, ...]
    accepted_extensions: tuple[str, ...]
    unsupported_extensions: tuple[str, ...]
    missing_required_extensions: tuple[str, ...] = ()


class A2AExtensionNegotiationError(ValueError):
    """Raised when required A2A extensions cannot be negotiated."""

    def __init__(
        self,
        missing_extensions: tuple[str, ...],
        *,
        supported_extensions: tuple[str, ...] = (),
        requested_extensions: tuple[str, ...] = (),
    ) -> None:
        self.missing_extensions = missing_extensions
        self.supported_extensions = supported_extensions
        self.requested_extensions = requested_extensions
        suffix = ", ".join(missing_extensions)
        super().__init__(
            "The requested A2A operation requires unsupported extensions: "
            f"{suffix}",
        )


@dataclass(frozen=True, slots=True)
class A2AExtensionNegotiationPolicy:
    """Negotiate A2A Agent Card capability extensions for operation calls."""

    local_extensions: tuple[A2AAgentExtension, ...] = ()
    supported_extensions: tuple[str, ...] = ()

    def __init__(
        self,
        *,
        local_extensions: tuple[A2AAgentExtension, ...] = (),
        supported_extensions: tuple[str, ...] = (),
    ) -> None:
        object.__setattr__(
            self,
            "local_extensions",
            tuple(local_extensions),
        )
        object.__setattr__(
            self,
            "supported_extensions",
            _unique_non_empty_strings(supported_extensions),
        )

    def negotiate_inbound(
        self,
        headers: Mapping[str, str] | None,
    ) -> A2AExtensionNegotiationResult:
        """Validate peer-declared extensions against local required extensions."""

        requested = self.requested_extensions(headers)
        local_supported = self.local_supported_extensions()
        required = self.local_required_extensions()
        missing = tuple(uri for uri in required if uri not in requested)
        accepted = tuple(uri for uri in requested if uri in local_supported)
        unsupported = tuple(uri for uri in requested if uri not in local_supported)
        result = A2AExtensionNegotiationResult(
            requested_extensions=requested,
            supported_extensions=local_supported,
            accepted_extensions=accepted,
            unsupported_extensions=unsupported,
            missing_required_extensions=missing,
        )
        if missing:
            raise A2AExtensionNegotiationError(
                missing,
                supported_extensions=local_supported,
                requested_extensions=requested,
            )
        return result

    def extension_header_for_card(
        self,
        card: A2AAgentCard,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> str | None:
        """Return the outbound A2A-Extensions header for a peer card."""

        peer_extensions = _card_extension_uris(card)
        peer_required = _card_required_extension_uris(card)
        explicit = self.requested_extensions(headers)
        call_supported = _unique_non_empty_strings(
            self.supported_extensions + explicit,
        )
        missing = tuple(uri for uri in peer_required if uri not in call_supported)
        if missing:
            raise A2AExtensionNegotiationError(
                missing,
                supported_extensions=call_supported,
                requested_extensions=explicit,
            )
        advertised = tuple(uri for uri in peer_extensions if uri in call_supported)
        if not advertised:
            return None
        return ", ".join(advertised)

    def requested_extensions(
        self,
        headers: Mapping[str, str] | None,
    ) -> tuple[str, ...]:
        """Parse the A2A-Extensions header."""

        raw = _header_value(headers, A2A_EXTENSIONS_HEADER)
        if raw is None:
            return ()
        return _unique_non_empty_strings(raw.split(","))

    def local_supported_extensions(self) -> tuple[str, ...]:
        """Return local extension URIs declared by the server card."""

        return _unique_non_empty_strings(
            tuple(extension.uri for extension in self.local_extensions),
        )

    def local_required_extensions(self) -> tuple[str, ...]:
        """Return local extension URIs that peers must support."""

        return _unique_non_empty_strings(
            tuple(
                extension.uri
                for extension in self.local_extensions
                if extension.required
            ),
        )


class A2AOperationRunner(Protocol):
    """Boundary for executing protocol-facing A2A operations."""

    async def send_message(self, message: A2AMessage) -> A2ATask:
        """Execute A2A message/send and return a task projection."""


class A2ATaskLifecycleRunner(Protocol):
    """Boundary for protocol-facing A2A task lifecycle operations."""

    def get_task(self, task_id: str) -> A2ATask | None:
        """Return a projected task by id."""

    def cancel_task(self, task_id: str) -> A2ATask | None:
        """Request task cancel and return the updated projection."""

    def next_task_update(
        self,
        task_id: str,
        *,
        after_version: int | None = None,
    ) -> A2ATaskSubscriptionEvent | None:
        """Return the next task update after a client cursor."""


class AgentA2AOperationRunner:
    """Adapt a single Agent to the A2A message/send operation."""

    def __init__(self, agent: Agent) -> None:
        self._agent = agent

    async def send_message(self, message: A2AMessage) -> A2ATask:
        """Run the agent with text content from the incoming message."""

        task_id = message.task_id or f"a2a_task_{int(time.time() * 1000)}"
        result = await execute_a2a_agent(self._agent, message.text_content())
        return project_a2a_task(
            result,
            task_id=task_id,
            context_id=message.context_id,
            request_message=message,
            make_text_part=A2AMessagePart.from_text,
            make_message=A2AMessage,
            make_task=A2ATask,
        )


class TaskStoreA2ATaskLifecycleRunner:
    """Project an agent-os TaskStore into A2A task lifecycle operations."""

    def __init__(
        self,
        task_store: TaskStore,
        *,
        clock: callable | None = None,
    ) -> None:
        self._task_store = task_store
        self._clock = clock or time.time

    def get_task(self, task_id: str) -> A2ATask | None:
        """Return a projected task by id."""

        record = self._task_store.get(task_id)
        if record is None:
            return None
        return a2a_task_from_task_record(record)

    def cancel_task(self, task_id: str) -> A2ATask | None:
        """Request task cancel and return the updated projection."""

        record = self._task_store.get(task_id)
        if record is None or record.status in {
            "completed",
            "failed",
            "cancelled",
            "timeout",
        }:
            return None
        self._task_store.request_cancel(task_id, now=float(self._clock()))
        updated = self._task_store.get(task_id)
        if updated is None:
            return None
        return a2a_task_from_task_record(updated)

    def next_task_update(
        self,
        task_id: str,
        *,
        after_version: int | None = None,
    ) -> A2ATaskSubscriptionEvent | None:
        """Return the current task projection if it is newer than the cursor."""

        record = self._task_store.get(task_id)
        if record is None:
            return None
        if after_version is not None and record.version <= after_version:
            return None
        return A2ATaskSubscriptionEvent(
            event_id=str(record.version),
            task=a2a_task_from_task_record(record),
            final=record.status in {"completed", "failed", "cancelled", "timeout"},
        )


class A2AOperationServer:
    """Inbound A2A protocol operation server."""

    def __init__(
        self,
        runner: A2AOperationRunner,
        *,
        task_lifecycle: A2ATaskLifecycleRunner | None = None,
        push_notification_configs: A2APushNotificationConfigStore | None = None,
        inbound_auth_policy: A2AInboundAuthPolicy | None = None,
        rate_limit_policy: A2AOperationRateLimitPolicy | None = None,
        supported_protocol_versions: tuple[str, ...] = (
            A2A_DEFAULT_SUPPORTED_PROTOCOL_VERSIONS
        ),
        protocol_version_policy: A2AProtocolVersionPolicy | None = None,
        extension_negotiation_policy: A2AExtensionNegotiationPolicy | None = None,
    ) -> None:
        self._runner = runner
        self._task_lifecycle = task_lifecycle
        self._push_notification_configs = push_notification_configs
        self._inbound_auth_policy = (
            inbound_auth_policy or RejectAllA2AInboundAuthPolicy()
        )
        self._rate_limit_policy = rate_limit_policy
        self._protocol_version_policy = (
            protocol_version_policy
            or A2AProtocolVersionPolicy(
                supported_versions=supported_protocol_versions,
            )
        )
        self._extension_negotiation_policy = (
            extension_negotiation_policy or A2AExtensionNegotiationPolicy()
        )

    async def handle_message_send(
        self,
        payload: Mapping[str, object],
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, object]:
        """Handle a message/send payload and return a JSON-safe response."""

        return await self._handle_path_bound_operation(
            payload,
            headers=headers,
            expected_operation="SendMessage",
        )

    async def handle_message_stream(
        self,
        payload: Mapping[str, object],
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, object]:
        """Handle a message/stream payload and return an initial task response."""

        return await self._handle_path_bound_operation(
            payload,
            headers=headers,
            expected_operation="SendStreamingMessage",
        )

    @property
    def task_lifecycle(self) -> A2ATaskLifecycleRunner | None:
        """Return the configured task lifecycle runner."""

        return self._task_lifecycle

    @property
    def push_notification_configs(
        self,
    ) -> A2APushNotificationConfigStore | None:
        """Return the configured push notification config store."""

        return self._push_notification_configs

    async def handle_operation(
        self,
        payload: Mapping[str, object],
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, object]:
        """Handle a JSON-RPC-like A2A operation payload."""

        request_id = payload.get("id")
        try:
            request = a2a_operation_request_from_dict(payload)
            request_id = request.request_id
            self._protocol_version_policy.ensure_supported(headers)
            self._extension_negotiation_policy.negotiate_inbound(headers)
            operation_name = request.operation_name
            if operation_name == "SubscribeToTask":
                task_id = request.params.get("id")
                if not isinstance(task_id, str) or not task_id:
                    raise ValueError("params.id is required")
                after_event_id = _optional_int(request.params.get("afterEventId"))
                return self.handle_task_resubscribe(
                    task_id,
                    after_event_id=after_event_id,
                    headers=headers,
                    request_id=request_id,
                )
            if operation_name not in {"SendMessage", "SendStreamingMessage"}:
                return self._unsupported_method_response(request_id, request.method)
            return await self._handle_message_operation(
                request,
                headers=headers,
            )
        except A2AProtocolVersionError as error:
            return self._version_not_supported_response(request_id, error)
        except A2AExtensionNegotiationError as error:
            return self._extension_support_required_response(request_id, error)
        except A2AInboundAuthError:
            return self._unauthorized_peer_response()
        except A2ARateLimitError as error:
            return self._rate_limit_exceeded_response(request_id, error)
        except ValueError as error:
            return self._error_response(
                request_id,
                A2AOperationError(
                    code=-32602,
                    message="invalid params",
                    data={"error": str(error)},
                ),
            )
        except Exception:
            return self._error_response(
                request_id,
                A2AOperationError(
                    code=-32603,
                    message="internal error",
                ),
            )

    async def _handle_path_bound_operation(
        self,
        payload: Mapping[str, object],
        *,
        headers: Mapping[str, str] | None = None,
        expected_operation: A2AOfficialOperationName,
    ) -> dict[str, object]:
        request_id = payload.get("id")
        try:
            request = a2a_operation_request_from_dict(payload)
            request_id = request.request_id
            self._protocol_version_policy.ensure_supported(headers)
            self._extension_negotiation_policy.negotiate_inbound(headers)
            operation_name = request.operation_name
            if operation_name != expected_operation:
                self._authorize(headers, operation=expected_operation)
                return self._unsupported_method_response(request_id, request.method)
            return await self._handle_message_operation(request, headers=headers)
        except A2AProtocolVersionError as error:
            return self._version_not_supported_response(request_id, error)
        except A2AExtensionNegotiationError as error:
            return self._extension_support_required_response(request_id, error)
        except A2AInboundAuthError:
            return self._unauthorized_peer_response()
        except A2ARateLimitError as error:
            return self._rate_limit_exceeded_response(request_id, error)
        except ValueError as error:
            return self._error_response(
                request_id,
                A2AOperationError(
                    code=-32602,
                    message="invalid params",
                    data={"error": str(error)},
                ),
            )
        except Exception:
            return self._error_response(
                request_id,
                A2AOperationError(
                    code=-32603,
                    message="internal error",
                ),
            )

    async def _handle_message_operation(
        self,
        request: A2AOperationRequest,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, object]:
        """Handle a protocol message operation after version/extension checks."""

        operation = request.operation_name or request.method
        self._authorize(headers, operation=operation)
        rate_limit_error = self._rate_limit_response(
            headers,
            operation=operation,
            request_id=request.request_id,
        )
        if rate_limit_error is not None:
            return rate_limit_error
        message_payload = request.params.get("message")
        if not isinstance(message_payload, Mapping):
            raise ValueError("params.message is required")
        message = a2a_message_from_dict(message_payload)
        with use_incoming_trace_headers(headers):
            task = await self._runner.send_message(message)
        return a2a_operation_response_to_dict(
            A2AOperationResponse(request_id=request.request_id, task=task),
        )

    def handle_task_get(
        self,
        task_id: str,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, object]:
        """Handle protocol task lookup."""

        version_error = self._version_response(headers)
        if version_error is not None:
            return version_error
        extension_error = self._extension_response(headers)
        if extension_error is not None:
            return extension_error
        auth_error = self._authorize_response(
            headers,
            operation="tasks/get",
            task_id=task_id,
            resource_type="task",
            resource_id=task_id,
        )
        if auth_error is not None:
            return auth_error
        rate_limit_error = self._rate_limit_response(
            headers,
            operation="tasks/get",
            task_id=task_id,
            resource_type="task",
            resource_id=task_id,
        )
        if rate_limit_error is not None:
            return rate_limit_error
        if not task_id:
            return self._error_response(
                None,
                A2AOperationError(code=-32602, message="task id is required"),
            )
        if self._task_lifecycle is None:
            return self._error_response(
                None,
                A2AOperationError(
                    code=-32601,
                    message="task lifecycle is not configured",
                ),
            )
        with use_incoming_trace_headers(headers):
            task = self._task_lifecycle.get_task(task_id)
        if task is None:
            return self._error_response(
                None,
                A2AOperationError(code=-32001, message="task not found"),
            )
        return a2a_operation_response_to_dict(
            A2AOperationResponse(request_id=None, task=task),
        )

    def handle_task_resubscribe(
        self,
        task_id: str,
        *,
        after_event_id: int | None = None,
        headers: Mapping[str, str] | None = None,
        request_id: str | int | None = None,
    ) -> dict[str, object]:
        """Handle protocol task resubscribe and return the next task event."""

        version_error = self._version_response(headers, request_id=request_id)
        if version_error is not None:
            return version_error
        extension_error = self._extension_response(headers, request_id=request_id)
        if extension_error is not None:
            return extension_error
        auth_error = self._authorize_response(
            headers,
            operation="SubscribeToTask",
            task_id=task_id,
            resource_type="task",
            resource_id=task_id,
        )
        if auth_error is not None:
            return auth_error
        rate_limit_error = self._rate_limit_response(
            headers,
            operation="SubscribeToTask",
            request_id=request_id,
            task_id=task_id,
            resource_type="task",
            resource_id=task_id,
        )
        if rate_limit_error is not None:
            return rate_limit_error
        if not task_id:
            return self._error_response(
                request_id,
                A2AOperationError(code=-32602, message="task id is required"),
            )
        if self._task_lifecycle is None:
            return self._error_response(
                request_id,
                A2AOperationError(
                    code=-32601,
                    message="task lifecycle is not configured",
                ),
            )
        with use_incoming_trace_headers(headers):
            event = self._task_lifecycle.next_task_update(
                task_id,
                after_version=after_event_id,
            )
        if event is None:
            current = self._task_lifecycle.get_task(task_id)
            if current is None:
                return self._error_response(
                    request_id,
                    A2AOperationError(code=-32001, message="task not found"),
                )
        return a2a_operation_response_to_dict(
            A2AOperationResponse(request_id=request_id, task_event=event),
        )

    def handle_task_cancel(
        self,
        task_id: str,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, object]:
        """Handle protocol task cancel."""

        version_error = self._version_response(headers)
        if version_error is not None:
            return version_error
        extension_error = self._extension_response(headers)
        if extension_error is not None:
            return extension_error
        auth_error = self._authorize_response(
            headers,
            operation="tasks/cancel",
            task_id=task_id,
            resource_type="task",
            resource_id=task_id,
        )
        if auth_error is not None:
            return auth_error
        rate_limit_error = self._rate_limit_response(
            headers,
            operation="tasks/cancel",
            task_id=task_id,
            resource_type="task",
            resource_id=task_id,
        )
        if rate_limit_error is not None:
            return rate_limit_error
        if not task_id:
            return self._error_response(
                None,
                A2AOperationError(code=-32602, message="task id is required"),
            )
        if self._task_lifecycle is None:
            return self._error_response(
                None,
                A2AOperationError(
                    code=-32601,
                    message="task lifecycle is not configured",
                ),
            )
        with use_incoming_trace_headers(headers):
            task = self._task_lifecycle.cancel_task(task_id)
        if task is None:
            current = self._task_lifecycle.get_task(task_id)
            if current is None:
                return self._error_response(
                    None,
                    A2AOperationError(code=-32001, message="task not found"),
                )
            return self._error_response(
                None,
                A2AOperationError(code=-32002, message="task not cancelable"),
            )
        return a2a_operation_response_to_dict(
            A2AOperationResponse(request_id=None, task=task),
        )

    def handle_push_notification_config_create(
        self,
        task_id: str,
        config: A2APushNotificationConfig,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, object]:
        """Create a task push notification config."""

        version_error = self._version_response(headers)
        if version_error is not None:
            return version_error
        extension_error = self._extension_response(headers)
        if extension_error is not None:
            return extension_error
        auth_error = self._authorize_response(
            headers,
            operation="pushNotificationConfigs/create",
            task_id=task_id,
            resource_type="pushNotificationConfig",
            resource_id=config.config_id,
        )
        if auth_error is not None:
            return auth_error
        rate_limit_error = self._rate_limit_response(
            headers,
            operation="pushNotificationConfigs/create",
            task_id=task_id,
            resource_type="pushNotificationConfig",
            resource_id=config.config_id,
        )
        if rate_limit_error is not None:
            return rate_limit_error
        if not task_id:
            return self._error_response(
                None,
                A2AOperationError(code=-32602, message="task id is required"),
            )
        if self._push_notification_configs is None:
            return self._push_config_not_supported()
        try:
            with use_incoming_trace_headers(headers):
                created = self._push_notification_configs.create(task_id, config)
        except A2APushNotificationConfigError as error:
            return self._error_response(
                None,
                A2AOperationError(code=-32602, message=str(error)),
            )
        return {"jsonrpc": "2.0", "result": _push_config_result(created)}

    def handle_push_notification_config_get(
        self,
        task_id: str,
        config_id: str,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, object]:
        """Get a task push notification config."""

        version_error = self._version_response(headers)
        if version_error is not None:
            return version_error
        extension_error = self._extension_response(headers)
        if extension_error is not None:
            return extension_error
        auth_error = self._authorize_response(
            headers,
            operation="pushNotificationConfigs/get",
            task_id=task_id,
            resource_type="pushNotificationConfig",
            resource_id=config_id,
        )
        if auth_error is not None:
            return auth_error
        rate_limit_error = self._rate_limit_response(
            headers,
            operation="pushNotificationConfigs/get",
            task_id=task_id,
            resource_type="pushNotificationConfig",
            resource_id=config_id,
        )
        if rate_limit_error is not None:
            return rate_limit_error
        if not task_id or not config_id:
            return self._error_response(
                None,
                A2AOperationError(code=-32602, message="task and config id are required"),
            )
        if self._push_notification_configs is None:
            return self._push_config_not_supported()
        with use_incoming_trace_headers(headers):
            config = self._push_notification_configs.get(task_id, config_id)
        if config is None:
            return self._error_response(
                None,
                A2AOperationError(
                    code=-32011,
                    message="push notification config not found",
                ),
            )
        return {"jsonrpc": "2.0", "result": _push_config_result(config)}

    def handle_push_notification_config_list(
        self,
        task_id: str,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, object]:
        """List task push notification configs."""

        version_error = self._version_response(headers)
        if version_error is not None:
            return version_error
        extension_error = self._extension_response(headers)
        if extension_error is not None:
            return extension_error
        auth_error = self._authorize_response(
            headers,
            operation="pushNotificationConfigs/list",
            task_id=task_id,
            resource_type="pushNotificationConfig",
        )
        if auth_error is not None:
            return auth_error
        rate_limit_error = self._rate_limit_response(
            headers,
            operation="pushNotificationConfigs/list",
            task_id=task_id,
            resource_type="pushNotificationConfig",
        )
        if rate_limit_error is not None:
            return rate_limit_error
        if not task_id:
            return self._error_response(
                None,
                A2AOperationError(code=-32602, message="task id is required"),
            )
        if self._push_notification_configs is None:
            return self._push_config_not_supported()
        with use_incoming_trace_headers(headers):
            configs = self._push_notification_configs.list(task_id)
        return {
            "jsonrpc": "2.0",
            "result": {
                "pushNotificationConfigs": [
                    a2a_push_notification_config_to_public_dict(config)
                    for config in configs
                ],
            },
        }

    def handle_push_notification_config_delete(
        self,
        task_id: str,
        config_id: str,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, object]:
        """Delete a task push notification config."""

        version_error = self._version_response(headers)
        if version_error is not None:
            return version_error
        extension_error = self._extension_response(headers)
        if extension_error is not None:
            return extension_error
        auth_error = self._authorize_response(
            headers,
            operation="pushNotificationConfigs/delete",
            task_id=task_id,
            resource_type="pushNotificationConfig",
            resource_id=config_id,
        )
        if auth_error is not None:
            return auth_error
        rate_limit_error = self._rate_limit_response(
            headers,
            operation="pushNotificationConfigs/delete",
            task_id=task_id,
            resource_type="pushNotificationConfig",
            resource_id=config_id,
        )
        if rate_limit_error is not None:
            return rate_limit_error
        if not task_id or not config_id:
            return self._error_response(
                None,
                A2AOperationError(code=-32602, message="task and config id are required"),
            )
        if self._push_notification_configs is None:
            return self._push_config_not_supported()
        with use_incoming_trace_headers(headers):
            self._push_notification_configs.delete(task_id, config_id)
        return {"jsonrpc": "2.0", "result": {}}

    def _push_config_not_supported(self) -> dict[str, object]:
        return self._error_response(
            None,
            A2AOperationError(
                code=-32010,
                message="push notifications are not supported",
            ),
        )

    def _error_response(
        self,
        request_id: str | int | None,
        error: A2AOperationError,
    ) -> dict[str, object]:
        return a2a_operation_response_to_dict(
            A2AOperationResponse(request_id=request_id, error=error),
        )

    def _unsupported_method_response(
        self,
        request_id: str | int | None,
        method: str,
    ) -> dict[str, object]:
        return a2a_operation_response_to_dict(
            A2AOperationResponse(
                request_id=request_id,
                error=A2AOperationError(
                    code=-32601,
                    message=f"unsupported method: {method}",
                ),
            ),
        )

    def _authorize(
        self,
        headers: Mapping[str, str] | None,
        *,
        operation: str,
        task_id: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
    ) -> None:
        policy = self._inbound_auth_policy
        authorize_resource = getattr(policy, "authorize_resource", None)
        if callable(authorize_resource):
            cast(A2AResourceInboundAuthPolicy, policy).authorize_resource(
                headers or {},
                operation=operation,
                task_id=task_id,
                resource_type=resource_type,
                resource_id=resource_id,
            )
            return
        authorize_operation = getattr(policy, "authorize_operation", None)
        if callable(authorize_operation):
            cast(A2AOperationInboundAuthPolicy, policy).authorize_operation(
                headers or {},
                operation=operation,
            )
            return
        policy.authorize(headers or {})

    def _authorize_response(
        self,
        headers: Mapping[str, str] | None,
        *,
        operation: str,
        task_id: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
    ) -> dict[str, object] | None:
        try:
            self._authorize(
                headers,
                operation=operation,
                task_id=task_id,
                resource_type=resource_type,
                resource_id=resource_id,
            )
        except A2AInboundAuthError:
            return self._unauthorized_peer_response()
        return None

    def _rate_limit(
        self,
        headers: Mapping[str, str] | None,
        *,
        operation: str,
        task_id: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
    ) -> None:
        policy = self._rate_limit_policy
        if policy is None:
            return
        policy.check_operation(
            headers or {},
            operation=operation,
            task_id=task_id,
            resource_type=resource_type,
            resource_id=resource_id,
        )

    def _rate_limit_response(
        self,
        headers: Mapping[str, str] | None,
        *,
        operation: str,
        request_id: str | int | None = None,
        task_id: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
    ) -> dict[str, object] | None:
        try:
            self._rate_limit(
                headers,
                operation=operation,
                task_id=task_id,
                resource_type=resource_type,
                resource_id=resource_id,
            )
        except A2ARateLimitError as error:
            return self._rate_limit_exceeded_response(request_id, error)
        return None

    def _version_response(
        self,
        headers: Mapping[str, str] | None,
        *,
        request_id: str | int | None = None,
    ) -> dict[str, object] | None:
        try:
            self._protocol_version_policy.ensure_supported(headers)
        except A2AProtocolVersionError as error:
            return self._version_not_supported_response(request_id, error)
        return None

    def _extension_response(
        self,
        headers: Mapping[str, str] | None,
        *,
        request_id: str | int | None = None,
    ) -> dict[str, object] | None:
        try:
            self._extension_negotiation_policy.negotiate_inbound(headers)
        except A2AExtensionNegotiationError as error:
            return self._extension_support_required_response(request_id, error)
        return None

    def _version_not_supported_response(
        self,
        request_id: str | int | None,
        error: A2AProtocolVersionError,
    ) -> dict[str, object]:
        return self._error_response(
            request_id,
            A2AOperationError(
                code=-32009,
                message="protocol version not supported",
                data={
                    "type": (
                        "https://a2a-protocol.org/errors/"
                        "version-not-supported"
                    ),
                    "title": "Protocol Version Not Supported",
                    "status": 400,
                    "detail": str(error),
                    "requestedVersion": error.requested_version,
                    "supportedVersions": list(error.supported_versions),
                },
            ),
        )

    def _extension_support_required_response(
        self,
        request_id: str | int | None,
        error: A2AExtensionNegotiationError,
    ) -> dict[str, object]:
        return self._error_response(
            request_id,
            A2AOperationError(
                code=-32008,
                message="extension support required",
                data={
                    "type": (
                        "https://a2a-protocol.org/errors/"
                        "extension-support-required"
                    ),
                    "title": "Extension Support Required",
                    "status": 400,
                    "detail": str(error),
                    "missingExtensions": list(error.missing_extensions),
                    "supportedExtensions": list(error.supported_extensions),
                    "requestedExtensions": list(error.requested_extensions),
                },
            ),
        )

    def _unauthorized_peer_response(self) -> dict[str, object]:
        return self._error_response(
            None,
            A2AOperationError(code=-32030, message="unauthorized peer"),
        )

    def _rate_limit_exceeded_response(
        self,
        request_id: str | int | None,
        error: A2ARateLimitError,
    ) -> dict[str, object]:
        return self._error_response(
            request_id,
            A2AOperationError(
                code=-32029,
                message="rate limit exceeded",
                data={
                    "type": (
                        "https://a2a-protocol.org/errors/"
                        "rate-limit-exceeded"
                    ),
                    "title": "Rate Limit Exceeded",
                    "status": 429,
                    "retryAfterSeconds": error.retry_after_seconds,
                },
            ),
        )


class A2AOperationClient:
    """Outbound A2A protocol operation client."""

    def __init__(
        self,
        transport: A2ATransport | None = None,
        *,
        auth_provider: A2AAuthProvider | None = None,
        protocol_version_policy: A2AProtocolVersionPolicy | None = None,
        extension_negotiation_policy: A2AExtensionNegotiationPolicy | None = None,
        egress_url_policy: A2AEgressUrlPolicy | None = None,
    ) -> None:
        self._transport = transport or UrllibA2ATransport()
        self._auth_provider = auth_provider
        self._egress_url_policy = egress_url_policy or PublicHttpsA2AEgressUrlPolicy()
        self._protocol_version_policy = (
            protocol_version_policy or A2AProtocolVersionPolicy()
        )
        self._extension_negotiation_policy = (
            extension_negotiation_policy or A2AExtensionNegotiationPolicy()
        )

    def send_message(
        self,
        card: A2AAgentCard,
        message: A2AMessage,
        *,
        request_id: str | int | None = None,
        timeout_seconds: float = 300,
        headers: dict[str, str] | None = None,
    ) -> A2AOperationResponse:
        """Send message/send to an A2A agent card URL."""

        request = A2AOperationRequest.message_send(
            message,
            request_id=request_id,
        )
        payload = a2a_operation_request_to_dict(request)
        url = self._jsonrpc_operation_url(card, "/message:send")
        self._validate_url(url)
        response = self._transport.post_json(
            url,
            payload,
            timeout_seconds,
            headers=self._headers_for_card(card, headers),
        )
        return a2a_operation_response_from_dict(response)

    def stream_message(
        self,
        card: A2AAgentCard,
        message: A2AMessage,
        *,
        request_id: str | int | None = None,
        timeout_seconds: float = 300,
        headers: dict[str, str] | None = None,
    ) -> A2AOperationResponse:
        """Send message/stream to an A2A agent card URL."""

        request = A2AOperationRequest.message_stream(
            message,
            request_id=request_id,
        )
        payload = a2a_operation_request_to_dict(request)
        url = self._jsonrpc_operation_url(card, "/message:stream")
        self._validate_url(url)
        response = self._transport.post_json(
            url,
            payload,
            timeout_seconds,
            headers=self._headers_for_card(card, headers),
        )
        return a2a_operation_response_from_dict(response)

    def stream_message_events(
        self,
        card: A2AAgentCard,
        message: A2AMessage,
        *,
        request_id: str | int | None = None,
        timeout_seconds: float = 300,
        headers: dict[str, str] | None = None,
    ) -> tuple[A2AMessageStreamEvent, ...]:
        """Send message/stream and parse its SSE response events."""

        request = A2AOperationRequest.message_stream(
            message,
            request_id=request_id,
        )
        payload = a2a_operation_request_to_dict(request)
        url = self._jsonrpc_operation_url(card, "/message:stream")
        self._validate_url(url)
        chunks = self._transport.post_sse(
            url,
            payload,
            timeout_seconds,
            headers=self._headers_for_card(card, headers),
        )
        return parse_a2a_sse_events(chunks)

    def task_resubscribe(
        self,
        card: A2AAgentCard,
        task_id: str,
        *,
        after_event_id: int | None = None,
        request_id: str | int | None = None,
        timeout_seconds: float = 300,
        headers: dict[str, str] | None = None,
    ) -> A2AOperationResponse:
        """Request the next task subscription event after a cursor."""

        request = A2AOperationRequest.task_resubscribe(
            task_id,
            after_event_id=after_event_id,
            request_id=request_id,
        )
        payload = a2a_operation_request_to_dict(request)
        url = self._jsonrpc_operation_url(card, f"/tasks/{task_id}:subscribe")
        self._validate_url(url)
        response = self._transport.post_json(
            url,
            payload,
            timeout_seconds,
            headers=self._headers_for_card(card, headers),
        )
        return a2a_operation_response_from_dict(response)

    def get_task(
        self,
        card: A2AAgentCard,
        task_id: str,
        *,
        timeout_seconds: float = 300,
        headers: dict[str, str] | None = None,
    ) -> A2AOperationResponse:
        """Fetch an A2A task by id."""

        url = card.url.rstrip("/") + f"/tasks/{task_id}"
        self._validate_url(url)
        response = self._transport.get_json(
            url,
            timeout_seconds,
            headers=self._headers_for_card(card, headers),
        )
        return a2a_operation_response_from_dict(response)

    def cancel_task(
        self,
        card: A2AAgentCard,
        task_id: str,
        *,
        timeout_seconds: float = 300,
        headers: dict[str, str] | None = None,
    ) -> A2AOperationResponse:
        """Request cancel for an A2A task."""

        payload = {
            "jsonrpc": "2.0",
            "method": "tasks/cancel",
            "params": {"id": task_id},
        }
        url = card.url.rstrip("/") + f"/tasks/{task_id}:cancel"
        self._validate_url(url)
        response = self._transport.post_json(
            url,
            payload,
            timeout_seconds,
            headers=self._headers_for_card(card, headers),
        )
        return a2a_operation_response_from_dict(response)

    def create_push_notification_config(
        self,
        card: A2AAgentCard,
        task_id: str,
        config: A2APushNotificationConfig,
        *,
        timeout_seconds: float = 300,
        headers: dict[str, str] | None = None,
    ) -> A2APushNotificationConfig:
        """Create a push notification config for a remote A2A task."""

        url = card.url.rstrip("/") + f"/tasks/{task_id}/pushNotificationConfigs"
        self._validate_url(url)
        response = self._transport.post_json(
            url,
            a2a_push_notification_config_to_dict(config),
            timeout_seconds,
            headers=self._headers_for_card(card, headers),
        )
        return _push_config_from_response(response)

    def get_push_notification_config(
        self,
        card: A2AAgentCard,
        task_id: str,
        config_id: str,
        *,
        timeout_seconds: float = 300,
        headers: dict[str, str] | None = None,
    ) -> A2APushNotificationConfig:
        """Fetch a push notification config for a remote A2A task."""

        url = card.url.rstrip("/") + (
            f"/tasks/{task_id}/pushNotificationConfigs/{config_id}"
        )
        self._validate_url(url)
        response = self._transport.get_json(
            url,
            timeout_seconds,
            headers=self._headers_for_card(card, headers),
        )
        return _push_config_from_response(response)

    def list_push_notification_configs(
        self,
        card: A2AAgentCard,
        task_id: str,
        *,
        timeout_seconds: float = 300,
        headers: dict[str, str] | None = None,
    ) -> tuple[A2APushNotificationConfig, ...]:
        """List push notification configs for a remote A2A task."""

        url = card.url.rstrip("/") + f"/tasks/{task_id}/pushNotificationConfigs"
        self._validate_url(url)
        response = self._transport.get_json(
            url,
            timeout_seconds,
            headers=self._headers_for_card(card, headers),
        )
        result_payload = response.get("result", {})
        result = result_payload if isinstance(result_payload, Mapping) else {}
        configs_payload = result.get("pushNotificationConfigs", [])
        if not isinstance(configs_payload, list):
            return ()
        return tuple(
            a2a_push_notification_config_from_dict(config)
            for config in configs_payload
            if isinstance(config, Mapping)
        )

    def delete_push_notification_config(
        self,
        card: A2AAgentCard,
        task_id: str,
        config_id: str,
        *,
        timeout_seconds: float = 300,
        headers: dict[str, str] | None = None,
    ) -> None:
        """Delete a push notification config for a remote A2A task."""

        url = card.url.rstrip("/") + (
            f"/tasks/{task_id}/pushNotificationConfigs/{config_id}"
        )
        self._validate_url(url)
        self._transport.delete_json(
            url,
            timeout_seconds,
            headers=self._headers_for_card(card, headers),
        )

    def _validate_url(self, url: str) -> None:
        _validate_a2a_egress_url(self._egress_url_policy, url)

    def _jsonrpc_operation_url(
        self,
        card: A2AAgentCard,
        legacy_suffix: str,
    ) -> str:
        interface_url = _official_jsonrpc_interface_url(card)
        if interface_url is not None:
            return interface_url
        return card.url.rstrip("/") + legacy_suffix

    def _headers_for_card(
        self,
        card: A2AAgentCard,
        headers: dict[str, str] | None,
    ) -> dict[str, str] | None:
        merged: dict[str, str] = {
            A2A_PROTOCOL_VERSION_HEADER: (
                self._protocol_version_policy.default_client_version
            ),
        }
        if self._auth_provider is not None:
            merged.update(self._auth_provider.headers_for_card(card))
        extensions = self._extension_negotiation_policy.extension_header_for_card(
            card,
            headers=headers,
        )
        if extensions is not None:
            merged[A2A_EXTENSIONS_HEADER] = extensions
        if headers:
            merged.update(headers)
            explicit_extensions = (
                self._extension_negotiation_policy.extension_header_for_card(
                    card,
                    headers=merged,
                )
            )
            if explicit_extensions is not None:
                merged[A2A_EXTENSIONS_HEADER] = explicit_extensions
        return merged or None


def _official_jsonrpc_interface_url(card: A2AAgentCard) -> str | None:
    for interface in card.supported_interfaces:
        if interface.protocol_binding.upper() == "JSONRPC":
            return interface.url.rstrip("/")
    return None


def a2a_push_notification_authentication_to_dict(
    authentication: A2APushNotificationAuthentication,
) -> dict[str, object]:
    """Serialize push notification authentication settings."""

    payload: dict[str, object] = {"schemes": list(authentication.schemes)}
    if authentication.credentials is not None:
        payload["credentials"] = authentication.credentials
    return payload


def a2a_push_notification_authentication_from_dict(
    payload: Mapping[str, object],
) -> A2APushNotificationAuthentication:
    """Deserialize push notification authentication settings."""

    schemes_payload = payload.get("schemes", [])
    schemes = (
        tuple(str(scheme) for scheme in schemes_payload if isinstance(scheme, str))
        if isinstance(schemes_payload, list)
        else ()
    )
    credentials = payload.get("credentials")
    return A2APushNotificationAuthentication(
        schemes=schemes,
        credentials=credentials if isinstance(credentials, str) else None,
    )


def a2a_push_notification_config_to_dict(
    config: A2APushNotificationConfig,
) -> dict[str, object]:
    """Serialize a push notification config."""

    payload: dict[str, object] = {"url": config.url}
    if config.config_id is not None:
        payload["id"] = config.config_id
    if config.token is not None:
        payload["token"] = config.token
    if config.authentication is not None:
        payload["authentication"] = (
            a2a_push_notification_authentication_to_dict(config.authentication)
        )
    return payload


def a2a_push_notification_config_to_public_dict(
    config: A2APushNotificationConfig,
) -> dict[str, object]:
    """Serialize a push notification config for read/list responses."""

    payload: dict[str, object] = {"url": config.url}
    if config.config_id is not None:
        payload["id"] = config.config_id
    if config.authentication is not None:
        payload["authentication"] = {
            "schemes": list(config.authentication.schemes),
        }
    return payload


def a2a_push_notification_config_from_dict(
    payload: Mapping[str, object],
) -> A2APushNotificationConfig:
    """Deserialize a push notification config."""

    url = payload.get("url")
    if not isinstance(url, str) or not url:
        raise ValueError("push notification url is required")
    authentication_payload = payload.get("authentication")
    authentication = (
        a2a_push_notification_authentication_from_dict(authentication_payload)
        if isinstance(authentication_payload, Mapping)
        else None
    )
    config_id = payload.get("id")
    token = payload.get("token")
    return A2APushNotificationConfig(
        url=url,
        authentication=authentication,
        config_id=config_id if isinstance(config_id, str) else None,
        token=token if isinstance(token, str) else None,
    )


def a2a_push_notification_payload_to_dict(
    event: A2ATaskSubscriptionEvent,
) -> dict[str, object]:
    """Serialize a webhook payload for a task status update."""

    return a2a_task_subscription_event_to_dict(event)


def a2a_push_notification_delivery_record_to_dict(
    record: A2APushNotificationDeliveryRecord,
) -> dict[str, object]:
    """Serialize queued push notification delivery state."""

    event_payload = a2a_task_subscription_event_to_dict(record.event)
    event_payload["id"] = record.event.event_id
    payload: dict[str, object] = {
        "delivery_id": record.delivery_id,
        "task_id": record.task_id,
        "config": a2a_push_notification_config_to_dict(record.config),
        "event": event_payload,
        "created_at": record.created_at,
        "next_run_at": record.next_run_at,
        "status": record.status,
        "attempts": record.attempts,
    }
    if record.last_error is not None:
        payload["last_error"] = record.last_error
    if record.response is not None:
        payload["response"] = dict(record.response)
    if record.worker_id is not None:
        payload["worker_id"] = record.worker_id
    if record.lease_expires_at is not None:
        payload["lease_expires_at"] = record.lease_expires_at
    if record.delivered_at is not None:
        payload["delivered_at"] = record.delivered_at
    if record.dead_lettered_at is not None:
        payload["dead_lettered_at"] = record.dead_lettered_at
    return payload


def a2a_push_notification_delivery_record_from_dict(
    payload: Mapping[str, object],
) -> A2APushNotificationDeliveryRecord:
    """Deserialize queued push notification delivery state."""

    config_payload = payload.get("config")
    if not isinstance(config_payload, Mapping):
        raise ValueError("delivery config is required")
    event_payload = payload.get("event")
    if not isinstance(event_payload, Mapping):
        raise ValueError("delivery event is required")
    status = str(payload.get("status", "queued"))
    if status not in {
        "queued",
        "running",
        "delivered",
        "retry_scheduled",
        "dead_letter",
    }:
        raise ValueError(f"unsupported delivery status: {status}")
    response_payload = payload.get("response")
    return A2APushNotificationDeliveryRecord(
        delivery_id=str(payload["delivery_id"]),
        task_id=str(payload["task_id"]),
        config=a2a_push_notification_config_from_dict(config_payload),
        event=a2a_task_subscription_event_from_dict(event_payload),
        created_at=float(payload["created_at"]),
        next_run_at=float(payload["next_run_at"]),
        status=cast(A2APushNotificationDeliveryRecordStatus, status),
        attempts=int(payload.get("attempts", 0)),
        last_error=(
            None if payload.get("last_error") is None else str(payload["last_error"])
        ),
        response=(
            dict(response_payload)
            if isinstance(response_payload, Mapping)
            else None
        ),
        worker_id=(
            None if payload.get("worker_id") is None else str(payload["worker_id"])
        ),
        lease_expires_at=_optional_float(payload.get("lease_expires_at")),
        delivered_at=_optional_float(payload.get("delivered_at")),
        dead_lettered_at=_optional_float(payload.get("dead_lettered_at")),
    )


def _delivery_record_params(
    record: A2APushNotificationDeliveryRecord,
) -> tuple[object, ...]:
    return (
        record.delivery_id,
        record.task_id,
        record.config.config_id,
        record.status,
        record.next_run_at,
        record.created_at,
        record.attempts,
        record.worker_id,
        record.lease_expires_at,
        record.delivered_at,
        record.dead_lettered_at,
        json.dumps(
            a2a_push_notification_delivery_record_to_dict(record),
            ensure_ascii=False,
            allow_nan=False,
        ),
    )


def _push_notification_headers(
    config: A2APushNotificationConfig,
) -> dict[str, str] | None:
    authentication = config.authentication
    if authentication is None or authentication.credentials is None:
        return None
    schemes = {scheme.lower() for scheme in authentication.schemes}
    if "bearer" in schemes:
        return {"Authorization": f"Bearer {authentication.credentials}"}
    if "basic" in schemes:
        encoded = base64.b64encode(
            authentication.credentials.encode("utf-8"),
        ).decode("ascii")
        return {"Authorization": f"Basic {encoded}"}
    return None


def _redact_push_notification_error(
    config: A2APushNotificationConfig,
    error: str | None,
) -> str | None:
    if error is None:
        return None
    redacted = error
    sensitive_values = [config.token]
    if config.authentication is not None:
        sensitive_values.append(config.authentication.credentials)
    for value in sensitive_values:
        if value:
            redacted = redacted.replace(value, "<redacted>")
    return redacted


def _push_config_result(config: A2APushNotificationConfig) -> dict[str, object]:
    return {
        "pushNotificationConfig": a2a_push_notification_config_to_public_dict(config),
    }


def _push_config_from_response(
    response: Mapping[str, object],
) -> A2APushNotificationConfig:
    result_payload = response.get("result", {})
    result = result_payload if isinstance(result_payload, Mapping) else {}
    config_payload = result.get("pushNotificationConfig")
    if not isinstance(config_payload, Mapping):
        raise ValueError("pushNotificationConfig is required")
    return a2a_push_notification_config_from_dict(config_payload)


def _validate_push_notification_config(
    config: A2APushNotificationConfig,
) -> None:
    PublicHttpsA2APushNotificationUrlPolicy().validate(config)


def _normalized_push_notification_hostname(hostname: str) -> str:
    return hostname.strip().lower().rstrip(".").split("%", 1)[0]


def _normalized_push_notification_domain_suffix(suffix: str) -> str:
    return _normalized_push_notification_hostname(suffix).lstrip(".")


def _push_notification_hostname_matches_suffix(
    hostname: str,
    suffix: str,
) -> bool:
    return hostname == suffix or hostname.endswith(f".{suffix}")


def a2a_message_part_to_dict(part: A2AMessagePart) -> dict[str, object]:
    """Serialize a message part."""

    if part.kind == "text":
        if part.text is None:
            raise ValueError("text part requires text")
        return {"text": part.text}
    if part.kind == "file":
        payload: dict[str, object] = {}
        if part.raw is not None and part.url is not None:
            raise ValueError("file part requires exactly one payload")
        if part.raw is not None:
            payload["raw"] = part.raw
        elif part.url is not None:
            payload["url"] = part.url
        else:
            raise ValueError("file part requires raw or url")
        if part.filename is not None:
            payload["filename"] = part.filename
        if part.media_type is not None:
            payload["mediaType"] = part.media_type
        return payload
    if part.kind == "data":
        if part.data is None:
            raise ValueError("data part requires data")
        payload = {"data": dict(part.data)}
        if part.media_type is not None:
            payload["mediaType"] = part.media_type
        return payload
    raise ValueError(f"unsupported A2A message part kind: {part.kind}")


def a2a_message_part_from_dict(payload: Mapping[str, object]) -> A2AMessagePart:
    """Deserialize a message part."""

    payload_fields = [
        name for name in ("text", "raw", "url", "data")
        if name in payload
    ]
    if len(payload_fields) > 1:
        raise ValueError("message part requires exactly one payload")
    kind = payload.get("kind")
    if kind == "file":
        return _legacy_a2a_file_part_from_dict(payload)
    if kind == "data":
        data = payload.get("data")
        if not isinstance(data, Mapping):
            raise ValueError("data part requires data")
        return A2AMessagePart.from_data(
            data,
            media_type=_a2a_message_part_media_type(payload),
        )
    if kind is not None and kind != "text":
        raise ValueError(f"unsupported A2A message part kind: {kind}")
    text = payload.get("text")
    if text is not None:
        if not isinstance(text, str):
            raise ValueError("text part requires text")
        return A2AMessagePart.from_text(text)
    raw = payload.get("raw")
    if raw is not None:
        if not isinstance(raw, str) or not raw:
            raise ValueError("file part requires raw")
        return A2AMessagePart.from_file_bytes(
            raw,
            filename=_optional_str(payload.get("filename")),
            media_type=_a2a_message_part_media_type(payload),
        )
    url = payload.get("url")
    if url is not None:
        if not isinstance(url, str) or not url:
            raise ValueError("file part requires url")
        return A2AMessagePart.from_file_url(
            url,
            filename=_optional_str(payload.get("filename")),
            media_type=_a2a_message_part_media_type(payload),
        )
    data = payload.get("data")
    if data is not None:
        if not isinstance(data, Mapping):
            raise ValueError("data part requires data")
        return A2AMessagePart.from_data(
            data,
            media_type=_a2a_message_part_media_type(payload),
        )
    raise ValueError("message part requires exactly one payload")


def _legacy_a2a_file_part_from_dict(
    payload: Mapping[str, object],
) -> A2AMessagePart:
    file_payload = payload.get("file")
    if not isinstance(file_payload, Mapping):
        raise ValueError("file part requires file")
    filename = _optional_str(
        file_payload.get("name", file_payload.get("filename")),
    )
    media_type = _optional_str(
        file_payload.get(
            "mimeType",
            file_payload.get("mediaType", payload.get("mediaType")),
        ),
    )
    raw = file_payload.get("fileWithBytes", file_payload.get("raw"))
    if isinstance(raw, str) and raw:
        return A2AMessagePart.from_file_bytes(
            raw,
            filename=filename,
            media_type=media_type,
        )
    url = file_payload.get(
        "uri",
        file_payload.get("fileWithUri", file_payload.get("url")),
    )
    if isinstance(url, str) and url:
        return A2AMessagePart.from_file_url(
            url,
            filename=filename,
            media_type=media_type,
        )
    raise ValueError("file part requires raw or url")


def _a2a_message_part_media_type(payload: Mapping[str, object]) -> str | None:
    media_type = payload.get("mediaType")
    if isinstance(media_type, str):
        return media_type
    metadata = payload.get("metadata")
    if isinstance(metadata, Mapping):
        metadata_media_type = metadata.get("mediaType")
        if isinstance(metadata_media_type, str):
            return metadata_media_type
    return None


def a2a_message_to_dict(message: A2AMessage) -> dict[str, object]:
    """Serialize an A2A message."""

    payload: dict[str, object] = {
        "role": message.role,
        "parts": [a2a_message_part_to_dict(part) for part in message.parts],
    }
    if message.message_id is not None:
        payload["messageId"] = message.message_id
    if message.context_id is not None:
        payload["contextId"] = message.context_id
    if message.task_id is not None:
        payload["taskId"] = message.task_id
    if message.metadata:
        payload["metadata"] = dict(message.metadata)
    return payload


def a2a_message_from_dict(payload: Mapping[str, object]) -> A2AMessage:
    """Deserialize an A2A message."""

    role = payload.get("role")
    if role not in {"user", "agent"}:
        raise ValueError("message.role must be user or agent")
    parts_payload = payload.get("parts")
    if not isinstance(parts_payload, list) or not parts_payload:
        raise ValueError("message.parts must be a non-empty list")
    parts = tuple(
        a2a_message_part_from_dict(part)
        for part in parts_payload
        if isinstance(part, Mapping)
    )
    if not parts:
        raise ValueError("message.parts must contain objects")
    metadata_payload = payload.get("metadata", {})
    metadata = (
        dict(metadata_payload)
        if isinstance(metadata_payload, Mapping)
        else {}
    )
    return A2AMessage(
        role=role,  # type: ignore[arg-type]
        parts=parts,
        message_id=_optional_str(payload.get("messageId")),
        context_id=_optional_str(payload.get("contextId")),
        task_id=_optional_str(payload.get("taskId")),
        metadata=metadata,
    )


def a2a_artifact_to_dict(artifact: A2AArtifact) -> dict[str, object]:
    """Serialize an A2A artifact projection."""

    if not artifact.artifact_id:
        raise ValueError("artifact.artifact_id is required")
    if not artifact.parts:
        raise ValueError("artifact.parts must be a non-empty tuple")
    payload: dict[str, object] = {
        "artifactId": artifact.artifact_id,
    }
    if artifact.name is not None:
        payload["name"] = artifact.name
    if artifact.description is not None:
        payload["description"] = artifact.description
    payload["parts"] = [
        a2a_message_part_to_dict(part)
        for part in artifact.parts
    ]
    if artifact.metadata:
        payload["metadata"] = dict(artifact.metadata)
    return payload


def a2a_artifact_from_dict(payload: Mapping[str, object]) -> A2AArtifact:
    """Deserialize an A2A artifact projection."""

    artifact_id = payload.get("artifactId", payload.get("id"))
    if not isinstance(artifact_id, str) or not artifact_id:
        raise ValueError("artifact.artifactId is required")
    parts_payload = payload.get("parts")
    if isinstance(parts_payload, list) and parts_payload:
        parts = tuple(
            a2a_message_part_from_dict(part)
            for part in parts_payload
            if isinstance(part, Mapping)
        )
    else:
        parts = ()
    if not parts:
        parts = (_legacy_artifact_data_part(payload),)
    metadata_payload = payload.get("metadata", {})
    metadata = (
        dict(metadata_payload)
        if isinstance(metadata_payload, Mapping)
        else {}
    )
    return A2AArtifact(
        artifact_id=artifact_id,
        parts=parts,
        name=_optional_str(payload.get("name")),
        description=_optional_str(payload.get("description")),
        metadata=metadata,
    )


def _legacy_artifact_data_part(payload: Mapping[str, object]) -> A2AMessagePart:
    data = {
        key: value
        for key, value in payload.items()
        if key not in {"artifactId", "id", "name", "description", "metadata"}
    }
    if not data:
        raise ValueError("artifact.parts must contain objects")
    return A2AMessagePart.from_data(data)


def a2a_task_to_dict(task: A2ATask) -> dict[str, object]:
    """Serialize an A2A task projection."""

    payload: dict[str, object] = {
        "id": task.task_id,
        "status": {"state": task.state},
    }
    if task.context_id is not None:
        payload["contextId"] = task.context_id
    if task.messages:
        payload["messages"] = [
            a2a_message_to_dict(message)
            for message in task.messages
        ]
    if task.artifacts:
        payload["artifacts"] = [
            a2a_artifact_to_dict(artifact)
            for artifact in task.artifacts
        ]
    if task.metadata:
        payload["metadata"] = dict(task.metadata)
    return payload


def a2a_task_from_dict(payload: Mapping[str, object]) -> A2ATask:
    """Deserialize an A2A task projection."""

    task_id = payload.get("id")
    if not isinstance(task_id, str) or not task_id:
        raise ValueError("task.id is required")
    status_payload = payload.get("status", {})
    status = status_payload if isinstance(status_payload, Mapping) else {}
    state = status.get("state", "unknown")
    messages_payload = payload.get("messages", [])
    messages = (
        tuple(
            a2a_message_from_dict(message)
            for message in messages_payload
            if isinstance(message, Mapping)
        )
        if isinstance(messages_payload, list)
        else ()
    )
    artifacts_payload = payload.get("artifacts", [])
    artifacts = (
        tuple(
            a2a_artifact_from_dict(artifact)
            for artifact in artifacts_payload
            if isinstance(artifact, Mapping)
        )
        if isinstance(artifacts_payload, list)
        else ()
    )
    metadata_payload = payload.get("metadata", {})
    metadata = (
        dict(metadata_payload)
        if isinstance(metadata_payload, Mapping)
        else {}
    )
    return A2ATask(
        task_id=task_id,
        context_id=_optional_str(payload.get("contextId")),
        state=_task_state(str(state)),
        messages=messages,
        artifacts=artifacts,
        metadata=metadata,
    )


def a2a_task_subscription_event_to_dict(
    event: A2ATaskSubscriptionEvent,
) -> dict[str, object]:
    """Serialize an A2A task subscription update event."""

    update_metadata: dict[str, object] = {}
    if event.task.metadata:
        update_metadata.update(_a2a_event_metadata_with_reserved_keys(
            event.task.metadata,
        ))
    update_metadata["version"] = event.event_id
    update_metadata["final"] = event.final
    update_payload: dict[str, object] = {
        "taskId": event.task.task_id,
        "status": {"state": event.task.state},
        "metadata": update_metadata,
    }
    if event.task.context_id is not None:
        update_payload["contextId"] = event.task.context_id
    if event.task.messages:
        update_payload["messages"] = [
            a2a_message_to_dict(message)
            for message in event.task.messages
        ]
    if event.task.artifacts:
        update_payload["artifacts"] = [
            a2a_artifact_to_dict(artifact)
            for artifact in event.task.artifacts
        ]
    return {
        "statusUpdate": update_payload,
    }


def a2a_task_subscription_event_from_dict(
    payload: Mapping[str, object],
) -> A2ATaskSubscriptionEvent:
    """Deserialize an A2A task subscription update event."""

    status_update = payload.get("statusUpdate")
    if isinstance(status_update, Mapping):
        return _a2a_task_subscription_event_from_status_update(status_update)
    task_payload = payload.get("task")
    if not isinstance(task_payload, Mapping):
        raise ValueError("task subscription event requires task")
    metadata_payload = task_payload.get("metadata", {})
    metadata = metadata_payload if isinstance(metadata_payload, Mapping) else {}
    version = metadata.get("version", metadata.get("internalVersion"))
    if version is None:
        version = payload.get("id", "0")
    task_payload = _a2a_legacy_status_task_payload(task_payload)
    return A2ATaskSubscriptionEvent(
        event_id=str(version),
        task=a2a_task_from_dict(task_payload),
        final=bool(payload.get("final", False)),
    )


def _a2a_task_subscription_event_from_status_update(
    payload: Mapping[str, object],
) -> A2ATaskSubscriptionEvent:
    task_id = payload.get("taskId")
    if not isinstance(task_id, str) or not task_id:
        raise ValueError("status update requires taskId")
    status_payload = payload.get("status", {})
    status = status_payload if isinstance(status_payload, Mapping) else {}
    state = status.get("state", "unknown")
    metadata_payload = payload.get("metadata", {})
    metadata = (
        dict(metadata_payload)
        if isinstance(metadata_payload, Mapping)
        else {}
    )
    version = metadata.pop("version", payload.get("id", "0"))
    final = bool(metadata.pop("final", payload.get("final", False)))
    metadata = _a2a_event_metadata_restore_reserved_keys(metadata)
    messages_payload = payload.get("messages", [])
    messages = (
        tuple(
            a2a_message_from_dict(message)
            for message in messages_payload
            if isinstance(message, Mapping)
        )
        if isinstance(messages_payload, list)
        else ()
    )
    artifacts_payload = payload.get("artifacts", [])
    artifacts = (
        tuple(
            a2a_artifact_from_dict(artifact)
            for artifact in artifacts_payload
            if isinstance(artifact, Mapping)
        )
        if isinstance(artifacts_payload, list)
        else ()
    )
    return A2ATaskSubscriptionEvent(
        event_id=str(version),
        task=A2ATask(
            task_id=task_id,
            context_id=_optional_str(payload.get("contextId")),
            state=_task_state(str(state)),
            messages=messages,
            artifacts=artifacts,
            metadata=metadata,
        ),
        final=final,
    )


def _a2a_legacy_status_task_payload(
    payload: Mapping[str, object],
) -> dict[str, object]:
    task_payload = dict(payload)
    metadata_payload = task_payload.get("metadata", {})
    if isinstance(metadata_payload, Mapping):
        metadata = dict(metadata_payload)
        metadata.pop("version", None)
        if "internalVersion" in metadata:
            metadata["version"] = metadata.pop("internalVersion")
        if metadata:
            task_payload["metadata"] = metadata
        else:
            task_payload.pop("metadata", None)
    return task_payload


def a2a_task_artifact_update_event_to_dict(
    event: A2ATaskArtifactUpdateEvent,
) -> dict[str, object]:
    """Serialize an A2A task artifact update event."""

    update_metadata: dict[str, object] = {}
    if event.metadata:
        update_metadata.update(_a2a_event_metadata_with_reserved_keys(
            event.metadata,
        ))
    update_metadata["version"] = event.event_id
    update_payload: dict[str, object] = {
        "taskId": event.task_id,
        "artifact": a2a_artifact_to_dict(event.artifact),
        "append": event.append,
        "lastChunk": event.last_chunk,
        "metadata": update_metadata,
    }
    if event.context_id is not None:
        update_payload["contextId"] = event.context_id
    return {"artifactUpdate": update_payload}


def a2a_task_artifact_update_event_from_dict(
    payload: Mapping[str, object],
) -> A2ATaskArtifactUpdateEvent:
    """Deserialize an A2A task artifact update event."""

    artifact_update = payload.get("artifactUpdate")
    if not isinstance(artifact_update, Mapping):
        raise ValueError("artifact update event requires artifactUpdate")
    task_id = artifact_update.get("taskId")
    if not isinstance(task_id, str) or not task_id:
        raise ValueError("artifact update requires taskId")
    artifact_payload = artifact_update.get("artifact")
    if not isinstance(artifact_payload, Mapping):
        raise ValueError("artifact update requires artifact")
    metadata_payload = artifact_update.get("metadata", {})
    metadata = (
        dict(metadata_payload)
        if isinstance(metadata_payload, Mapping)
        else {}
    )
    version = metadata.pop("version", artifact_update.get("id", "0"))
    metadata = _a2a_event_metadata_restore_reserved_keys(metadata)
    return A2ATaskArtifactUpdateEvent(
        event_id=str(version),
        task_id=task_id,
        context_id=_optional_str(artifact_update.get("contextId")),
        artifact=a2a_artifact_from_dict(artifact_payload),
        append=bool(artifact_update.get("append", False)),
        last_chunk=bool(artifact_update.get("lastChunk", False)),
        metadata=metadata,
    )


def a2a_message_stream_event_from_dict(
    payload: Mapping[str, object],
    *,
    event: str | None = None,
) -> A2AMessageStreamEvent:
    """Deserialize one A2A message/stream event payload."""

    raw = dict(payload)
    task_payload = payload.get("task")
    if isinstance(task_payload, Mapping):
        return A2AMessageStreamEvent(
            event=event,
            raw=raw,
            task=a2a_task_from_dict(task_payload),
        )
    message_payload = payload.get("message")
    if isinstance(message_payload, Mapping):
        return A2AMessageStreamEvent(
            event=event,
            raw=raw,
            message=a2a_message_from_dict(message_payload),
        )
    if isinstance(payload.get("statusUpdate"), Mapping):
        return A2AMessageStreamEvent(
            event=event,
            raw=raw,
            task_event=a2a_task_subscription_event_from_dict(payload),
        )
    if isinstance(payload.get("artifactUpdate"), Mapping):
        return A2AMessageStreamEvent(
            event=event,
            raw=raw,
            artifact_event=a2a_task_artifact_update_event_from_dict(payload),
        )
    raise ValueError("message stream event requires task, message, statusUpdate, or artifactUpdate")


def parse_a2a_sse_events(
    chunks: object,
) -> tuple[A2AMessageStreamEvent, ...]:
    """Parse A2A message/stream SSE chunks into typed stream events."""

    events: list[A2AMessageStreamEvent] = []
    event_name: str | None = None
    data_lines: list[str] = []

    for raw_line in _a2a_sse_lines(chunks):
        line = raw_line.rstrip("\r")
        if line == "":
            if data_lines:
                payload = json.loads("\n".join(data_lines))
                if not isinstance(payload, Mapping):
                    raise ValueError("message stream event data must be a JSON object")
                events.append(
                    a2a_message_stream_event_from_dict(
                        payload,
                        event=event_name,
                    ),
                )
            event_name = None
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        field, separator, value = line.partition(":")
        if separator and value.startswith(" "):
            value = value[1:]
        if field == "event":
            event_name = value
        elif field == "data":
            data_lines.append(value)

    if data_lines:
        payload = json.loads("\n".join(data_lines))
        if not isinstance(payload, Mapping):
            raise ValueError("message stream event data must be a JSON object")
        events.append(a2a_message_stream_event_from_dict(payload, event=event_name))
    return tuple(events)


def _a2a_sse_lines(chunks: object) -> tuple[str, ...]:
    if isinstance(chunks, (str, bytes)):
        iterable = (chunks,)
    else:
        iterable = chunks
    lines: list[str] = []
    buffer = ""
    for chunk in iterable:  # type: ignore[union-attr]
        if isinstance(chunk, bytes):
            text = chunk.decode("utf-8")
        else:
            text = str(chunk)
        buffer += text
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            lines.append(line)
    if buffer:
        lines.append(buffer)
    return tuple(lines)


def _a2a_event_metadata_with_reserved_keys(
    metadata: Mapping[str, object],
) -> dict[str, object]:
    payload: dict[str, object] = {}
    for key, value in metadata.items():
        if key == "version":
            payload["internalVersion"] = value
        elif key == "final":
            payload["internalFinal"] = value
        else:
            payload[key] = value
    return payload


def _a2a_event_metadata_restore_reserved_keys(
    metadata: Mapping[str, object],
) -> dict[str, object]:
    payload = dict(metadata)
    if "internalVersion" in payload:
        payload["version"] = payload.pop("internalVersion")
    if "internalFinal" in payload:
        payload["final"] = payload.pop("internalFinal")
    return payload


def a2a_state_from_task_status(status: TaskStatus) -> A2ATaskState:
    """Map internal agent-os task status to A2A task state."""

    mapping: dict[TaskStatus, A2ATaskState] = {
        "queued": "submitted",
        "running": "working",
        "completed": "completed",
        "failed": "failed",
        "cancelled": "canceled",
        "timeout": "failed",
    }
    return mapping.get(status, "unknown")


def a2a_task_from_task_record(record: TaskRecord) -> A2ATask:
    """Project an internal TaskRecord into an A2A task."""

    messages: list[A2AMessage] = []
    artifacts: list[A2AArtifact] = []
    if record.result is not None:
        messages.append(
            A2AMessage(
                role="agent",
                parts=(A2AMessagePart.from_text(record.result.summary),),
                task_id=record.task_id,
                context_id=record.parent_agent_id,
            ),
        )
        if record.result.artifacts:
            artifacts.append(
                A2AArtifact(
                    artifact_id=f"{record.task_id}-result",
                    name="Task result artifacts",
                    parts=(
                        A2AMessagePart.from_data(
                            dict(record.result.artifacts),
                            media_type="application/json",
                        ),
                    ),
                ),
            )
    metadata: dict[str, object] = {
        "parentAgentId": record.parent_agent_id,
        "targetAgentId": record.target_agent_id,
        "internalStatus": record.status,
        "createdAt": record.created_at,
        "deadlineAt": record.deadline_at,
        "attempt": record.attempt,
        "version": record.version,
    }
    if record.worker_id is not None:
        metadata["workerId"] = record.worker_id
    if record.completed_at is not None:
        metadata["completedAt"] = record.completed_at
    if record.cancel_requested_at is not None:
        metadata["cancelRequested"] = True
        metadata["cancelRequestedAt"] = record.cancel_requested_at
    return A2ATask(
        task_id=record.task_id,
        context_id=record.parent_agent_id,
        state=a2a_state_from_task_status(record.status),
        messages=tuple(messages),
        artifacts=tuple(artifacts),
        metadata=metadata,
    )


def a2a_operation_request_to_dict(
    request: A2AOperationRequest,
) -> dict[str, object]:
    """Serialize an A2A operation request."""

    payload: dict[str, object] = {
        "jsonrpc": request.jsonrpc,
        "method": request.method,
        "params": dict(request.params),
    }
    if request.request_id is not None:
        payload["id"] = request.request_id
    return payload


def a2a_operation_request_from_dict(
    payload: Mapping[str, object],
) -> A2AOperationRequest:
    """Deserialize an A2A operation request."""

    method = payload.get("method")
    if not isinstance(method, str) or not method:
        raise ValueError("method is required")
    params_payload = payload.get("params", {})
    if not isinstance(params_payload, Mapping):
        raise ValueError("params must be an object")
    return A2AOperationRequest(
        jsonrpc=str(payload.get("jsonrpc", "2.0")),
        method=method,
        params=dict(params_payload),
        request_id=payload.get("id"),
    )


def a2a_operation_error_to_dict(
    error: A2AOperationError,
) -> dict[str, object]:
    """Serialize an A2A operation error."""

    payload: dict[str, object] = {
        "code": error.code,
        "message": error.message,
    }
    if error.data:
        payload["data"] = dict(error.data)
    return payload


def a2a_operation_error_from_dict(
    payload: Mapping[str, object],
) -> A2AOperationError:
    """Deserialize an A2A operation error."""

    code = payload.get("code")
    if not isinstance(code, int):
        raise ValueError("error.code is required")
    message = payload.get("message")
    if not isinstance(message, str):
        raise ValueError("error.message is required")
    data_payload = payload.get("data")
    data = dict(data_payload) if isinstance(data_payload, Mapping) else None
    return A2AOperationError(code=code, message=message, data=data)


def a2a_operation_response_to_dict(
    response: A2AOperationResponse,
) -> dict[str, object]:
    """Serialize an A2A operation response."""

    payload: dict[str, object] = {"jsonrpc": response.jsonrpc}
    if response.request_id is not None:
        payload["id"] = response.request_id
    if response.error is not None:
        payload["error"] = a2a_operation_error_to_dict(response.error)
    elif response.task is not None:
        payload["result"] = {"task": a2a_task_to_dict(response.task)}
    elif response.task_event is not None:
        payload["result"] = a2a_task_subscription_event_to_dict(response.task_event)
    else:
        payload["result"] = {}
    return payload


def a2a_operation_response_from_dict(
    payload: Mapping[str, object],
) -> A2AOperationResponse:
    """Deserialize an A2A operation response."""

    error_payload = payload.get("error")
    error = (
        a2a_operation_error_from_dict(error_payload)
        if isinstance(error_payload, Mapping)
        else None
    )
    result_payload = payload.get("result", {})
    result = result_payload if isinstance(result_payload, Mapping) else {}
    task_payload = result.get("task")
    task = (
        a2a_task_from_dict(task_payload)
        if isinstance(task_payload, Mapping)
        else None
    )
    task_event = (
        a2a_task_subscription_event_from_dict(result)
        if isinstance(result.get("statusUpdate"), Mapping)
        else None
    )
    return A2AOperationResponse(
        jsonrpc=str(payload.get("jsonrpc", "2.0")),
        request_id=payload.get("id"),
        task=task,
        task_event=task_event,
        error=error,
    )


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    return float(value)


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    parsed = int(value)
    if parsed < 0:
        raise ValueError("value must be non-negative")
    return parsed


def _header_value(headers: Mapping[str, str] | None, name: str) -> str | None:
    if headers is None:
        return None
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value
    return None


def _unique_non_empty_strings(values: tuple[object, ...] | list[object] | object) -> tuple[str, ...]:
    if isinstance(values, str):
        iterable: tuple[object, ...] = (values,)
    elif isinstance(values, tuple):
        iterable = values
    elif isinstance(values, list):
        iterable = tuple(values)
    else:
        iterable = tuple(values) if hasattr(values, "__iter__") else (values,)
    seen: set[str] = set()
    items: list[str] = []
    for value in iterable:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        items.append(text)
    return tuple(items)


def _card_extension_uris(card: A2AAgentCard) -> tuple[str, ...]:
    return _unique_non_empty_strings(
        tuple(extension.uri for extension in card.capabilities.extensions),
    )


def _card_required_extension_uris(card: A2AAgentCard) -> tuple[str, ...]:
    return _unique_non_empty_strings(
        tuple(
            extension.uri
            for extension in card.capabilities.extensions
            if extension.required
        ),
    )


def _json_object(value: object, label: str) -> dict[str, object]:
    if isinstance(value, str):
        loaded = json.loads(value)
    else:
        loaded = value
    if not isinstance(loaded, dict):
        raise TypeError(f"{label} must be an object")
    return loaded


def _commit_postgres_connection(connection: object) -> None:
    commit = getattr(connection, "commit", None)
    if commit is not None:
        commit()


def _close_postgres_connection(owner: object) -> None:
    pool = getattr(owner, "_pool", None)
    connection = getattr(owner, "_connection")
    if pool is not None:
        putconn = getattr(pool, "putconn", None)
        if callable(putconn):
            putconn(connection)
            return
    context = getattr(owner, "_pool_context", None)
    if context is not None:
        context.__exit__(None, None, None)
        return
    close = getattr(connection, "close", None)
    if callable(close):
        close()


def _task_state(value: str) -> A2ATaskState:
    states = {
        "submitted",
        "working",
        "input-required",
        "completed",
        "canceled",
        "failed",
        "rejected",
        "auth-required",
        "unknown",
    }
    if value in states:
        return value  # type: ignore[return-value]
    return "unknown"
