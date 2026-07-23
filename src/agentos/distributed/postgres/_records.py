from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import json
from typing import cast

from agentos._waiting import WaitReason
from agentos.distributed.errors import CheckpointConflictError
from agentos.durable.serialization import (
    context_from_json,
    context_to_json,
    execution_cursor_from_json,
    execution_cursor_to_json,
    message_from_json,
    message_to_json,
)
from agentos.runtime.checkpoint import SessionCheckpoint
from agentos.runtime.run import AgentResult
from agentos.runtime.run_state import RunState, RunStatus


_SNAPSHOT_FIELDS = {
    "active_refs",
    "context",
    "execution_cursor",
    "messages",
    "next_turn_number",
    "session_id",
    "session_status",
    "version",
}


def session_checkpoint_to_json(checkpoint: SessionCheckpoint) -> str:
    if type(checkpoint) is not SessionCheckpoint:
        raise TypeError("checkpoint must be SessionCheckpoint")
    payload = {
        "active_refs": list(checkpoint.active_refs),
        "context": json.loads(context_to_json(checkpoint.context)),
        "execution_cursor": (
            None
            if checkpoint.execution_cursor is None
            else json.loads(execution_cursor_to_json(checkpoint.execution_cursor))
        ),
        "messages": [
            json.loads(message_to_json(message)) for message in checkpoint.messages
        ],
        "next_turn_number": checkpoint.next_turn_number,
        "session_id": checkpoint.session_id,
        "session_status": checkpoint.session_status,
        "version": 1,
    }
    return _canonical_json(payload)


def session_checkpoint_from_json(value: str) -> SessionCheckpoint:
    try:
        payload = json.loads(value, parse_constant=_reject_constant)
        if type(payload) is not dict or set(payload) != _SNAPSHOT_FIELDS:
            raise ValueError
        if payload["version"] != 1:
            raise ValueError
        messages = payload["messages"]
        active_refs = payload["active_refs"]
        if type(messages) is not list or type(active_refs) is not list:
            raise TypeError
        if any(type(item) is not dict for item in messages):
            raise TypeError
        if any(type(item) is not str for item in active_refs):
            raise TypeError
        context = payload["context"]
        cursor = payload["execution_cursor"]
        if type(context) is not dict:
            raise TypeError
        if cursor is not None and type(cursor) is not dict:
            raise TypeError
        checkpoint = SessionCheckpoint(
            session_id=payload["session_id"],
            session_status=payload["session_status"],
            next_turn_number=payload["next_turn_number"],
            messages=tuple(
                message_from_json(_canonical_json(item)) for item in messages
            ),
            active_refs=tuple(active_refs),
            context=context_from_json(_canonical_json(context)),
            execution_cursor=(
                None
                if cursor is None
                else execution_cursor_from_json(_canonical_json(cursor))
            ),
        )
        if session_checkpoint_to_json(checkpoint) != value:
            raise ValueError
        return checkpoint
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise CheckpointConflictError() from None


def run_state_from_row(row: Mapping[str, object]) -> RunState:
    reason = wait_reason_from_row(row)
    try:
        return RunState(
            run_id=cast(str, row["run_id"]),
            session_id=cast(str, row["session_id"]),
            status=RunStatus(cast(str, row["status"])),
            wait_reason=reason,
            aggregate_version=cast(int, row["aggregate_version"]),
        )
    except (KeyError, TypeError, ValueError):
        raise CheckpointConflictError() from None


def wait_reason_from_row(row: Mapping[str, object]) -> WaitReason | None:
    try:
        kind = row["wait_kind"]
        if kind is None:
            if any(
                row[name] is not None
                for name in ("wait_handle", "wait_detail", "wait_not_before")
            ):
                raise ValueError
            return None
        not_before = row["wait_not_before"]
        if not_before is not None and type(not_before) is not datetime:
            raise TypeError
        return WaitReason(
            kind=cast(object, kind),  # type: ignore[arg-type]
            handle=cast(str, row["wait_handle"]),
            detail=cast(str | None, row["wait_detail"]),
            not_before=cast(datetime | None, not_before),
        )
    except (KeyError, TypeError, ValueError):
        raise CheckpointConflictError() from None


def terminal_result_from_checkpoint(
    checkpoint: SessionCheckpoint,
    status: RunStatus,
) -> AgentResult | None:
    if type(checkpoint) is not SessionCheckpoint:
        raise TypeError("checkpoint must be SessionCheckpoint")
    if type(status) is not RunStatus:
        raise TypeError("status must be RunStatus")
    if status is not RunStatus.COMPLETED:
        return None
    _validate_tool_pairs(checkpoint)
    if not checkpoint.messages:
        raise CheckpointConflictError()
    terminal = checkpoint.messages[-1]
    if (
        terminal.role != "assistant"
        or terminal.tool_calls
        or terminal.tool_call_id is not None
    ):
        raise CheckpointConflictError()
    return AgentResult(terminal.content)


def _validate_tool_pairs(checkpoint: SessionCheckpoint) -> None:
    messages_by_id = {message.id: message for message in checkpoint.messages}
    messages = tuple(messages_by_id[ref] for ref in checkpoint.active_refs)
    index = 0
    while index < len(messages):
        message = messages[index]
        if message.role == "tool":
            raise CheckpointConflictError()
        if message.role != "assistant" or not message.tool_calls:
            index += 1
            continue
        expected_ids = tuple(call.id for call in message.tool_calls)
        results = messages[index + 1:index + 1 + len(expected_ids)]
        if (
            len(results) != len(expected_ids)
            or any(result.role != "tool" for result in results)
            or tuple(result.tool_call_id for result in results) != expected_ids
        ):
            raise CheckpointConflictError()
        index += len(expected_ids) + 1


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _reject_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


__all__ = [
    "run_state_from_row",
    "session_checkpoint_from_json",
    "session_checkpoint_to_json",
    "terminal_result_from_checkpoint",
    "wait_reason_from_row",
]
