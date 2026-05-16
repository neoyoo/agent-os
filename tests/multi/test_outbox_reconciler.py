from __future__ import annotations

from dataclasses import dataclass

from agentos.multi import AgentEnvelope, TaskRecord, TaskRequest, TaskResult
from agentos.multi.reconciler import OutboxEntry, OutboxReconciler


@dataclass
class FakeOutboxStore:
    entries: list[OutboxEntry]
    delivered: list[int]

    def pending_outbox(self, *, limit: int) -> list[OutboxEntry]:
        return self.entries[:limit]

    def mark_outbox_delivered(self, outbox_id: int, *, delivered_at: float) -> bool:
        self.delivered.append(outbox_id)
        return True


class FakeQueue:
    def __init__(self) -> None:
        self.sent: list[AgentEnvelope] = []

    def send(self, envelope: AgentEnvelope) -> str:
        self.sent.append(envelope)
        return "delivery_1"


def test_outbox_reconciler_sends_result_and_marks_delivered() -> None:
    record = TaskRecord(
        task_id="task_1",
        mode="dispatch",
        parent_agent_id="parent",
        target_agent_id="expert",
        request=TaskRequest(task_id="task_1", instruction="work"),
        status="completed",
        created_at=1,
        deadline_at=10,
        result=TaskResult(task_id="task_1", status="completed", summary="done"),
    )
    store = FakeOutboxStore(
        entries=[OutboxEntry(outbox_id=1, event_type="result_ready", record=record)],
        delivered=[],
    )
    queue = FakeQueue()

    delivered = OutboxReconciler(task_store=store, message_queue=queue).run_once(now=2)

    assert delivered == 1
    assert store.delivered == [1]
    assert queue.sent[0].to_agent_id == "parent"
    assert queue.sent[0].payload == record.result
