from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal, Mapping, Protocol, Sequence

from agentos.multi import AgentCard


NacosRegistryOperation = Literal["register", "deregister", "resolve", "discover"]

_PREFIX = "agentos."
_FORBIDDEN_METADATA_KEYS = frozenset(
    {
        "task_id",
        "task",
        "task_record",
        "plan_id",
        "plan",
        "plan_record",
        "session",
        "session_snapshot",
        "snapshot",
        "message",
        "message_payload",
        "queue",
        "inbox",
        "wakeup",
        "worker_state",
        "worker_process_state",
        "process_state",
        "env",
        "secret",
        "credential",
        "password",
        "token",
    },
)
_FORBIDDEN_METADATA_KEY_PARTS = frozenset(
    {
        "credential",
        "credentials",
        "env",
        "password",
        "secret",
        "token",
    },
)


class NacosRegistryError(ValueError):
    """Raised when Nacos registry metadata cannot be projected safely."""


class NacosRegistryClient(Protocol):
    """Minimal Nacos service-discovery client boundary used by AgentOS."""

    def register_instance(
        self,
        *,
        service_name: str,
        group_name: str,
        cluster_name: str,
        namespace_id: str | None = None,
        ip: str,
        port: int,
        metadata: Mapping[str, str],
        healthy: bool,
        ephemeral: bool,
    ) -> None:
        """Register one service instance."""

    def deregister_instance(
        self,
        *,
        service_name: str,
        group_name: str,
        cluster_name: str,
        namespace_id: str | None = None,
        ip: str,
        port: int,
        ephemeral: bool,
    ) -> None:
        """Deregister one service instance."""

    def list_instances(
        self,
        *,
        service_name: str,
        group_name: str,
        cluster_name: str | None = None,
        namespace_id: str | None = None,
        healthy_only: bool = True,
    ) -> Sequence[object]:
        """List service instances for one service."""


@dataclass(frozen=True, slots=True)
class NacosRegistryConfig:
    """Nacos discovery configuration owned by the adapter boundary."""

    service_name_prefix: str = "agentos"
    service_name: str | None = None
    group_name: str = "DEFAULT_GROUP"
    cluster_name: str = "DEFAULT"
    namespace_id: str | None = None
    ephemeral: bool = True
    healthy_only: bool = True

    def service_for_agent(self, agent_id: str) -> str:
        """Return the Nacos service name used for an agent id."""

        if self.service_name is not None:
            return self.service_name
        if not agent_id:
            raise NacosRegistryError("agent_id must not be empty")
        prefix = self.service_name_prefix.strip(".")
        if not prefix:
            raise NacosRegistryError("service_name_prefix must not be empty")
        return f"{prefix}.{agent_id}"

    @property
    def discovery_service_name(self) -> str:
        """Return the service name used for capability discovery."""

        if self.service_name is None:
            raise NacosRegistryError(
                "NacosAgentCardResolver.discover requires config.service_name "
                "when agents are not registered under a shared service",
            )
        return self.service_name


@dataclass(frozen=True, slots=True)
class NacosRegistryEvidence:
    """JSON-safe evidence for Nacos registry operations."""

    operation: NacosRegistryOperation
    service_name: str
    group_name: str
    cluster_name: str
    namespace_id: str | None = None
    agent_id: str | None = None
    ip: str | None = None
    port: int | None = None
    healthy: bool | None = None
    ephemeral: bool | None = None
    metadata_keys: tuple[str, ...] = ()
    instance_count: int | None = None

    def to_dict(self) -> dict[str, object]:
        """Return JSON-safe evidence suitable for readiness or audit payloads."""

        payload: dict[str, object] = {
            "operation": self.operation,
            "service_name": self.service_name,
            "group_name": self.group_name,
            "cluster_name": self.cluster_name,
            "metadata_keys": list(self.metadata_keys),
        }
        if self.namespace_id is not None:
            payload["namespace_id"] = self.namespace_id
        if self.agent_id is not None:
            payload["agent_id"] = self.agent_id
        if self.ip is not None:
            payload["ip"] = self.ip
        if self.port is not None:
            payload["port"] = self.port
        if self.healthy is not None:
            payload["healthy"] = self.healthy
        if self.ephemeral is not None:
            payload["ephemeral"] = self.ephemeral
        if self.instance_count is not None:
            payload["instance_count"] = self.instance_count
        return payload


