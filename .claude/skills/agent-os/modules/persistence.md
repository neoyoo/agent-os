---
name: agent-os-persistence
description: Reference for session persistence and multi-node state primitives — Redis hot store, Postgres durable store, session snapshot lifecycle, and current production gaps
---

# Persistence & Multi-Node State

## Current Readiness

agent-os currently has real persistence primitives and an SDK session lifecycle boundary for durable web turns. Production multi-node deployment can use SDK Redis/Postgres adapters and `DistributedWebSessionOperationsProfile` readiness metadata, while the deployment still owns credentials, migrations, TTL policy, stale lease recovery policy, auth and tenant integration, workspace enforcement, and live backend verification.

For production state-plane planning, use `ProductionStatePlaneDeploymentProfile`
with the `production_state_plane` readiness payload. Keep `agent_registry`,
`message_queue`, `task_store`, `plan_store`, `worker_process_supervisor`,
`session_snapshot_persistence`, `state_plane_boundary_policy`, and
`live_backend_verification` separate. `NacosAgentRegistryAdapter`,
`NacosAgentCardResolver`, `NacosRegistryClient`, `NacosRegistryConfig`, and
`NacosRegistryEvidence` or a custom registry owns AgentCard/endpoint/
capabilities/version/health metadata. This Nacos registry boundary provides
AgentCard-to-Nacos metadata projection, healthy Nacos instances filtering,
capability-based discovery, JSON-safe evidence, and discovery-only Nacos
metadata, plus Nacos namespace_id evidence.
Nacos namespace_id is passed to register, unregister, list, resolve, and discover.
The registry is not task truth, not plan truth, not session snapshot storage,
not message queue, and not worker runtime state. Nacos credentials and live
backend verification remain deployment-owned.
`RedisAgentMessageQueue` owns delivery, inbox, wakeup, and
fan-out hints; queue is not final task or plan state. `PostgresTaskStore` owns
task truth/result/retry/assignment evidence. `PostgresPlanStore` owns plan
truth/claims/scheduler recovery/planner evidence. `WorkerProcessSupervisor`
owns worker process start/running/stop/exit/fail evidence. `SessionSnapshotPersistence`
owns context, messages, compression, working state, and session runtime
snapshots.

For live backend verification evidence, use `BackendVerificationRecord`,
`DeploymentLiveBackendVerificationGateReport`,
`DeploymentLiveBackendVerificationProfile`, and
`LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS`. The
`deployment_live_backend_verification` probe covers `agent_registry`,
`message_queue`, `task_store`, `plan_store`, `worker_process_supervisor`, and
`session_snapshot_persistence`, and it sets `block_production_readiness` when
there is missing or failed backend evidence. The SDK consumes JSON-safe
evidence; backend check execution, credentials and secret distribution,
CI matrix execution, alert routing and runbooks remain deployment-owned.

For repeatable backend verification evidence collection, use
`BackendVerificationInvocationPlan`, `BackendVerificationRunner`,
`BackendVerificationCliRunner`, `BackendVerificationReportImporter`,
`BackendVerificationReportImportError`, and
`DeploymentLiveBackendVerificationRunResult`. The reference runner is
argv-only, uses no shell parsing, imports a report path or stdout JSON, records
bounded stdout/stderr summaries and `env_keys`, redacts configured secret
values and other secret values from captured output, emits a no backend client claim,
and is not a live backend client.
The real Redis/Postgres/Nacos/supervisor/session probes, credentials,
migrations, CI matrix execution, alert routing, runbooks, release approval, and
certification remain deployment-owned.

Phase 97: Reference State Plane Stack adds `ReferenceStatePlaneStack`,
`ReferenceStatePlaneStackProfile`, and
`REFERENCE_STATE_PLANE_REQUIRED_COMPONENTS` as the reference state plane that
composes persistence and coordination backends. It ties
`NacosAgentRegistryAdapter`, `RedisAgentMessageQueue`, `PostgresTaskStore`,
`PostgresPlanStore`, `WorkerProcessSupervisor`,
`LocalSubprocessWorkerSupervisor`, `SessionSnapshotPersistence`,
`PostgresSessionSnapshotPersistence`, `AgentServiceReference`,
`DistributedWebRuntimeProfile`, and `ProductionReadinessEvidenceBundle` through
readiness source aggregation and component identity evidence. It does not
create backend clients; credentials, migrations, CI matrix execution, alert
routing and runbooks remain deployment-owned.

