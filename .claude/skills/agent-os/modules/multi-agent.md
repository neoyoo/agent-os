---
name: agent-os-multi-agent
description: Reference for multi-agent coordination in agent-os — local spawn, distributed task stores, message queues, A2A dispatch, tracing
---

# Multi-Agent Coordination

## Runtime Boundaries

`AgentCoordinator` orchestrates multi-agent work. It depends on protocol-style boundaries:

| Boundary | Responsibility | In-memory adapter | Production adapter |
|----------|----------------|-------------------|--------------------|
| `TaskStore` | Task/result truth source, lease, cancel intent, late result | `TaskTable` | `PostgresTaskStore` |
| `AgentMessageQueue` | Envelope delivery/notification | `AgentInbox` | `RedisAgentMessageQueue` |

Use `TaskTable` + `AgentInbox` for local tests and single-process development. Use `PostgresTaskStore` + `RedisAgentMessageQueue` when task state and delivery must cross processes.

Source: `src/agentos/multi/coordinator.py`, `src/agentos/multi/task_store.py`, `src/agentos/multi/message_queue.py`, `src/agentos/multi/tasks.py`, `src/agentos/multi/inbox.py`, `src/agentos/multi/postgres_tasks.py`, `src/agentos/multi/redis_queue.py`.

## Local Coordinator

```python
from agentos.multi import (
    AgentCard,
    AgentCoordinator,
    AgentInbox,
    InMemoryRegistry,
    SpawnExecutor,
    TaskTable,
)

coordinator = AgentCoordinator(
    registry=InMemoryRegistry(),
    task_store=TaskTable(),
    message_queue=AgentInbox(),
    spawn_executor=SpawnExecutor(max_workers=4),
    subagent_factory=subagent_factory,
)

coordinator.attach_agent(
    AgentCard(
        agent_id="parent",
        name="Parent",
        description="Coordinates work.",
        capabilities=("coordinate",),
    ),
    parent_agent,
)
```

Source: `tests/multi/test_coordinator_distributed_boundaries.py`, `tests/multi/test_coordinator_spawn.py`.

## Dispatch To A Local Expert

Register an expert agent with capabilities, then dispatch by required capability:

```python
coordinator.attach_agent(
    AgentCard(
        agent_id="expert",
        name="Expert",
        description="Reviews Python code.",
        capabilities=("code-review", "python"),
        max_concurrent_tasks=1,
    ),
    expert_agent,
)

handle = coordinator.dispatch(
    instruction="Review this Python module.",
    required_capabilities=("code-review",),
    parent_agent_id="parent",
)

deliveries = coordinator.inbox.collect("expert")
for delivery in deliveries:
    coordinator.execute_expert_envelope(delivery.envelope)
    coordinator.inbox.ack("expert", delivery.delivery_id)

results = coordinator.collect_results("parent")
```

Source: `tests/multi/test_coordinator_dispatch.py`, `src/agentos/multi/expert.py`.

## Distributed Adapters

```python
from agentos.multi import AgentCoordinator, SpawnExecutor
from agentos.multi.postgres_tasks import PostgresTaskStore
from agentos.multi.redis_queue import RedisAgentMessageQueue

coordinator = AgentCoordinator(
    registry=registry,
    task_store=PostgresTaskStore(dsn="postgresql://user:pass@host/db"),
    message_queue=RedisAgentMessageQueue(url="redis://host:6379/0"),
    spawn_executor=SpawnExecutor(max_workers=4),
    subagent_factory=subagent_factory,
)
```

Run `docs/migrations/2026-05-16-postgres-multi-agent-tasks.sql` before using `PostgresTaskStore`. Optional dependencies are required at runtime: `agentos[postgres]` for Postgres and `agentos[redis]` for Redis.

Source: `src/agentos/multi/postgres_tasks.py`, `src/agentos/multi/redis_queue.py`, `docs/migrations/2026-05-16-postgres-multi-agent-tasks.sql`, `tests/multi/test_optional_adapters.py`.

## Cancellation Semantics

- Queued tasks can become terminal `cancelled` immediately.
- Running tasks receive `cancel_requested_at`; the worker must ack cancellation or the task converges through timeout/late-result handling.
- Terminal writes from claimed workers should include matching `worker_id` and `attempt` when using claim/lease flows.

Source: `src/agentos/multi/tasks.py`, `src/agentos/multi/coordinator.py`, `tests/multi/test_task_store_contract.py`, `tests/multi/test_coordinator_distributed_boundaries.py`.

## Remote A2A Dispatch

Endpoint-backed agents still use `RemoteTaskExecutor` + A2A HTTP dispatch. This is separate from Redis queue delivery.

Source: `src/agentos/multi/remote.py`, `src/agentos/channels/a2a.py`, `tests/multi/test_remote_dispatch.py`.

## Production Recovery Helpers

- `OutboxReconciler` scans task outbox rows and resends terminal task result envelopes.
- `RedisAgentMessageQueue.reclaim_pending()` reclaims idle Redis Stream pending messages and moves exhausted messages to a dead-letter stream.
- `RedisContinuationTrigger` publishes task completion notices through Redis Pub/Sub and can fall back to TaskStore polling.
- Live Redis/Postgres tests live under `tests/integration/` and are skipped unless `AGENTOS_RUN_INTEGRATION=1` is set with `docker-compose.test.yml` services.

Source: `src/agentos/multi/reconciler.py`, `src/agentos/multi/redis_queue.py`, `src/agentos/multi/redis_continuation.py`, `tests/multi/test_outbox_reconciler.py`, `tests/multi/test_redis_pending_retry.py`, `tests/multi/test_redis_continuation.py`, `tests/integration/test_live_backends.py`, `docker-compose.test.yml`.
