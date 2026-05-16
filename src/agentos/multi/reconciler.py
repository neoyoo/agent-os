from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4

from agentos.multi.message_queue import AgentMessageQueue
from agentos.multi.types import AgentEnvelope, TaskRecord


@dataclass(frozen=True, slots=True)
class OutboxEntry:
    """TaskStore outbox 中的一条待投递事件。"""

    outbox_id: int
    event_type: str
    record: TaskRecord


class OutboxTaskStore(Protocol):
    """OutboxReconciler 依赖的 TaskStore outbox 边界。"""

    def pending_outbox(self, *, limit: int) -> list[OutboxEntry]:
        """返回未投递 outbox rows。"""

    def mark_outbox_delivered(self, outbox_id: int, *, delivered_at: float) -> bool:
        """标记 outbox row 已投递。"""


@dataclass(slots=True)
class OutboxReconciler:
    """补发 TaskStore outbox 中未投递的 result notification。"""

    task_store: OutboxTaskStore
    message_queue: AgentMessageQueue
    batch_size: int = 100

    def run_once(self, *, now: float) -> int:
        """扫描一批 outbox 并投递成功后标记 delivered。"""

        delivered = 0
        for entry in self.task_store.pending_outbox(limit=self.batch_size):
            if entry.event_type != "result_ready":
                continue
            record = entry.record
            if record.result is None:
                continue
            self.message_queue.send(
                AgentEnvelope(
                    envelope_id=f"env_{uuid4().hex}",
                    from_agent_id=record.target_agent_id,
                    to_agent_id=record.parent_agent_id,
                    type="task_result",
                    payload=record.result,
                    created_at=now,
                    correlation_id=record.task_id,
                ),
            )
            if self.task_store.mark_outbox_delivered(
                entry.outbox_id,
                delivered_at=now,
            ):
                delivered += 1
        return delivered
