from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

import pytest

from agentos.capabilities import SideEffectPolicy
from agentos.distributed.errors import ActiveRunConflictError
from agentos.distributed.models import RequestScope, RunSubmission
from agentos.distributed.postgres._database import (
    AsyncConnection,
    PostgresPool,
    Row,
    fetchone,
)
from agentos.distributed.postgres.state import PostgresStateStore
from agentos.distributed.services import RunCommandService
from agentos.runtime.durable_commands import DurableRunCommand
from agentos.runtime.side_effect_types import (
    SideEffectResolution,
    SideEffectResolutionKind,
)
from tests.integration._distributed_failure_support import (
    ProcessCrash,
    cleanup_tenant,
    live_postgres_settings,
    open_migrated_pool,
)
from tests.integration._side_effect_resolution_support import (
    AllowResolutionAuthorizer,
    ReconciliationScenario,
    open_reconciliation_scenario,
)


pytestmark = pytest.mark.integration
CommitTiming = Literal["before", "after"]


@dataclass(slots=True)
class _CrashAtTransactionCommit:
    delegate: PostgresPool
    timing: CommitTiming
    fired: bool = False

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncConnection]:
        async with self.delegate.transaction() as connection:
            yield connection
            if self.timing == "before" and not self.fired:
                self.fired = True
                raise ProcessCrash
        if self.timing == "after" and not self.fired:
            self.fired = True
            raise ProcessCrash


@pytest.mark.parametrize("timing", ("before", "after"))
def test_live_submission_commit_window_replays_atomically(
    timing: CommitTiming,
) -> None:
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(_verify_submission_window(timing))


async def _verify_submission_window(timing: CommitTiming) -> None:
    settings = live_postgres_settings()
    suffix = uuid4().hex
    scope = RequestScope(f"tenant_submission_commit_{suffix}", "principal_1")
    session_id = f"session_{suffix}"
    submission = RunSubmission(
        session_id,
        f"submission_{suffix}",
        "commit the submission atomically",
    )
    pool = await open_migrated_pool(settings.dsn)
    try:
        crashing = _CrashAtTransactionCommit(pool, timing)
        with pytest.raises(ProcessCrash):
            await PostgresStateStore(crashing).submit(  # type: ignore[arg-type]
                scope=scope,
                submission=submission,
            )
        assert crashing.fired is True

        crashed_truth = await _submission_truth(pool, scope)
        expected_committed = timing == "after"
        assert crashed_truth == _expected_submission_truth(expected_committed)

        store = PostgresStateStore(pool)
        replayed = await store.submit(scope=scope, submission=submission)
        assert replayed.duplicate is expected_committed
        duplicate = await store.submit(scope=scope, submission=submission)
        assert duplicate.duplicate is True
        assert duplicate.run_id == replayed.run_id
        assert await _submission_truth(pool, scope) == _expected_submission_truth(True)
    finally:
        await cleanup_tenant(pool, scope.tenant_id)
        await pool.close()


def test_live_concurrent_first_submissions_admit_one_active_run() -> None:
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(_verify_concurrent_first_submissions())


async def _verify_concurrent_first_submissions() -> None:
    settings = live_postgres_settings()
    suffix = uuid4().hex
    scope = RequestScope(f"tenant_submission_race_{suffix}", "principal_1")
    session_id = f"session_{suffix}"
    pool = await open_migrated_pool(settings.dsn)
    store = PostgresStateStore(pool)
    submissions = tuple(
        RunSubmission(
            session_id,
            f"submission_{index}_{suffix}",
            f"concurrent request {index}",
        )
        for index in (1, 2)
    )
    try:
        outcomes = await asyncio.gather(
            *(store.submit(scope=scope, submission=item) for item in submissions),
            return_exceptions=True,
        )
        receipts = tuple(item for item in outcomes if not isinstance(item, BaseException))
        conflicts = tuple(
            item for item in outcomes if isinstance(item, ActiveRunConflictError)
        )
        assert len(receipts) == 1
        assert len(conflicts) == 1
        assert all(
            not isinstance(item, BaseException) or type(item) is ActiveRunConflictError
            for item in outcomes
        )
        assert await _submission_truth(pool, scope) == _expected_submission_truth(True)
        async with pool.connection() as connection:
            session = await fetchone(
                connection,
                """
                SELECT next_turn_number FROM agentos_distributed_sessions
                WHERE tenant_id = %s AND session_id = %s
                """,
                (scope.tenant_id, session_id),
            )
        assert session == {"next_turn_number": 2}
    finally:
        await cleanup_tenant(pool, scope.tenant_id)
        await pool.close()


@pytest.mark.parametrize("timing", ("before", "after"))
def test_live_command_commit_window_replays_atomically(
    timing: CommitTiming,
) -> None:
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(_verify_command_window(timing))


