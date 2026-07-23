from __future__ import annotations

import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest

from agentos.distributed.errors import StaleFenceError
from agentos.distributed.models import RequestScope, RunSubmission
from agentos.distributed.postgres.state import PostgresStateStore
from agentos.runtime.durable_commands import DurableRunCommand
from tests.integration._cancel_recovery_support import (
    open_claimed_run,
    prepare_pending_tool,
)
from tests.integration._cancel_recovery_truth import assert_cancelled_truth
from tests.integration._distributed_failure_support import (
    cleanup_tenant,
    live_postgres_settings,
    open_migrated_pool,
)


pytestmark = pytest.mark.integration


def test_live_queued_cancel_commits_one_atomic_terminal() -> None:
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(_verify_queued_cancel())


async def _verify_queued_cancel() -> None:
    settings = live_postgres_settings()
    suffix = uuid4().hex
    scope = RequestScope(f"tenant_cancel_queued_{suffix}", "principal_1")
    session_id = f"session_{suffix}"
    pool = await open_migrated_pool(settings.dsn)
    state = PostgresStateStore(pool)
    try:
        receipt = await state.submit(
            scope=scope,
            submission=RunSubmission(
                session_id,
                f"submission_{suffix}",
                "cancel before claim",
            ),
        )
        command = DurableRunCommand(
            receipt.run_id,
            f"cancel_{suffix}",
            "cancel",
        )

        accepted = await state.submit_command(
            scope=scope,
            session_id=session_id,
            command=command,
        )
        duplicate = await state.submit_command(
            scope=scope,
            session_id=session_id,
            command=command,
        )

        assert accepted.duplicate is False
        assert duplicate == replace(accepted, duplicate=True)
        await assert_cancelled_truth(
            pool,
            scope=scope,
            session_id=session_id,
            run_id=receipt.run_id,
            command_id=command.command_id,
            aggregate_version=accepted.aggregate_version,
        )
    finally:
        await cleanup_tenant(pool, scope.tenant_id)
        await pool.close()


def test_live_running_cancel_clears_recovery_cursor_and_fences_old_worker() -> None:
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(_verify_running_cancel())


async def _verify_running_cancel() -> None:
    run = await open_claimed_run("running")
    state = PostgresStateStore(run.pool)
    bound = state.bind(run.scope)
    try:
        guard, pending, _, _ = await prepare_pending_tool(run)
        command = DurableRunCommand(
            run.run_id,
            f"cancel_{uuid4().hex}",
            "cancel",
        )

        accepted = await state.submit_command(
            scope=run.scope,
            session_id=run.session_id,
            command=command,
        )

        await assert_cancelled_truth(
            run.pool,
            scope=run.scope,
            session_id=run.session_id,
            run_id=run.run_id,
            command_id=command.command_id,
            aggregate_version=accepted.aggregate_version,
            cleared_pending_assistant_id="assistant_tool_message",
        )
        with pytest.raises(StaleFenceError):
            await bound.commit_terminal(
                checkpoint=replace(pending, execution_cursor=None),
                run_id=run.run_id,
                turn_id=run.claimed.execution.input.turn_id,
                status="failed",
                guard=guard,
            )
    finally:
        await run.close()
