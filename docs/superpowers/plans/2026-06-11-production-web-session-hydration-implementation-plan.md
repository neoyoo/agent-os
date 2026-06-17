# Production Web Session Hydration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Phase 2A durable web session hydration so arbitrary web nodes can lock, hydrate, run, save, and release a session through a shared `SessionPersistence`.

**Architecture:** Add session lifecycle contracts under `agentos.channels` because channels own web request/session boundaries. `DurableAgentSessionProvider` uses injected `SessionPersistence`, `SessionLeaseStore`, and `SnapshotAgentFactory`; `AsgiAgentApp` detects async providers via `getattr` and keeps `QueryLoop` deployment-agnostic.

**Tech Stack:** Python 3.11 protocols/dataclasses, existing `SessionSnapshot`/`SessionPersistence`, ASGI app, pytest.

---

## Scope Contract

This plan implements only Phase 2A from `docs/superpowers/specs/2026-06-11-production-web-session-hydration-design.md`.

Target conclusion:

```text
Web session correctness comes from the provider lifecycle:
acquire lock/lease -> hydrate snapshot -> run turn -> save snapshot -> release lock.
RuntimeProfile assembles this provider; QueryLoop remains unaware of distributed deployment.
```

Deferred:

- Redis lock/session snapshot adapter.
- Postgres snapshot adapter.
- ASGI graceful drain.
- Workspace binding.
- A2A protocol upgrade.
- Team discussion and planner templates.

## File Structure

Create:

- `src/agentos/channels/durable_session.py`  
  `SessionLease`, `SessionLeaseError`, `SessionLeaseStore`, `InMemorySessionLeaseStore`, `SnapshotAgentFactory`, and `DurableAgentSessionProvider`.

- `tests/channels/test_durable_session_provider.py`  
  Provider and lease lifecycle tests.

Modify:

- `src/agentos/channels/session.py`  
  Add `AsyncAgentSessionProvider` protocol.

- `src/agentos/channels/asgi.py`  
  Add async JSON turn path and async session acquire/release helpers for JSON/SSE.

- `src/agentos/channels/__init__.py`  
  Export new public channel names.

- `src/agentos/__init__.py`  
  Export `DurableAgentSessionProvider` and lease/factory contracts at top level if existing channel exports are mirrored.

- `tests/channels/test_asgi_app_async.py`  
  Add async session provider tests for JSON and SSE paths.

- `tests/architecture/test_public_api.py`  
  Add public API assertions and keep import-boundary checks.

- `.claude/skills/agent-os/modules/persistence.md`  
  Document durable provider lifecycle and warn that `SessionPersistence` alone is not production web readiness.

- `.claude/skills/agent-os/modules/agent-forms.md`  
  Mark web distributed as primitives-ready until `DurableAgentSessionProvider` is wired with real distributed lease and persistence adapters.

Do not modify:

- `src/agentos/runtime/query_loop.py`
- `src/agentos/runtime/async_query_loop.py`
- `src/agentos/builder.py` unless a test proves a missing public seam

## Task 1: Add Session Lease Store

**Files:**
- Create: `src/agentos/channels/durable_session.py`
- Test: `tests/channels/test_durable_session_provider.py`

- [ ] **Step 1: Write failing lease tests**

Create `tests/channels/test_durable_session_provider.py` with:

```python
from __future__ import annotations

import pytest

from agentos.channels.durable_session import (
    InMemorySessionLeaseStore,
    SessionLeaseError,
)


def test_in_memory_session_lease_store_rejects_concurrent_acquire() -> None:
    store = InMemorySessionLeaseStore()
    first = store.acquire(
        "s1",
        owner_id="node-a",
        ttl_seconds=30.0,
        wait_timeout_seconds=0,
    )

    with pytest.raises(SessionLeaseError, match="session is locked"):
        store.acquire(
            "s1",
            owner_id="node-b",
            ttl_seconds=30.0,
            wait_timeout_seconds=0,
        )

    store.release(first)
    second = store.acquire(
        "s1",
        owner_id="node-b",
        ttl_seconds=30.0,
        wait_timeout_seconds=0,
    )

    assert second.owner_id == "node-b"
    assert second.token != first.token


def test_in_memory_session_lease_store_ignores_stale_release() -> None:
    store = InMemorySessionLeaseStore()
    first = store.acquire(
        "s1",
        owner_id="node-a",
        ttl_seconds=30.0,
        wait_timeout_seconds=0,
    )
    store.release(first)
    second = store.acquire(
        "s1",
        owner_id="node-b",
        ttl_seconds=30.0,
        wait_timeout_seconds=0,
    )

    store.release(first)

    with pytest.raises(SessionLeaseError, match="session is locked"):
        store.acquire(
            "s1",
            owner_id="node-c",
            ttl_seconds=30.0,
            wait_timeout_seconds=0,
        )
    store.release(second)
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest tests/channels/test_durable_session_provider.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'agentos.channels.durable_session'`.

