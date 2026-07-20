from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING

from agentos._builder_hydration import hydrate_runtime_checkpoint
from agentos._builder_state import RuntimeStateComponents
from agentos.artifacts import ArtifactRuntime
from agentos.artifacts.sqlite_filesystem import SqliteFilesystemArtifactStore
from agentos.context import ContextRuntime
from agentos.messages import MessageRuntime
from agentos.runtime.agent import Agent
from agentos.runtime.checkpoint import RuntimeCheckpointSource, SessionCheckpoint
from agentos.runtime.durable_runtime import (
    DurableCommandRuntime,
    DurableStateStore,
)
from agentos.runtime.payloads import PayloadProtectionContext, PayloadProtector
from agentos.runtime.run_runtime import RunRuntime
from agentos.runtime.session import SessionState
from agentos.runtime.tool_payloads import ToolPayloadRuntime

if TYPE_CHECKING:
    from agentos.builder import AgentBuilder
    from agentos.events import EventBus


async def build_durable_agent(
    *,
    builder: AgentBuilder,
    store: DurableStateStore,
    artifact_store: SqliteFilesystemArtifactStore,
    session_id: str,
    clock: Callable[[], datetime],
    payload_protector: PayloadProtector | None,
) -> Agent:
    """水合权威状态，并复用 AgentBuilder 的唯一 QueryLoop 装配路径。"""

    await store.recover_abandoned_runs(session_id)
    checkpoint = await store.load_checkpoint(session_id)
    payloads = ToolPayloadRuntime(
        payload_protector,
        PayloadProtectionContext(None, session_id),
    )
    state, messages = _runtime_components(
        checkpoint=checkpoint,
        session_id=session_id,
        store=store,
        artifact_store=artifact_store,
        event_bus=builder._event_bus,
        payloads=payloads,
    )
    await store.initialize_session(state.session)
    checkpoint_source = RuntimeCheckpointSource(
        state.session,
        messages,
        state.context,
        payloads,
    )
    kwargs = builder._query_loop_kwargs(
        session_id,
        state=state,
        messages=messages,
    )
    kwargs["checkpoint_source"] = checkpoint_source
    kwargs["checkpoint_store"] = store
    kwargs["side_effect_store"] = store.side_effect_store
    kwargs["tool_payload_runtime"] = payloads
    kwargs["recovery_cursor"] = (
        None if checkpoint is None else checkpoint.execution_cursor
    )
    command_runtime = DurableCommandRuntime(session_id, store, clock)
    return Agent(
        query_loop_kwargs=kwargs,
        durable_command_runtime=command_runtime,
    )


def validate_durable_builder(builder: AgentBuilder) -> None:
    """拒绝无法在重启后安全重建的预绑定 Runtime。"""

    forbidden = (
        builder._context_runtime,
        builder._message_runtime,
        builder._compression_runtime,
        builder._tool_call_router,
    )
    if any(item is not None for item in forbidden):
        raise ValueError("durable profile rejects pre-bound runtime state")


def _runtime_components(
    *,
    checkpoint: SessionCheckpoint | None,
    session_id: str,
    store: DurableStateStore,
    artifact_store: SqliteFilesystemArtifactStore,
    event_bus: EventBus | None,
    payloads: ToolPayloadRuntime,
) -> tuple[RuntimeStateComponents, MessageRuntime]:
    if checkpoint is None:
        session = SessionState(session_id)
        messages = MessageRuntime()
        context = ContextRuntime(event_bus=event_bus, session_id=session_id)
    else:
        session, messages, context = hydrate_runtime_checkpoint(
            checkpoint,
            event_bus,
            payloads,
        )
    return (
        RuntimeStateComponents(
            context=context,
            artifacts=ArtifactRuntime(
                session_id=session_id,
                store=artifact_store,
                event_bus=event_bus,
            ),
            runs=RunRuntime(session_id=session_id, store=store),
            session=session,
        ),
        messages,
    )
__all__ = ["build_durable_agent", "validate_durable_builder"]
