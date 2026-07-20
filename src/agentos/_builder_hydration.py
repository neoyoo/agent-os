from __future__ import annotations

from agentos._json_values import thaw_json_value
from agentos.context import ContextRuntime, ContextState, WorkingStateSchema
from agentos.context.models import ContextProtocolError
from agentos.context.schema import (
    validate_working_state_fields,
    validate_working_state_value,
)
from agentos.events import EventBus
from agentos.messages import ActiveWindow, MessageRef, MessageRuntime
from agentos.runtime.checkpoint import SessionCheckpoint
from agentos.runtime.errors import CheckpointCorruptedError
from agentos.runtime.session import SessionState
from agentos.runtime.tool_payloads import ToolPayloadRuntime


def hydrate_runtime_checkpoint(
    checkpoint: SessionCheckpoint,
    event_bus: EventBus | None,
    payloads: ToolPayloadRuntime,
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
        messages.hydrate_messages(list(payloads.restore_messages(checkpoint.messages)))
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
        raise CheckpointCorruptedError(
            "checkpoint hydration state is corrupted",
        ) from None


__all__ = ["hydrate_runtime_checkpoint"]
