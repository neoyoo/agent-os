---
name: agent-os-persistence
description: Reference for multi-node state management — Redis hot store, Postgres durable store, session snapshot lifecycle
---

# Persistence & Multi-Node State

## Architecture

```
Request → Load from Redis (hot) → Build Agent → Run Turn → Save to Redis → Response
                                                    ↘ Async: persist segment to Postgres (durable)
```

Two-tier design:
- **Hot Store (Redis)** — active session working set, fast read/write, TTL-based expiry
- **Durable Store (Postgres)** — permanent truth source, compressed segments, message archive

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
| TaskStore | `PostgresTaskStore` | `dsn` (psycopg) |
| AgentMessageQueue | `RedisAgentMessageQueue` | `url`, `key_prefix`, consumer group |

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

Source: `src/agentos/multi/postgres_tasks.py`, `src/agentos/multi/redis_queue.py`, `docs/migrations/2026-05-16-postgres-multi-agent-tasks.sql`.

## Stateless Session Provider Pattern

For multi-node deployment, implement `AgentSessionProvider` that loads/saves per request:

```python
from agentos.channels.session import AgentSessionProvider
from agentos.memory import RedisHotSessionStore
from agentos.runtime import Agent


class StatelessSessionProvider:
    """Each get_agent() loads state from Redis; release_agent() saves back."""

    def __init__(self, hot_store: RedisHotSessionStore, agent_factory):
        self._hot_store = hot_store
        self._factory = agent_factory

    def get_agent(self, session_id: str) -> Agent:
        hot_state = self._hot_store.load_hot_state(session_id)
        agent = self._factory(session_id)
        if hot_state is not None:
            # Restore messages and context from hot state
            agent.query_loop.message_runtime.restore_from_refs(hot_state.active_refs)
        return agent

    def release_agent(self, session_id: str, agent: Agent) -> None:
        # Save current state back to Redis
        state = agent.query_loop.message_runtime.export_hot_state(session_id)
        self._hot_store.save_hot_state(state)
```

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