| Layer | Current status |
|-------|----------------|
| Full session snapshot | Implemented: `SessionSnapshot`, `SessionPersistence`, `MemoryPersistence`, `SQLitePersistence`, `FileSystemPersistence` |
| Hot active-session projection | Implemented: `HotSessionStore`, `HotSessionState`, `RedisHotSessionStore` |
| Durable memory/message projection | Implemented: `DurableSessionStore`, `PostgresDurableSessionStore` |
| Channel session cache | Implemented: `AgentSessionProvider`, `InMemoryAgentSessionProvider` |
| Durable web session lifecycle | Implemented: `DurableAgentSessionProvider`, `SessionLeaseStore`, `SnapshotAgentFactory` |
| Distributed web session operations readiness | Implemented: `DistributedWebSessionOperationsProfile` |
| Production state-plane readiness | Implemented: `ProductionStatePlaneDeploymentProfile` |
| Reference state plane composition | Implemented: `ReferenceStatePlaneStack`, `ReferenceStatePlaneStackProfile`, `REFERENCE_STATE_PLANE_REQUIRED_COMPONENTS`, readiness source aggregation, component identity evidence |
| Production multi-node web storage | Implemented adapters: `RedisSessionLeaseStore`, `PostgresSessionSnapshotPersistence`; deployment policy remains app-owned |

Use this module to understand what can be composed today. For standard multi-node web sessions, prefer `DistributedWebRuntimeProfile` because it wires the durable provider to distributed lease and snapshot adapters. Use `DistributedWebSessionOperationsProfile` to expose readiness metadata for `durable_session_provider`, `lease_store`, `snapshot_persistence`, `snapshot_migration`, `lease_ttl_policy`, `stale_lease_recovery`, `credential_policy`, `auth_tenant_policy`, `workspace_policy`, and `live_backend_verification`. Do not claim production readiness until deployment-owned Redis/Postgres credentials, migration execution, lease TTL tuning, stale lease recovery policy, auth and tenant integration, workspace enforcement, and live backend verification are specified.

## Target Architecture

```
Request
  → acquire session lock/lease
  → load full `SessionSnapshot` or hot projection
  → build Agent with restored ContextRuntime/MessageRuntime/CompressionRuntime
  → run turn
  → save updated snapshot/projection
  → release lock/lease
  → response
```

Current storage split:
- **SessionPersistence** — full runtime snapshot, used by SQLite/FileSystem/Memory persistence.
- **Hot Store (Redis)** — active session working set projection, fast read/write, TTL-based expiry.
- **Durable Store (Postgres)** — permanent memory/session data projection for messages and compressed segment packages.

`SessionSnapshot`, `HotSessionState`, and durable segment packages are different projections. They are not interchangeable unless an adapter explicitly bridges them.

## Durable Web Session Provider

Production web sessions require a provider lifecycle:
lock/lease -> load `SessionSnapshot` -> run turn -> save `SessionSnapshot` -> release.

Use `DurableAgentSessionProvider` with a `SessionPersistence` and `SessionLeaseStore`.
For clustered web sessions, pair `RedisSessionLeaseStore` with `PostgresSessionSnapshotPersistence`.
`SessionPersistence` alone is not multi-node-ready because it does not prevent concurrent same-session writes.

The SDK boundary is now the provider lifecycle, not raw persistence alone. `DistributedWebSessionOperationsProfile` makes this boundary explicit: the SDK-owned side is `DurableAgentSessionProvider`, `RedisSessionLeaseStore`, `PostgresSessionSnapshotPersistence`, `SnapshotAgentFactory`, and the acquire/hydrate/save/release lifecycle; the deployment-owned side is Redis/Postgres credentials, migration execution, lease TTL tuning, stale lease recovery policy, auth and tenant integration, workspace policy configuration, live backend verification, rollout and rollback policy, and alerting and incident response.

## Protocols

### HotSessionStore

