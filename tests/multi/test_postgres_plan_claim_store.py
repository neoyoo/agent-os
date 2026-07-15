from __future__ import annotations

from pathlib import Path

import pytest

from agentos.planning import PlanClaimRecord
from agentos.multi.postgres_plan import PostgresPlanClaimStore
from agentos.testing.contracts.plan_claim_store import run_plan_claim_store_contract


class FakeCursor:
    def __init__(self, rows: list[tuple[object, ...]] | None = None) -> None:
        self._rows = rows or []

    def fetchone(self) -> tuple[object, ...] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple[object, ...]]:
        return self._rows


class FakeConnection:
    def __init__(self) -> None:
        self.claims: dict[str, dict[str, object]] = {}
        self.sql: list[str] = []
        self.commits = 0
        self.rollbacks = 0

    def execute(
        self,
        sql: str,
        params: tuple[object, ...] = (),
    ) -> FakeCursor:
        self.sql.append(sql)
        if "INSERT INTO agentos_plan_claims" in sql:
            return self._claim(params)
        if "SELECT plan_id, owner_agent_id, worker_id" in sql:
            if "lease_expires_at <= %s" in sql:
                return self._expired_claims(params)
            row = self.claims.get(str(params[0]))
            return FakeCursor([self._row(row)] if row is not None else [])
        if "DELETE FROM agentos_plan_claims" in sql:
            if len(params) in {2, 3}:
                plan_id = str(params[0])
                worker_id = str(params[1])
                owner_agent_id = str(params[2]) if len(params) == 3 else None
                row = self.claims.get(plan_id)
                if (
                    row is None
                    or row["worker_id"] != worker_id
                    or (
                        owner_agent_id is not None
                        and row["owner_agent_id"] != owner_agent_id
                    )
                ):
                    return FakeCursor()
                del self.claims[plan_id]
                return FakeCursor([(plan_id,)])
            plan_id = str(params[0])
            owner_agent_id = str(params[1])
            worker_id = str(params[2])
            claimed_at = float(params[3])
            lease_expires_at = float(params[4])
            generation = int(params[5])
            now = float(params[6])
            row = self.claims.get(plan_id)
            if (
                row is None
                or row["owner_agent_id"] != owner_agent_id
                or row["worker_id"] != worker_id
                or float(row["claimed_at"]) != claimed_at
                or float(row["lease_expires_at"]) != lease_expires_at
                or int(row["generation"]) != generation
                or float(row["lease_expires_at"]) > now
            ):
                return FakeCursor()
            del self.claims[plan_id]
            return FakeCursor([(plan_id,)])
        return FakeCursor()

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def _claim(self, params: tuple[object, ...]) -> FakeCursor:
        plan_id = str(params[0])
        owner_agent_id = str(params[1])
        worker_id = str(params[2])
        claimed_at = float(params[3])
        lease_expires_at = float(params[4])
        now = float(params[6])
        existing_owner_agent_id = str(params[7])
        existing_worker_id = str(params[8])
        existing = self.claims.get(plan_id)
        if (
            existing is not None
            and float(existing["lease_expires_at"]) > now
            and (
                existing["owner_agent_id"] != existing_owner_agent_id
                or existing["worker_id"] != existing_worker_id
            )
        ):
            return FakeCursor()
        generation = 1 if existing is None else int(existing["generation"]) + 1
        row = {
            "plan_id": plan_id,
            "owner_agent_id": owner_agent_id,
            "worker_id": worker_id,
            "claimed_at": claimed_at,
            "lease_expires_at": lease_expires_at,
            "generation": generation,
        }
        self.claims[plan_id] = row
        return FakeCursor([self._row(row)])

    def _expired_claims(self, params: tuple[object, ...]) -> FakeCursor:
        now = float(params[0])
        owner_agent_id = str(params[1]) if len(params) == 3 else None
        limit = int(params[-1])
        rows = [
            self._row(row)
            for row in self.claims.values()
            if float(row["lease_expires_at"]) <= now
            and (
                owner_agent_id is None
                or row["owner_agent_id"] == owner_agent_id
            )
        ]
        rows.sort(key=lambda row: (float(row[4]), str(row[0])))
        return FakeCursor(rows[:limit])

    def _row(self, row: dict[str, object]) -> tuple[object, ...]:
        return (
            row["plan_id"],
            row["owner_agent_id"],
            row["worker_id"],
            row["claimed_at"],
            row["lease_expires_at"],
            row["generation"],
        )


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


def test_postgres_plan_claim_store_satisfies_reusable_plan_claim_store_contract() -> None:
    run_plan_claim_store_contract(
        lambda: PostgresPlanClaimStore(
            dsn="postgresql://unused",
            connection=FakeConnection(),
        ),
    )


