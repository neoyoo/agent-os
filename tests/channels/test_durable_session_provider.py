from __future__ import annotations

from dataclasses import dataclass

import pytest

from agentos import AgentBuilder
from agentos.channels.durable_session import (
    DurableAgentSessionProvider,
    InMemorySessionLeaseStore,
    SessionLeaseError,
)
from agentos.compression import CompressionIndex
from agentos.context import ContextRuntime
from agentos.messages import MessageRuntime
from agentos.persistence import (
    MemoryPersistence,
    SessionSnapshot,
    SessionSnapshotRecord,
    SnapshotConflictError,
)
from agentos.providers import FakeProvider
from agentos.runtime import Agent, SessionState


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


@dataclass
class SnapshotTestFactory:
    responses: dict[str, list[str]]

    def create_agent(
        self,
        *,
        session_id: str,
        snapshot: SessionSnapshot | None,
    ) -> Agent:
        provider = FakeProvider(self.responses.setdefault(session_id, ["ok"]))
        if snapshot is None:
            context = ContextRuntime(session_id=session_id)
        else:
            context = ContextRuntime(
                state=snapshot.context_state,
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
    factory = SnapshotTestFactory(responses={"s1": ["node-a", "node-b"]})
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
        agent_factory=SnapshotTestFactory(responses={"s1": ["ok"]}),
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


class RecordingLeaseStore(InMemorySessionLeaseStore):
    def __init__(self) -> None:
        super().__init__()
        self.refresh_calls: list[str] = []

    def refresh(self, lease):
        self.refresh_calls.append(lease.session_id)
        return super().refresh(lease)


def test_durable_session_provider_refreshes_active_session_lease() -> None:
    lease_store = RecordingLeaseStore()
    provider = DurableAgentSessionProvider(
        agent_factory=SnapshotTestFactory(responses={"s1": ["ok"]}),
        persistence=MemoryPersistence(),
        lease_store=lease_store,
        owner_id="node-a",
    )

    agent = provider.get_agent("s1")
    provider.refresh_agent("s1")
    provider.release_agent("s1", agent)

    assert lease_store.refresh_calls == ["s1"]


def test_in_memory_session_lease_store_refresh_extends_original_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agentos.channels.durable_session as durable_session

    now = 100.0
    monkeypatch.setattr(durable_session.time, "monotonic", lambda: now)
    store = InMemorySessionLeaseStore()
    lease = store.acquire(
        "s1",
        owner_id="node-a",
        ttl_seconds=10.0,
        wait_timeout_seconds=0,
    )
    assert lease.expires_at == 110.0

    now = 105.0
    refreshed = store.refresh(lease)

    assert refreshed.expires_at == 115.0


def test_durable_session_provider_rejects_refresh_for_inactive_session() -> None:
    provider = DurableAgentSessionProvider(
        agent_factory=SnapshotTestFactory(responses={"s1": ["ok"]}),
        persistence=MemoryPersistence(),
        lease_store=InMemorySessionLeaseStore(),
        owner_id="node-a",
    )

    with pytest.raises(SessionLeaseError, match="session is not active"):
        provider.refresh_agent("s1")


class CasMemoryPersistence(MemoryPersistence):
    def __init__(self) -> None:
        super().__init__()
        self.revisions: dict[str, int] = {}
        self.save_if_unchanged_calls: list[tuple[str, int]] = []

    def load_record(self, session_id: str) -> SessionSnapshotRecord:
        snapshot = self.load(session_id)
        return SessionSnapshotRecord(
            snapshot=snapshot,
            revision=self.revisions[session_id],
        )

    def save_if_unchanged(
        self,
        snapshot: SessionSnapshot,
        *,
        expected_revision: int,
    ) -> SessionSnapshotRecord:
        session_id = snapshot.session_state.id
        self.save_if_unchanged_calls.append((session_id, expected_revision))
        current_revision = self.revisions.get(session_id, 0)
        if current_revision != expected_revision:
            raise SnapshotConflictError(
                f"snapshot revision conflict: {session_id}",
            )
        super().save(snapshot)
        revision = current_revision + 1
        self.revisions[session_id] = revision
        return SessionSnapshotRecord(snapshot=snapshot, revision=revision)

    def save(self, snapshot: SessionSnapshot) -> None:
        super().save(snapshot)
        self.revisions[snapshot.session_state.id] = (
            self.revisions.get(snapshot.session_state.id, 0) + 1
        )


def test_durable_session_provider_uses_snapshot_cas_when_supported() -> None:
    persistence = CasMemoryPersistence()
    lease_store = InMemorySessionLeaseStore()
    provider = DurableAgentSessionProvider(
        agent_factory=SnapshotTestFactory(responses={"s1": ["ok"]}),
        persistence=persistence,
        lease_store=lease_store,
        owner_id="node-a",
    )

    agent = provider.get_agent("s1")
    agent.run("hello")
    provider.release_agent("s1", agent)

    assert persistence.save_if_unchanged_calls == [("s1", 0)]


def test_durable_session_provider_rejects_stale_snapshot_before_release() -> None:
    persistence = CasMemoryPersistence()
    persistence.save(
        SnapshotTestFactory(responses={"s1": ["seed"]}).create_snapshot(
            session_id="s1",
            agent=(
                AgentBuilder()
                .provider(FakeProvider(["seed"]))
                .context_runtime(ContextRuntime(session_id="s1"))
                .message_runtime(MessageRuntime())
                .build()
            ),
        ),
    )
    lease_store = InMemorySessionLeaseStore()
    provider = DurableAgentSessionProvider(
        agent_factory=SnapshotTestFactory(responses={"s1": ["node-a"]}),
        persistence=persistence,
        lease_store=lease_store,
        owner_id="node-a",
    )
    agent = provider.get_agent("s1")
    agent.run("hello")
    persistence.save(
        SnapshotTestFactory(responses={"s1": ["external"]}).create_snapshot(
            session_id="s1",
            agent=(
                AgentBuilder()
                .provider(FakeProvider(["external"]))
                .context_runtime(ContextRuntime(session_id="s1"))
                .message_runtime(MessageRuntime())
                .build()
            ),
        ),
    )

    with pytest.raises(SnapshotConflictError, match="snapshot revision conflict"):
        provider.release_agent("s1", agent)

    lease_store.acquire(
        "s1",
        owner_id="node-b",
        ttl_seconds=30.0,
        wait_timeout_seconds=0,
    )


def test_durable_session_provider_fences_snapshot_save_with_live_lease_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agentos.channels.durable_session as durable_session

    now = 100.0
    monkeypatch.setattr(durable_session.time, "monotonic", lambda: now)
    persistence = MemoryPersistence()
    lease_store = InMemorySessionLeaseStore()
    node_a = DurableAgentSessionProvider(
        agent_factory=SnapshotTestFactory(responses={"s1": ["node-a"]}),
        persistence=persistence,
        lease_store=lease_store,
        owner_id="node-a",
        lease_ttl_seconds=1.0,
        acquire_timeout_seconds=0,
    )

    agent_a = node_a.get_agent("s1")
    agent_a.run("hello")

    now = 102.0
    node_b_lease = lease_store.acquire(
        "s1",
        owner_id="node-b",
        ttl_seconds=30.0,
        wait_timeout_seconds=0,
    )

    with pytest.raises(SessionLeaseError, match="session lease is not owned"):
        node_a.release_agent("s1", agent_a)

    with pytest.raises(KeyError):
        persistence.load("s1")

    lease_store.release(node_b_lease)


def test_durable_session_provider_abandons_session_without_snapshot_save() -> None:
    persistence = MemoryPersistence()
    lease_store = InMemorySessionLeaseStore()
    provider = DurableAgentSessionProvider(
        agent_factory=SnapshotTestFactory(responses={"s1": ["node-a"]}),
        persistence=persistence,
        lease_store=lease_store,
        owner_id="node-a",
        acquire_timeout_seconds=0,
    )

    agent = provider.get_agent("s1")
    agent.run("hello")
    provider.abandon_agent("s1", agent)

    with pytest.raises(KeyError):
        persistence.load("s1")

    lease_store.acquire(
        "s1",
        owner_id="node-b",
        ttl_seconds=30.0,
        wait_timeout_seconds=0,
    )
    with pytest.raises(SessionLeaseError, match="session is not active"):
        provider.refresh_agent("s1")
