from __future__ import annotations

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest

from agentos.distributed.errors import ClaimExpiredError
from agentos.distributed.models import RequestScope, RunSubmission
from agentos.distributed.postgres._database import PostgresPool, fetchone
from agentos.distributed.postgres.claims import PostgresClaimStore
from agentos.distributed.postgres.state import PostgresStateStore
from tests.integration._distributed_failure_support import (
    cleanup_tenant,
    execution_outbox_id,
    live_postgres_settings,
    open_migrated_pool,
)


pytestmark = pytest.mark.integration


def test_live_claim_expiry_uses_database_time_despite_node_clock_drift() -> None:
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(_verify_database_time_authority())


async def _verify_database_time_authority() -> None:
    settings = live_postgres_settings()
    suffix = uuid4().hex
    scope = RequestScope(f"tenant_clock_{suffix}", "principal_1")
    session_id = f"session_{suffix}"
    pool = await open_migrated_pool(settings.dsn)
    claims = PostgresClaimStore(pool)
    try:
        receipt = await PostgresStateStore(pool).submit(
            scope=scope,
            submission=RunSubmission(
                session_id,
                f"submission_{suffix}",
                "verify database time authority",
            ),
        )
        outbox_id = await execution_outbox_id(
            pool,
            scope=scope,
            session_id=session_id,
            run_id=receipt.run_id,
        )
        claimed = await claims.claim_pending_turn(
            scope=scope,
            outbox_id=outbox_id,
            owner_id=f"worker_{suffix}",
            ttl=timedelta(minutes=2),
        )
        assert claimed is not None

        active = await _claim_clock(pool, scope, session_id)
        database_now = active["database_now"]
        expires_at = active["active_claim_expires_at"]
        assert claimed.claim.expires_at == expires_at
        assert expires_at > database_now  # type: ignore[operator]
        simulated_fast_node = database_now + timedelta(days=365)  # type: ignore[operator]
        assert simulated_fast_node > expires_at  # type: ignore[operator]

        expired = await _expire_with_database_clock(pool, scope, session_id)
        database_now = expired["database_now"]
        expires_at = expired["active_claim_expires_at"]
        simulated_slow_node = database_now - timedelta(days=365)  # type: ignore[operator]
        assert simulated_slow_node < expires_at <= database_now  # type: ignore[operator]

        with pytest.raises(ClaimExpiredError):
            await claims.heartbeat(
                scope=scope,
                claim=claimed.claim,
                ttl=timedelta(minutes=2),
            )

        unchanged = await _claim_clock(pool, scope, session_id)
        assert unchanged["active_claim_id"] == claimed.claim.claim_id
        assert unchanged["active_claim_expires_at"] <= unchanged["database_now"]  # type: ignore[operator]
    finally:
        await cleanup_tenant(pool, scope.tenant_id)
        await pool.close()


async def _claim_clock(
    pool: PostgresPool,
    scope: RequestScope,
    session_id: str,
) -> dict[str, object]:
    async with pool.connection() as connection:
        row = await fetchone(
            connection,
            """
            SELECT active_claim_id, active_claim_expires_at,
                   clock_timestamp() AS database_now
            FROM agentos_distributed_sessions
            WHERE tenant_id = %s AND session_id = %s
            """,
            (scope.tenant_id, session_id),
        )
    assert row is not None
    return row


async def _expire_with_database_clock(
    pool: PostgresPool,
    scope: RequestScope,
    session_id: str,
) -> dict[str, object]:
    async with pool.transaction() as connection:
        row = await fetchone(
            connection,
            """
            UPDATE agentos_distributed_sessions
            SET active_claim_expires_at = clock_timestamp() - interval '1 second'
            WHERE tenant_id = %s AND session_id = %s
            RETURNING active_claim_id, active_claim_expires_at,
                      clock_timestamp() AS database_now
            """,
            (scope.tenant_id, session_id),
        )
    assert row is not None
    return row
