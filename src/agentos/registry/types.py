from __future__ import annotations

from dataclasses import dataclass

from agentos.multi import AgentCard


@dataclass(frozen=True, slots=True)
class AgentRegistryRecord:
    """持久 registry 中的一条 agent 注册记录。"""

    card: AgentCard
    heartbeat_at: float


@dataclass(frozen=True, slots=True)
class SessionAffinity:
    """session 到 agent 的亲和性绑定。"""

    session_id: str
    agent_id: str
    expires_at: float
