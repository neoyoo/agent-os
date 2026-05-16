# Distributed Multi-Agent Messaging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first production-ready distributed multi-agent messaging foundation: task truth in `TaskStore`, delivery in `AgentMessageQueue`, in-memory compatibility preserved, and Postgres/Redis adapters added behind protocols.

**Architecture:** Keep Postgres-backed `TaskStore` as the task/result truth source and Redis Streams-backed `AgentMessageQueue` as delivery/notification only. Keep current `TaskTable` and `AgentInbox` as in-memory adapters, then move `AgentCoordinator` to protocol dependencies without changing current local behavior.

**Tech Stack:** Python 3.11 dataclasses/protocols, standard-library JSON, existing fake DB/client test style, optional `psycopg` and `redis` extras, pytest.

---

## Scope Contract

1. Phase/spec: follows the distributed follow-up to Phase 8 multi-agent coordination, grounded in `docs/todo-distributed-multi-agent-messaging.md`.
2. Acceptance items in scope: protocol boundaries, in-memory adapters, task metadata, Postgres task store, Redis message queue, coordinator dependency inversion, tests and migrations.
3. Completed by this plan: implementation sequence and TDD checkpoints for the distributed messaging foundation.
4. Deferred: Nacos, full cross-organization A2A protocol expansion, async QueryLoop redesign, actual Redis/Postgres integration tests against live services.
5. Design rule to preserve: `EventBus` remains observation-only; default prompt must not expose runtime metadata; `TaskStore` is truth source and message queue is delivery-only.

Design references used:

- `AGENTS.md`
- `docs/design/sdk-architecture.md`
- `docs/design/llm-context-only-example.md`
- `docs/todo-distributed-multi-agent-messaging.md`
- `docs/superpowers/specs/2026-05-05-phase-8-multi-agent-coordination-design.md`
- `ai-knowledge/wiki/multi-agent.md`
- `ai-knowledge/wiki/agent-registry-discovery.md`
- `ai-knowledge/wiki/channel-remote.md`

## File Structure

- Modify `src/agentos/multi/types.py`: add distributed task metadata with backwards-compatible defaults.
- Create `src/agentos/multi/serializers.py`: JSON-safe serialization for `TaskRequest`, `TaskResult`, `TaskRecord`, and `AgentEnvelope`.
- Create `src/agentos/multi/task_store.py`: `TaskStore` Protocol and `TaskClaim` dataclass.
- Modify `src/agentos/multi/tasks.py`: make `TaskTable` satisfy `TaskStore` as the in-memory adapter.
- Create `src/agentos/multi/message_queue.py`: `AgentMessageQueue` Protocol and `QueueEnvelope` dataclass.
- Modify `src/agentos/multi/inbox.py`: make `AgentInbox` satisfy `AgentMessageQueue` as the in-memory adapter.
- Create `src/agentos/multi/postgres_tasks.py`: `PostgresTaskStore`.
- Create `src/agentos/multi/redis_queue.py`: `RedisAgentMessageQueue`.
- Modify `src/agentos/multi/coordinator.py`: accept protocol boundaries and preserve existing constructor compatibility.
- Modify `src/agentos/multi/__init__.py`: export new protocols/adapters/serializers.
- Add `docs/migrations/2026-05-16-postgres-multi-agent-tasks.sql`: Postgres schema.
- Add tests under `tests/multi/`: protocol contracts, serializers, Postgres store fake, Redis queue fake, coordinator compatibility.

---

### Task 1: Distributed Task Metadata And Serializers

**Files:**
- Modify: `src/agentos/multi/types.py`
- Create: `src/agentos/multi/serializers.py`
- Test: `tests/multi/test_task_serializers.py`

- [ ] **Step 1: Write failing serializer and metadata tests**

Add `tests/multi/test_task_serializers.py`:

```python
from agentos.multi import AgentEnvelope, TaskRecord, TaskRequest, TaskResult
from agentos.multi.serializers import (
    envelope_from_dict,
    envelope_to_dict,
    task_record_from_dict,
    task_record_to_dict,
)


def test_task_record_round_trips_distributed_metadata() -> None:
    record = TaskRecord(
        task_id="task_1",
        mode="dispatch",
        parent_agent_id="parent",
        target_agent_id="worker-capability",
        request=TaskRequest(task_id="task_1", instruction="Do work"),
        status="running",
        created_at=1.0,
        deadline_at=30.0,
        worker_id="worker-instance-1",
        lease_expires_at=20.0,
        attempt=2,
        updated_at=3.0,
        version=4,
        cancel_requested_at=5.0,
        result_notified_at=6.0,
    )

    assert task_record_from_dict(task_record_to_dict(record)) == record


def test_envelope_round_trips_task_request_and_result_payloads() -> None:
    request_envelope = AgentEnvelope(
        envelope_id="env_req",
        from_agent_id="parent",
        to_agent_id="worker",
        type="task_request",
        payload=TaskRequest(task_id="task_1", instruction="Do work"),
        created_at=1.0,
        correlation_id="task_1",
    )
    result_envelope = AgentEnvelope(
        envelope_id="env_res",
        from_agent_id="worker",
        to_agent_id="parent",
        type="task_result",
        payload=TaskResult(task_id="task_1", status="completed", summary="done"),
        created_at=2.0,
        correlation_id="task_1",
    )

    assert envelope_from_dict(envelope_to_dict(request_envelope)) == request_envelope
    assert envelope_from_dict(envelope_to_dict(result_envelope)) == result_envelope
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest tests/multi/test_task_serializers.py -q
```

Expected: FAIL because `agentos.multi.serializers` does not exist and `TaskRecord` has no distributed metadata fields.

- [ ] **Step 3: Add backwards-compatible metadata fields**

Modify `TaskRecord` in `src/agentos/multi/types.py`:

```python
@dataclass(frozen=True, slots=True)
class TaskRecord:
    """TaskStore 中保存的任务状态事实。"""

    task_id: str
    mode: CoordinationMode
    parent_agent_id: str
    target_agent_id: str
    request: TaskRequest
    status: TaskStatus
    created_at: float
    deadline_at: float
    result: TaskResult | None = None
    late_result: TaskResult | None = None
    completed_at: float | None = None
    consumed_at: float | None = None
    worker_id: str | None = None
    lease_expires_at: float | None = None
    attempt: int = 0
    updated_at: float | None = None
    version: int = 0
    cancel_requested_at: float | None = None
    result_notified_at: float | None = None
```

