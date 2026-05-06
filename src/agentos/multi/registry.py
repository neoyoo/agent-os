from __future__ import annotations

from dataclasses import replace
from threading import RLock
from typing import Protocol

from agentos.multi.types import AgentCard, AgentStatus


class AgentRegistry(Protocol):
    """AgentCard 的声明和发现边界。"""

    def register(self, card: AgentCard) -> None:
        """注册一个 agent card。"""

    def unregister(self, agent_id: str) -> None:
        """注销一个 agent card。"""

    def resolve(self, agent_id: str) -> AgentCard | None:
        """按 agent id 返回 card。"""

    def discover(self, capabilities: tuple[str, ...]) -> list[AgentCard]:
        """按 capability 全量匹配 agent card。"""

    def update_status(self, agent_id: str, status: AgentStatus) -> None:
        """更新 agent status。"""

    @property
    def all_agents(self) -> list[AgentCard]:
        """返回所有已注册 agent card。"""


class InMemoryRegistry:
    """单进程内存 AgentRegistry。"""

    def __init__(self) -> None:
        """创建空 registry。"""

        self._cards: dict[str, AgentCard] = {}
        self._lock = RLock()

    def register(self, card: AgentCard) -> None:
        """注册一个 agent card，agent_id 必须唯一。"""

        with self._lock:
            if card.agent_id in self._cards:
                raise ValueError(f"agent already registered: {card.agent_id}")
            self._cards[card.agent_id] = card

    def unregister(self, agent_id: str) -> None:
        """注销一个 agent card；不存在时保持幂等。"""

        with self._lock:
            self._cards.pop(agent_id, None)

    def resolve(self, agent_id: str) -> AgentCard | None:
        """按 agent id 返回 card。"""

        with self._lock:
            return self._cards.get(agent_id)

    def discover(self, capabilities: tuple[str, ...]) -> list[AgentCard]:
        """返回包含全部请求 capabilities 的 cards。"""

        required = set(capabilities)
        with self._lock:
            return [
                card
                for card in self._cards.values()
                if required.issubset(set(card.capabilities))
            ]

    def update_status(self, agent_id: str, status: AgentStatus) -> None:
        """用 replace-copy 更新 frozen card 的 status。"""

        with self._lock:
            card = self._cards.get(agent_id)
            if card is None:
                raise KeyError(agent_id)
            self._cards[agent_id] = replace(card, status=status)

    @property
    def all_agents(self) -> list[AgentCard]:
        """返回所有已注册 cards。"""

        with self._lock:
            return list(self._cards.values())
