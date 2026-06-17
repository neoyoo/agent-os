# Production Web Session Hydration Design (Phase 2)

> Status: draft  
> Date: 2026-06-11  
> Parent roadmap: `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`  
> Previous phase: `docs/superpowers/specs/2026-06-11-runtime-profile-design.md`

## Scope Contract

This design belongs to Phase 2: production web session hydration.

Target conclusion:

```text
For web deployment, the session is the truth boundary for runtime state.
Any node that receives a request for a session must follow:
acquire lock/lease -> hydrate snapshot -> run turn -> save snapshot -> release lock.
RuntimeProfile assembles that lifecycle; QueryLoop stays deployment-agnostic.
```

This phase completes:

- Define a production-grade session provider lifecycle for arbitrary-node web traffic.
- Define lock/lease contracts required to prevent same-session concurrent writes.
- Define snapshot hydrate/save behavior using existing `SessionSnapshot` and `SessionPersistence`.
- Define ASGI integration so JSON and SSE turns can use async session providers without blocking the event loop.
- Define failure policy and readiness metadata.
- Define the first implementation slice, Phase 2A, that uses in-memory test locks plus existing `SessionPersistence`.

This phase does not complete:

- Redis lock/store implementation.
- Postgres snapshot table implementation.
- Workspace sandboxing.
- Full A2A protocol.
- Team discussion runtime.
- Planner/subagent templates.

Those are later phases. This phase creates the correct production boundary first.

## Current Evidence

Current code already has real primitives:

| Area | Evidence | Readiness |
|------|----------|-----------|
| Web channel | `src/agentos/channels/asgi.py`, `HttpAgentChannel` | Single-process direct |
| Session provider seam | `AgentSessionProvider`, `InMemoryAgentSessionProvider` | Sync-only, single-process |
| Snapshot model | `SessionSnapshot` | Direct |
| Snapshot serialization | `persistence/serializers.py` | Direct |
| Snapshot stores | `MemoryPersistence`, `FileSystemPersistence`, `SQLitePersistence` | Direct |
| Runtime profile | `WebRuntimeProfile` | Assembly exists |
| Full durable provider | none | Missing |
| Distributed lock/lease | none | Missing |
| ASGI async session path | none | Missing |

Important current behavior:

- `HttpAgentChannel.handle_turn()` calls `get_agent()`, runs `Agent.run()`, then calls `release_agent()`.
- `AsgiAgentApp` wraps JSON turns with `asyncio.to_thread(self._http.handle_turn, ...)`.
- SSE turn creation calls `self._sessions.get_agent(session_id)` directly and releases on terminal GC.
- `SessionSnapshot` includes `SessionState`, `ContextState`, `MessageRuntime`, `CompressionIndex`, `next_segment_number`, and event records.
- `AgentBuilder` can build sync/async agents, but does not yet expose a first-class snapshot hydration helper.

## External Baseline

AgentScope 2.0 Agent Service treats service hosting as more than HTTP wrapping. Its service layer owns request routing, per-user resources, session state, persistence, scheduling, and tool offloading. It states that distributed deployment puts shared state in Redis storage plus message bus so multiple workers or nodes can serve one logical service. Its minimal embedded service requires storage, message bus, and workspace manager; the Redis message bus includes session locks, replay logs, inbox queues, and wakeup signals. It also models Session as the unit carrying agent state, message transcript, and runtime config, and its `ChatService` takes the bus session lock before driving the reply stream.

Sources:

- https://docs.agentscope.io/v2/deploy/agent-service
- https://docs.agentscope.io/v2/deploy/agent-team

For later A2A phases, A2A requires Agent Cards, well-known discovery, registries/direct configuration, capabilities, skills, authentication, and optional signing. That confirms the current `/a2a/tasks` endpoint must remain labeled as an internal bridge until Phase 4.

Sources:

- https://a2a-protocol.org/latest/topics/agent-discovery/
- https://a2a-protocol.org/latest/specification/

## Design Principles

1. Session lifecycle is a channel/provider concern, not a `QueryLoop` concern.
2. The durable provider must hydrate in the lock/lease window, not from an unsafe default local cache.
3. The provider must save after every turn, including failed turns unless a rollback policy is explicitly configured.
4. A production provider requires a lock/lease backend; persistence alone is not production-ready.
5. `SessionSnapshot` is the coarse-grained recovery unit for this phase.
6. Hot Redis state and durable Postgres projections are different shapes; neither should be silently treated as `SessionSnapshot` unless an adapter implements `SessionPersistence`.
7. ASGI should call async session provider methods when available and avoid blocking the event loop.
8. Workspace, auth, tenant isolation, A2A compliance, and team wakeup remain separate boundaries.

## Architecture Decision

Recommended approach:

```text
Build a DurableAgentSessionProvider around injected snapshot and lock protocols.
Do not make Redis/Postgres mandatory in the first implementation.
```

Why:

- The SDK already has `SessionPersistence`; the missing production boundary is lifecycle orchestration and locking.
- Redis/Postgres adapters can be added later behind the same protocols.
- The provider can be tested deterministically with `MemoryPersistence` and an in-memory lease store.
- `WebRuntimeProfile` can immediately distinguish `single-process` vs `durable-session` without changing `QueryLoop`.

