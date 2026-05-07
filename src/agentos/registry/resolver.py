from __future__ import annotations

from threading import Lock
from typing import Protocol, Sequence

from agentos.multi import AgentCard
from agentos.registry.persistent import PersistentAgentRegistry


class AgentResolver(Protocol):
    """远程或静态 AgentCard 解析边界。"""

    def resolve(
        self,
        agent_id: str,
        *,
        session_id: str | None = None,
    ) -> AgentCard | None:
        """按 agent id 解析 card。"""

    def discover(
        self,
        capabilities: Sequence[str],
        *,
        session_id: str | None = None,
    ) -> list[AgentCard]:
        """按 capability 发现候选 cards。"""

    def select(
        self,
        capabilities: Sequence[str],
        *,
        session_id: str | None = None,
    ) -> AgentCard | None:
        """按 capability 选择一个可调用 card。"""


class StaticResolver:
    """静态 AgentCard resolver，适合本地配置和测试。"""

    def __init__(self, cards: Sequence[AgentCard]) -> None:
        self._cards = {card.agent_id: card for card in cards}

    def resolve(
        self,
        agent_id: str,
        *,
        session_id: str | None = None,
    ) -> AgentCard | None:
        """按 agent id 解析非 offline card。"""

        card = self._cards.get(agent_id)
        if card is None or card.status == "offline":
            return None
        return card

    def discover(
        self,
        capabilities: Sequence[str],
        *,
        session_id: str | None = None,
    ) -> list[AgentCard]:
        """按 capability 全量匹配非 offline cards。"""

        required = set(capabilities)
        return [
            card
            for card in self._cards.values()
            if card.status != "offline"
            and required.issubset(set(card.capabilities))
        ]

    def select(
        self,
        capabilities: Sequence[str],
        *,
        session_id: str | None = None,
    ) -> AgentCard | None:
        """选择第一个匹配 card。"""

        candidates = self.discover(capabilities, session_id=session_id)
        return candidates[0] if candidates else None


class ServiceResolver:
    """基于 PersistentAgentRegistry 的健康路由和 session affinity resolver。"""

    def __init__(self, registry: PersistentAgentRegistry) -> None:
        self._registry = registry
        self._next_index_by_capabilities: dict[tuple[str, ...], int] = {}
        self._lock = Lock()

    def resolve(
        self,
        agent_id: str,
        *,
        session_id: str | None = None,
    ) -> AgentCard | None:
        """按 agent id 解析健康 card。"""

        return self._registry.resolve(agent_id)

    def discover(
        self,
        capabilities: Sequence[str],
        *,
        session_id: str | None = None,
    ) -> list[AgentCard]:
        """按 capability 发现健康 candidates。"""

        return self._registry.discover(capabilities)

    def select(
        self,
        capabilities: Sequence[str],
        *,
        session_id: str | None = None,
    ) -> AgentCard | None:
        """优先使用 session affinity，否则选择第一个健康 candidate 并绑定。"""

        required = set(capabilities)
        if session_id is not None:
            bound_agent_id = self._registry.resolve_session(session_id)
            if bound_agent_id is not None:
                bound_card = self._registry.resolve(bound_agent_id)
                if bound_card is not None and required.issubset(
                    set(bound_card.capabilities),
                ):
                    return bound_card

        candidates = self._registry.discover(capabilities)
        if not candidates:
            return None

        key = tuple(capabilities)
        with self._lock:
            next_index = self._next_index_by_capabilities.get(key, 0)
            selected = candidates[next_index % len(candidates)]
            self._next_index_by_capabilities[key] = next_index + 1
        if session_id is not None:
            self._registry.bind_session(session_id, selected.agent_id)
        return selected
