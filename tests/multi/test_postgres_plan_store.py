from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from agentos.multi.planner import (
    EvidenceHandle,
    PlanClaimRecord,
    PlanAssignment,
    PlanNotFoundError,
    PlanState,
    PlanStep,
)
from agentos.multi.postgres_plan import PostgresPlanClaimStore, PostgresPlanStore
from agentos.multi.serializers import plan_state_from_dict, plan_state_to_dict
from agentos.testing.contracts.plan_store import run_plan_store_contract
from agentos.workspace import WorkspaceHandle


class FakeCursor:
    def __init__(self, rows: list[tuple[object, ...]] | None = None) -> None:
        self._rows = rows or []

    def fetchone(self) -> tuple[object, ...] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple[object, ...]]:
        return list(self._rows)


class FakeConnection:
    def __init__(self) -> None:
        self.plans: dict[str, dict[str, object]] = {}
        self.claims: dict[str, PlanClaimRecord] = {}
        self.sql: list[str] = []
        self.commits = 0
        self.rollbacks = 0

    def execute(
        self,
        sql: str,
        params: tuple[object, ...] = (),
    ) -> FakeCursor:
        self.sql.append(sql)
        if "INSERT INTO agentos_plans" in sql:
            plan_id = str(params[0])
            if plan_id in self.plans and "ON CONFLICT" not in sql:
                raise ValueError(f"duplicate plan: {plan_id}")
            payload = json.loads(str(params[5]))
            self.plans[plan_id] = {
                "owner_agent_id": params[1],
                "status": params[2],
                "created_at": params[3],
                "updated_at": params[4],
                "payload": payload,
                "revision": 0,
            }
            return FakeCursor()
        if "UPDATE agentos_plans AS plans" in sql:
            plan_id = str(params[5])
            if plan_id not in self.plans:
                return FakeCursor()
            if int(self.plans[plan_id]["revision"]) != int(params[6]):
                return FakeCursor()
            claim = self.claims.get(plan_id)
            if claim != PlanClaimRecord(
                plan_id=plan_id,
                owner_agent_id=str(params[7]),
                worker_id=str(params[8]),
                claimed_at=float(params[9]),
                lease_expires_at=float(params[10]),
                generation=int(params[11]),
            ) or claim.lease_expires_at <= float(params[12]):
                return FakeCursor()
            payload = json.loads(str(params[4]))
            self.plans[plan_id] = {
                "owner_agent_id": params[0],
                "status": params[1],
                "created_at": params[2],
                "updated_at": params[3],
                "payload": payload,
                "revision": int(self.plans[plan_id]["revision"]) + 1,
            }
            return FakeCursor([(payload,)])
        if "UPDATE agentos_plans" in sql and "AND revision = %s" in sql:
            plan_id = str(params[5])
            expected_revision = int(params[6])
            if plan_id not in self.plans:
                return FakeCursor()
            if int(self.plans[plan_id]["revision"]) != expected_revision:
                return FakeCursor()
            payload = json.loads(str(params[4]))
            self.plans[plan_id] = {
                "owner_agent_id": params[0],
                "status": params[1],
                "created_at": params[2],
                "updated_at": params[3],
                "payload": payload,
                "revision": expected_revision + 1,
            }
            return FakeCursor([(payload, expected_revision + 1)])
        if "UPDATE agentos_plans" in sql:
            plan_id = str(params[5])
            if plan_id not in self.plans:
                return FakeCursor()
            payload = json.loads(str(params[4]))
            self.plans[plan_id] = {
                "owner_agent_id": params[0],
                "status": params[1],
                "created_at": params[2],
                "updated_at": params[3],
                "payload": payload,
                "revision": int(self.plans[plan_id]["revision"]) + 1,
            }
            return FakeCursor([(payload,)])
        if "SELECT payload, revision FROM agentos_plans" in sql and "plan_id = %s" in sql:
            row = self.plans.get(str(params[0]))
            return FakeCursor([(row["payload"], row["revision"])] if row else [])
        if "SELECT payload FROM agentos_plans" in sql and "plan_id = %s" in sql:
            row = self.plans.get(str(params[0]))
            return FakeCursor([(row["payload"],)] if row else [])
        if "SELECT payload FROM agentos_plans" in sql and "owner_agent_id = %s" in sql:
            owner = str(params[0])
            rows = [
                (row["payload"],)
                for _plan_id, row in sorted(
                    self.plans.items(),
                    key=lambda item: (float(item[1]["updated_at"]), item[0]),
                )
                if row["owner_agent_id"] == owner
            ]
            return FakeCursor(rows)
        if "SELECT payload FROM agentos_plans" in sql:
            rows = [
                (row["payload"],)
                for _plan_id, row in sorted(
                    self.plans.items(),
                    key=lambda item: (float(item[1]["updated_at"]), item[0]),
                )
            ]
            return FakeCursor(rows)
        return FakeCursor()

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