- [ ] **Step 3: Implement lease contracts**

Create `src/agentos/channels/durable_session.py`:

```python
from __future__ import annotations

import time
from dataclasses import dataclass
from threading import RLock
from typing import Protocol
from uuid import uuid4


class SessionLeaseError(RuntimeError):
    """Raised when a session lease cannot be acquired or refreshed."""


@dataclass(frozen=True, slots=True)
class SessionLease:
    """Exclusive ownership token for one session."""

    session_id: str
    owner_id: str
    token: str
    expires_at: float | None = None


class SessionLeaseStore(Protocol):
    """Exclusive lease backend for session mutation."""

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
        """Release a lease if still owned by the token."""

    def refresh(self, lease: SessionLease) -> SessionLease:
        """Extend a lease if still owned by the token."""


class InMemorySessionLeaseStore:
    """In-process lease store for tests and single-node development."""

    def __init__(self) -> None:
        self._leases: dict[str, SessionLease] = {}
        self._lock = RLock()

    def acquire(
        self,
        session_id: str,
        *,
        owner_id: str,
        ttl_seconds: float,
        wait_timeout_seconds: float | None = None,
    ) -> SessionLease:
        deadline = (
            None
            if wait_timeout_seconds is None
            else time.monotonic() + wait_timeout_seconds
        )
        while True:
            with self._lock:
                self._drop_expired_locked(session_id)
                if session_id not in self._leases:
                    lease = SessionLease(
                        session_id=session_id,
                        owner_id=owner_id,
                        token=uuid4().hex,
                        expires_at=time.monotonic() + ttl_seconds,
                    )
                    self._leases[session_id] = lease
                    return lease
            if wait_timeout_seconds == 0:
                raise SessionLeaseError(f"session is locked: {session_id}")
            if deadline is not None and time.monotonic() >= deadline:
                raise SessionLeaseError(f"session is locked: {session_id}")
            time.sleep(0.01)

    def release(self, lease: SessionLease) -> None:
        with self._lock:
            current = self._leases.get(lease.session_id)
            if current is not None and current.token == lease.token:
                del self._leases[lease.session_id]

    def refresh(self, lease: SessionLease) -> SessionLease:
        with self._lock:
            current = self._leases.get(lease.session_id)
            if current is None or current.token != lease.token:
                raise SessionLeaseError(f"session lease is not owned: {lease.session_id}")
            ttl = (
                0.0
                if current.expires_at is None
                else max(0.0, current.expires_at - time.monotonic())
            )
            refreshed = SessionLease(
                session_id=current.session_id,
                owner_id=current.owner_id,
                token=current.token,
                expires_at=time.monotonic() + ttl,
            )
            self._leases[lease.session_id] = refreshed
            return refreshed

    def _drop_expired_locked(self, session_id: str) -> None:
        current = self._leases.get(session_id)
        if current is not None and current.expires_at is not None:
            if current.expires_at <= time.monotonic():
                del self._leases[session_id]
```

- [ ] **Step 4: Run lease tests**

Run:

```bash
uv run pytest tests/channels/test_durable_session_provider.py -q
```

Expected: PASS for the lease tests written so far.

## Task 2: Add DurableAgentSessionProvider

**Files:**
- Modify: `src/agentos/channels/durable_session.py`
- Test: `tests/channels/test_durable_session_provider.py`

- [ ] **Step 1: Append failing provider tests**

Append to `tests/channels/test_durable_session_provider.py`:

