from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest

from agentos._json_values import freeze_json_mapping
from agentos.capabilities import SideEffectPolicy
from agentos.capabilities.invocation import ToolInvocation, ToolInvocationContext
from agentos.capabilities.result_refs import InlineToolResultRef
from agentos.distributed.errors import StaleFenceError
from agentos.distributed.models import RequestScope, RunSubmission
from agentos.distributed.postgres.claims import PostgresClaimStore
from agentos.distributed.postgres.side_effects import PostgresSideEffectStore
from agentos.distributed.postgres.state import PostgresStateStore
from agentos.runtime.checkpoint import (
    CheckpointStoredMessage,
    ContextCheckpoint,
    SessionCheckpoint,
)
from agentos.runtime.execution import RunExecutionCursor
from agentos.runtime.payloads import (
    PayloadProtectionContext,
    ProtectedPayloadRef,
    protect_payload,
)
from agentos.runtime.run_runtime import RunWriteGuard
from agentos.runtime.run_state import RunStatus
from agentos.runtime.side_effect_integrity import result_ref_digest
from agentos.runtime.side_effect_types import (
    SideEffectCompletion,
    SideEffectOutcomeKind,
    SideEffectStatus,
)
from agentos.runtime.tool_identity import (
    invocation_digest,
    invocation_id,
    operation_id,
)
from agentos.security import FernetPayloadProtector
from tests.integration._distributed_failure_support import (
    cleanup_tenant,
    execution_outbox_id,
    expire_and_reclaim,
    live_postgres_settings,
    open_migrated_pool,
    side_effect_records,
)


pytestmark = pytest.mark.integration


def test_live_takeover_rejects_old_guard_for_all_persistent_writes() -> None:
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(_verify_stale_fence_write_matrix())


async def _verify_stale_fence_write_matrix() -> None:
    settings = live_postgres_settings()
    suffix = uuid4().hex
    scope = RequestScope(f"tenant_stale_{suffix}", "principal_1")
    session_id = f"session_{suffix}"
    pool = await open_migrated_pool(settings.dsn)
    state = PostgresStateStore(pool)
    bound_state = state.bind(scope)
    claims = PostgresClaimStore(pool)
    ledger = PostgresSideEffectStore(pool)
    try:
        receipt = await state.submit(
            scope=scope,
            submission=RunSubmission(
                session_id,
                f"submission_{suffix}",
                "verify stale fencing",
            ),
        )
        outbox_id = await execution_outbox_id(
            pool,
            scope=scope,
            session_id=session_id,
            run_id=receipt.run_id,
        )
        old_claimed = await claims.claim_pending_turn(
            scope=scope,
            outbox_id=outbox_id,
            owner_id=f"worker_old_{suffix}",
            ttl=timedelta(minutes=1),
        )
        assert old_claimed is not None
        recovered = await expire_and_reclaim(
            pool,
            claims=claims,
            scope=scope,
            session_id=session_id,
            owner_id=f"worker_new_{suffix}",
        )
        old_guard = old_claimed.execution.guard
        new_guard = recovered.execution.guard
        assert new_guard.expected_version == old_guard.expected_version
        assert new_guard.claim_id != old_guard.claim_id
        assert new_guard.fencing_token > old_guard.fencing_token

        with pytest.raises(StaleFenceError):
            await bound_state.transition(
                session_id=session_id,
                run_id=receipt.run_id,
                status=RunStatus.RUNNING,
                guard=old_guard,
            )
        queued = await state.get_run(
            scope=scope,
            session_id=session_id,
            run_id=receipt.run_id,
        )
        assert queued is not None
        assert queued.status is RunStatus.QUEUED
        assert queued.aggregate_version == old_guard.expected_version

        running = await bound_state.transition(
            session_id=session_id,
            run_id=receipt.run_id,
            status=RunStatus.RUNNING,
            guard=new_guard,
        )
        new_guard = replace(
            new_guard,
            expected_version=running.aggregate_version,
        )
        old_guard = _stale_guard_at_current_version(old_guard, new_guard)
        checkpoint = _running_checkpoint(
            session_id=session_id,
            turn_id=recovered.execution.input.turn_id,
            user_message_id=recovered.execution.input.user_message_id,
            content=recovered.execution.input.input.content,
        )

        with pytest.raises(StaleFenceError):
            await bound_state.commit_running(
                checkpoint=checkpoint,
                run_id=receipt.run_id,
                turn_id=recovered.execution.input.turn_id,
                guard=old_guard,
            )
        assert await bound_state.latest_checkpoint(session_id, receipt.run_id) is None

        running_checkpoint = await bound_state.commit_running(
            checkpoint=checkpoint,
            run_id=receipt.run_id,
            turn_id=recovered.execution.input.turn_id,
            guard=new_guard,
        )
        new_guard = replace(
            new_guard,
            expected_version=running_checkpoint.aggregate_version,
        )
        old_guard = _stale_guard_at_current_version(old_guard, new_guard)
        terminal_checkpoint = _terminal_checkpoint(checkpoint)

        with pytest.raises(StaleFenceError):
            await bound_state.commit_terminal(
                checkpoint=terminal_checkpoint,
                run_id=receipt.run_id,
                turn_id=recovered.execution.input.turn_id,
                status="completed",
                guard=old_guard,
            )
        current_run = await state.get_run(
            scope=scope,
            session_id=session_id,
            run_id=receipt.run_id,
        )
        assert current_run is not None
        assert current_run.status is RunStatus.RUNNING
        assert current_run.aggregate_version == new_guard.expected_version
        assert await bound_state.latest_checkpoint(
            session_id,
            receipt.run_id,
        ) == running_checkpoint

        invocation, invocation_ref = _invocation(
            scope=scope,
            session_id=session_id,
            run_id=receipt.run_id,
            turn_id=recovered.execution.input.turn_id,
        )
        digest = invocation_digest(invocation)
        with pytest.raises(StaleFenceError):
            await ledger.reserve(
                invocation=invocation,
                policy=SideEffectPolicy.PURE,
                invocation_digest=digest,
                invocation_ref=invocation_ref,
                guard=old_guard,
            )
        assert await side_effect_records(
            pool,
            tenant_id=scope.tenant_id,
            session_id=session_id,
        ) == ()

        reserved = await ledger.reserve(
            invocation=invocation,
            policy=SideEffectPolicy.PURE,
            invocation_digest=digest,
            invocation_ref=invocation_ref,
            guard=new_guard,
        )
        result_ref = InlineToolResultRef("completed by the current worker")
        completion = SideEffectCompletion(
            SideEffectOutcomeKind.PROVIDER_RESULT,
            result_ref,
            result_ref_digest(result_ref),
        )
        with pytest.raises(StaleFenceError):
            await ledger.mark_started(
                attempt_id=reserved.attempt_id,
                guard=old_guard,
            )
        with pytest.raises(StaleFenceError):
            await ledger.complete(
                attempt_id=reserved.attempt_id,
                completion=completion,
                guard=old_guard,
            )
        assert await side_effect_records(
            pool,
            tenant_id=scope.tenant_id,
            session_id=session_id,
        ) == (reserved,)

        current_started = await ledger.mark_started(
            attempt_id=reserved.attempt_id,
            guard=new_guard,
        )
        current_completed = await ledger.complete(
            attempt_id=reserved.attempt_id,
            completion=completion,
            guard=new_guard,
        )
        terminal = await bound_state.commit_terminal(
            checkpoint=terminal_checkpoint,
            run_id=receipt.run_id,
            turn_id=recovered.execution.input.turn_id,
            status="completed",
            guard=new_guard,
        )

        assert current_started.fencing_token == new_guard.fencing_token
        assert current_completed.status is SideEffectStatus.COMPLETED
        assert current_completed.fencing_token == new_guard.fencing_token
        assert terminal.aggregate_version == new_guard.expected_version + 1
        final_run = await state.get_run(
            scope=scope,
            session_id=session_id,
            run_id=receipt.run_id,
        )
        assert final_run is not None
        assert final_run.status is RunStatus.COMPLETED
        assert final_run.result is not None
        assert final_run.result.content == "fresh claim completed"
    finally:
        await cleanup_tenant(pool, scope.tenant_id)
        await pool.close()


