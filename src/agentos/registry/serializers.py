from __future__ import annotations

from agentos.multi import AgentCard
from agentos.registry.types import AgentRegistryRecord, SessionAffinity


def card_to_dict(card: AgentCard) -> dict[str, object]:
    """序列化 AgentCard。"""

    return {
        "agent_id": card.agent_id,
        "name": card.name,
        "description": card.description,
        "capabilities": list(card.capabilities),
        "version": card.version,
        "endpoint": card.endpoint,
        "status": card.status,
        "lifecycle": card.lifecycle,
        "max_concurrent_tasks": card.max_concurrent_tasks,
    }


def card_from_dict(data: dict[str, object]) -> AgentCard:
    """反序列化 AgentCard。"""

    return AgentCard(
        agent_id=str(data["agent_id"]),
        name=str(data["name"]),
        description=str(data["description"]),
        capabilities=tuple(str(item) for item in data.get("capabilities", [])),
        version=str(data.get("version", "0.1.0")),
        endpoint=(
            None
            if data.get("endpoint") is None
            else str(data.get("endpoint"))
        ),
        status=data.get("status", "idle"),  # type: ignore[arg-type]
        lifecycle=data.get("lifecycle", "persistent"),  # type: ignore[arg-type]
        max_concurrent_tasks=int(data.get("max_concurrent_tasks", 1)),
    )


def record_to_dict(record: AgentRegistryRecord) -> dict[str, object]:
    """序列化 AgentRegistryRecord。"""

    return {
        "card": card_to_dict(record.card),
        "heartbeat_at": record.heartbeat_at,
    }


def record_from_dict(data: dict[str, object]) -> AgentRegistryRecord:
    """反序列化 AgentRegistryRecord。"""

    return AgentRegistryRecord(
        card=card_from_dict(data["card"]),  # type: ignore[arg-type]
        heartbeat_at=float(data["heartbeat_at"]),
    )


def affinity_to_dict(affinity: SessionAffinity) -> dict[str, object]:
    """序列化 SessionAffinity。"""

    return {
        "session_id": affinity.session_id,
        "agent_id": affinity.agent_id,
        "expires_at": affinity.expires_at,
    }


def affinity_from_dict(data: dict[str, object]) -> SessionAffinity:
    """反序列化 SessionAffinity。"""

    return SessionAffinity(
        session_id=str(data["session_id"]),
        agent_id=str(data["agent_id"]),
        expires_at=float(data["expires_at"]),
    )


__all__ = [
    "affinity_from_dict",
    "affinity_to_dict",
    "card_from_dict",
    "card_to_dict",
    "record_from_dict",
    "record_to_dict",
]
