from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from uuid import uuid4

from agentos import AgentBuilder
from agentos._builder_distributed import ClaimScopedAgentFactory
from agentos._waiting import AgentWaiting, WaitReason
from agentos.capabilities import (
    RegisteredTool,
    SideEffectPolicy,
    ToolCompensationInvocation,
    ToolInvocation,
)
from agentos.distributed.models import ClaimedExecution, RequestScope, RunSubmission
from agentos.distributed.postgres._database import PostgresPool
from agentos.distributed.postgres.artifacts import PostgresArtifactStore
from agentos.distributed.postgres.claims import PostgresClaimStore
from agentos.distributed.postgres.resume_validation import (
    PostgresSideEffectResumeValidator,
)
from agentos.distributed.postgres.side_effects import PostgresSideEffectStore
from agentos.distributed.postgres.state import PostgresStateStore
from agentos.distributed.services import RunCommandService
from agentos.providers import ProviderResponse, ProviderToolCall
from agentos.runtime.durable_commands import DurableCommandReceipt, DurableRunCommand
from agentos.runtime.payloads import PayloadProtector
from agentos.runtime.run_state import RunStatus
from agentos.runtime.side_effect_store import SideEffectStore
from agentos.runtime.side_effect_resume import SideEffectResume
from agentos.runtime.side_effect_types import (
    SideEffectRecord,
    SideEffectResolution,
    SideEffectStatus,
)
from agentos.security import FernetPayloadProtector
from tests.integration._cancel_recovery_support import ScriptedProvider
from tests.integration._distributed_failure_support import (
    NoopBlobStore,
    cleanup_tenant,
    execution_outbox_id,
    live_postgres_settings,
    open_migrated_pool,
    side_effect_records,
)


@dataclass(slots=True)
class ExternalEffectProbe:
    handler_attempts: list[int] = field(default_factory=list)
    compensation_invocations: list[str] = field(default_factory=list)
    compensation_effects: set[str] = field(default_factory=set)

    async def invoke(self, invocation: ToolInvocation) -> str:
        self.handler_attempts.append(invocation.context.attempt)
        if invocation.context.attempt == 1:
            raise RuntimeError("external outcome is unknown")
        return "retried safely"

    async def compensate(self, invocation: ToolCompensationInvocation) -> None:
        operation_id = invocation.context.compensation_operation_id
        self.compensation_invocations.append(operation_id)
        self.compensation_effects.add(operation_id)


@dataclass(slots=True)
class AllowResolutionAuthorizer:
    calls: list[SideEffectResolution] = field(default_factory=list)

    async def authorize(
        self,
        *,
        scope: RequestScope,
        session_id: str,
        run_id: str,
        resolution: SideEffectResolution,
    ) -> None:
        del scope, session_id, run_id
        self.calls.append(resolution)


@dataclass(frozen=True, slots=True)
class ReconciliationScenario:
    pool: PostgresPool
    scope: RequestScope
    session_id: str
    run_id: str
    artifacts: PostgresArtifactStore
    protector: PayloadProtector
    claims: PostgresClaimStore
    tool: RegisteredTool
    ambiguous: SideEffectRecord
    probe: ExternalEffectProbe

    async def close(self) -> None:
        await self.artifacts.close()
        await cleanup_tenant(self.pool, self.scope.tenant_id)
        await self.pool.close()


@dataclass(frozen=True, slots=True)
class ResolutionClaim:
    receipt: DurableCommandReceipt
    claimed: ClaimedExecution
    authorizer: AllowResolutionAuthorizer