def _stale_guard_at_current_version(
    old_guard: RunWriteGuard,
    new_guard: RunWriteGuard,
) -> RunWriteGuard:
    return RunWriteGuard(
        new_guard.expected_version,
        old_guard.claim_id,
        old_guard.fencing_token,
    )


def _running_checkpoint(
    *,
    session_id: str,
    turn_id: str,
    user_message_id: str,
    content: str,
) -> SessionCheckpoint:
    user_message = CheckpointStoredMessage(user_message_id, "user", content)
    return SessionCheckpoint(
        session_id=session_id,
        session_status="running",
        next_turn_number=2,
        messages=(user_message,),
        active_refs=(user_message.id,),
        context=ContextCheckpoint(
            schema=(),
            working_state=freeze_json_mapping({}),
            compressed_history=(),
            inherited_state=(),
        ),
        execution_cursor=RunExecutionCursor(turn_id, "before_provider", 0),
    )


def _terminal_checkpoint(checkpoint: SessionCheckpoint) -> SessionCheckpoint:
    assistant = CheckpointStoredMessage(
        "assistant_message_1",
        "assistant",
        "fresh claim completed",
    )
    return replace(
        checkpoint,
        messages=(*checkpoint.messages, assistant),
        active_refs=(*checkpoint.active_refs, assistant.id),
        execution_cursor=None,
    )


def _invocation(
    *,
    scope: RequestScope,
    session_id: str,
    run_id: str,
    turn_id: str,
) -> tuple[ToolInvocation, ProtectedPayloadRef]:
    stable_invocation_id = invocation_id(
        tenant_id=scope.tenant_id,
        session_id=session_id,
        run_id=run_id,
        turn_id=turn_id,
        provider_call_index=0,
        tool_index=0,
    )
    stable_operation_id = operation_id(
        tenant_id=scope.tenant_id,
        session_id=session_id,
        run_id=run_id,
        turn_id=turn_id,
        invocation_id=stable_invocation_id,
    )
    invocation = ToolInvocation(
        "write_probe",
        {"value": "probe"},
        ToolInvocationContext(
            stable_invocation_id,
            stable_operation_id,
            scope.tenant_id,
            session_id,
            run_id,
            turn_id,
            "tool_call_1",
            1,
        ),
    )
    protector = FernetPayloadProtector(FernetPayloadProtector.generate_key())
    reference = protect_payload(
        protector,
        invocation.arguments,
        context=PayloadProtectionContext(
            tenant_id=scope.tenant_id,
            session_id=session_id,
            run_id=run_id,
            turn_id=turn_id,
            invocation_id=stable_invocation_id,
            message_id="assistant_message_1",
            tool_call_id="tool_call_1",
            tool_name="write_probe",
        ),
    )
    return invocation, reference
