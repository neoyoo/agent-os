from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from time import sleep

from agentos.multi import AgentCard


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def card(agent_id: str, *capabilities: str) -> AgentCard:
    return AgentCard(
        agent_id=agent_id,
        name=agent_id.title(),
        description=f"{agent_id} agent",
        capabilities=tuple(capabilities),
        endpoint=f"https://agents.test/{agent_id}",
    )


def test_static_resolver_resolves_and_discovers_non_offline_cards() -> None:
    from agentos.registry import StaticResolver

    active = card("active", "search", "code")
    offline = AgentCard(
        agent_id="offline",
        name="Offline",
        description="offline",
        capabilities=("search",),
        endpoint="https://agents.test/offline",
        status="offline",
    )
    resolver = StaticResolver([active, offline])

    assert resolver.resolve("active") == active
    assert resolver.resolve("missing") is None
    assert resolver.discover(("search",)) == [active]
    assert resolver.select(("search",), session_id="session_1") == active


def test_persistent_registry_filters_stale_heartbeats_and_records_affinity() -> None:
    from agentos.registry import InMemoryAgentRegistryStore, PersistentAgentRegistry

    clock = FakeClock()
    registry = PersistentAgentRegistry(
        store=InMemoryAgentRegistryStore(),
        heartbeat_ttl_seconds=30,
        session_affinity_ttl_seconds=300,
        clock=clock,
    )
    registry.register(card("worker_1", "search"))

    assert registry.discover(("search",)) == [card("worker_1", "search")]

    registry.bind_session("session_1", "worker_1")
    assert registry.resolve_session("session_1") == "worker_1"

    clock.advance(31)
    assert registry.discover(("search",)) == []
    registry.heartbeat("worker_1", status="idle")
    assert registry.discover(("search",)) == [card("worker_1", "search")]


def test_service_resolver_keeps_session_sticky_until_worker_goes_unhealthy() -> None:
    from agentos.registry import (
        InMemoryAgentRegistryStore,
        PersistentAgentRegistry,
        ServiceResolver,
    )

    clock = FakeClock()
    registry = PersistentAgentRegistry(
        store=InMemoryAgentRegistryStore(),
        heartbeat_ttl_seconds=30,
        session_affinity_ttl_seconds=300,
        clock=clock,
    )
    first = card("worker_1", "search")
    second = card("worker_2", "search")
    registry.register(first)
    registry.register(second)
    resolver = ServiceResolver(registry)

    assert resolver.select(("search",), session_id="session_1") == first
    assert resolver.select(("search",), session_id="session_1") == first

    registry.mark_unhealthy("worker_1")

    assert resolver.select(("search",), session_id="session_1") == second
    assert registry.resolve_session("session_1") == "worker_2"


def test_service_resolver_round_robins_new_sessions() -> None:
    from agentos.registry import (
        InMemoryAgentRegistryStore,
        PersistentAgentRegistry,
        ServiceResolver,
    )

    registry = PersistentAgentRegistry(
        store=InMemoryAgentRegistryStore(),
        heartbeat_ttl_seconds=30,
        session_affinity_ttl_seconds=300,
        clock=FakeClock(),
    )
    first = card("worker_1", "search")
    second = card("worker_2", "search")
    registry.register(first)
    registry.register(second)
    resolver = ServiceResolver(registry)

    assert resolver.select(("search",), session_id="session_1") == first
    assert resolver.select(("search",), session_id="session_2") == second
    assert resolver.select(("search",), session_id="session_3") == first
    assert resolver.select(("search",), session_id="session_2") == second


def test_service_resolver_serializes_concurrent_round_robin_updates() -> None:
    from agentos.registry import (
        InMemoryAgentRegistryStore,
        PersistentAgentRegistry,
        ServiceResolver,
    )

    class YieldingCounterDict(dict[tuple[str, ...], int]):
        def get(self, key: tuple[str, ...], default: int = 0) -> int:
            value = super().get(key, default)
            sleep(0.02)
            return value

    registry = PersistentAgentRegistry(
        store=InMemoryAgentRegistryStore(),
        heartbeat_ttl_seconds=30,
        session_affinity_ttl_seconds=300,
        clock=FakeClock(),
    )
    first = card("worker_1", "search")
    second = card("worker_2", "search")
    registry.register(first)
    registry.register(second)
    resolver = ServiceResolver(registry)
    resolver._next_index_by_capabilities = YieldingCounterDict()  # type: ignore[attr-defined]
    start = Barrier(3)

    def select_for(session_id: str) -> AgentCard:
        start.wait()
        selected = resolver.select(("search",), session_id=session_id)
        assert selected is not None
        return selected

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(select_for, "session_1"),
            executor.submit(select_for, "session_2"),
        ]
        start.wait()
        selected = [future.result() for future in futures]

    assert {card.agent_id for card in selected} == {"worker_1", "worker_2"}