async def open_reconciliation_scenario(
    policy: SideEffectPolicy,
) -> ReconciliationScenario:
    settings = live_postgres_settings()
    suffix = uuid4().hex
    scope = RequestScope(f"tenant_resolution_{suffix}", "principal_1")
    session_id = f"session_{suffix}"
    pool = await open_migrated_pool(settings.dsn)
    artifacts = PostgresArtifactStore(pool, NoopBlobStore())
    protector: PayloadProtector = FernetPayloadProtector(
        FernetPayloadProtector.generate_key(),
    )
    claims = PostgresClaimStore(pool)
    probe = ExternalEffectProbe()
    tool = RegisteredTool(
        name="unsafe_tool",
        description="Perform an external operation.",
        parameters={"type": "object"},
        handler=probe.invoke,
        side_effect_policy=policy,
        compensation_handler=(
            probe.compensate
            if policy is SideEffectPolicy.COMPENSATABLE
            else None
        ),
    )
    provider = ScriptedProvider(
        [
            ProviderResponse(
                tool_calls=(
                    ProviderToolCall("call_unsafe", "unsafe_tool", {}),
                ),
            ),
        ],
    )
    try:
        receipt = await PostgresStateStore(pool).submit(
            scope=scope,
            submission=RunSubmission(
                session_id,
                f"submission_{suffix}",
                "perform the external operation",
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
            owner_id=f"worker_initial_{suffix}",
            ttl=timedelta(minutes=1),
        )
        assert claimed is not None
        factory = scenario_agent_factory(
            pool=pool,
            protector=protector,
            artifacts=artifacts,
            builder=AgentBuilder().provider(provider).tools([tool]),
        )
        agent = await factory.hydrate(claimed=claimed)

        outcome = await agent.run(claimed.execution)

        records = await side_effect_records(
            pool,
            tenant_id=scope.tenant_id,
            session_id=session_id,
        )
        assert len(records) == 1
        ambiguous = records[0]
        reason = WaitReason(
            "side_effect_reconciliation",
            ambiguous.attempt_id.operation_id,
        )
        assert outcome == AgentWaiting(receipt.run_id, reason)
        assert provider.calls == 1
        assert probe.handler_attempts == [1]
        assert ambiguous.status is SideEffectStatus.AMBIGUOUS
        run = await PostgresStateStore(pool).get_run(
            scope=scope,
            session_id=session_id,
            run_id=receipt.run_id,
        )
        assert run is not None
        assert run.status is RunStatus.WAITING
        return ReconciliationScenario(
            pool,
            scope,
            session_id,
            receipt.run_id,
            artifacts,
            protector,
            claims,
            tool,
            ambiguous,
            probe,
        )
    except BaseException:
        await artifacts.close()
        await cleanup_tenant(pool, scope.tenant_id)
        await pool.close()
        raise


async def submit_resolution(
    scenario: ReconciliationScenario,
    resolution: SideEffectResolution,
) -> ResolutionClaim:
    command = DurableRunCommand(
        scenario.run_id,
        f"resolution_{uuid4().hex}",
        "resolve_side_effect",
        resolution,
    )
    authorizer = AllowResolutionAuthorizer()
    service = RunCommandService(
        PostgresStateStore(scenario.pool),
        authorizer,
    )
    receipt = await service.submit(
        scenario.scope,
        scenario.session_id,
        command,
    )
    duplicate = await service.submit(
        scenario.scope,
        scenario.session_id,
        command,
    )
    assert duplicate.duplicate is True
    assert authorizer.calls == [resolution, resolution]
    outbox_id = await execution_outbox_id(
        scenario.pool,
        scope=scenario.scope,
        session_id=scenario.session_id,
        run_id=scenario.run_id,
        latest=True,
    )
    claimed = await scenario.claims.claim_pending_turn(
        scope=scenario.scope,
        outbox_id=outbox_id,
        owner_id=f"worker_resolution_{uuid4().hex}",
        ttl=timedelta(minutes=1),
    )
    assert claimed is not None
    assert type(claimed.execution.preparation) is SideEffectResume
    return ResolutionClaim(receipt, claimed, authorizer)


def scenario_agent_factory(
    *,
    pool: PostgresPool,
    protector: PayloadProtector,
    artifacts: PostgresArtifactStore,
    builder: AgentBuilder,
    state: PostgresStateStore | None = None,
    side_effects: SideEffectStore | None = None,
) -> ClaimScopedAgentFactory:
    return ClaimScopedAgentFactory(
        builder=builder,
        state_store=state or PostgresStateStore(pool),
        artifact_store=artifacts,
        side_effect_store=side_effects or PostgresSideEffectStore(pool),
        side_effect_resume_validator=PostgresSideEffectResumeValidator(pool),
        payload_protector=protector,
    )


__all__ = [
    "ExternalEffectProbe",
    "ReconciliationScenario",
    "ResolutionClaim",
    "open_reconciliation_scenario",
    "scenario_agent_factory",
    "submit_resolution",
]
