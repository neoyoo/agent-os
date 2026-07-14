from __future__ import annotations

from collections.abc import Iterator, Mapping
from threading import RLock

from agentos.multi.message_queue import AgentMessageQueue
from agentos.multi.registry import AgentRegistry
from agentos.multi.types import AgentCard
from agentos.runtime.agent import Agent
from agentos.runtime.run import AgentResult, AgentWaiting
from agentos.sync.agent import SyncAgent


class SyncAgentRegistry(Mapping[str, SyncAgent]):
    """管理本地协调器使用的 SyncAgent 及其所有权。"""

    def __init__(self) -> None:
        self._agents: dict[str, SyncAgent] = {}
        self._owned: set[str] = set()
        self._lock = RLock()

    def __getitem__(self, agent_id: str) -> SyncAgent:
        with self._lock:
            return self._agents[agent_id]

    def __iter__(self) -> Iterator[str]:
        with self._lock:
            return iter(tuple(self._agents))

    def __len__(self) -> int:
        with self._lock:
            return len(self._agents)

    def attach_borrowed(self, agent_id: str, agent: SyncAgent) -> None:
        """附着由应用持有生命周期的 SyncAgent。"""

        self._attach(agent_id, agent, owned=False)

    def attach_owned(self, agent_id: str, agent: Agent) -> SyncAgent:
        """包装并附着由 registry 持有生命周期的 Agent。"""

        sync_agent = SyncAgent(agent)
        try:
            self._attach(agent_id, sync_agent, owned=True)
        except BaseException as attach_error:
            try:
                sync_agent.close()
            except BaseException as cleanup_error:
                attach_error.add_note(
                    f"close owned agent rollback failed: {cleanup_error!r}",
                )
            raise
        return sync_agent

    def detach(self, agent_id: str) -> SyncAgent | None:
        """移除 agent，并只关闭 registry 自己拥有的实例。"""

        with self._lock:
            agent = self._agents.pop(agent_id, None)
            owned = agent_id in self._owned
            self._owned.discard(agent_id)
        if agent is not None and owned:
            agent.close()
        return agent

    def interrupt(self, agent_id: str) -> bool:
        """中断指定 SyncAgent 底层 Agent 的当前执行租约。"""

        with self._lock:
            agent = self._agents.get(agent_id)
        return False if agent is None else agent.agent.interrupt()

    def close(self) -> None:
        """移除全部实例，并关闭所有 owned SyncAgent。"""

        with self._lock:
            agent_ids = tuple(self._agents)
        for agent_id in agent_ids:
            self.detach(agent_id)

    def _attach(self, agent_id: str, agent: SyncAgent, *, owned: bool) -> None:
        with self._lock:
            if agent_id in self._agents:
                raise ValueError(f"agent already attached: {agent_id}")
            self._agents[agent_id] = agent
            if owned:
                self._owned.add(agent_id)


class CoordinatorSyncAgentRegistry(SyncAgentRegistry):
    """管理 Coordinator 的 card、inbox 与 SyncAgent 绑定。"""

    def __init__(
        self,
        *,
        registry: AgentRegistry,
        inbox: AgentMessageQueue,
    ) -> None:
        super().__init__()
        self._registry = registry
        self._inbox = inbox

    def attach_borrowed_card(self, card: AgentCard, agent: SyncAgent) -> None:
        """原子附着应用拥有的 SyncAgent 与本地发现边界。"""

        self.attach_borrowed(card.agent_id, agent)
        self._attach_card_resources(card)

    def attach_owned_card(self, card: AgentCard, agent: Agent) -> SyncAgent:
        """包装并附着 Coordinator 拥有的 ephemeral Agent。"""

        sync_agent = self.attach_owned(card.agent_id, agent)
        self._attach_card_resources(card)
        return sync_agent

    def _attach_card_resources(self, card: AgentCard) -> None:
        card_registered = False
        inbox_creation_started = False
        try:
            self._registry.register(card)
            card_registered = True
            inbox_creation_started = True
            self._inbox.create_inbox(card.agent_id)
        except BaseException as attach_error:
            self._rollback_card_attach(
                card.agent_id,
                attach_error=attach_error,
                remove_inbox=inbox_creation_started,
                unregister_card=card_registered,
            )
            raise

    def _rollback_card_attach(
        self,
        agent_id: str,
        *,
        attach_error: BaseException,
        remove_inbox: bool,
        unregister_card: bool,
    ) -> None:
        if remove_inbox:
            try:
                self._inbox.remove_inbox(agent_id)
            except BaseException as cleanup_error:
                attach_error.add_note(
                    f"remove inbox rollback failed: {cleanup_error!r}",
                )
        if unregister_card:
            try:
                self._registry.unregister(agent_id)
            except BaseException as cleanup_error:
                attach_error.add_note(
                    f"unregister card rollback failed: {cleanup_error!r}",
                )
        try:
            super().detach(agent_id)
        except BaseException as cleanup_error:
            attach_error.add_note(
                f"detach agent rollback failed: {cleanup_error!r}",
            )

    def detach_card(self, agent_id: str) -> SyncAgent | None:
        """移除完整本地绑定，并遵守 owned/borrowed 生命周期。"""

        agent = super().detach(agent_id)
        self._registry.unregister(agent_id)
        self._inbox.remove_inbox(agent_id)
        return agent

    @staticmethod
    def completed_content(outcome: AgentResult | AgentWaiting) -> str:
        """把本地 Agent 完成结果收窄为 task summary。"""

        if isinstance(outcome, AgentResult):
            return outcome.content
        raise RuntimeError("local multi-agent task entered waiting state")