- [ ] **Step 4: Implement serializers**

Create `src/agentos/multi/serializers.py`:

```python
from __future__ import annotations

from typing import Any

from agentos.multi.types import (
    AgentEnvelope,
    TaskRecord,
    TaskRequest,
    TaskResult,
)


JsonDict = dict[str, Any]


def task_request_to_dict(request: TaskRequest) -> JsonDict:
    """序列化 TaskRequest。"""

    return {
        "task_id": request.task_id,
        "instruction": request.instruction,
        "allowed_tool_names": list(request.allowed_tool_names),
        "timeout_seconds": request.timeout_seconds,
        "trace_context": request.trace_context,
    }


def task_request_from_dict(data: JsonDict) -> TaskRequest:
    """反序列化 TaskRequest。"""

    trace_context = data.get("trace_context")
    return TaskRequest(
        task_id=str(data["task_id"]),
        instruction=str(data["instruction"]),
        allowed_tool_names=tuple(
            str(name) for name in data.get("allowed_tool_names", [])
        ),
        timeout_seconds=float(data.get("timeout_seconds", 300)),
        trace_context=(
            None if trace_context is None else {str(k): str(v) for k, v in dict(trace_context).items()}
        ),
    )


def task_result_to_dict(result: TaskResult) -> JsonDict:
    """序列化 TaskResult。"""

    return {
        "task_id": result.task_id,
        "status": result.status,
        "summary": result.summary,
        "artifacts": dict(result.artifacts),
        "error": result.error,
        "elapsed_seconds": result.elapsed_seconds,
    }


def task_result_from_dict(data: JsonDict) -> TaskResult:
    """反序列化 TaskResult。"""

    return TaskResult(
        task_id=str(data["task_id"]),
        status=data["status"],  # type: ignore[arg-type]
        summary=str(data["summary"]),
        artifacts=dict(data.get("artifacts", {})),
        error=None if data.get("error") is None else str(data.get("error")),
        elapsed_seconds=float(data.get("elapsed_seconds", 0)),
    )


def task_record_to_dict(record: TaskRecord) -> JsonDict:
    """序列化 TaskRecord。"""

    return {
        "task_id": record.task_id,
        "mode": record.mode,
        "parent_agent_id": record.parent_agent_id,
        "target_agent_id": record.target_agent_id,
        "request": task_request_to_dict(record.request),
        "status": record.status,
        "created_at": record.created_at,
        "deadline_at": record.deadline_at,
        "result": None if record.result is None else task_result_to_dict(record.result),
        "late_result": (
            None if record.late_result is None else task_result_to_dict(record.late_result)
        ),
        "completed_at": record.completed_at,
        "consumed_at": record.consumed_at,
        "worker_id": record.worker_id,
        "lease_expires_at": record.lease_expires_at,
        "attempt": record.attempt,
        "updated_at": record.updated_at,
        "version": record.version,
        "cancel_requested_at": record.cancel_requested_at,
        "result_notified_at": record.result_notified_at,
    }


def task_record_from_dict(data: JsonDict) -> TaskRecord:
    """反序列化 TaskRecord。"""

    return TaskRecord(
        task_id=str(data["task_id"]),
        mode=data["mode"],  # type: ignore[arg-type]
        parent_agent_id=str(data["parent_agent_id"]),
        target_agent_id=str(data["target_agent_id"]),
        request=task_request_from_dict(data["request"]),
        status=data["status"],  # type: ignore[arg-type]
        created_at=float(data["created_at"]),
        deadline_at=float(data["deadline_at"]),
        result=(
            None if data.get("result") is None else task_result_from_dict(data["result"])
        ),
        late_result=(
            None
            if data.get("late_result") is None
            else task_result_from_dict(data["late_result"])
        ),
        completed_at=None if data.get("completed_at") is None else float(data["completed_at"]),
        consumed_at=None if data.get("consumed_at") is None else float(data["consumed_at"]),
        worker_id=None if data.get("worker_id") is None else str(data["worker_id"]),
        lease_expires_at=(
            None if data.get("lease_expires_at") is None else float(data["lease_expires_at"])
        ),
        attempt=int(data.get("attempt", 0)),
        updated_at=None if data.get("updated_at") is None else float(data["updated_at"]),
        version=int(data.get("version", 0)),
        cancel_requested_at=(
            None
            if data.get("cancel_requested_at") is None
            else float(data["cancel_requested_at"])
        ),
        result_notified_at=(
            None if data.get("result_notified_at") is None else float(data["result_notified_at"])
        ),
    )


def envelope_to_dict(envelope: AgentEnvelope) -> JsonDict:
    """序列化 AgentEnvelope。"""

    return {
        "envelope_id": envelope.envelope_id,
        "from_agent_id": envelope.from_agent_id,
        "to_agent_id": envelope.to_agent_id,
        "type": envelope.type,
        "payload": (
            task_request_to_dict(envelope.payload)
            if envelope.type == "task_request"
            else task_result_to_dict(envelope.payload)  # type: ignore[arg-type]
        ),
        "created_at": envelope.created_at,
        "correlation_id": envelope.correlation_id,
    }


def envelope_from_dict(data: JsonDict) -> AgentEnvelope:
    """反序列化 AgentEnvelope。"""

    envelope_type = data["type"]
    payload = (
        task_request_from_dict(data["payload"])
        if envelope_type == "task_request"
        else task_result_from_dict(data["payload"])
    )
    return AgentEnvelope(
        envelope_id=str(data["envelope_id"]),
        from_agent_id=str(data["from_agent_id"]),
        to_agent_id=str(data["to_agent_id"]),
        type=envelope_type,  # type: ignore[arg-type]
        payload=payload,
        created_at=float(data["created_at"]),
        correlation_id=(
            None if data.get("correlation_id") is None else str(data["correlation_id"])
        ),
    )
```

- [ ] **Step 5: Run tests**

Run:

```bash
uv run pytest tests/multi/test_task_serializers.py tests/multi/test_task_table.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/agentos/multi/types.py src/agentos/multi/serializers.py tests/multi/test_task_serializers.py
git commit -m "feat: add distributed task serializers"
```

---

### Task 2: TaskStore Protocol And In-Memory Adapter

**Files:**
- Create: `src/agentos/multi/task_store.py`
- Modify: `src/agentos/multi/tasks.py`
- Modify: `src/agentos/multi/__init__.py`
- Test: `tests/multi/test_task_store_contract.py`

- [ ] **Step 1: Write failing TaskStore contract tests**

Add `tests/multi/test_task_store_contract.py`:

```python
from agentos.multi import TaskRecord, TaskRequest, TaskResult, TaskTable


def record(task_id: str = "task_1", capabilities: tuple[str, ...] = ("code",)) -> TaskRecord:
    return TaskRecord(
        task_id=task_id,
        mode="dispatch",
        parent_agent_id="parent",
        target_agent_id="code-worker",
        request=TaskRequest(task_id=task_id, instruction="Do work"),
        status="queued",
        created_at=1.0,
        deadline_at=30.0,
    )


def result(task_id: str = "task_1", status: str = "completed") -> TaskResult:
    return TaskResult(task_id=task_id, status=status, summary=f"{status} result")


def test_task_table_claims_queued_task_with_worker_lease() -> None:
    store = TaskTable()
    store.create(record())

    claims = store.claim_queued(
        worker_id="worker-instance-1",
        capabilities=("code",),
        limit=1,
        lease_expires_at=20.0,
        now=2.0,
    )

    assert len(claims) == 1
    claimed = store.get("task_1")
    assert claimed is not None
    assert claimed.status == "running"
    assert claimed.worker_id == "worker-instance-1"
    assert claimed.lease_expires_at == 20.0
    assert claimed.attempt == 1
    assert claimed.updated_at == 2.0
    assert claimed.version == 1


def test_task_table_running_cancel_is_request_then_ack() -> None:
    store = TaskTable()
    store.create(record())
    store.claim_queued(
        worker_id="worker-instance-1",
        capabilities=("code",),
        limit=1,
        lease_expires_at=20.0,
        now=2.0,
    )

    assert store.request_cancel("task_1", now=3.0) is True
    requested = store.get("task_1")
    assert requested is not None
    assert requested.status == "running"
    assert requested.cancel_requested_at == 3.0

    assert store.ack_cancelled("task_1", result(status="cancelled"), now=4.0) is True
    cancelled = store.get("task_1")
    assert cancelled is not None
    assert cancelled.status == "cancelled"
    assert cancelled.result == result(status="cancelled")


def test_task_table_marks_result_notified_once() -> None:
    store = TaskTable()
    store.create(record())
    store.claim_queued(
        worker_id="worker-instance-1",
        capabilities=("code",),
        limit=1,
        lease_expires_at=20.0,
        now=2.0,
    )
    store.mark_completed("task_1", result(), now=3.0)

    assert store.mark_result_notified("task_1", now=4.0) is True
    assert store.mark_result_notified("task_1", now=5.0) is False
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest tests/multi/test_task_store_contract.py -q
```

Expected: FAIL because `claim_queued`, `request_cancel`, `ack_cancelled`, and `mark_result_notified` do not exist.

- [ ] **Step 3: Add TaskStore Protocol**

Create `src/agentos/multi/task_store.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from agentos.multi.types import TaskHandle, TaskRecord, TaskResult


@dataclass(frozen=True, slots=True)
class TaskClaim:
    """worker 对任务的 lease claim 结果。"""

    task_id: str
    worker_id: str
    lease_expires_at: float
    attempt: int


class TaskStore(Protocol):
    """分布式 multi-agent 任务 truth source 边界。"""

    def create(self, record: TaskRecord) -> TaskHandle:
        """创建 queued task record。"""

    def get(self, task_id: str) -> TaskRecord | None:
        """返回 task record。"""

    def claim_queued(
        self,
        *,
        worker_id: str,
        capabilities: Sequence[str],
        limit: int,
        lease_expires_at: float,
        now: float,
    ) -> list[TaskClaim]:
        """原子领取 queued 或 lease-expired tasks。"""

    def request_cancel(self, task_id: str, *, now: float) -> bool:
        """请求取消 queued/running task。"""

    def ack_cancelled(self, task_id: str, result: TaskResult, *, now: float) -> bool:
        """worker 确认 running task 已取消。"""
```

- [ ] **Step 4: Extend TaskTable minimally**

Modify `src/agentos/multi/tasks.py`:

```python
from agentos.multi.task_store import TaskClaim
```

Add methods to `TaskTable`:

```python
def claim_queued(
    self,
    *,
    worker_id: str,
    capabilities: Sequence[str],
    limit: int,
    lease_expires_at: float,
    now: float,
) -> list[TaskClaim]:
    """领取 queued task，并写入 worker lease。"""

    if limit < 1:
        return []
    required = set(capabilities)
    claims: list[TaskClaim] = []
    with self._lock:
        for task_id, record in list(self._records.items()):
            if len(claims) >= limit:
                break
            if record.status != "queued":
                continue
            request_capabilities = tuple(record.request.allowed_tool_names)
            if request_capabilities and not set(request_capabilities).issubset(required):
                continue
            attempt = record.attempt + 1
            updated = replace(
                record,
                status="running",
                worker_id=worker_id,
                lease_expires_at=lease_expires_at,
                attempt=attempt,
                updated_at=now,
                version=record.version + 1,
            )
            self._records[task_id] = updated
            claims.append(
                TaskClaim(
                    task_id=task_id,
                    worker_id=worker_id,
                    lease_expires_at=lease_expires_at,
                    attempt=attempt,
                ),
            )
    return claims

def request_cancel(self, task_id: str, *, now: float) -> bool:
    """queued 直接取消，running 写入 cancel intent。"""

    with self._lock:
        record = self._records.get(task_id)
        if record is None:
            return False
        if record.status == "queued":
            result = TaskResult(
                task_id=task_id,
                status="cancelled",
                summary="task cancelled",
            )
            self._records[task_id] = replace(
                record,
                status="cancelled",
                result=result,
                completed_at=now,
                updated_at=now,
                version=record.version + 1,
            )
            return True
        if record.status == "running" and record.cancel_requested_at is None:
            self._records[task_id] = replace(
                record,
                cancel_requested_at=now,
                updated_at=now,
                version=record.version + 1,
            )
            return True
        return record.status == "running" and record.cancel_requested_at is not None

def ack_cancelled(self, task_id: str, result: TaskResult, *, now: float) -> bool:
    """running task 响应 cancel intent 后进入 cancelled。"""

    with self._lock:
        record = self._records.get(task_id)
        if (
            record is None
            or record.status != "running"
            or record.cancel_requested_at is None
        ):
            return False
        self._records[task_id] = replace(
            record,
            status="cancelled",
            result=result,
            completed_at=now,
            updated_at=now,
            version=record.version + 1,
        )
        return True

def mark_result_notified(self, task_id: str, *, now: float) -> bool:
    """标记 terminal result 已发送 result-ready 通知。"""

    with self._lock:
        record = self._records.get(task_id)
        if (
            record is None
            or record.result is None
            or record.result_notified_at is not None
        ):
            return False
        self._records[task_id] = replace(
            record,
            result_notified_at=now,
            updated_at=now,
            version=record.version + 1,
        )
        return True
```

