from __future__ import annotations

from dataclasses import dataclass
import logging

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
    def __init__(self, *, fail_task_ids: set[str] | None = None) -> None:
        self.sent: list[AgentEnvelope] = []
        self.fail_task_ids = fail_task_ids or set()

    def send(self, envelope: AgentEnvelope) -> str:
        if envelope.correlation_id in self.fail_task_ids:
            raise RuntimeError(f"failed to send {envelope.correlation_id}")
        self.sent.append(envelope)
        return "delivery_1"


def record(task_id: str) -> TaskRecord:
    return TaskRecord(
        task_id=task_id,
        mode="dispatch",
        parent_agent_id="parent",
        target_agent_id="expert",
        request=TaskRequest(task_id=task_id, instruction="work"),
        status="completed",
        created_at=1,
        deadline_at=10,
        result=TaskResult(task_id=task_id, status="completed", summary="done"),
    )


def test_outbox_reconciler_sends_result_and_marks_delivered() -> None:
    task = record("task_1")
    store = FakeOutboxStore(
        entries=[OutboxEntry(outbox_id=1, event_type="result_ready", record=task)],
        delivered=[],
    )
    queue = FakeQueue()

    delivered = OutboxReconciler(task_store=store, message_queue=queue).run_once(now=2)

    assert delivered == 1
    assert store.delivered == [1]
    assert queue.sent[0].to_agent_id == "parent"
    assert queue.sent[0].payload == task.result


def test_outbox_reconciler_logs_entry_failures_and_continues(caplog) -> None:
    store = FakeOutboxStore(
        entries=[
            OutboxEntry(outbox_id=1, event_type="result_ready", record=record("task_1")),
            OutboxEntry(outbox_id=2, event_type="result_ready", record=record("task_2")),
        ],
        delivered=[],
    )
    queue = FakeQueue(fail_task_ids={"task_1"})

    with caplog.at_level(logging.WARNING):
        delivered = OutboxReconciler(
            task_store=store,
            message_queue=queue,
        ).run_once(now=2)

    assert delivered == 1
    assert store.delivered == [2]
    assert [envelope.correlation_id for envelope in queue.sent] == ["task_2"]
    assert "outbox_id=1" in caplog.text