```python
class HotSessionStore(Protocol):
    def load_hot_state(self, session_id: str) -> HotSessionState | None: ...
    def save_hot_state(self, state: HotSessionState) -> None: ...
    def append_hot_message(self, session_id: str, message: Message) -> None: ...
    def get_hot_messages(self, session_id: str, message_ids: Sequence[str]) -> list[Message] | None: ...
    def save_segment_refs(self, session_id: str, segment_id: str, message_ids: Sequence[str]) -> None: ...
    def get_segment_refs(self, session_id: str, segment_id: str) -> tuple[str, ...] | None: ...
    def set_temporary_recalled_refs(self, session_id: str, message_ids: Sequence[str]) -> None: ...
    def consume_temporary_recalled_refs(self, session_id: str) -> tuple[str, ...]: ...
```

### DurableSessionStore

```python
class DurableSessionStore(Protocol):
    def save_session(self, session: SessionState) -> None: ...
    def load_session(self, session_id: str) -> SessionState: ...
    def append_message(self, session_id: str, message: Message) -> None: ...
    def get_messages(self, session_id: str, message_ids: Sequence[str]) -> list[Message]: ...
    def save_active_refs(self, session_id: str, refs: Sequence[MessageRef]) -> None: ...
    def load_active_refs(self, session_id: str) -> tuple[MessageRef, ...]: ...
    def save_compressed_segment(self, session_id: str, package: CompressedSegmentPackage) -> None: ...
    def get_segment_refs(self, session_id: str, segment_id: str) -> tuple[str, ...]: ...
    def list_compressed_segments(self, session_id: str) -> tuple[CompressedSegment, ...]: ...
```

### SessionPersistence (full snapshot)

```python
class SessionPersistence(Protocol):
    def save(self, snapshot: SessionSnapshot) -> None: ...
    def load(self, session_id: str) -> SessionSnapshot: ...
    def list_ids(self) -> list[str]: ...
    def delete(self, session_id: str) -> None: ...
```

## SDK Implementations

| Protocol | Implementation | Config |
|----------|---------------|--------|
| HotSessionStore | `RedisHotSessionStore` | `url`, `key_prefix`, `ttl_seconds` |
| DurableSessionStore | `PostgresDurableSessionStore` | `dsn` (psycopg) |
| SessionPersistence | `SQLitePersistence` | `db_path` |
| SessionPersistence | `FileSystemPersistence` | `base_dir` |
| SessionPersistence | `MemoryPersistence` | (in-memory, for tests) |
| SessionPersistence | `PostgresSessionSnapshotPersistence` | `dsn` (psycopg) |
| SessionLeaseStore | `RedisSessionLeaseStore` | `url`, `key_prefix`, `ttl_seconds per acquire` |
| TaskStore | `PostgresTaskStore` | `dsn` (psycopg) |
| AgentMessageQueue | `RedisAgentMessageQueue` | `url`, `key_prefix`, consumer group |

Important: `RedisHotSessionStore` and `PostgresDurableSessionStore` implement memory/session projection protocols. They are not currently drop-in `SessionPersistence` implementations for full `SessionSnapshot` save/load.

## Redis Key Schema

```
{prefix}:hot:{session_id}:state              → JSON (HotSessionState)
{prefix}:hot:{session_id}:messages           → HASH (message_id → JSON)
{prefix}:hot:{session_id}:segment_refs       → HASH (segment_id → JSON array)
{prefix}:hot:{session_id}:temporary_recalled_refs → JSON array (consumed atomically)
```

All keys share sliding TTL — refreshed on every read/write operation.

## Postgres Schema

```sql
-- From docs/migrations/2026-05-07-postgres-memory-backends.sql
CREATE TABLE agentos_sessions (
    session_id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'active',
    next_turn_number INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE agentos_messages (
    session_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (session_id, message_id)
);

CREATE TABLE agentos_active_refs (
    session_id TEXT PRIMARY KEY,
    refs JSONB NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE agentos_compressed_segments (
    session_id TEXT NOT NULL,
    segment_id TEXT NOT NULL,
    package JSONB NOT NULL,
    source_refs JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (session_id, segment_id)
);
```

Multi-agent task state uses a separate migration: `docs/migrations/2026-05-16-postgres-multi-agent-tasks.sql`.

