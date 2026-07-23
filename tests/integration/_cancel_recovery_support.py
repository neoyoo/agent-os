from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta
from uuid import uuid4

from agentos import AgentBuilder
from agentos._builder_distributed import ClaimScopedAgentFactory
from agentos._json_values import freeze_json_mapping
from agentos.capabilities import ToolInvocation
from agentos.capabilities.invocation import ToolInvocationContext
from agentos.distributed.models import ClaimedExecution, RequestScope, RunSubmission
from agentos.distributed.postgres._database import PostgresPool
from agentos.distributed.postgres.artifacts import PostgresArtifactStore
from agentos.distributed.postgres.claims import PostgresClaimStore
from agentos.distributed.postgres.resume_validation import (
    PostgresSideEffectResumeValidator,
)
from agentos.distributed.postgres.state import PostgresStateStore
from agentos.providers import ProviderRequest, ProviderResponse
from agentos.runtime.checkpoint import (
    CheckpointStoredMessage,
    CheckpointToolCall,
    ContextCheckpoint,
    SessionCheckpoint,
)
from agentos.runtime.execution import (
    AcceptedStartInput,
    PendingToolInvocation,
    RunExecutionCursor,
)
from agentos.runtime.payloads import (
    PayloadProtectionContext,
    PayloadProtector,
    ProtectedPayloadRef,
    protect_payload,
)
from agentos.runtime.run_runtime import RunWriteGuard
from agentos.runtime.run_state import RunStatus
from agentos.runtime.side_effect_store import SideEffectStore
from agentos.runtime.tool_identity import invocation_id, operation_id
from agentos.security import FernetPayloadProtector
from tests.integration._distributed_failure_support import (
    cleanup_tenant,
    execution_outbox_id,
    live_postgres_settings,
    open_migrated_pool,
)


class ScriptedProvider:
    def __init__(self, responses: list[ProviderResponse]) -> None:
        self._responses = responses
        self.calls = 0

    async def async_complete(self, _request: ProviderRequest) -> ProviderResponse:
        response = self._responses[self.calls]
        self.calls += 1
        return response


@dataclass(frozen=True, slots=True)
class ClaimedRun:
    pool: PostgresPool
    scope: RequestScope
    session_id: str
    run_id: str
    outbox_id: str
    claimed: ClaimedExecution
    protector: PayloadProtector

    async def close(self) -> None:
        await cleanup_tenant(self.pool, self.scope.tenant_id)
        await self.pool.close()


async def open_claimed_run(label: str) -> ClaimedRun:
    settings = live_postgres_settings()
    suffix = uuid4().hex
    scope = RequestScope(f"tenant_cancel_{label}_{suffix}", "principal_1")
    session_id = f"session_{suffix}"
    pool = await open_migrated_pool(settings.dsn)
    state = PostgresStateStore(pool)
    receipt = await state.submit(
        scope=scope,
        submission=RunSubmission(
            session_id,
            f"submission_{suffix}",
            f"exercise {label} cancel window",
        ),
    )
    outbox_id = await execution_outbox_id(
        pool,
        scope=scope,
        session_id=session_id,
        run_id=receipt.run_id,
    )
    claimed = await PostgresClaimStore(pool).claim_pending_turn(
        scope=scope,
        outbox_id=outbox_id,
        owner_id=f"worker_{suffix}",
        ttl=timedelta(minutes=1),
    )
    assert claimed is not None
    protector: PayloadProtector = FernetPayloadProtector(
        FernetPayloadProtector.generate_key(),
    )
    return ClaimedRun(
        pool,
        scope,
        session_id,
        receipt.run_id,
        outbox_id,
        claimed,
        protector,
    )


def cancel_agent_factory(
    *,
    run: ClaimedRun,
    builder: AgentBuilder,
    state: PostgresStateStore,
    artifacts: PostgresArtifactStore,
    side_effects: SideEffectStore,
) -> ClaimScopedAgentFactory:
    return ClaimScopedAgentFactory(
        builder=builder,
        state_store=state,
        artifact_store=artifacts,
        side_effect_store=side_effects,
        side_effect_resume_validator=PostgresSideEffectResumeValidator(run.pool),
        payload_protector=run.protector,
    )


