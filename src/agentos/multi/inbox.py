from __future__ import annotations

from queue import Empty, Queue
from threading import Event, RLock

from agentos.events import AgentInboxBackpressureEvent, EventBus
from agentos.multi.types import AgentEnvelope


class AgentInboxError(RuntimeError):
    """AgentInbox 基础错误。"""


class AgentInboxMissingError(AgentInboxError):
    """目标 agent inbox 不存在。"""


class AgentInboxFullError(AgentInboxError):
    """目标 agent inbox 达到容量上限。"""


class AgentInbox:
    """本地 agent 点对点执行消息队列。"""

    def __init__(
        self,
        max_pending_envelopes: int = 100,
        event_bus: EventBus | None = None,
    ) -> None:
        """创建空 inbox 集合。"""

        if max_pending_envelopes < 1:
            raise ValueError("max_pending_envelopes must be >= 1")
        self.max_pending_envelopes = max_pending_envelopes
        self.event_bus = event_bus
        self._queues: dict[str, Queue[AgentEnvelope]] = {}
        self._events: dict[str, Event] = {}
        self._lock = RLock()

    def create_inbox(self, agent_id: str) -> None:
        """为 agent 创建 inbox；重复创建保持幂等。"""

        with self._lock:
            self._queues.setdefault(agent_id, Queue())
            self._events.setdefault(agent_id, Event())

    def remove_inbox(self, agent_id: str) -> None:
        """移除 agent inbox。"""

        with self._lock:
            self._queues.pop(agent_id, None)
            self._events.pop(agent_id, None)

    def send(self, envelope: AgentEnvelope) -> None:
        """向目标 inbox 发送 envelope，缺失或满载时 fail-closed。"""

        with self._lock:
            queue = self._queue_for(envelope.to_agent_id)
            if queue.qsize() >= self.max_pending_envelopes:
                if self.event_bus is not None:
                    self.event_bus.emit(
                        AgentInboxBackpressureEvent(
                            agent_id=envelope.to_agent_id,
                            pending_count=queue.qsize(),
                            max_pending_envelopes=self.max_pending_envelopes,
                        ),
                    )
                raise AgentInboxFullError(
                    f"inbox is full: {envelope.to_agent_id}",
                )
            queue.put(envelope)
            self._events[envelope.to_agent_id].set()

    def collect(self, agent_id: str) -> list[AgentEnvelope]:
        """Drain 并返回当前 inbox 中所有 envelopes。"""

        with self._lock:
            queue = self._queue_for(agent_id)
            envelopes: list[AgentEnvelope] = []
            while True:
                try:
                    envelopes.append(queue.get_nowait())
                except Empty:
                    break
            if queue.empty():
                self._events[agent_id].clear()
            return envelopes

    def wait(self, agent_id: str, timeout: float | None = None) -> bool:
        """阻塞等待 inbox 中出现消息。"""

        with self._lock:
            event = self._event_for(agent_id)
        return event.wait(timeout)

    def has_pending(self, agent_id: str) -> bool:
        """判断 inbox 中是否有待处理消息。"""

        with self._lock:
            return not self._queue_for(agent_id).empty()

    def _queue_for(self, agent_id: str) -> Queue[AgentEnvelope]:
        try:
            return self._queues[agent_id]
        except KeyError as error:
            raise AgentInboxMissingError(f"missing inbox: {agent_id}") from error

    def _event_for(self, agent_id: str) -> Event:
        try:
            return self._events[agent_id]
        except KeyError as error:
            raise AgentInboxMissingError(f"missing inbox: {agent_id}") from error