async def _verify_command_window(timing: CommitTiming) -> None:
    scenario = await open_reconciliation_scenario(SideEffectPolicy.NON_RETRYABLE)
    suffix = uuid4().hex
    command_id = f"resolution_{suffix}"
    resolution = SideEffectResolution(
        scenario.ambiguous.attempt_id.operation_id,
        SideEffectResolutionKind.FAIL,
    )
    command = DurableRunCommand(
        scenario.run_id,
        command_id,
        "resolve_side_effect",
        resolution,
    )
    try:
        baseline = await _command_truth(scenario, command_id)
        crashing = _CrashAtTransactionCommit(scenario.pool, timing)
        authorizer = AllowResolutionAuthorizer()
        service = RunCommandService(
            PostgresStateStore(crashing),  # type: ignore[arg-type]
            authorizer,
        )
        with pytest.raises(ProcessCrash):
            await service.submit(scenario.scope, scenario.session_id, command)
        assert crashing.fired is True
        assert authorizer.calls == [resolution]

        crashed_truth = await _command_truth(scenario, command_id)
        expected_committed = timing == "after"
        assert crashed_truth == _expected_command_truth(
            baseline,
            committed=expected_committed,
        )

        replay_authorizer = AllowResolutionAuthorizer()
        replay_service = RunCommandService(
            PostgresStateStore(scenario.pool),
            replay_authorizer,
        )
        replayed = await replay_service.submit(
            scenario.scope,
            scenario.session_id,
            command,
        )
        assert replayed.duplicate is expected_committed
        duplicate = await replay_service.submit(
            scenario.scope,
            scenario.session_id,
            command,
        )
        assert duplicate.duplicate is True
        assert replay_authorizer.calls == [resolution, resolution]
        assert await _command_truth(
            scenario,
            command_id,
        ) == _expected_command_truth(baseline, committed=True)
    finally:
        await scenario.close()


async def _submission_truth(
    pool: PostgresPool,
    scope: RequestScope,
) -> Row:
    async with pool.connection() as connection:
        truth = await fetchone(
            connection,
            """
            SELECT
              (SELECT COUNT(*) FROM agentos_distributed_sessions
               WHERE tenant_id = %s) AS session_count,
              (SELECT COUNT(*) FROM agentos_distributed_runs
               WHERE tenant_id = %s) AS run_count,
              (SELECT COUNT(*) FROM agentos_distributed_submissions
               WHERE tenant_id = %s) AS submission_count,
              (SELECT COUNT(*) FROM agentos_distributed_accepted_inputs
               WHERE tenant_id = %s) AS input_count,
              (SELECT COUNT(*) FROM agentos_distributed_outbox
               WHERE tenant_id = %s) AS outbox_count,
              (SELECT status FROM agentos_distributed_runs
               WHERE tenant_id = %s LIMIT 1) AS run_status,
              (SELECT status FROM agentos_distributed_accepted_inputs
               WHERE tenant_id = %s LIMIT 1) AS input_status
            """,
            (scope.tenant_id,) * 7,
        )
    assert truth is not None
    return truth


def _expected_submission_truth(committed: bool) -> dict[str, object]:
    count = int(committed)
    return {
        "session_count": count,
        "run_count": count,
        "submission_count": count,
        "input_count": count,
        "outbox_count": 2 * count,
        "run_status": "queued" if committed else None,
        "input_status": "accepted" if committed else None,
    }


async def _command_truth(
    scenario: ReconciliationScenario,
    command_id: str,
) -> Row:
    async with scenario.pool.connection() as connection:
        truth = await fetchone(
            connection,
            """
            SELECT
              (SELECT status FROM agentos_distributed_runs
               WHERE tenant_id = %s AND session_id = %s AND run_id = %s)
                AS run_status,
              (SELECT COUNT(*) FROM agentos_distributed_commands
               WHERE tenant_id = %s AND command_id = %s) AS command_count,
              (SELECT COUNT(*) FROM agentos_distributed_accepted_inputs
               WHERE tenant_id = %s AND source_kind = 'command' AND source_id = %s)
                AS command_input_count,
              (SELECT COUNT(*) FROM agentos_distributed_outbox
               WHERE tenant_id = %s AND session_id = %s AND run_id = %s)
                AS outbox_count
            """,
            (
                scenario.scope.tenant_id,
                scenario.session_id,
                scenario.run_id,
                scenario.scope.tenant_id,
                command_id,
                scenario.scope.tenant_id,
                command_id,
                scenario.scope.tenant_id,
                scenario.session_id,
                scenario.run_id,
            ),
        )
    assert truth is not None
    return truth


def _expected_command_truth(
    baseline: Row,
    *,
    committed: bool,
) -> dict[str, object]:
    return {
        "run_status": "queued" if committed else baseline["run_status"],
        "command_count": int(committed),
        "command_input_count": int(committed),
        "outbox_count": int(baseline["outbox_count"]) + 2 * int(committed),
    }