async def prepare_pending_tool(
    run: ClaimedRun,
) -> tuple[
    RunWriteGuard,
    SessionCheckpoint,
    ToolInvocation,
    ProtectedPayloadRef,
]:
    bound = PostgresStateStore(run.pool).bind(run.scope)
    started = await bound.transition(
        session_id=run.session_id,
        run_id=run.run_id,
        status=RunStatus.RUNNING,
        guard=run.claimed.execution.guard,
    )
    guard = replace(
        run.claimed.execution.guard,
        expected_version=started.aggregate_version,
    )
    accepted = run.claimed.execution.input
    assert type(accepted) is AcceptedStartInput
    before = _before_provider_checkpoint(
        session_id=run.session_id,
        turn_id=accepted.turn_id,
        user_message_id=accepted.user_message_id,
        content=accepted.input.content,
    )
    committed = await bound.commit_running(
        checkpoint=before,
        run_id=run.run_id,
        turn_id=accepted.turn_id,
        guard=guard,
    )
    guard = replace(guard, expected_version=committed.aggregate_version)
    invocation, reference = _tool_invocation(run, accepted.turn_id)
    pending = _pending_tools_checkpoint(before, invocation, reference)
    committed = await bound.commit_running(
        checkpoint=pending,
        run_id=run.run_id,
        turn_id=accepted.turn_id,
        guard=guard,
    )
    return (
        replace(guard, expected_version=committed.aggregate_version),
        pending,
        invocation,
        reference,
    )


def _before_provider_checkpoint(
    *,
    session_id: str,
    turn_id: str,
    user_message_id: str,
    content: str,
) -> SessionCheckpoint:
    user = CheckpointStoredMessage(user_message_id, "user", content)
    return SessionCheckpoint(
        session_id,
        "running",
        2,
        (user,),
        (user.id,),
        ContextCheckpoint((), freeze_json_mapping({}), (), ()),
        RunExecutionCursor(turn_id, "before_provider", 0),
    )


def _tool_invocation(
    run: ClaimedRun,
    turn_id: str,
) -> tuple[ToolInvocation, ProtectedPayloadRef]:
    stable_invocation_id = invocation_id(
        tenant_id=run.scope.tenant_id,
        session_id=run.session_id,
        run_id=run.run_id,
        turn_id=turn_id,
        provider_call_index=0,
        tool_index=0,
    )
    stable_operation_id = operation_id(
        tenant_id=run.scope.tenant_id,
        session_id=run.session_id,
        run_id=run.run_id,
        turn_id=turn_id,
        invocation_id=stable_invocation_id,
    )
    invocation = ToolInvocation(
        "charge",
        {},
        ToolInvocationContext(
            stable_invocation_id,
            stable_operation_id,
            run.scope.tenant_id,
            run.session_id,
            run.run_id,
            turn_id,
            "call_charge",
            1,
        ),
    )
    reference = protect_payload(
        run.protector,
        invocation.arguments,
        context=PayloadProtectionContext(
            tenant_id=run.scope.tenant_id,
            session_id=run.session_id,
            run_id=run.run_id,
            turn_id=turn_id,
            invocation_id=stable_invocation_id,
            message_id="assistant_tool_message",
            tool_call_id="call_charge",
            tool_name="charge",
        ),
    )
    return invocation, reference


def _pending_tools_checkpoint(
    before: SessionCheckpoint,
    invocation: ToolInvocation,
    reference: ProtectedPayloadRef,
) -> SessionCheckpoint:
    call = CheckpointToolCall(
        "call_charge",
        "charge",
        invocation.context.run_id,
        invocation.context.turn_id,
        invocation.context.invocation_id,
        reference,
    )
    assistant = CheckpointStoredMessage(
        "assistant_tool_message",
        "assistant",
        "",
        tool_calls=(call,),
    )
    pending = PendingToolInvocation(
        invocation.context.invocation_id,
        "call_charge",
        "charge",
        reference,
    )
    return replace(
        before,
        messages=(*before.messages, assistant),
        active_refs=(*before.active_refs, assistant.id),
        execution_cursor=RunExecutionCursor(
            invocation.context.turn_id,
            "pending_tools",
            0,
            assistant.id,
            (pending,),
        ),
    )


__all__ = [
    "ClaimedRun",
    "ScriptedProvider",
    "cancel_agent_factory",
    "open_claimed_run",
    "prepare_pending_tool",
]