```python
from dataclasses import dataclass

from agentos import AgentBuilder
from agentos.compression import CompressionIndex
from agentos.context import ContextRuntime
from agentos.messages import MessageRuntime
from agentos.persistence import MemoryPersistence, SessionSnapshot
from agentos.providers import FakeProvider
from agentos.runtime import Agent, SessionState
from agentos.channels.durable_session import DurableAgentSessionProvider


@dataclass
class TestSnapshotFactory:
    responses: dict[str, list[str]]

    def create_agent(
        self,
        *,
        session_id: str,
        snapshot: SessionSnapshot | None,
    ) -> Agent:
        provider = FakeProvider(self.responses.setdefault(session_id, ["ok"]))
        context = ContextRuntime(
            state=snapshot.context_state if snapshot is not None else None,
            session_id=session_id,
        )
        messages = snapshot.message_runtime if snapshot is not None else MessageRuntime()
        return (
            AgentBuilder()
            .provider(provider)
            .context_runtime(context)
            .message_runtime(messages)
            .build()
        )

    def create_snapshot(self, *, session_id: str, agent: Agent) -> SessionSnapshot:
        query_loop = agent.query_loop
        return SessionSnapshot(
            session_state=query_loop.session_state or SessionState(id=session_id),
            context_state=query_loop.context_runtime.snapshot(),
            message_runtime=query_loop.message_runtime,
            compression_index=(
                query_loop.compression_runtime.index
                if query_loop.compression_runtime is not None
                else CompressionIndex()
            ),
            next_segment_number=(
                query_loop.compression_runtime.next_segment_number()
                if query_loop.compression_runtime is not None
                else 1
            ),
        )


def test_durable_session_provider_hydrates_from_shared_persistence() -> None:
    persistence = MemoryPersistence()
    lease_store = InMemorySessionLeaseStore()
    factory = TestSnapshotFactory(responses={"s1": ["node-a", "node-b"]})
    node_a = DurableAgentSessionProvider(
        agent_factory=factory,
        persistence=persistence,
        lease_store=lease_store,
        owner_id="node-a",
    )
    node_b = DurableAgentSessionProvider(
        agent_factory=factory,
        persistence=persistence,
        lease_store=lease_store,
        owner_id="node-b",
    )

    agent_a = node_a.get_agent("s1")
    assert agent_a.run("hello").content == "node-a"
    node_a.release_agent("s1", agent_a)

    agent_b = node_b.get_agent("s1")
    try:
        assert [m.content for m in agent_b.query_loop.message_runtime.store.all()] == [
            "hello",
            "node-a",
        ]
        assert agent_b.run("next").content == "node-b"
    finally:
        node_b.release_agent("s1", agent_b)


def test_durable_session_provider_releases_lease_when_save_fails() -> None:
    class FailingPersistence(MemoryPersistence):
        def save(self, snapshot: SessionSnapshot) -> None:
            raise RuntimeError("save failed")

    lease_store = InMemorySessionLeaseStore()
    provider = DurableAgentSessionProvider(
        agent_factory=TestSnapshotFactory(responses={"s1": ["ok"]}),
        persistence=FailingPersistence(),
        lease_store=lease_store,
        owner_id="node-a",
    )
    agent = provider.get_agent("s1")

    with pytest.raises(RuntimeError, match="save failed"):
        provider.release_agent("s1", agent)

    lease_store.acquire(
        "s1",
        owner_id="node-b",
        ttl_seconds=30.0,
        wait_timeout_seconds=0,
    )
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest tests/channels/test_durable_session_provider.py -q
```

Expected: FAIL with `ImportError` for `DurableAgentSessionProvider`.

- [ ] **Step 3: Implement provider and factory protocol**

Append to `src/agentos/channels/durable_session.py`:

```python
import asyncio
from agentos.channels.session import AgentSessionProvider
from agentos.persistence import SessionPersistence, SessionSnapshot
from agentos.runtime import Agent


class SnapshotAgentFactory(Protocol):
    """Build agents from snapshots and extract snapshots from agents."""

    def create_agent(
        self,
        *,
        session_id: str,
        snapshot: SessionSnapshot | None,
    ) -> Agent:
        """Build an agent from an optional snapshot."""

    def create_snapshot(self, *, session_id: str, agent: Agent) -> SessionSnapshot:
        """Extract a snapshot from an agent."""


class DurableAgentSessionProvider:
    """Session provider with lock, hydrate, save, and release lifecycle."""

    def __init__(
        self,
        *,
        agent_factory: SnapshotAgentFactory,
        persistence: SessionPersistence,
        lease_store: SessionLeaseStore,
        owner_id: str,
        lease_ttl_seconds: float = 60.0,
        acquire_timeout_seconds: float | None = 10.0,
    ) -> None:
        self._agent_factory = agent_factory
        self._persistence = persistence
        self._lease_store = lease_store
        self._owner_id = owner_id
        self._lease_ttl_seconds = lease_ttl_seconds
        self._acquire_timeout_seconds = acquire_timeout_seconds
        self._active_leases: dict[str, SessionLease] = {}
        self._active_lock = RLock()

    def get_agent(self, session_id: str) -> Agent:
        with self._active_lock:
            if session_id in self._active_leases:
                raise SessionLeaseError(f"session is already active: {session_id}")
        lease = self._lease_store.acquire(
            session_id,
            owner_id=self._owner_id,
            ttl_seconds=self._lease_ttl_seconds,
            wait_timeout_seconds=self._acquire_timeout_seconds,
        )
        try:
            try:
                snapshot = self._persistence.load(session_id)
            except KeyError:
                snapshot = None
            agent = self._agent_factory.create_agent(
                session_id=session_id,
                snapshot=snapshot,
            )
        except Exception:
            self._lease_store.release(lease)
            raise
        with self._active_lock:
            self._active_leases[session_id] = lease
        return agent

    def release_agent(self, session_id: str, agent: Agent) -> None:
        with self._active_lock:
            lease = self._active_leases.pop(session_id, None)
        if lease is None:
            raise SessionLeaseError(f"session is not active: {session_id}")
        try:
            snapshot = self._agent_factory.create_snapshot(
                session_id=session_id,
                agent=agent,
            )
            self._persistence.save(snapshot)
        finally:
            self._lease_store.release(lease)

    async def async_get_agent(self, session_id: str) -> Agent:
        return await asyncio.to_thread(self.get_agent, session_id)

    async def async_release_agent(self, session_id: str, agent: Agent) -> None:
        await asyncio.to_thread(self.release_agent, session_id, agent)

    def shutdown(self) -> None:
        with self._active_lock:
            leases = list(self._active_leases.values())
            self._active_leases.clear()
        for lease in leases:
            self._lease_store.release(lease)
```

- [ ] **Step 4: Run provider tests**

Run:

```bash
uv run pytest tests/channels/test_durable_session_provider.py -q
```

Expected: PASS.

## Task 3: Add Async Session Provider Protocol And Exports

**Files:**
- Modify: `src/agentos/channels/session.py`
- Modify: `src/agentos/channels/__init__.py`
- Modify: `src/agentos/__init__.py`
- Modify: `tests/architecture/test_public_api.py`

- [ ] **Step 1: Add failing public API tests**

In `tests/architecture/test_public_api.py`, extend `test_remote_registry_and_channel_public_api_exports` channel and agentos lists with:

```python
        "AsyncAgentSessionProvider",
        "DurableAgentSessionProvider",
        "InMemorySessionLeaseStore",
        "SessionLease",
        "SessionLeaseError",
        "SessionLeaseStore",
        "SnapshotAgentFactory",
```

- [ ] **Step 2: Run public API test to verify failure**

Run:

```bash
uv run pytest tests/architecture/test_public_api.py::test_remote_registry_and_channel_public_api_exports -q
```

Expected: FAIL because names are not exported.

- [ ] **Step 3: Add protocol**

Append to `src/agentos/channels/session.py` after `AgentSessionProvider`:

```python
class AsyncAgentSessionProvider(Protocol):
    """Async channel session provider extension."""

    async def async_get_agent(self, session_id: str) -> Agent:
        """Return an agent for a session without blocking the event loop."""

    async def async_release_agent(self, session_id: str, agent: Agent) -> None:
        """Release an agent after an async channel turn."""
```

- [ ] **Step 4: Export channel names**

Modify `src/agentos/channels/__init__.py` to import from `durable_session` and include all new names in `__all__`.

Modify `src/agentos/__init__.py` to mirror the same names if they are intended top-level API.

- [ ] **Step 5: Run public API tests**

Run:

```bash
uv run pytest tests/architecture/test_public_api.py -q
```

Expected: PASS.

## Task 4: Add ASGI Async JSON Turn Path

**Files:**
- Modify: `src/agentos/channels/asgi.py`
- Test: `tests/channels/test_asgi_app_async.py`

- [ ] **Step 1: Add failing ASGI JSON async provider test**

Append to `tests/channels/test_asgi_app_async.py`:

```python
def test_json_turn_uses_async_session_provider_when_available() -> None:
    class AsyncSessionProvider:
        def __init__(self) -> None:
            self.agent = AsyncOnlyAgent("json ok")
            self.async_get_calls = 0
            self.async_release_calls = 0

        async def async_get_agent(self, session_id: str) -> AsyncOnlyAgent:
            self.async_get_calls += 1
            return self.agent

        async def async_release_agent(
            self,
            session_id: str,
            agent: AsyncOnlyAgent,
        ) -> None:
            self.async_release_calls += 1

        def get_agent(self, session_id: str) -> AsyncOnlyAgent:
            raise AssertionError("sync get_agent should not be called")

        def release_agent(self, session_id: str, agent: AsyncOnlyAgent) -> None:
            raise AssertionError("sync release_agent should not be called")

    provider = AsyncSessionProvider()
    app = AsgiAgentApp(sessions=provider)  # type: ignore[arg-type]

    async def run() -> list[dict[str, Any]]:
        return await call_asgi(app, body=b'{"message":"hello"}')

    sent = asyncio.run(run())

    assert response_status(sent) == 200
    assert json.loads(response_body(sent))["content"] == "json ok"
    assert provider.async_get_calls == 1
    assert provider.async_release_calls == 1
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
uv run pytest tests/channels/test_asgi_app_async.py::test_json_turn_uses_async_session_provider_when_available -q
```

Expected: FAIL because `AsgiAgentApp` still routes JSON turns through `HttpAgentChannel`.

- [ ] **Step 3: Implement async JSON helper**

Modify `src/agentos/channels/asgi.py`:

```python
    async def _handle_json_turn(
        self,
        session_id: str,
        body: bytes,
        send: AsgiSend,
    ) -> None:
        async_get_agent = getattr(self._sessions, "async_get_agent", None)
        async_release_agent = getattr(self._sessions, "async_release_agent", None)
        if callable(async_get_agent) and callable(async_release_agent):
            try:
                request = parse_channel_turn_request(body)
            except ValueError as error:
                await self._send_json(
                    send,
                    400,
                    {"session_id": session_id, "status": "failed", "error": str(error)},
                )
                return
            agent = await async_get_agent(session_id)
            try:
                result = await agent.async_run(
                    request.message,
                    thinking=request.thinking,
                    show_thinking=request.show_thinking,
                )
                await self._send_json(
                    send,
                    200,
                    {
                        "session_id": session_id,
                        "status": "completed",
                        "content": result.content,
                        "error": None,
                    },
                )
                return
            except Exception as error:
                await self._send_json(
                    send,
                    500,
                    {
                        "session_id": session_id,
                        "status": "failed",
                        "content": None,
                        "error": str(error),
                    },
                )
                return
            finally:
                await async_release_agent(session_id, agent)

        result = await asyncio.to_thread(self._http.handle_turn, session_id, body)
        await self._send_json(
            send,
            result.status_code,
            {
                "session_id": result.session_id,
                "status": result.status,
                "content": result.content,
                "error": result.error,
            },
        )
```

Replace the existing JSON turn block in `__call__` with:

```python
        await self._handle_json_turn(session_id, body, send)
```

- [ ] **Step 4: Run ASGI async JSON test**

Run:

```bash
uv run pytest tests/channels/test_asgi_app_async.py::test_json_turn_uses_async_session_provider_when_available -q
```

Expected: PASS.

## Task 5: Add ASGI Async SSE Acquire/Release

**Files:**
- Modify: `src/agentos/channels/asgi.py`
- Test: `tests/channels/test_asgi_app_async.py`

- [ ] **Step 1: Add failing SSE async provider test**

Append to `tests/channels/test_asgi_app_async.py`:

```python
def test_sse_turn_uses_async_session_provider_when_available() -> None:
    class AsyncSessionProvider:
        def __init__(self) -> None:
            self.agent = AsyncStreamAgent("sse ok")
            self.async_get_calls = 0
            self.async_release_calls = 0

        async def async_get_agent(self, session_id: str) -> AsyncStreamAgent:
            self.async_get_calls += 1
            return self.agent

        async def async_release_agent(
            self,
            session_id: str,
            agent: AsyncStreamAgent,
        ) -> None:
            self.async_release_calls += 1

        def get_agent(self, session_id: str) -> AsyncStreamAgent:
            raise AssertionError("sync get_agent should not be called")

        def release_agent(self, session_id: str, agent: AsyncStreamAgent) -> None:
            raise AssertionError("sync release_agent should not be called")

    provider = AsyncSessionProvider()
    app = AsgiAgentApp(
        sessions=provider,  # type: ignore[arg-type]
        sse_terminal_retention_seconds=0,
    )

    sent = asyncio.run(call_asgi(app))

    assert response_status(sent) == 200
    assert b"event: done" in response_body(sent)
    assert provider.async_get_calls == 1
    assert provider.async_release_calls == 1
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
uv run pytest tests/channels/test_asgi_app_async.py::test_sse_turn_uses_async_session_provider_when_available -q
```

