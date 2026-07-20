from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, cast

from agentos._builder_hydration import hydrate_runtime_checkpoint
from agentos._builder_state import RuntimeStateComponents
from agentos.artifacts import ArtifactRuntime
from agentos.context import ContextRuntime
from agentos.distributed._claim_artifacts import ClaimArtifactStore
from agentos.distributed._tool_result_artifacts import (
    DistributedToolResultRefProjector,
)
from agentos.distributed.models import ClaimedExecution, RequestScope
from agentos.distributed.protocols import DistributedArtifactPort
from agentos.messages import MessageRuntime
from agentos.policies import ToolResultBudget
from agentos.runtime.agent import Agent
from agentos.runtime.checkpoint import RuntimeCheckpointSource
from agentos.runtime.durable_runtime import DurableStateStore
from agentos.runtime.payloads import PayloadProtectionContext, PayloadProtector
from agentos.runtime.run_runtime import RunRuntime
from agentos.runtime.session import SessionState
from agentos.runtime.side_effect_resume_validator import SideEffectResumeValidator
from agentos.runtime.side_effect_store import SideEffectStore
from agentos.runtime.tool_payloads import ToolPayloadRuntime
from agentos.tokens import TokenCounter

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentos.builder import AgentBuilder


class ScopedStateStoreFactory(Protocol):
    def bind(self, scope: RequestScope) -> DurableStateStore: ...


@dataclass(frozen=True, slots=True)
class ClaimScopedAgentFactory:
    """Hydrate one isolated Agent from a PostgreSQL-authoritative claim."""

    builder: AgentBuilder
    state_store: ScopedStateStoreFactory
    artifact_store: DistributedArtifactPort
    side_effect_store: SideEffectStore
    side_effect_resume_validator: SideEffectResumeValidator
    payload_protector: PayloadProtector | None = None

    def __post_init__(self) -> None:
        validate_distributed_builder(self.builder)

    async def hydrate(self, *, claimed: ClaimedExecution) -> Agent:
        if type(claimed) is not ClaimedExecution:
            raise TypeError("claimed must be ClaimedExecution")
        scope = claimed.target.scope
        session_id = claimed.target.session_id
        store = self.state_store.bind(scope)
        checkpoint = await store.load_checkpoint(session_id)
        payloads = ToolPayloadRuntime(
            self.payload_protector,
            PayloadProtectionContext(scope.tenant_id, session_id),
        )
        if checkpoint is None:
            session = SessionState(session_id)
            messages = MessageRuntime()
            context = ContextRuntime(
                event_bus=self.builder._event_bus,
                session_id=session_id,
            )
        else:
            session, messages, context = hydrate_runtime_checkpoint(
                checkpoint,
                self.builder._event_bus,
                payloads,
            )
        state = RuntimeStateComponents(
            context=context,
            artifacts=ArtifactRuntime(
                session_id=session_id,
                store=ClaimArtifactStore(scope, session_id, self.artifact_store),
                event_bus=self.builder._event_bus,
            ),
            runs=RunRuntime(session_id=session_id, store=store),
            session=session,
        )
        checkpoint_source = RuntimeCheckpointSource(
            session,
            messages,
            context,
            payloads,
        )
        kwargs = self.builder._query_loop_kwargs(
            session_id,
            state=state,
            messages=messages,
        )
        kwargs["checkpoint_source"] = checkpoint_source
        kwargs["checkpoint_store"] = store
        kwargs["side_effect_store"] = self.side_effect_store
        kwargs["side_effect_resume_validator"] = self.side_effect_resume_validator
        kwargs["tool_payload_runtime"] = payloads
        kwargs["tool_result_ref_projector"] = DistributedToolResultRefProjector(
            scope=scope,
            session_id=session_id,
            artifacts=self.artifact_store,
            budget=cast(ToolResultBudget, kwargs["tool_result_budget"]),
            token_counter=cast(TokenCounter, kwargs["token_counter"]),
        )
        return Agent(query_loop_kwargs=kwargs)


def validate_distributed_builder(builder: AgentBuilder) -> None:
    forbidden = (
        builder._context_runtime,
        builder._message_runtime,
        builder._compression_runtime,
        builder._tool_call_router,
    )
    if any(item is not None for item in forbidden):
        raise ValueError("distributed profile rejects pre-bound runtime state")
    if builder._context_projection_providers is not None:
        raise ValueError(
            "distributed profile rejects static context projections",
        )


__all__ = ["ClaimScopedAgentFactory", "validate_distributed_builder"]
