from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING

from agentos._builder_state import RuntimeStateComponents
from agentos._json_values import thaw_json_value
from agentos.artifacts import ArtifactRuntime
from agentos.artifacts.sqlite_filesystem import SqliteFilesystemArtifactStore
from agentos.context import ContextRuntime, ContextState, WorkingStateSchema
from agentos.context.models import ContextProtocolError
from agentos.context.schema import (
    validate_working_state_fields,
    validate_working_state_value,
)
from agentos.messages import ActiveWindow, MessageRef, MessageRuntime
from agentos.runtime.agent import Agent
from agentos.runtime.checkpoint import RuntimeCheckpointSource, SessionCheckpoint
from agentos.runtime.durable_runtime import (
    DurableCommandRuntime,
    DurableWaitingRuntime,
    DurableStateStore,
)
from agentos.runtime.errors import CheckpointCorruptedError
from agentos.runtime.run_runtime import RunRuntime
from agentos.runtime.session import SessionState

if TYPE_CHECKING:
    from agentos.builder import AgentBuilder
    from agentos.events import EventBus


def build_durable_agent(
    *,
    builder: AgentBuilder,
    store: DurableStateStore,
    artifact_store: SqliteFilesystemArtifactStore,
    session_id: str,
    clock: Callable[[], datetime],
) -> Agent:
    """水合权威状态，并复用 AgentBuilder 的唯一 QueryLoop 装配路径。"""

    store.recover_abandoned_runs(session_id)
    checkpoint = store.load_checkpoint(session_id)
    state, messages = _runtime_components(
        checkpoint=checkpoint,
        session_id=session_id,
        store=store,
        artifact_store=artifact_store,
        event_bus=builder._event_bus,
    )
    store.initialize_session(state.session)
    checkpoint_source = RuntimeCheckpointSource(
        state.session,
        messages,
        state.context,
    )
    store.bind_checkpoint_source(session_id, checkpoint_source)
    kwargs = builder._query_loop_kwargs(
        session_id,
        state=state,
        messages=messages,
    )
    kwargs["waiting_runtime"] = DurableWaitingRuntime(
        session_id,
        store,
        checkpoint_source,
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
) -> tuple[RuntimeStateComponents, MessageRuntime]:
    if checkpoint is None:
        session = SessionState(session_id)
        messages = MessageRuntime()
        context = ContextRuntime(event_bus=event_bus, session_id=session_id)
    else:
        session, messages, context = _hydrate(checkpoint, event_bus)
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


def _hydrate(
    checkpoint: SessionCheckpoint,
    event_bus: EventBus | None,
) -> tuple[SessionState, MessageRuntime, ContextRuntime]:
    try:
        fields = validate_working_state_fields(
            checkpoint.context.schema,
            allow_empty=True,
        )
        declared = {field.name: field for field in fields}
        working = thaw_json_value(checkpoint.context.working_state)
        if type(working) is not dict or set(working) - set(declared):
            raise ContextProtocolError("checkpoint working state is invalid")
        for name, value in working.items():
            validate_working_state_value(declared[name].type, value)
        messages = MessageRuntime()
        messages.hydrate_messages(list(checkpoint.messages))
        messages.active_window = ActiveWindow(
            MessageRef(message_id) for message_id in checkpoint.active_refs
        )
        context = ContextRuntime(
            state=ContextState(
                working_state_schema=WorkingStateSchema(fields),
                working_state=working,
                compressed_history=checkpoint.context.compressed_history,
                inherited_state=checkpoint.context.inherited_state,
            ),
            event_bus=event_bus,
            session_id=checkpoint.session_id,
        )
        session = SessionState.from_snapshot(
            checkpoint.session_id,
            checkpoint.session_status,
            checkpoint.next_turn_number,
        )
        return session, messages, context
    except (ContextProtocolError, KeyError, TypeError, ValueError):
        raise CheckpointCorruptedError("checkpoint hydration state is corrupted") from None


__all__ = ["build_durable_agent", "validate_durable_builder"]