Full session snapshots use `docs/migrations/2026-06-12-postgres-session-snapshots.sql`.

Source: `src/agentos/multi/postgres_tasks.py`, `src/agentos/multi/redis_queue.py`, `docs/migrations/2026-05-16-postgres-multi-agent-tasks.sql`.

## Manual Snapshot Restore Pattern

Today, restoring a full session is explicit: load `SessionSnapshot`, rebuild the runtime components, and pass restored collaborators into `AgentBuilder` or `QueryLoop`.

```python
from agentos import AgentBuilder
from agentos.compression import CompressionRuntime
from agentos.context import ContextRuntime
from agentos.persistence import MemoryPersistence
from agentos.policies import BudgetPolicy
from agentos.providers import AnthropicProvider

persistence = MemoryPersistence()
snapshot = persistence.load("session_1")

context = ContextRuntime(state=snapshot.context_state)
messages = snapshot.message_runtime
compression = CompressionRuntime(
    context_runtime=context,
    message_runtime=messages,
    budget_policy=BudgetPolicy(max_active_messages=20, retain_latest_messages=6),
    index=snapshot.compression_index,
    next_segment_number=snapshot.next_segment_number,
)

agent = (
    AgentBuilder()
    .provider(AnthropicProvider(api_key="...", model="claude-sonnet-4-6"))
    .context_runtime(context)
    .message_runtime(messages)
    .compression_runtime(compression)
    .build()
)
```

Source: `tests/runtime/test_session_recovery.py`, `tests/persistence/test_serializers.py`.

## Multi-Node Session Provider Pattern

For standard multi-node deployment, use `DistributedWebRuntimeProfile` with:

- `DurableAgentSessionProvider`
- `RedisSessionLeaseStore`
- `PostgresSessionSnapshotPersistence`
- `SnapshotAgentFactory`
- `DistributedWebSessionOperationsProfile`

The preset owns the hydrate/lease/run/save/release SDK wiring. The application still owns:

- session lock or lease
- snapshot/projection load
- Agent construction with restored runtime collaborators
- release-time snapshot/projection save
- failure policy for partial turns
- Redis/Postgres credentials and migrations
- tenant/auth policy
- workspace sandbox enforcement
- TTL and stale lease recovery tuning
- live backend verification

Use a custom `AgentSessionProvider` only when the standard preset cannot express
the lifecycle policy. Custom providers should preserve the same shape:

```python
from agentos.channels.session import AgentSessionProvider
from agentos.runtime import Agent


class CustomDurableSessionProvider:
    """Application-owned provider for non-standard lifecycle policy."""

    def __init__(self, lock_store, persistence, agent_factory):
        self._lock_store = lock_store
        self._persistence = persistence
        self._factory = agent_factory
        self._held_leases = {}

    def get_agent(self, session_id: str) -> Agent:
        lease = self._lock_store.acquire(session_id)
        self._held_leases[session_id] = lease
        try:
            snapshot = self._persistence.load(session_id)
        except KeyError:
            snapshot = None
        return self._factory(session_id, snapshot)

    def release_agent(self, session_id: str, agent: Agent) -> None:
        try:
            snapshot = build_snapshot_from_agent(session_id, agent)
            self._persistence.save(snapshot)
        finally:
            self._lock_store.release(self._held_leases.pop(session_id))
```

The SDK standard version is `DurableAgentSessionProvider`, assembled by
`DistributedWebRuntimeProfile`. App-specific providers should remain compatible
with `AgentSessionProvider` so they can still be injected into `WebRuntimeProfile`.

## MemoryRuntime (Recall Coordination)

```python
from agentos.memory import MemoryRuntime, RedisHotSessionStore
from agentos.persistence import PostgresDurableSessionStore
from agentos.memory.recall_index import RecallIndex

memory = MemoryRuntime(
    hot_store=RedisHotSessionStore(url="redis://..."),
    durable_store=PostgresDurableSessionStore(dsn="postgresql://..."),
    recall_index=RecallIndex(),  # in-memory keyword search
)

# After compression produces a package:
memory.record_compressed_segment(package)

# When model calls recall_context tool:
messages = memory.recall_by_handle(session_id, "seg_3")
messages = memory.recall_by_query(session_id, "database migration", limit=2)
```