def test_json_file_registry_store_persists_cards_and_affinity(tmp_path) -> None:
    from agentos.registry import JsonFileAgentRegistryStore, PersistentAgentRegistry

    clock = FakeClock()
    path = tmp_path / "registry.json"
    first = PersistentAgentRegistry(
        store=JsonFileAgentRegistryStore(path),
        heartbeat_ttl_seconds=30,
        session_affinity_ttl_seconds=300,
        clock=clock,
    )
    first.register(card("worker_1", "search"))
    first.bind_session("session_1", "worker_1")

    second = PersistentAgentRegistry(
        store=JsonFileAgentRegistryStore(path),
        heartbeat_ttl_seconds=30,
        session_affinity_ttl_seconds=300,
        clock=clock,
    )

    assert second.resolve("worker_1") == card("worker_1", "search")
    assert second.resolve_session("session_1") == "worker_1"


def test_json_file_registry_store_documents_single_process_scope() -> None:
    from agentos.registry import JsonFileAgentRegistryStore

    assert "single-process" in (JsonFileAgentRegistryStore.__doc__ or "")


def test_registry_serializers_are_public() -> None:
    from agentos.registry.serializers import (
        affinity_from_dict,
        affinity_to_dict,
        card_from_dict,
        card_to_dict,
    )

    worker = card("worker_1", "search")
    assert card_from_dict(card_to_dict(worker)) == worker
    assert callable(affinity_from_dict)
    assert callable(affinity_to_dict)


class FakeRegistryPostgresCursor:
    def __init__(self, rows: list[tuple[object, ...]] | None = None) -> None:
        self._rows = rows or []

    def fetchone(self) -> tuple[object, ...] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple[object, ...]]:
        return list(self._rows)


class FakeRegistryPostgresConnection:
    def __init__(self) -> None:
        self.records: dict[str, tuple[str, float]] = {}
        self.affinity: dict[str, tuple[str, float]] = {}
        self.ddl_calls: list[str] = []
        self.commits = 0

    def execute(
        self,
        sql: str,
        params: tuple[object, ...] | None = None,
    ) -> FakeRegistryPostgresCursor:
        params = params or ()
        if "CREATE TABLE" in sql:
            self.ddl_calls.append(sql)
            return FakeRegistryPostgresCursor()
        if "INSERT INTO agentos_agent_registry" in sql:
            self.records[str(params[0])] = (str(params[1]), float(params[2]))
            return FakeRegistryPostgresCursor()
        if "DELETE FROM agentos_agent_registry" in sql:
            self.records.pop(str(params[0]), None)
            return FakeRegistryPostgresCursor()
        if "SELECT card, heartbeat_at FROM agentos_agent_registry WHERE" in sql:
            row = self.records.get(str(params[0]))
            return FakeRegistryPostgresCursor([row] if row else [])
        if "SELECT card, heartbeat_at FROM agentos_agent_registry" in sql:
            return FakeRegistryPostgresCursor(list(self.records.values()))
        if "INSERT INTO agentos_agent_session_affinity" in sql:
            self.affinity[str(params[0])] = (str(params[1]), float(params[2]))
            return FakeRegistryPostgresCursor()
        if "SELECT agent_id, expires_at FROM agentos_agent_session_affinity" in sql:
            row = self.affinity.get(str(params[0]))
            return FakeRegistryPostgresCursor([row] if row else [])
        if "DELETE FROM agentos_agent_session_affinity" in sql:
            self.affinity.pop(str(params[0]), None)
            return FakeRegistryPostgresCursor()
        raise AssertionError(f"unexpected SQL: {sql}")

    def commit(self) -> None:
        self.commits += 1


def test_postgres_registry_store_persists_records_and_affinity() -> None:
    from agentos.registry import (
        AgentRegistryRecord,
        PostgresAgentRegistryStore,
        SessionAffinity,
    )

    connection = FakeRegistryPostgresConnection()
    store = PostgresAgentRegistryStore(
        dsn="postgresql://unused",
        connection=connection,
    )
    record = AgentRegistryRecord(card=card("worker_1", "search"), heartbeat_at=1000)
    affinity = SessionAffinity(
        session_id="session_1",
        agent_id="worker_1",
        expires_at=1300,
    )

    store.save_record(record)
    store.save_affinity(affinity)

    assert store.load_record("worker_1") == record
    assert store.list_records() == [record]
    assert store.load_affinity("session_1") == affinity
    store.delete_affinity("session_1")
    store.delete_record("worker_1")
    assert store.load_affinity("session_1") is None
    assert store.load_record("worker_1") is None
    assert connection.commits > 0


def test_postgres_registry_store_does_not_create_tables_at_runtime() -> None:
    from agentos.registry import PostgresAgentRegistryStore

    connection = FakeRegistryPostgresConnection()

    PostgresAgentRegistryStore(dsn="postgresql://unused", connection=connection)

    assert connection.ddl_calls == []