class FakePool:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection
        self.gets = 0
        self.puts: list[FakeConnection] = []

    def getconn(self) -> FakeConnection:
        self.gets += 1
        return self.connection

    def putconn(self, connection: FakeConnection) -> None:
        self.puts.append(connection)


class DistinctConnectionPool:
    def __init__(self) -> None:
        self.connections: list[object] = []
        self.puts: list[object] = []

    def getconn(self) -> object:
        connection = object()
        self.connections.append(connection)
        return connection

    def putconn(self, connection: object) -> None:
        self.puts.append(connection)


def plan(plan_id: str = "plan_1", *, owner_agent_id: str = "leader") -> PlanState:
    return PlanState(
        plan_id=plan_id,
        objective="Review agent-os architecture.",
        owner_agent_id=owner_agent_id,
        status="running",
        steps=(
            PlanStep(
                step_id="step_1",
                instruction="Review planner persistence.",
                status="assigned",
                required_capabilities=("architecture-review",),
                assigned_agent_id="worker",
                template_id="reviewer",
                task_id="task_1",
                depends_on=("step_0",),
                evidence_ids=("evidence_1",),
                attempts=2,
                last_failed_at=2.0,
                next_retry_at=9.0,
                retry_status="scheduled",
                retry_exhausted_at=None,
                error="temporary failure",
            ),
        ),
        evidence=(
            EvidenceHandle(
                evidence_id="evidence_1",
                kind="text",
                summary="Planner persistence needs a Postgres adapter.",
                uri="memory://evidence/1",
                producer_agent_id="worker",
                metadata={"confidence": "high"},
            ),
        ),
        assignments=(
            PlanAssignment(
                plan_id=plan_id,
                step_id="step_1",
                template_id="reviewer",
                task_id="task_1",
                target_agent_id="worker",
                created_at=3.0,
                dispatch_status="submitted",
                submitted_at=3.5,
                dispatch_error=None,
            ),
        ),
        created_at=1.0,
        updated_at=4.0,
        workspace=WorkspaceHandle(
            workspace_id="task:plan_1",
            scope="task",
            root="/work/plans/plan_1",
            parent_workspace_id="session:session_1",
            metadata={"tenant": "acme"},
        ),
    )


def test_postgres_plan_store_satisfies_reusable_plan_store_contract() -> None:
    connections: dict[int, FakeConnection] = {}

    def factory() -> PostgresPlanStore:
        connection = FakeConnection()
        store = PostgresPlanStore(
            dsn="postgresql://unused",
            connection=connection,
        )
        connections[id(store)] = connection
        return store

    def seed_claim(store: object, claim: PlanClaimRecord) -> None:
        connections[id(store)].claims[claim.plan_id] = claim

    run_plan_store_contract(factory, seed_claim=seed_claim)


def test_plan_state_serializer_round_trips_workspace_and_nested_records() -> None:
    original = plan()

    assert plan_state_from_dict(plan_state_to_dict(original)) == original
    assert plan_state_to_dict(original)["steps"][0]["depends_on"] == ["step_0"]
    assert plan_state_to_dict(original)["steps"][0]["attempts"] == 2
    assert plan_state_to_dict(original)["steps"][0]["retry_status"] == "scheduled"
    assert plan_state_to_dict(original)["assignments"][0]["dispatch_status"] == (
        "submitted"
    )
    assert plan_state_to_dict(original)["assignments"][0]["submitted_at"] == 3.5


def test_plan_state_serializer_reads_legacy_assignment_without_dispatch_fields() -> None:
    payload = plan_state_to_dict(plan())
    legacy_assignment = payload["assignments"][0]
    del legacy_assignment["dispatch_status"]
    del legacy_assignment["submitted_at"]
    del legacy_assignment["dispatch_error"]

    restored = plan_state_from_dict(payload)

    assert restored.assignments[0].dispatch_status == "pending"
    assert restored.assignments[0].submitted_at is None
    assert restored.assignments[0].dispatch_error is None


def test_postgres_plan_store_round_trips_plans() -> None:
    connection = FakeConnection()
    store = PostgresPlanStore(dsn="postgresql://unused", connection=connection)
    original = plan()
    other = plan("plan_2", owner_agent_id="other")

    store.create_plan(original)
    store.create_plan(other)
    updated = original.with_status("completed", now=5.0)
    store.save_plan(updated)

    assert store.get_plan("plan_1") == updated
    assert store.list_plans("leader") == [updated]
    assert store.list_plans() == [other, updated]
    assert connection.commits == 3


def test_postgres_plan_store_rolls_back_after_read_only_scope() -> None:
    connection = FakeConnection()
    store = PostgresPlanStore(dsn="postgresql://unused", connection=connection)
    store.create_plan(plan())

    rollbacks_before_read = connection.rollbacks
    assert store.get_plan("plan_1") == plan()

    assert connection.rollbacks == rollbacks_before_read + 1