Rejected approach:

```text
Treat RedisHotSessionStore or PostgresDurableSessionStore as production web sessions directly.
```

Reason: they are current projections, not a standard `lock -> hydrate -> run -> save -> release` lifecycle. Using them directly would preserve the exact ambiguity Phase 2 is meant to remove.

Rejected approach:

```text
Default to local LRU/TTL cache before persistence.
```

Reason: arbitrary-node routing requires shared truth. A node-local cache is safe only with sticky sessions, explicit invalidation, or very short read-only optimizations. It must not be the production default.

## Core Contracts

### AsyncAgentSessionProvider

```python
class AsyncAgentSessionProvider(Protocol):
    """Async extension for web session providers."""

    async def async_get_agent(self, session_id: str) -> Agent:
        """Acquire and hydrate an agent for a session."""

    async def async_release_agent(self, session_id: str, agent: Agent) -> None:
        """Save and release the agent after a turn."""
```

The existing `AgentSessionProvider` remains unchanged. ASGI checks for `async_get_agent` with `getattr`, not `isinstance`, so existing sync providers remain compatible.

### SessionLease

```python
@dataclass(frozen=True, slots=True)
class SessionLease:
    session_id: str
    owner_id: str
    token: str
    expires_at: float | None = None
```

The token is an opaque fencing value returned by the lease store. A Redis or database implementation can use it to prevent stale releases or stale writes.

### SessionLeaseStore

```python
class SessionLeaseStore(Protocol):
    def acquire(
        self,
        session_id: str,
        *,
        owner_id: str,
        ttl_seconds: float,
        wait_timeout_seconds: float | None = None,
    ) -> SessionLease:
        """Acquire exclusive ownership for one session."""

    def release(self, lease: SessionLease) -> None:
        """Release a lease if the token still owns it."""

    def refresh(self, lease: SessionLease) -> SessionLease:
        """Extend the lease if the token still owns it."""
```

Phase 2A should include `InMemorySessionLeaseStore` for deterministic contract tests. Redis/Postgres lease stores are later adapter work.

### SnapshotAgentFactory

```python
class SnapshotAgentFactory(Protocol):
    def create_agent(
        self,
        *,
        session_id: str,
        snapshot: SessionSnapshot | None,
    ) -> Agent:
        """Build a new Agent from an optional snapshot."""

    def create_snapshot(
        self,
        *,
        session_id: str,
        agent: Agent,
    ) -> SessionSnapshot:
        """Extract a snapshot from a completed agent turn."""
```

The first implementation may accept two callbacks instead of a concrete class, but the public meaning should match this protocol.

Reason: the SDK cannot reconstruct a provider, tool router, security policy, or app-owned capabilities from a snapshot alone. The app or builder helper must supply those dependencies.

### DurableAgentSessionProvider

```python
class DurableAgentSessionProvider:
    def __init__(
        self,
        *,
        agent_factory: SnapshotAgentFactory,
        persistence: SessionPersistence,
        lease_store: SessionLeaseStore,
        owner_id: str,
        lease_ttl_seconds: float = 60.0,
        acquire_timeout_seconds: float | None = 10.0,
        save_on_failure: bool = True,
    ) -> None: ...

    def get_agent(self, session_id: str) -> Agent: ...
    def release_agent(self, session_id: str, agent: Agent) -> None: ...
    async def async_get_agent(self, session_id: str) -> Agent: ...
    async def async_release_agent(self, session_id: str, agent: Agent) -> None: ...
    def shutdown(self) -> None: ...
```

Lifecycle:

```text
get_agent(session_id)
  -> lease_store.acquire(session_id)
  -> persistence.load(session_id)
       hit: factory.create_agent(snapshot=loaded)
       miss: factory.create_agent(snapshot=None)
  -> remember active lease for this session on this provider instance
  -> return agent

release_agent(session_id, agent)
  -> factory.create_snapshot(agent)
  -> persistence.save(snapshot)
  -> lease_store.release(lease)
```

Rules:

- `get_agent()` must fail if the same provider already holds an active lease for the session.
- `release_agent()` must release the lease in `finally` even if snapshot save fails.
- If save fails, the error should propagate so the channel can fail the request and operators see the issue.
- `shutdown()` should release any active leases and may optionally save active agents if the provider can safely snapshot them.
- The default implementation should not keep a reusable local agent cache.

## ASGI Integration

Current JSON turn path delegates to `HttpAgentChannel` inside `asyncio.to_thread`. That is acceptable for sync providers, but async session providers should not be forced through that sync channel.

Phase 2 should add an async JSON turn path in `AsgiAgentApp`:

```text
if sessions has async_get_agent/async_release_agent:
    parse request
    agent = await async_get_agent(session_id)
    try:
        result = await agent.async_run(...)
    finally:
        await async_release_agent(session_id, agent)
else:
    keep existing to_thread(HttpAgentChannel.handle_turn)
```

SSE should also use async session methods when available:

```text
_create_sse_turn_entry
  -> async_get_agent if available

_release_sse_entry
  -> async_release_agent if available
```

The release timing for SSE remains tied to terminal/GC in Phase 2A. A stronger production design may later release immediately after the runner completes while retaining replay buffer separately.

## Failure Policy

The default Phase 2A policy is:

```text
save whatever runtime state exists at release time, then release the lease.
```

This preserves user messages and failure events when the turn already mutated runtime state. It also avoids silent rollback. A future policy can add rollback-to-pre-turn snapshot if product requirements demand it.

Acceptance tests must cover provider failure and tool failure once the provider is implemented. If snapshot save fails, the request must fail rather than pretending the turn is durable.

## Readiness Metadata

`WebRuntimeProfile` already exposes `session_lifecycle`. Phase 2 should tighten this:

| Provider | session_lifecycle |
|----------|-------------------|
| `InMemoryAgentSessionProvider` | `single-process` |
| `DurableAgentSessionProvider` with lease store + persistence | `durable-session` |
| App-owned provider without known lock support | explicit app-owned metadata, not auto durable |

Docs must keep saying: `WebRuntimeProfile` is assembly. Durable semantics come from the provider it injects.

## Phase 2A Implementation Slice

Phase 2A should implement only:

- `AsyncAgentSessionProvider` protocol.
- `SessionLease`, `SessionLeaseStore`, `InMemorySessionLeaseStore`.
- `SnapshotAgentFactory` protocol.
- `DurableAgentSessionProvider` using existing `SessionPersistence`.
- ASGI async provider path for JSON turns.
- ASGI async provider path for SSE acquire/release.
- Tests proving two provider instances can hand off the same session through shared persistence.
- Tests proving same-session concurrent acquire is rejected or waits according to timeout.

Phase 2A should not implement Redis/Postgres stores. It should make those next adapters small.

## Acceptance Criteria

### AC-2A-1: Cross-node handoff

```text
Given node_a and node_b DurableAgentSessionProvider instances
And both share the same SessionPersistence and SessionLeaseStore
When node_a runs and releases session "s1"
And node_b later gets session "s1"
Then node_b hydrates the messages, context state, compression index, and session turn counter saved by node_a.
```

### AC-2A-2: Same-session write protection

```text
Given node_a holds a lease for session "s1"
When node_b tries to get session "s1" with wait_timeout_seconds=0
Then node_b receives a SessionLeaseError
And no second agent is created for the same session.
```

### AC-2A-3: Lock released on turn failure

```text
Given a turn raises after get_agent
When release_agent is called in finally
Then the lease is released
And a later request can acquire the same session.
```

### AC-2A-4: Snapshot save failure is visible

```text
Given persistence.save raises
When release_agent is called
Then the exception propagates
And the lease is still released in finally.
```

### AC-2A-5: ASGI JSON turn uses async session provider

```text
Given a provider implementing async_get_agent and async_release_agent
When POST /v1/sessions/{id}/turns is called
Then AsgiAgentApp calls async_get_agent and async_release_agent
And does not route through HttpAgentChannel.handle_turn.
```

### AC-2A-6: ASGI SSE turn uses async session provider

```text
Given a provider implementing async_get_agent and async_release_agent
When POST /v1/sessions/{id}/turns/stream is called
Then AsgiAgentApp uses async_get_agent to start the runner
And async_release_agent is called when the entry is released.
```

### AC-2A-7: QueryLoop remains deployment-agnostic

```text
No imports from query_loop.py or async_query_loop.py to channels, persistence adapters, Redis, Postgres, or runtime.profile.
```

## Later Phase 2B/2C Work

After Phase 2A:

- Redis lease store and Redis snapshot `SessionPersistence` adapter.
- Postgres snapshot `SessionPersistence` adapter, opt-in and clearly separated from existing fine-grained durable projections.
- `AgentBuilder` or helper factory for snapshot hydration to reduce app glue.
- ASGI graceful drain for in-flight turns.
- SSE release timing review: separate agent lease lifetime from replay buffer lifetime.
- Workspace binding integration: session hydrate should return workspace boundary alongside runtime state.

## Non-Goals

- Do not introduce local/web/distributed branches into `QueryLoop`.
- Do not claim Redis hot state equals full `SessionSnapshot`.
- Do not claim `WebRuntimeProfile` alone makes a service multi-node-ready.
- Do not make local LRU cache the default correctness path.
- Do not add a scheduler, team wakeup, or background tool completion in this phase.
- Do not implement A2A Agent Card discovery in this phase.

## Completion Checklist

Before Phase 2A can be called complete:

| Requirement | Evidence |
|-------------|----------|
| Durable provider contract exists | source + public API tests |
| Cross-node hydrate/save works | targeted provider tests |
| Same-session concurrent write protected | lease tests |
| Save failure visible and lease released | provider failure tests |
| ASGI JSON async path works | channel tests |
| ASGI SSE async path works | channel tests |
| Web docs distinguish assembly from durable lifecycle | skill docs diff |
| QueryLoop agnostic | import-boundary tests |
| Existing suite passes | targeted tests, compileall, full pytest, diff check |