Also update existing `mark_completed`, `mark_failed`, `mark_cancelled`, and `mark_timed_out` signatures to accept optional `now: float | None = None`, using `time.time()` when omitted.

- [ ] **Step 5: Export new names**

Modify `src/agentos/multi/__init__.py`:

```python
from agentos.multi.task_store import TaskClaim, TaskStore
```

Add `"TaskClaim"` and `"TaskStore"` to `__all__`.

- [ ] **Step 6: Run tests**

Run:

```bash
uv run pytest tests/multi/test_task_store_contract.py tests/multi/test_task_table.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/agentos/multi/task_store.py src/agentos/multi/tasks.py src/agentos/multi/__init__.py tests/multi/test_task_store_contract.py
git commit -m "feat: add task store boundary"
```

---

### Task 3: AgentMessageQueue Protocol And In-Memory Adapter

**Files:**
- Create: `src/agentos/multi/message_queue.py`
- Modify: `src/agentos/multi/inbox.py`
- Modify: `src/agentos/multi/__init__.py`
- Test: `tests/multi/test_message_queue_contract.py`

- [ ] **Step 1: Write failing queue contract tests**

Add `tests/multi/test_message_queue_contract.py`:

```python
from agentos.multi import AgentEnvelope, AgentInbox, TaskRequest


def request_envelope() -> AgentEnvelope:
    return AgentEnvelope(
        envelope_id="env_1",
        from_agent_id="parent",
        to_agent_id="worker",
        type="task_request",
        payload=TaskRequest(task_id="task_1", instruction="Do work"),
        created_at=1.0,
        correlation_id="task_1",
    )


def test_agent_inbox_returns_delivery_ids_and_acks() -> None:
    queue = AgentInbox()
    queue.create_inbox("worker")

    delivery_id = queue.send(request_envelope())
    deliveries = queue.collect("worker")

    assert deliveries[0].delivery_id == delivery_id
    assert deliveries[0].envelope == request_envelope()
    assert queue.ack("worker", delivery_id) is True
    assert queue.ack("worker", delivery_id) is False
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest tests/multi/test_message_queue_contract.py -q
```

Expected: FAIL because `send()` returns `None`, `collect()` returns bare envelopes, and `ack()` does not exist.

- [ ] **Step 3: Add protocol and delivery wrapper**

Create `src/agentos/multi/message_queue.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agentos.multi.types import AgentEnvelope


@dataclass(frozen=True, slots=True)
class QueueDelivery:
    """message queue 返回的 envelope delivery。"""

    delivery_id: str
    envelope: AgentEnvelope


class AgentMessageQueue(Protocol):
    """分布式 agent 点对点消息和通知投递边界。"""

    def create_inbox(self, agent_id: str) -> None:
        """创建目标 inbox。"""

    def remove_inbox(self, agent_id: str) -> None:
        """移除目标 inbox。"""

    def send(self, envelope: AgentEnvelope) -> str:
        """发送 envelope，并返回 delivery id。"""

    def collect(self, agent_id: str) -> list[QueueDelivery]:
        """读取当前可处理 deliveries。"""

    def wait(self, agent_id: str, timeout: float | None = None) -> bool:
        """等待 inbox 出现可处理消息。"""

    def ack(self, agent_id: str, delivery_id: str) -> bool:
        """确认 delivery 已处理。"""
```

- [ ] **Step 4: Adapt AgentInbox**

Modify `src/agentos/multi/inbox.py`:

```python
from agentos.multi.message_queue import QueueDelivery
```

Change queue type to store `QueueDelivery`, and update methods:

```python
self._acked_delivery_ids: set[str] = set()
```

```python
def send(self, envelope: AgentEnvelope) -> str:
    """向目标 inbox 发送 envelope，缺失或满载时 fail-closed。"""

    delivery_id = envelope.envelope_id
    with self._lock:
        queue = self._queue_for(envelope.to_agent_id)
        if queue.qsize() >= self.max_pending_envelopes:
            ...
        queue.put(QueueDelivery(delivery_id=delivery_id, envelope=envelope))
        self._events[envelope.to_agent_id].set()
    return delivery_id

def collect(self, agent_id: str) -> list[QueueDelivery]:
    """Drain 并返回当前 inbox 中所有 deliveries。"""

    with self._lock:
        queue = self._queue_for(agent_id)
        deliveries: list[QueueDelivery] = []
        while True:
            try:
                deliveries.append(queue.get_nowait())
            except Empty:
                break
        if queue.empty():
            self._events[agent_id].clear()
        return deliveries

def collect_envelopes(self, agent_id: str) -> list[AgentEnvelope]:
    """兼容旧调用方：只返回 envelopes。"""

    return [delivery.envelope for delivery in self.collect(agent_id)]

def ack(self, agent_id: str, delivery_id: str) -> bool:
    """in-memory delivery drain 后即可视为已处理；ack 只做幂等记录。"""

    with self._lock:
        self._queue_for(agent_id)
        if delivery_id in self._acked_delivery_ids:
            return False
        self._acked_delivery_ids.add(delivery_id)
        return True
```