def test_postgres_plan_store_from_pool_borrows_and_returns_per_method() -> None:
    connection = FakeConnection()
    pool = FakePool(connection)
    store = PostgresPlanStore.from_pool(
        dsn="postgresql://unused",
        pool=pool,
    )

    assert pool.gets == 0

    store.create_plan(plan())
    assert pool.gets == 1
    assert pool.puts == [connection]

    assert store.get_plan("plan_1") == plan()
    assert pool.gets == 2
    assert pool.puts == [connection, connection]

    store.close()
    assert pool.puts == [connection, connection]


@pytest.mark.parametrize("store_cls", [PostgresPlanStore, PostgresPlanClaimStore])
def test_postgres_plan_store_pool_connection_scope_is_thread_local(
    store_cls: type[PostgresPlanStore] | type[PostgresPlanClaimStore],
) -> None:
    pool = DistinctConnectionPool()
    store = store_cls.from_pool(
        dsn="postgresql://unused",
        pool=pool,
    )
    first_entered = threading.Event()
    release_first = threading.Event()
    first_connection_holder: list[object] = []

    def hold_first_connection() -> None:
        with store._connection_scope() as first_connection:
            first_connection_holder.append(first_connection)
            first_entered.set()
            assert release_first.wait(timeout=2)

    worker = threading.Thread(target=hold_first_connection)
    worker.start()
    assert first_entered.wait(timeout=2)
    first_connection = first_connection_holder[0]

    try:
        with store._connection_scope() as second_connection:
            assert second_connection is not first_connection
    finally:
        release_first.set()
        worker.join(timeout=2)

    assert not worker.is_alive()
    assert len(pool.connections) == 2
    assert set(pool.puts) == set(pool.connections)


def test_postgres_plan_store_rejects_missing_and_duplicate_plans() -> None:
    connection = FakeConnection()
    store = PostgresPlanStore(dsn="postgresql://unused", connection=connection)
    original = plan()

    store.create_plan(original)
    with pytest.raises(ValueError, match="plan already exists"):
        store.create_plan(original)
    assert connection.rollbacks == 1
    with pytest.raises(PlanNotFoundError):
        store.save_plan(plan("missing"))
    assert connection.rollbacks == 2


def test_postgres_plan_store_saves_only_when_revision_is_unchanged() -> None:
    connection = FakeConnection()
    store = PostgresPlanStore(dsn="postgresql://unused", connection=connection)
    original = plan()
    store.create_plan(original)
    record = store.get_plan_record("plan_1")
    assert record is not None
    store.save_plan(original.with_status("running", now=5.0))

    rollbacks_before_conflict = connection.rollbacks
    saved = store.save_plan_if_unchanged(
        original.with_status("completed", now=6.0),
        expected_revision=record.revision,
    )

    assert saved is False
    assert connection.rollbacks == rollbacks_before_conflict + 1
    assert store.get_plan("plan_1").status == "running"
    fresh = store.get_plan_record("plan_1")
    assert fresh is not None
    assert fresh.revision == record.revision + 1
    assert store.save_plan_if_unchanged(
        fresh.plan.with_status("completed", now=7.0),
        expected_revision=fresh.revision,
    ) is True
    assert store.get_plan("plan_1").status == "completed"


def test_postgres_plan_store_saves_only_when_exact_claim_is_current() -> None:
    connection = FakeConnection()
    store = PostgresPlanStore(dsn="postgresql://unused", connection=connection)
    original = plan()
    store.create_plan(original)
    record = store.get_plan_record("plan_1")
    assert record is not None
    claim = PlanClaimRecord(
        plan_id="plan_1",
        owner_agent_id="leader",
        worker_id="scheduler_a",
        claimed_at=10.0,
        lease_expires_at=30.0,
        generation=3,
    )
    connection.claims["plan_1"] = claim

    saved = store.save_plan_if_claimed(
        original.with_status("completed", now=12.0),
        claim,
        expected_revision=record.revision,
        now=12.0,
    )

    assert saved is True
    rollbacks_before_claim_mismatch = connection.rollbacks
    assert store.save_plan_if_claimed(
        original.with_status("failed", now=13.0),
        claim,
        expected_revision=record.revision,
        now=13.0,
    ) is False
    assert connection.rollbacks == rollbacks_before_claim_mismatch + 1
    assert store.get_plan("plan_1").status == "completed"
    joined_sql = "\n".join(connection.sql)
    assert "agentos_plan_claims" in joined_sql
    assert "plans.revision = %s" in joined_sql
    assert "worker_id = %s" in joined_sql
    assert "claimed_at = %s" in joined_sql
    assert "lease_expires_at = %s" in joined_sql
    assert "generation = %s" in joined_sql
    assert "lease_expires_at > %s" in joined_sql


def test_postgres_plan_store_migration_defines_tables_and_indexes() -> None:
    migration = Path(
        "docs/migrations/2026-06-15-postgres-plan-store.sql",
    ).read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS agentos_plans" in migration
    assert "payload JSONB NOT NULL" in migration
    assert "agentos_plans_owner_idx" in migration
    assert "agentos_plans_status_idx" in migration
    assert "revision BIGINT NOT NULL DEFAULT 0" in migration
    assert "-- migrate:down" in migration