def test_postgres_plan_claim_store_claims_and_reports_busy_claims() -> None:
    connection = FakeConnection()
    store = PostgresPlanClaimStore(
        dsn="postgresql://unused",
        connection=connection,
    )

    first = store.claim_plan(
        plan_id="plan_1",
        owner_agent_id="leader",
        worker_id="scheduler_a",
        lease_seconds=30.0,
        now=10.0,
    )
    second = store.claim_plan(
        plan_id="plan_1",
        owner_agent_id="leader",
        worker_id="scheduler_b",
        lease_seconds=30.0,
        now=20.0,
    )

    assert first.status == "claimed"
    assert first.claim == PlanClaimRecord(
        plan_id="plan_1",
        owner_agent_id="leader",
        worker_id="scheduler_a",
        claimed_at=10.0,
        lease_expires_at=40.0,
        generation=1,
    )
    assert second.status == "busy"
    assert second.existing_claim == first.claim
    assert connection.rollbacks == 1
    assert connection.commits == 1
    joined_sql = "\n".join(connection.sql)
    assert "ON CONFLICT (plan_id) DO UPDATE" in joined_sql
    assert "agentos_plan_claims.lease_expires_at <= %s" in joined_sql


def test_postgres_plan_claim_store_from_pool_borrows_and_returns_per_method() -> None:
    connection = FakeConnection()
    pool = FakePool(connection)
    store = PostgresPlanClaimStore.from_pool(
        dsn="postgresql://unused",
        pool=pool,
    )

    assert pool.gets == 0

    result = store.claim_plan(
        plan_id="plan_1",
        owner_agent_id="leader",
        worker_id="scheduler_a",
        lease_seconds=30.0,
        now=10.0,
    )
    assert result.status == "claimed"
    assert pool.gets == 1
    assert pool.puts == [connection]

    assert store.get_claim("plan_1") == result.claim
    assert pool.gets == 2
    assert pool.puts == [connection, connection]

    store.close()
    assert pool.puts == [connection, connection]


def test_postgres_plan_claim_store_renews_and_takes_over_expired_claims() -> None:
    connection = FakeConnection()
    store = PostgresPlanClaimStore(
        dsn="postgresql://unused",
        connection=connection,
    )

    first = store.claim_plan(
        plan_id="plan_1",
        owner_agent_id="leader",
        worker_id="scheduler_a",
        lease_seconds=10.0,
        now=10.0,
    )
    renewed = store.claim_plan(
        plan_id="plan_1",
        owner_agent_id="leader",
        worker_id="scheduler_a",
        lease_seconds=10.0,
        now=15.0,
    )
    takeover = store.claim_plan(
        plan_id="plan_1",
        owner_agent_id="leader",
        worker_id="scheduler_b",
        lease_seconds=10.0,
        now=30.0,
    )

    assert first.claim is not None
    assert renewed.status == "claimed"
    assert renewed.claim is not None
    assert renewed.claim.worker_id == "scheduler_a"
    assert renewed.claim.generation == 2
    assert takeover.status == "claimed"
    assert takeover.claim is not None
    assert takeover.claim.worker_id == "scheduler_b"
    assert takeover.claim.generation == 3
    assert connection.commits == 3


def test_postgres_plan_claim_store_does_not_renew_active_claim_for_other_owner() -> None:
    connection = FakeConnection()
    store = PostgresPlanClaimStore(
        dsn="postgresql://unused",
        connection=connection,
    )
    first = store.claim_plan(
        plan_id="plan_1",
        owner_agent_id="leader",
        worker_id="shared_scheduler",
        lease_seconds=30.0,
        now=10.0,
    )

    other_owner = store.claim_plan(
        plan_id="plan_1",
        owner_agent_id="other",
        worker_id="shared_scheduler",
        lease_seconds=30.0,
        now=20.0,
    )

    assert other_owner.status == "busy"
    assert other_owner.existing_claim == first.claim
    assert store.get_claim("plan_1") == first.claim
    joined_sql = "\n".join(connection.sql)
    assert "agentos_plan_claims.owner_agent_id = %s" in joined_sql
    assert "agentos_plan_claims.worker_id = %s" in joined_sql


def test_postgres_plan_claim_store_releases_matching_worker_only() -> None:
    connection = FakeConnection()
    store = PostgresPlanClaimStore(
        dsn="postgresql://unused",
        connection=connection,
    )
    store.claim_plan(
        plan_id="plan_1",
        owner_agent_id="leader",
        worker_id="scheduler_a",
        lease_seconds=10.0,
        now=10.0,
    )

    assert store.release_plan(plan_id="plan_1", worker_id="scheduler_b") is False
    assert connection.rollbacks == 1
    assert store.get_claim("plan_1") is not None
    assert store.release_plan(plan_id="plan_1", worker_id="scheduler_a") is True
    assert store.get_claim("plan_1") is None
    assert connection.commits == 2