Update old tests that expected `collect()` to return envelopes to call `collect_envelopes()`, or update assertions to unwrap `delivery.envelope`.

- [ ] **Step 5: Export new names**

Modify `src/agentos/multi/__init__.py`:

```python
from agentos.multi.message_queue import AgentMessageQueue, QueueDelivery
```

Add both names to `__all__`.

- [ ] **Step 6: Run tests**

Run:

```bash
uv run pytest tests/multi/test_message_queue_contract.py tests/multi/test_inbox.py tests/multi -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/agentos/multi/message_queue.py src/agentos/multi/inbox.py src/agentos/multi/__init__.py tests/multi/test_message_queue_contract.py tests/multi/test_inbox.py
git commit -m "feat: add agent message queue boundary"
```

---

### Task 4: PostgresTaskStore And Migration

**Files:**
- Create: `src/agentos/multi/postgres_tasks.py`
- Create: `docs/migrations/2026-05-16-postgres-multi-agent-tasks.sql`
- Modify: `src/agentos/multi/__init__.py`
- Test: `tests/multi/test_postgres_task_store.py`

- [ ] **Step 1: Write failing fake-connection tests**

Add `tests/multi/test_postgres_task_store.py` with a fake connection that records SQL:

```python
import json

from agentos.multi import TaskRecord, TaskRequest, TaskResult
from agentos.multi.postgres_tasks import PostgresTaskStore


class FakeCursor:
    def __init__(self, rows=None):
        self._rows = rows or []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeConnection:
    def __init__(self):
        self.records = {}
        self.commits = 0
        self.sql = []

    def execute(self, sql, params=()):
        self.sql.append(sql)
        if "INSERT INTO agentos_multi_agent_tasks" in sql:
            self.records[str(params[0])] = params
            return FakeCursor()
        if "SELECT payload FROM agentos_multi_agent_tasks WHERE task_id" in sql:
            row = self.records.get(str(params[0]))
            return FakeCursor([(row[1],)] if row else [])
        if "UPDATE agentos_multi_agent_tasks" in sql and "RETURNING payload" in sql:
            return FakeCursor([])
        return FakeCursor()

    def commit(self):
        self.commits += 1


def record() -> TaskRecord:
    return TaskRecord(
        task_id="task_1",
        mode="dispatch",
        parent_agent_id="parent",
        target_agent_id="worker",
        request=TaskRequest(task_id="task_1", instruction="Do work"),
        status="queued",
        created_at=1.0,
        deadline_at=30.0,
    )


def test_postgres_task_store_saves_and_loads_record() -> None:
    connection = FakeConnection()
    store = PostgresTaskStore(dsn="postgresql://unused", connection=connection)

    store.create(record())

    assert store.get("task_1") == record()
    assert connection.commits == 1


def test_postgres_task_store_uses_atomic_claim_sql() -> None:
    connection = FakeConnection()
    store = PostgresTaskStore(dsn="postgresql://unused", connection=connection)

    store.claim_queued(
        worker_id="worker-instance-1",
        capabilities=("code",),
        limit=1,
        lease_expires_at=20.0,
        now=2.0,
    )

    joined_sql = "\n".join(connection.sql)
    assert "FOR UPDATE SKIP LOCKED" in joined_sql
    assert "RETURNING payload" in joined_sql
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest tests/multi/test_postgres_task_store.py -q
```

Expected: FAIL because `agentos.multi.postgres_tasks` does not exist.

- [ ] **Step 3: Add migration**

Create `docs/migrations/2026-05-16-postgres-multi-agent-tasks.sql`:

```sql
-- migrate:up
CREATE TABLE agentos_multi_agent_tasks (
  task_id TEXT PRIMARY KEY,
  parent_agent_id TEXT NOT NULL,
  target_agent_id TEXT NOT NULL,
  status TEXT NOT NULL,
  worker_id TEXT,
  lease_expires_at DOUBLE PRECISION,
  deadline_at DOUBLE PRECISION NOT NULL,
  version INTEGER NOT NULL DEFAULT 0,
  payload JSONB NOT NULL,
  result_notified_at DOUBLE PRECISION,
  updated_at DOUBLE PRECISION NOT NULL
);

CREATE INDEX agentos_multi_agent_tasks_claim_idx
  ON agentos_multi_agent_tasks (status, target_agent_id, lease_expires_at, deadline_at);

CREATE TABLE agentos_multi_agent_task_outbox (
  outbox_id BIGSERIAL PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES agentos_multi_agent_tasks(task_id) ON DELETE CASCADE,
  event_type TEXT NOT NULL,
  payload JSONB NOT NULL,
  delivered_at DOUBLE PRECISION,
  created_at DOUBLE PRECISION NOT NULL
);

-- migrate:down
DROP TABLE IF EXISTS agentos_multi_agent_task_outbox;
DROP TABLE IF EXISTS agentos_multi_agent_tasks;
```

- [ ] **Step 4: Implement PostgresTaskStore skeleton and create/get**

Create `src/agentos/multi/postgres_tasks.py`:

```python
from __future__ import annotations

import json
from typing import Protocol, Sequence, cast

from agentos.multi.serializers import task_record_from_dict, task_record_to_dict
from agentos.multi.task_store import TaskClaim
from agentos.multi.types import TaskHandle, TaskRecord, TaskResult


class PostgresCursor(Protocol):
    def fetchone(self) -> tuple[object, ...] | None: ...
    def fetchall(self) -> list[tuple[object, ...]]: ...


class PostgresConnection(Protocol):
    def execute(self, sql: str, params: tuple[object, ...] = ()) -> PostgresCursor: ...


class PostgresTaskStore:
    """Postgres-backed TaskStore；schema 由 migration 预先创建。"""

    def __init__(self, dsn: str, connection: object | None = None) -> None:
        if connection is not None:
            self._connection = connection
            self._dsn = dsn
            return
        try:
            import psycopg
        except ImportError as error:
            raise RuntimeError(
                "PostgresTaskStore requires the optional dependency `agentos[postgres]`.",
            ) from error
        self._connection = psycopg.connect(dsn)
        self._dsn = dsn

    def create(self, record: TaskRecord) -> TaskHandle:
        payload = task_record_to_dict(record)
        self._execute(
            """
            INSERT INTO agentos_multi_agent_tasks (
              task_id, parent_agent_id, target_agent_id, status, worker_id,
              lease_expires_at, deadline_at, version, payload,
              result_notified_at, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
            """,
            (
                record.task_id,
                record.parent_agent_id,
                record.target_agent_id,
                record.status,
                record.worker_id,
                record.lease_expires_at,
                record.deadline_at,
                record.version,
                json.dumps(payload, ensure_ascii=False),
                record.result_notified_at,
                record.updated_at or record.created_at,
            ),
        )
        self._commit()
        return TaskHandle(
            task_id=record.task_id,
            mode=record.mode,
            target_agent_id=record.target_agent_id,
            status=record.status,
        )

    def get(self, task_id: str) -> TaskRecord | None:
        row = self._execute(
            """
            SELECT payload FROM agentos_multi_agent_tasks WHERE task_id = %s
            """,
            (task_id,),
        ).fetchone()
        if row is None:
            return None
        return task_record_from_dict(self._json_value(row[0]))

    def claim_queued(
        self,
        *,
        worker_id: str,
        capabilities: Sequence[str],
        limit: int,
        lease_expires_at: float,
        now: float,
    ) -> list[TaskClaim]:
        rows = self._execute(
            """
            WITH candidates AS (
              SELECT task_id, payload
              FROM agentos_multi_agent_tasks
              WHERE status = 'queued'
              ORDER BY deadline_at, task_id
              LIMIT %s
              FOR UPDATE SKIP LOCKED
            )
            UPDATE agentos_multi_agent_tasks AS tasks
            SET status = 'running',
                worker_id = %s,
                lease_expires_at = %s,
                version = tasks.version + 1,
                updated_at = %s,
                payload = candidates.payload
            FROM candidates
            WHERE tasks.task_id = candidates.task_id
            RETURNING tasks.task_id, tasks.payload
            """,
            (limit, worker_id, lease_expires_at, now),
        ).fetchall()
        self._commit()
        return [
            TaskClaim(
                task_id=str(row[0]),
                worker_id=worker_id,
                lease_expires_at=lease_expires_at,
                attempt=task_record_from_dict(self._json_value(row[1])).attempt + 1,
            )
            for row in rows
        ]

    def _execute(self, sql: str, params: tuple[object, ...] = ()) -> PostgresCursor:
        return cast(PostgresConnection, self._connection).execute(sql, params)

    def _commit(self) -> None:
        commit = getattr(self._connection, "commit", None)
        if commit is not None:
            commit()

    def _json_value(self, value: object) -> dict[str, object]:
        if isinstance(value, str):
            return json.loads(value)
        return dict(value)  # type: ignore[arg-type]
```

Implementation note for the worker: after the skeleton passes the initial tests, add request/cancel/terminal transition methods using the same CAS style as `TaskTable`. Keep each transition behind its own failing test before adding code.

- [ ] **Step 5: Export PostgresTaskStore**

Modify `src/agentos/multi/__init__.py` with lazy import if needed to avoid optional dependency import side effects:

```python
def __getattr__(name: str) -> object:
    if name == "RemoteTaskExecutor":
        from agentos.multi.remote import RemoteTaskExecutor
        return RemoteTaskExecutor
    if name == "PostgresTaskStore":
        from agentos.multi.postgres_tasks import PostgresTaskStore
        return PostgresTaskStore
    raise AttributeError(name)
```

Add `"PostgresTaskStore"` to `__all__`.

- [ ] **Step 6: Run tests**

Run:

```bash
uv run pytest tests/multi/test_postgres_task_store.py tests/architecture/test_public_api.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/agentos/multi/postgres_tasks.py src/agentos/multi/__init__.py docs/migrations/2026-05-16-postgres-multi-agent-tasks.sql tests/multi/test_postgres_task_store.py
git commit -m "feat: add postgres multi-agent task store"
```

---

### Task 5: Redis Streams AgentMessageQueue

**Files:**
- Create: `src/agentos/multi/redis_queue.py`
- Modify: `src/agentos/multi/__init__.py`
- Test: `tests/multi/test_redis_message_queue.py`

- [ ] **Step 1: Write failing Redis queue tests**

Add `tests/multi/test_redis_message_queue.py`:

```python
import json

from agentos.multi import AgentEnvelope, TaskRequest
from agentos.multi.redis_queue import RedisAgentMessageQueue


class FakeRedis:
    def __init__(self):
        self.streams = {}
        self.acked = []

    def xgroup_create(self, name, groupname, id="0", mkstream=False):
        self.streams.setdefault(name, [])

    def xadd(self, name, fields, maxlen=None, approximate=True):
        stream = self.streams.setdefault(name, [])
        message_id = f"{len(stream) + 1}-0"
        stream.append((message_id, fields))
        return message_id

    def xreadgroup(self, groupname, consumername, streams, count=100, block=None):
        result = []
        for name in streams:
            messages = self.streams.get(name, [])
            if messages:
                result.append((name, messages[:count]))
        return result

    def xack(self, name, groupname, message_id):
        self.acked.append((name, groupname, message_id))
        return 1


def envelope() -> AgentEnvelope:
    return AgentEnvelope(
        envelope_id="env_1",
        from_agent_id="parent",
        to_agent_id="worker",
        type="task_request",
        payload=TaskRequest(task_id="task_1", instruction="Do work"),
        created_at=1.0,
        correlation_id="task_1",
    )


def test_redis_queue_sends_collects_and_acks_envelope() -> None:
    client = FakeRedis()
    queue = RedisAgentMessageQueue(url="redis://unused", client=client)
    queue.create_inbox("worker")

    delivery_id = queue.send(envelope())
    deliveries = queue.collect("worker")

    assert deliveries[0].delivery_id == delivery_id
    assert deliveries[0].envelope == envelope()
    assert queue.ack("worker", delivery_id) is True
    assert client.acked == [("agentos:multi:inbox:worker", "agentos-workers", delivery_id)]
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest tests/multi/test_redis_message_queue.py -q
```

Expected: FAIL because `agentos.multi.redis_queue` does not exist.

- [ ] **Step 3: Implement RedisAgentMessageQueue**

Create `src/agentos/multi/redis_queue.py`:

```python
from __future__ import annotations

import json

from agentos.multi.message_queue import QueueDelivery
from agentos.multi.serializers import envelope_from_dict, envelope_to_dict
from agentos.multi.types import AgentEnvelope


class RedisAgentMessageQueue:
    """Redis Streams-backed AgentMessageQueue adapter。"""

    def __init__(
        self,
        url: str,
        client: object | None = None,
        *,
        key_prefix: str = "agentos",
        group_name: str = "agentos-workers",
        consumer_name: str = "agentos-worker",
        max_stream_length: int = 10_000,
    ) -> None:
        if client is not None:
            self._client = client
            self._url = url
        else:
            try:
                import redis
            except ImportError as error:
                raise RuntimeError(
                    "RedisAgentMessageQueue requires the optional dependency `agentos[redis]`.",
                ) from error
            self._client = redis.Redis.from_url(url)
            self._url = url
        self._key_prefix = key_prefix.rstrip(":")
        self._group_name = group_name
        self._consumer_name = consumer_name
        self._max_stream_length = max_stream_length

    def create_inbox(self, agent_id: str) -> None:
        """创建 stream consumer group；已存在时保持幂等。"""

        try:
            self._client.xgroup_create(
                self._stream_key(agent_id),
                self._group_name,
                id="0",
                mkstream=True,
            )
        except Exception as error:
            if "BUSYGROUP" not in str(error):
                raise

    def remove_inbox(self, agent_id: str) -> None:
        """第一版不删除 stream，避免误删 pending delivery。"""

    def send(self, envelope: AgentEnvelope) -> str:
        """写入目标 agent stream，并返回 Redis stream message id。"""

        return str(
            self._client.xadd(
                self._stream_key(envelope.to_agent_id),
                {"payload": json.dumps(envelope_to_dict(envelope), ensure_ascii=False)},
                maxlen=self._max_stream_length,
                approximate=True,
            ),
        )

    def collect(self, agent_id: str) -> list[QueueDelivery]:
        """读取当前可处理 deliveries。"""

        raw_streams = self._client.xreadgroup(
            self._group_name,
            self._consumer_name,
            {self._stream_key(agent_id): ">"},
            count=100,
            block=1,
        )
        deliveries: list[QueueDelivery] = []
        for _stream_name, messages in raw_streams:
            for message_id, fields in messages:
                payload = fields.get("payload")
                if isinstance(payload, bytes):
                    payload = payload.decode("utf-8")
                deliveries.append(
                    QueueDelivery(
                        delivery_id=str(message_id),
                        envelope=envelope_from_dict(json.loads(str(payload))),
                    ),
                )
        return deliveries

    def wait(self, agent_id: str, timeout: float | None = None) -> bool:
        """Redis collect 本身阻塞；wait 使用一次短 collect 判断。"""

        return bool(self.collect(agent_id))

    def ack(self, agent_id: str, delivery_id: str) -> bool:
        """确认 stream message 已处理。"""

        return bool(
            self._client.xack(
                self._stream_key(agent_id),
                self._group_name,
                delivery_id,
            ),
        )

    def _stream_key(self, agent_id: str) -> str:
        return f"{self._key_prefix}:multi:inbox:{agent_id}"
```

- [ ] **Step 4: Export RedisAgentMessageQueue**

Modify `src/agentos/multi/__init__.py` lazy import:

```python
if name == "RedisAgentMessageQueue":
    from agentos.multi.redis_queue import RedisAgentMessageQueue
    return RedisAgentMessageQueue
```

Add `"RedisAgentMessageQueue"` to `__all__`.

- [ ] **Step 5: Run tests**

Run:

```bash
uv run pytest tests/multi/test_redis_message_queue.py tests/memory/test_optional_adapters.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/agentos/multi/redis_queue.py src/agentos/multi/__init__.py tests/multi/test_redis_message_queue.py
git commit -m "feat: add redis agent message queue"
```

---

### Task 6: Coordinator Protocol Dependency Inversion

**Files:**
- Modify: `src/agentos/multi/coordinator.py`
- Modify: `src/agentos/multi/expert.py`
- Modify: tests that consume `AgentInbox.collect()`
- Test: `tests/multi/test_coordinator_distributed_boundaries.py`

- [ ] **Step 1: Write failing coordinator boundary test**

Add `tests/multi/test_coordinator_distributed_boundaries.py`:

```python
from agentos.multi import (
    AgentCard,
    AgentCoordinator,
    AgentInbox,
    InMemoryRegistry,
    SpawnExecutor,
    TaskTable,
)
from tests.multi.helpers import build_agent_with_response
from tests.multi.test_coordinator_spawn import StaticSubagentFactory


def test_coordinator_accepts_task_store_and_message_queue_boundaries() -> None:
    registry = InMemoryRegistry()
    task_store = TaskTable()
    message_queue = AgentInbox()
    coordinator = AgentCoordinator(
        registry=registry,
        inbox=message_queue,
        task_table=task_store,
        spawn_executor=SpawnExecutor(max_workers=1),
        subagent_factory=StaticSubagentFactory(),
    )
    parent = AgentCard(
        agent_id="parent",
        name="Parent",
        description="Parent",
        capabilities=("parent",),
    )
    expert = AgentCard(
        agent_id="expert",
        name="Expert",
        description="Expert",
        capabilities=("worker",),
        max_concurrent_tasks=1,
    )

    coordinator.attach_agent(parent, build_agent_with_response("parent"))
    coordinator.attach_agent(expert, build_agent_with_response("expert"))
    handle = coordinator.dispatch(
        instruction="Do work",
        required_capabilities=("worker",),
        parent_agent_id="parent",
    )

    assert task_store.get(handle.task_id) is not None
    assert message_queue.collect("expert")[0].envelope.payload.task_id == handle.task_id
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest tests/multi/test_coordinator_distributed_boundaries.py -q
```

Expected: FAIL until coordinator accepts protocol-style methods and queue deliveries consistently.

- [ ] **Step 3: Update coordinator internals to unwrap deliveries**

Modify `AgentCoordinator.collect_results()`:

```python
def collect_results(self, agent_id: str) -> list[TaskResult]:
    """drain inbox，并从 TaskStore 返回未消费的终态 results。"""

    self._mark_due_timeouts()
    for delivery in self.inbox.collect(agent_id):
        self.inbox.ack(agent_id, delivery.delivery_id)
    return self.task_table.consume_results_for_agent(agent_id)
```

Where old code directly iterates envelopes, switch to `delivery.envelope`.

- [ ] **Step 4: Update cancel semantics**

Modify `AgentCoordinator.cancel()` so queued/running distributed semantics go through task store:

```python
def cancel(self, task_id: str) -> bool:
    """取消 queued task 或请求 running task 取消。"""

    record = self.task_table.get(task_id)
    if record is None:
        return False
    if record.status in {"completed", "failed", "cancelled", "timeout"}:
        return True
    agent = self._agents.get(record.target_agent_id)
    if agent is not None:
        agent.interrupt()
    changed = self.task_table.request_cancel(task_id, now=time.time())
    if changed and record.status == "queued":
        current = self.task_table.get(task_id)
        if current is not None and current.result is not None:
            self._send_result(record, current.result)
            self._notify_task_completed(record.parent_agent_id, record.task_id)
    return changed
```

Keep existing tests for queued cancellation green by making `TaskTable.request_cancel()` produce a terminal cancelled result for queued records.

- [ ] **Step 5: Run coordinator and multi-agent tests**

Run:

```bash
uv run pytest tests/multi -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/agentos/multi/coordinator.py src/agentos/multi/expert.py tests/multi
git commit -m "refactor: depend on multi-agent messaging protocols"
```

---

### Task 7: Public API, Architecture Tests, And Documentation

**Files:**
- Modify: `tests/architecture/test_public_api.py`
- Modify: `docs/readme-online.md`
- Modify: `docs/todo-distributed-multi-agent-messaging.md`

- [ ] **Step 1: Add public API expectations**

Modify `tests/architecture/test_public_api.py` in `test_phase8_multi_agent_public_api_exports()`:

```python
for name in [
    "AgentMessageQueue",
    "PostgresTaskStore",
    "QueueDelivery",
    "RedisAgentMessageQueue",
    "TaskClaim",
    "TaskStore",
]:
    assert hasattr(multi, name)
```

- [ ] **Step 2: Run architecture test to verify failure if exports are missing**

Run:

```bash
uv run pytest tests/architecture/test_public_api.py::test_phase8_multi_agent_public_api_exports -q
```

Expected: PASS if earlier export tasks were complete; otherwise FAIL naming the missing export.

- [ ] **Step 3: Update docs**

In `docs/readme-online.md`, update the Multi-Agent section from “`TaskTable` 和 `AgentInbox` 当前是单进程内存实现” to describe both layers:

```markdown
`TaskStore` 是分布式任务 truth source 边界；`TaskTable` 是 in-memory adapter。
`AgentMessageQueue` 是 delivery/notification 边界；`AgentInbox` 是 in-memory adapter。
Postgres/Redis adapters are available behind optional extras.
```

In `docs/todo-distributed-multi-agent-messaging.md`, mark the P0/P1/P2/P3 items implemented only if the code tasks above were completed and verified.

- [ ] **Step 4: Run doc and public API checks**

Run:

```bash
uv run pytest tests/architecture/test_public_api.py -q
git diff --check
```

Expected: PASS and no whitespace errors.

- [ ] **Step 5: Commit**

```bash
git add tests/architecture/test_public_api.py docs/readme-online.md docs/todo-distributed-multi-agent-messaging.md
git commit -m "docs: document distributed multi-agent messaging"
```

---

### Task 8: Full Verification

**Files:**
- No code changes expected.

- [ ] **Step 1: Run full test suite**

Run:

```bash
uv run pytest -q
```

Expected: all tests pass.

- [ ] **Step 2: Run compileall**

Run:

```bash
python -m compileall -q src tests
```

Expected: no output and exit code 0.

- [ ] **Step 3: Run diff check**

Run:

```bash
git diff --check
```

Expected: no output and exit code 0.

- [ ] **Step 4: Drift search**

Run:

```bash
rg -n "TaskTable` 是结果真值源|EventBus.*message queue|Redis Streams.*truth source|directly depends on AgentInbox|directly depends on TaskTable" docs src tests
```

Expected: no stale claims that contradict `TaskStore` as truth source and `AgentMessageQueue` as delivery.

- [ ] **Step 5: Final status checklist**

Before claiming completion, produce this checklist:

| Design requirement | Implementation file(s) | Test file(s) / command | Status |
|---|---|---|---|
| TaskStore truth source boundary | `src/agentos/multi/task_store.py`, `src/agentos/multi/tasks.py` | `tests/multi/test_task_store_contract.py` | complete/deferred |
| AgentMessageQueue delivery boundary | `src/agentos/multi/message_queue.py`, `src/agentos/multi/inbox.py` | `tests/multi/test_message_queue_contract.py` | complete/deferred |
| Postgres task store | `src/agentos/multi/postgres_tasks.py`, migration | `tests/multi/test_postgres_task_store.py` | complete/deferred |
| Redis Streams queue | `src/agentos/multi/redis_queue.py` | `tests/multi/test_redis_message_queue.py` | complete/deferred |
| Coordinator protocol dependencies | `src/agentos/multi/coordinator.py` | `tests/multi` | complete/deferred |
| Full verification | all touched files | `uv run pytest -q`; `python -m compileall -q src tests`; `git diff --check` | complete/deferred |

If any row is deferred, final response must say “partially complete”.

---

## Self-Review

Spec coverage:

- Protocol boundaries: Task 2 and Task 3.
- In-memory compatibility: Task 2, Task 3, Task 6.
- Postgres truth source and outbox: Task 4.
- Redis delivery/notification: Task 5.
- Coordinator dependency inversion and cancel semantics: Task 6.
- Public docs and verification: Task 7 and Task 8.

Placeholder scan:

- Placeholder markers were scanned and removed.
- Each test task names files and target behavior.
- No task relies on runtime metadata in default prompts.

Type consistency:

- `TaskStore`, `TaskClaim`, `AgentMessageQueue`, and `QueueDelivery` are introduced before use.
- `TaskTable` remains the in-memory adapter.
- `AgentInbox` remains the in-memory delivery adapter.