Expected: FAIL because `_create_sse_turn_entry` and `_release_sse_entry` use sync provider methods.

- [ ] **Step 3: Implement async acquire/release helpers**

Modify `src/agentos/channels/asgi.py`:

```python
    async def _get_agent_for_session(self, session_id: str) -> object:
        async_get_agent = getattr(self._sessions, "async_get_agent", None)
        if callable(async_get_agent):
            return await async_get_agent(session_id)
        return self._sessions.get_agent(session_id)

    async def _release_agent_for_session(self, session_id: str, agent: object) -> None:
        async_release_agent = getattr(self._sessions, "async_release_agent", None)
        if callable(async_release_agent):
            await async_release_agent(session_id, agent)
            return
        self._sessions.release_agent(session_id, agent)
```

In `_create_sse_turn_entry`, replace:

```python
            agent = self._sessions.get_agent(session_id)
```

with:

```python
            agent = await self._get_agent_for_session(session_id)
```

Change `_release_sse_entry` from sync to async:

```python
    async def _release_sse_entry(self, entry: SseTurnEntry) -> None:
        if entry.released:
            return
        entry.released = True
        await self._release_agent_for_session(entry.session_id, entry.agent)
```

Await both call sites:

```python
await self._release_sse_entry(entry)
```

- [ ] **Step 4: Run ASGI SSE async test**

Run:

```bash
uv run pytest tests/channels/test_asgi_app_async.py::test_sse_turn_uses_async_session_provider_when_available -q
```

Expected: PASS.

## Task 6: Documentation Alignment

**Files:**
- Modify: `.claude/skills/agent-os/modules/persistence.md`
- Modify: `.claude/skills/agent-os/modules/agent-forms.md`

- [ ] **Step 1: Update persistence docs**

Add a `Durable Web Session Provider` subsection that states:

```markdown
Production web sessions require a provider lifecycle:
lock/lease -> load SessionSnapshot -> run turn -> save SessionSnapshot -> release.

Use `DurableAgentSessionProvider` with a `SessionPersistence` and `SessionLeaseStore`.
`SessionPersistence` alone is not multi-node-ready because it does not prevent concurrent same-session writes.
```

- [ ] **Step 2: Update agent forms docs**

In Web Distributed Agent, change missing line to:

```markdown
**Missing**: Redis/Postgres production lease/store adapters and workspace policy; `DurableAgentSessionProvider` provides the SDK lifecycle boundary.
```

- [ ] **Step 3: Run drift search**

Run:

```bash
rg "SessionPersistence alone|multi-node-ready|WebRuntimeProfile alone|RedisHotSessionStore.*drop-in" .claude docs src tests
```

Expected: only intentional warnings, no claim that persistence alone is production web readiness.

## Task 7: Verification

**Files:** all changed files

- [ ] **Step 1: Run targeted tests**

Run:

```bash
uv run pytest tests/channels/test_durable_session_provider.py tests/channels/test_asgi_app_async.py tests/architecture/test_public_api.py -q
```

Expected: PASS.

- [ ] **Step 2: Run compileall**

Run:

```bash
uv run python -m compileall -q src tests
```

Expected: no output and exit code 0.

- [ ] **Step 3: Run full suite**

Run:

```bash
uv run pytest -q
```

Expected: all tests pass.

- [ ] **Step 4: Run final diff check**

Run:

```bash
git diff --check
```

Expected: no whitespace errors. Windows line-ending warnings are acceptable if no error lines are printed.

- [ ] **Step 5: Boundary search**

Run:

```bash
rg "agentos.channels|agentos.persistence|runtime.profile|Redis|Postgres" src/agentos/runtime/query_loop.py src/agentos/runtime/async_query_loop.py
```

Expected: no matches.

## Self-Review

Spec coverage:

- Cross-node durable session lifecycle: Tasks 1 and 2.
- Lock/lease boundary: Task 1.
- Snapshot hydrate/save: Task 2.
- ASGI async provider path: Tasks 4 and 5.
- Public API: Task 3.
- Docs alignment: Task 6.
- QueryLoop agnostic invariant: Task 7.

Placeholder scan:

- No implementation step contains red-flag placeholders or unspecified tests.

Type consistency:

- Names match the Phase 2 design spec: `AsyncAgentSessionProvider`, `SessionLease`, `SessionLeaseStore`, `InMemorySessionLeaseStore`, `SnapshotAgentFactory`, `DurableAgentSessionProvider`.
