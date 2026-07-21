from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime

from agentos._json_values import thaw_json_value
from agentos.artifacts.types import validate_artifact_id
from agentos.distributed.models import RunReadModel, RunSubmission
from agentos.runtime.durable_commands import DurableRunCommand
from agentos.runtime.run_state import RunStatus
from agentos.transports.a2a._protojson import A2A_UNSET
from agentos.transports.a2a.message_types import (
    A2AMessage,
    A2APart,
    A2ARole,
    A2ATask,
    A2ATaskState,
    A2ATaskStatus,
)
from agentos.transports.a2a.operation_types import A2ACancelTaskParams


MAX_COMMAND_PAYLOAD_BYTES = 64 * 1024


class A2AMappingError(ValueError):
    code = "invalid_params"
    message = "A2A mapping input is invalid"

    def __init__(self) -> None:
        super().__init__(self.message)


class A2ACommandNotDueError(A2AMappingError):
    code = "command_not_due"
    message = "task command is not due"


class A2ACommandStateError(A2AMappingError):
    code = "command_state"
    message = "task command conflicts with current state"


def a2a_message_to_submission(
    message: A2AMessage,
    *,
    session_id: str,
    artifact_handles: Sequence[str] = (),
) -> RunSubmission:
    handles = _artifact_handles(message, artifact_handles)
    return RunSubmission(
        session_id=session_id,
        submission_id=message.message_id,
        content=_normalized_content(message),
        artifact_handles=handles,
    )


def a2a_message_to_command(
    message: A2AMessage,
    *,
    run: RunReadModel,
    artifact_handles: Sequence[str] = (),
) -> DurableRunCommand:
    if type(run) is not RunReadModel or type(message) is not A2AMessage:
        raise TypeError("message and run must use canonical DTO")
    if message.task_id is not None and message.task_id != run.run_id:
        raise A2ACommandStateError
    if message.context_id is not None and message.context_id != run.session_id:
        raise A2ACommandStateError
    if run.status is not RunStatus.WAITING or run.wait_reason is None:
        raise A2ACommandStateError
    kind = run.wait_reason.kind
    if kind in {"timer", "retry_backoff"}:
        raise A2ACommandNotDueError
    if kind == "side_effect_reconciliation":
        raise A2ACommandStateError
    if kind == "human_input":
        command_kind = "hitl_answer"
    elif kind in {"remote_result", "resource_availability"}:
        command_kind = "wakeup"
    else:
        raise A2ACommandStateError
    handles = _artifact_handles(message, artifact_handles)
    payload = {
        "artifact_handles": list(handles),
        "content": _normalized_content(message),
        "metadata": {}
        if message.metadata is None
        else thaw_json_value(message.metadata),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")
    if len(encoded) > MAX_COMMAND_PAYLOAD_BYTES:
        raise A2AMappingError
    return DurableRunCommand(
        run_id=run.run_id,
        command_id=message.message_id,
        kind=command_kind,
        payload=payload,
    )


def a2a_cancel_to_command(
    params: A2ACancelTaskParams,
    *,
    run_id: str,
    command_id: str,
) -> DurableRunCommand:
    if type(params) is not A2ACancelTaskParams or params.id != run_id:
        raise A2ACommandStateError
    return DurableRunCommand(
        run_id=run_id,
        command_id=command_id,
        kind="cancel",
        payload={},
    )


def run_read_model_to_a2a_task(
    run: RunReadModel,
    *,
    history_length: int | None = None,
    include_artifacts: bool = False,
    status_timestamp: datetime | None = None,
) -> A2ATask:
    if type(run) is not RunReadModel:
        raise TypeError("run must be RunReadModel")
    if history_length is not None and (
        type(history_length) is not int or history_length < 0
    ):
        raise ValueError("history_length must be non-negative or None")
    if type(include_artifacts) is not bool:
        raise TypeError("include_artifacts must be bool")
    history: tuple[A2AMessage, ...] | None = None
    if run.status is RunStatus.COMPLETED and history_length != 0:
        assert run.result is not None
        history = (
            A2AMessage(
                message_id=f"a2a_result_{run.run_id}",
                context_id=run.session_id,
                task_id=run.run_id,
                role=A2ARole.ROLE_AGENT,
                parts=(A2APart(text=run.result.content),),
            ),
        )
    return A2ATask(
        id=run.run_id,
        context_id=run.session_id,
        status=A2ATaskStatus(
            state=_task_state(run),
            timestamp=status_timestamp,
        ),
        artifacts=() if include_artifacts else None,
        history=history,
    )


def _normalized_content(message: A2AMessage) -> str:
    fragments: list[str] = []
    for part in message.parts:
        if part.text is not A2A_UNSET:
            fragments.append(part.text)  # type: ignore[arg-type]
        elif part.data is not A2A_UNSET:
            fragments.append(
                json.dumps(
                    thaw_json_value(part.data),  # type: ignore[arg-type]
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                    allow_nan=False,
                ),
            )
        elif part.url is not A2A_UNSET:
            raise A2AMappingError
    return "\n".join(fragments)


def _artifact_handles(
    message: A2AMessage,
    values: Sequence[str],
) -> tuple[str, ...]:
    if type(values) is str:
        raise TypeError("artifact_handles must contain str values")
    handles = tuple(values)
    if len(handles) != sum(part.raw is not A2A_UNSET for part in message.parts):
        raise A2AMappingError
    for handle in handles:
        validate_artifact_id(handle)
    return handles


def _task_state(run: RunReadModel) -> A2ATaskState:
    if run.status in {RunStatus.CREATED, RunStatus.QUEUED}:
        return A2ATaskState.TASK_STATE_SUBMITTED
    if run.status is RunStatus.RUNNING:
        return A2ATaskState.TASK_STATE_WORKING
    if run.status is RunStatus.WAITING:
        assert run.wait_reason is not None
        return (
            A2ATaskState.TASK_STATE_INPUT_REQUIRED
            if run.wait_reason.kind == "human_input"
            else A2ATaskState.TASK_STATE_WORKING
        )
    return {
        RunStatus.COMPLETED: A2ATaskState.TASK_STATE_COMPLETED,
        RunStatus.FAILED: A2ATaskState.TASK_STATE_FAILED,
        RunStatus.CANCELLED: A2ATaskState.TASK_STATE_CANCELED,
    }[run.status]


__all__ = [
    "A2ACommandNotDueError",
    "A2ACommandStateError",
    "A2AMappingError",
    "MAX_COMMAND_PAYLOAD_BYTES",
    "a2a_cancel_to_command",
    "a2a_message_to_command",
    "a2a_message_to_submission",
    "run_read_model_to_a2a_task",
]