class NacosAgentRegistryAdapter:
    """Register AgentOS AgentCards as Nacos service discovery metadata."""

    def __init__(
        self,
        *,
        client: NacosRegistryClient,
        config: NacosRegistryConfig | None = None,
    ) -> None:
        self._client = client
        self._config = config or NacosRegistryConfig()

    def register(
        self,
        card: AgentCard,
        *,
        ip: str,
        port: int,
        healthy: bool = True,
        a2a_card_url: str | None = None,
        worker_service: str | None = None,
        health: Mapping[str, object] | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> NacosRegistryEvidence:
        """Register or update an AgentCard in Nacos discovery metadata."""

        service_name = self._config.service_for_agent(card.agent_id)
        nacos_metadata = agent_card_to_nacos_metadata(
            card,
            a2a_card_url=a2a_card_url,
            worker_service=worker_service,
            health=health,
            metadata=metadata,
        )
        self._client.register_instance(
            service_name=service_name,
            group_name=self._config.group_name,
            cluster_name=self._config.cluster_name,
            namespace_id=self._config.namespace_id,
            ip=ip,
            port=port,
            metadata=nacos_metadata,
            healthy=healthy,
            ephemeral=self._config.ephemeral,
        )
        return NacosRegistryEvidence(
            operation="register",
            service_name=service_name,
            group_name=self._config.group_name,
            cluster_name=self._config.cluster_name,
            namespace_id=self._config.namespace_id,
            agent_id=card.agent_id,
            ip=ip,
            port=port,
            healthy=healthy,
            ephemeral=self._config.ephemeral,
            metadata_keys=tuple(sorted(nacos_metadata)),
        )

    def unregister(
        self,
        agent_id: str,
        *,
        ip: str,
        port: int,
    ) -> NacosRegistryEvidence:
        """Deregister one AgentCard service instance from Nacos."""

        service_name = self._config.service_for_agent(agent_id)
        self._client.deregister_instance(
            service_name=service_name,
            group_name=self._config.group_name,
            cluster_name=self._config.cluster_name,
            namespace_id=self._config.namespace_id,
            ip=ip,
            port=port,
            ephemeral=self._config.ephemeral,
        )
        return NacosRegistryEvidence(
            operation="deregister",
            service_name=service_name,
            group_name=self._config.group_name,
            cluster_name=self._config.cluster_name,
            namespace_id=self._config.namespace_id,
            agent_id=agent_id,
            ip=ip,
            port=port,
            ephemeral=self._config.ephemeral,
        )


class NacosAgentCardResolver:
    """Resolve and discover AgentCards from healthy Nacos instances."""

    def __init__(
        self,
        *,
        client: NacosRegistryClient,
        config: NacosRegistryConfig | None = None,
    ) -> None:
        self._client = client
        self._config = config or NacosRegistryConfig()
        self.last_evidence: NacosRegistryEvidence | None = None

    def resolve(
        self,
        agent_id: str,
        *,
        session_id: str | None = None,
    ) -> AgentCard | None:
        """Resolve one healthy AgentCard by id."""

        service_name = self._config.service_for_agent(agent_id)
        cards = self._cards_for_service(service_name)
        self.last_evidence = NacosRegistryEvidence(
            operation="resolve",
            service_name=service_name,
            group_name=self._config.group_name,
            cluster_name=self._config.cluster_name,
            namespace_id=self._config.namespace_id,
            agent_id=agent_id,
            instance_count=len(cards),
        )
        for card in cards:
            if card.agent_id == agent_id:
                return card
        return None

    def discover(
        self,
        capabilities: Sequence[str],
        *,
        session_id: str | None = None,
    ) -> list[AgentCard]:
        """Discover healthy AgentCards matching all required capabilities."""

        service_name = self._config.discovery_service_name
        required = set(capabilities)
        cards = [
            card
            for card in self._cards_for_service(service_name)
            if required.issubset(set(card.capabilities))
        ]
        self.last_evidence = NacosRegistryEvidence(
            operation="discover",
            service_name=service_name,
            group_name=self._config.group_name,
            cluster_name=self._config.cluster_name,
            namespace_id=self._config.namespace_id,
            metadata_keys=tuple(sorted(required)),
            instance_count=len(cards),
        )
        return cards

    def select(
        self,
        capabilities: Sequence[str],
        *,
        session_id: str | None = None,
    ) -> AgentCard | None:
        """Select the first healthy AgentCard matching capabilities."""

        candidates = self.discover(capabilities, session_id=session_id)
        return candidates[0] if candidates else None

    def _cards_for_service(self, service_name: str) -> list[AgentCard]:
        instances = self._client.list_instances(
            service_name=service_name,
            group_name=self._config.group_name,
            cluster_name=self._config.cluster_name,
            namespace_id=self._config.namespace_id,
            healthy_only=self._config.healthy_only,
        )
        return [
            nacos_instance_to_agent_card(instance)
            for instance in instances
            if _instance_is_healthy(instance) or not self._config.healthy_only
        ]


def agent_card_to_nacos_metadata(
    card: AgentCard,
    *,
    a2a_card_url: str | None = None,
    worker_service: str | None = None,
    health: Mapping[str, object] | None = None,
    metadata: Mapping[str, object] | None = None,
) -> dict[str, str]:
    """Project an AgentCard into discovery-only Nacos metadata."""

    _validate_payload_keys("health", health or {})
    _validate_payload_keys("metadata", metadata or {})
    result = {
        f"{_PREFIX}agent_id": card.agent_id,
        f"{_PREFIX}name": card.name,
        f"{_PREFIX}description": card.description,
        f"{_PREFIX}capabilities": _json_dumps(list(card.capabilities)),
        f"{_PREFIX}version": card.version,
        f"{_PREFIX}endpoint": card.endpoint or "",
        f"{_PREFIX}status": card.status,
        f"{_PREFIX}lifecycle": card.lifecycle,
        f"{_PREFIX}max_concurrent_tasks": str(card.max_concurrent_tasks),
    }
    if a2a_card_url is not None:
        result[f"{_PREFIX}a2a_card_url"] = a2a_card_url
    if worker_service is not None:
        result[f"{_PREFIX}worker_service"] = worker_service
    if health:
        result[f"{_PREFIX}health"] = _json_dumps(dict(health))
    if metadata:
        result[f"{_PREFIX}metadata"] = _json_dumps(dict(metadata))
    return result


def nacos_instance_to_agent_card(instance: object) -> AgentCard:
    """Project one Nacos instance into an AgentOS AgentCard."""

    metadata = _instance_metadata(instance)
    capabilities = _load_json_list(metadata, f"{_PREFIX}capabilities")
    return AgentCard(
        agent_id=_metadata_value(metadata, f"{_PREFIX}agent_id"),
        name=_metadata_value(metadata, f"{_PREFIX}name"),
        description=_metadata_value(metadata, f"{_PREFIX}description"),
        capabilities=tuple(capabilities),
        version=metadata.get(f"{_PREFIX}version", "0.1.0"),
        endpoint=_metadata_value(
            metadata,
            f"{_PREFIX}endpoint",
            default=_endpoint_from_instance(instance),
        )
        or None,
        status=metadata.get(f"{_PREFIX}status", "idle"),  # type: ignore[arg-type]
        lifecycle=metadata.get(f"{_PREFIX}lifecycle", "persistent"),  # type: ignore[arg-type]
        max_concurrent_tasks=int(
            metadata.get(f"{_PREFIX}max_concurrent_tasks", "1"),
        ),
    )


def _instance_metadata(instance: object) -> Mapping[str, str]:
    metadata = _get(instance, "metadata", {})
    if not isinstance(metadata, Mapping):
        raise NacosRegistryError("instance metadata must be a mapping")
    return {str(key): str(value) for key, value in metadata.items()}


def _instance_is_healthy(instance: object) -> bool:
    healthy = _get(instance, "healthy", True)
    return bool(healthy)


def _endpoint_from_instance(instance: object) -> str:
    ip = _get(instance, "ip", "")
    port = _get(instance, "port", "")
    if ip == "" or port == "":
        return ""
    return f"http://{ip}:{port}"


def _metadata_value(
    metadata: Mapping[str, str],
    key: str,
    *,
    default: str | None = None,
) -> str:
    value = metadata.get(key, default)
    if value is None:
        raise NacosRegistryError(f"missing {key}")
    return value


def _load_json_list(metadata: Mapping[str, str], key: str) -> list[str]:
    raw = _metadata_value(metadata, key)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise NacosRegistryError(f"invalid {key}: {exc.msg}") from exc
    if not isinstance(parsed, list):
        raise NacosRegistryError(f"invalid {key}: expected list")
    return [str(item) for item in parsed]


def _json_dumps(payload: object) -> str:
    try:
        return json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise NacosRegistryError("metadata must be JSON-safe") from exc


def _validate_payload_keys(scope: str, payload: Mapping[str, object]) -> None:
    for key, value in payload.items():
        key_text = str(key)
        lowered = key_text.lower()
        key_parts = {
            part
            for part in lowered.replace("-", "_").replace(".", "_").split("_")
            if part
        }
        if lowered in _FORBIDDEN_METADATA_KEYS or (
            key_parts & _FORBIDDEN_METADATA_KEY_PARTS
        ):
            raise NacosRegistryError(
                f"{scope} contains non-discovery metadata key {key_text!r}",
            )
        if isinstance(value, Mapping):
            _validate_payload_keys(f"{scope}.{key_text}", value)


def _get(instance: object, name: str, default: object = None) -> object:
    if isinstance(instance, Mapping):
        return instance.get(name, default)
    return getattr(instance, name, default)


__all__ = [
    "NacosAgentCardResolver",
    "NacosAgentRegistryAdapter",
    "NacosRegistryClient",
    "NacosRegistryConfig",
    "NacosRegistryError",
    "NacosRegistryEvidence",
    "agent_card_to_nacos_metadata",
    "nacos_instance_to_agent_card",
]
