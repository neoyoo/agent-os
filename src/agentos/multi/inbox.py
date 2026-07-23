from __future__ import annotations

from queue import Empty, Queue
from threading import Event, RLock
from typing import Callable

from agentos.events import AgentInboxBackpressureEvent, EventBus
from agentos.multi.message_queue import QueueDelivery
from agentos.multi.types import AgentEnvelope, AgentEnvelopeType


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
        self._queues: dict[str, Queue[QueueDelivery]] = {}
        self._events: dict[str, Event] = {}
        self._acked_delivery_ids: set[tuple[str, str]] = set()
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

    def send(self, envelope: AgentEnvelope) -> str:
        """向目标 inbox 发送 envelope，缺失或满载时 fail-closed。"""

        delivery_id = envelope.envelope_id
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
            queue.put(QueueDelivery(delivery_id=delivery_id, envelope=envelope))
            self._events[envelope.to_agent_id].set()
        return delivery_id

    def collect(
        self,
        agent_id: str,
        *,
        envelope_types: tuple[AgentEnvelopeType, ...] | None = None,
    ) -> list[QueueDelivery]:
        """Drain 并返回当前 inbox 中所有 deliveries。"""

        with self._lock:
            queue = self._queue_for(agent_id)
            deliveries: list[QueueDelivery] = []
            retained: list[QueueDelivery] = []
            allowed_types = None if envelope_types is None else set(envelope_types)
            while True:
                try:
                    delivery = queue.get_nowait()
                except Empty:
                    break
                if (
                    allowed_types is None
                    or delivery.envelope.type in allowed_types
                ):
                    deliveries.append(delivery)
                else:
                    retained.append(delivery)
            for delivery in retained:
                queue.put(delivery)
            if queue.empty():
                self._events[agent_id].clear()
            else:
                self._events[agent_id].set()
            return deliveries

    def collect_matching(
        self,
        agent_id: str,
        *,
        envelope_types: tuple[AgentEnvelopeType, ...] | None = None,
        predicate: Callable[[QueueDelivery], bool] | None = None,
    ) -> list[QueueDelivery]:
        """Drain deliveries matching type and predicate, retaining the rest."""

        with self._lock:
            queue = self._queue_for(agent_id)
            deliveries: list[QueueDelivery] = []
            retained: list[QueueDelivery] = []
            allowed_types = None if envelope_types is None else set(envelope_types)
            while True:
                try:
                    delivery = queue.get_nowait()
                except Empty:
                    break
                if (
                    allowed_types is None
                    or delivery.envelope.type in allowed_types
                ):
                    if predicate is None or predicate(delivery):
                        deliveries.append(delivery)
                    else:
                        retained.append(delivery)
                else:
                    retained.append(delivery)
            for delivery in retained:
                queue.put(delivery)
            if queue.empty():
                self._events[agent_id].clear()
            else:
                self._events[agent_id].set()
            return deliveries

    def collect_envelopes(
        self,
        agent_id: str,
        *,
        envelope_types: tuple[AgentEnvelopeType, ...] | None = None,
    ) -> list[AgentEnvelope]:
        """兼容旧调用方：只返回 envelopes。"""

        return [
            delivery.envelope
            for delivery in self.collect(
                agent_id,
                envelope_types=envelope_types,
            )
        ]

    def ack(self, agent_id: str, delivery_id: str) -> bool:
        """in-memory delivery drain 后即可视为已处理；ack 只做幂等记录。"""

        with self._lock:
            self._queue_for(agent_id)
            key = (agent_id, delivery_id)
            if key in self._acked_delivery_ids:
                return False
            self._acked_delivery_ids.add(key)
            return True

    def requeue(self, agent_id: str, delivery: QueueDelivery) -> None:
        """Put an unhandled in-memory delivery back for a later runner attempt."""

        with self._lock:
            queue = self._queue_for(agent_id)
            queue.put(delivery)
            self._events[agent_id].set()

    def wait(self, agent_id: str, timeout: float | None = None) -> bool:
        """阻塞等待 inbox 中出现消息。"""

        with self._lock:
            event = self._event_for(agent_id)
        return event.wait(timeout)

    def has_pending(self, agent_id: str) -> bool:
        """判断 inbox 中是否有待处理消息。"""

        with self._lock:
            return not self._queue_for(agent_id).empty()

    def _queue_for(self, agent_id: str) -> Queue[QueueDelivery]:
        try:
            return self._queues[agent_id]
        except KeyError as error:
            raise AgentInboxMissingError(f"missing inbox: {agent_id}") from error

    def _event_for(self, agent_id: str) -> Event:
        try:
            return self._events[agent_id]
        except KeyError as error:
            raise AgentInboxMissingError(f"missing inbox: {agent_id}") from error
