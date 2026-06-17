from __future__ import annotations

from collections.abc import Callable
from threading import RLock
from typing import Protocol

from agentos.runtime import Agent


class AgentSessionProvider(Protocol):
    """Channel session 到 Agent 实例的解析边界。"""

    def get_agent(self, session_id: str) -> Agent:
        """按 session id 返回 agent；未知 session 可自动创建。"""

    def release_agent(self, session_id: str, agent: Agent) -> None:
        """标记本轮 channel 调用结束。"""


class AsyncAgentSessionProvider(Protocol):
    """Async channel session provider extension."""

    async def async_get_agent(self, session_id: str) -> Agent:
        """Return an agent for a session without blocking the event loop."""

    async def async_release_agent(self, session_id: str, agent: Agent) -> None:
        """Release an agent after an async channel turn."""


class AbandonableAgentSessionProvider(Protocol):
    """Optional extension for releasing a session without saving turn state."""

    def abandon_agent(self, session_id: str, agent: Agent) -> None:
        """Discard an active session lease without persisting the agent."""


class AsyncAbandonableAgentSessionProvider(Protocol):
    """Async extension for releasing a session without saving turn state."""

    async def async_abandon_agent(self, session_id: str, agent: Agent) -> None:
        """Discard an active async session without persisting the agent."""


class InMemoryAgentSessionProvider:
    """单进程内存 session provider，按 session_id 缓存 Agent。"""

    def __init__(self, agent_factory: Callable[[str], Agent]) -> None:
        """创建 provider。"""

        self._agent_factory = agent_factory
        self._agents: dict[str, Agent] = {}
        self._lock = RLock()

    def get_agent(self, session_id: str) -> Agent:
        """按 session id 返回 cached agent，未知 session 自动创建。"""

        with self._lock:
            agent = self._agents.get(session_id)
            if agent is None:
                agent = self._agent_factory(session_id)
                self._agents[session_id] = agent
            return agent

    def release_agent(self, session_id: str, agent: Agent) -> None:
        """本轮 channel 调用结束；默认不销毁 agent。"""