def test_postgres_plan_claim_store_release_can_require_matching_owner() -> None:
    connection = FakeConnection()
    store = PostgresPlanClaimStore(
        dsn="postgresql://unused",
        connection=connection,
    )
    first = store.claim_plan(
        plan_id="plan_1",
        owner_agent_id="leader",
        worker_id="shared_scheduler",
        lease_seconds=10.0,
        now=10.0,
    )

    assert (
        store.release_plan(
            plan_id="plan_1",
            worker_id="shared_scheduler",
            owner_agent_id="other",
        )
        is False
    )
    assert store.get_claim("plan_1") == first.claim
    assert (
        store.release_plan(
            plan_id="plan_1",
            worker_id="shared_scheduler",
            owner_agent_id="leader",
        )
        is True
    )
    assert store.get_claim("plan_1") is None
    joined_sql = "\n".join(connection.sql)
    assert "owner_agent_id = %s" in joined_sql


def test_postgres_plan_claim_store_lists_expired_claims_with_owner_and_limit() -> None:
    connection = FakeConnection()
    store = PostgresPlanClaimStore(
        dsn="postgresql://unused",
        connection=connection,
    )
    store.claim_plan(
        plan_id="expired_leader",
        owner_agent_id="leader",
        worker_id="scheduler_a",
        lease_seconds=10.0,
        now=10.0,
    )
    store.claim_plan(
        plan_id="active_leader",
        owner_agent_id="leader",
        worker_id="scheduler_b",
        lease_seconds=50.0,
        now=10.0,
    )
    store.claim_plan(
        plan_id="expired_other",
        owner_agent_id="other",
        worker_id="scheduler_c",
        lease_seconds=10.0,
        now=10.0,
    )

    expired = store.expired_claims(
        now=30.0,
        owner_agent_id="leader",
        limit=5,
    )

    assert [claim.plan_id for claim in expired] == ["expired_leader"]
    joined_sql = "\n".join(connection.sql)
    assert "lease_expires_at <= %s" in joined_sql
    assert "owner_agent_id = %s" in joined_sql
    assert "LIMIT %s" in joined_sql


def test_postgres_plan_claim_store_releases_exact_expired_claim_only() -> None:
    connection = FakeConnection()
    store = PostgresPlanClaimStore(
        dsn="postgresql://unused",
        connection=connection,
    )
    first = store.claim_plan(
        plan_id="expired_plan",
        owner_agent_id="leader",
        worker_id="scheduler_a",
        lease_seconds=10.0,
        now=10.0,
    )
    assert first.claim is not None

    assert store.release_expired_claim(first.claim, now=30.0) is True
    assert store.get_claim("expired_plan") is None

    renewed = store.claim_plan(
        plan_id="expired_plan",
        owner_agent_id="leader",
        worker_id="scheduler_b",
        lease_seconds=30.0,
        now=30.0,
    )
    assert renewed.claim is not None
    rollbacks_before_stale_release = connection.rollbacks
    assert store.release_expired_claim(first.claim, now=30.0) is False
    assert connection.rollbacks == rollbacks_before_stale_release + 1
    assert store.get_claim("expired_plan") == renewed.claim

    joined_sql = "\n".join(connection.sql)
    assert "claimed_at = %s" in joined_sql
    assert "lease_expires_at = %s" in joined_sql
    assert "generation = %s" in joined_sql
    assert "lease_expires_at <= %s" in joined_sql


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        (
            {
                "plan_id": "",
                "owner_agent_id": "leader",
                "worker_id": "worker",
                "lease_seconds": 1,
                "now": 1,
            },
            "plan_id must not be empty",
        ),
        (
            {
                "plan_id": "plan_1",
                "owner_agent_id": "",
                "worker_id": "worker",
                "lease_seconds": 1,
                "now": 1,
            },
            "owner_agent_id must not be empty",
        ),
        (
            {
                "plan_id": "plan_1",
                "owner_agent_id": "leader",
                "worker_id": " ",
                "lease_seconds": 1,
                "now": 1,
            },
            "worker_id must not be empty",
        ),
        (
            {
                "plan_id": "plan_1",
                "owner_agent_id": "leader",
                "worker_id": "worker",
                "lease_seconds": 0,
                "now": 1,
            },
            "lease_seconds must be > 0",
        ),
    ],
)
def test_postgres_plan_claim_store_validates_inputs(
    kwargs: dict[str, object],
    match: str,
) -> None:
    store = PostgresPlanClaimStore(
        dsn="postgresql://unused",
        connection=FakeConnection(),
    )

    with pytest.raises(ValueError, match=match):
        store.claim_plan(**kwargs)  # type: ignore[arg-type]


def test_postgres_plan_claim_store_migration_defines_table_and_indexes() -> None:
    migration = Path(
        "docs/migrations/2026-06-16-postgres-plan-claims.sql",
    ).read_text(encoding="utf-8")

    assert "-- migrate:up" in migration
    assert "-- migrate:down" in migration
    assert "CREATE TABLE IF NOT EXISTS agentos_plan_claims" in migration
    assert "PRIMARY KEY" in migration
    assert "payload JSONB NOT NULL" in migration
    assert "agentos_plan_claims_owner_expiry_idx" in migration
    assert "agentos_plan_claims_worker_expiry_idx" in migration
