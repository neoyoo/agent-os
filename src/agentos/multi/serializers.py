from __future__ import annotations

import json
from typing import Any, cast

from agentos.multi.team import (
    TeamMemberRecord,
    TeamMemberRole,
    TeamMemberStatus,
    TeamMessage,
    TeamMessageKind,
    TeamRecord,
    TeamStatus,
    TeamUiEvent,
    TeamUiEventKind,
)
from agentos.multi.types import (
    AgentEnvelope,
    AgentEnvelopeType,
    CoordinationMode,
    TaskRecord,
    TaskRequest,
    TaskResult,
    TaskStatus,
)
from agentos.workspace import WorkspaceHandle, WorkspaceScope


JsonDict = dict[str, Any]
_TASK_STATUSES: frozenset[str] = frozenset(
    {"queued", "running", "completed", "failed", "cancelled", "timeout"},
)
_COORDINATION_MODES: frozenset[str] = frozenset({"spawn", "dispatch"})
_ENVELOPE_TYPES: frozenset[str] = frozenset(
    {"task_request", "task_result", "team_message"},
)
_TEAM_MESSAGE_KINDS: frozenset[str] = frozenset(
    {"instruction", "observation", "result", "notice"},
)
_TEAM_STATUSES: frozenset[str] = frozenset({"active", "deleted"})
_TEAM_MEMBER_ROLES: frozenset[str] = frozenset({"leader", "worker"})
_TEAM_MEMBER_STATUSES: frozenset[str] = frozenset(
    {"active", "offline", "removed"},
)
_TEAM_UI_EVENT_KINDS: frozenset[str] = frozenset(
    {
        "team_created",
        "member_added",
        "message_appended",
        "worker_run_completed",
        "worker_run_failed",
        "worker_run_retry_skipped",
        "worker_run_cancelled",
        "team_deleted",
    },
)
_WORKSPACE_SCOPES: frozenset[str] = frozenset(
    {"process", "agent", "user", "session", "team", "task"},
)


def workspace_handle_to_dict(handle: WorkspaceHandle) -> JsonDict:
    """Serialize WorkspaceHandle."""

    return {
        "workspace_id": handle.workspace_id,
        "scope": _workspace_scope(handle.scope),
        "root": handle.root,
        "parent_workspace_id": handle.parent_workspace_id,
        "metadata": {
            str(key): str(value)
            for key, value in dict(handle.metadata).items()
        },
    }


def workspace_handle_from_dict(data: JsonDict) -> WorkspaceHandle:
    """Deserialize WorkspaceHandle."""

    return WorkspaceHandle(
        workspace_id=str(data["workspace_id"]),
        scope=_workspace_scope(data["scope"]),
        root=None if data.get("root") is None else str(data["root"]),
        parent_workspace_id=(
            None
            if data.get("parent_workspace_id") is None
            else str(data["parent_workspace_id"])
        ),
        metadata={
            str(key): str(value)
            for key, value in dict(data.get("metadata", {})).items()
        },
    )


def team_record_to_dict(record: TeamRecord) -> JsonDict:
    """Serialize TeamRecord."""

    return {
        "team_id": record.team_id,
        "name": record.name,
        "description": record.description,
        "leader_agent_id": record.leader_agent_id,
        "created_at": record.created_at,
        "status": _team_status(record.status),
        "workspace": (
            None
            if record.workspace is None
            else workspace_handle_to_dict(record.workspace)
        ),
    }


def team_record_from_dict(data: JsonDict) -> TeamRecord:
    """Deserialize TeamRecord."""

    workspace = data.get("workspace")
    return TeamRecord(
        team_id=str(data["team_id"]),
        name=str(data["name"]),
        description=str(data["description"]),
        leader_agent_id=str(data["leader_agent_id"]),
        created_at=float(data["created_at"]),
        status=_team_status(data.get("status", "active")),
        workspace=(
            None
            if workspace is None
            else workspace_handle_from_dict(cast(JsonDict, workspace))
        ),
    )


def team_member_record_to_dict(record: TeamMemberRecord) -> JsonDict:
    """Serialize TeamMemberRecord."""

    return {
        "team_id": record.team_id,
        "agent_id": record.agent_id,
        "role": _team_member_role(record.role),
        "session_id": record.session_id,
        "capabilities": list(record.capabilities),
        "workspace": (
            None
            if record.workspace is None
            else workspace_handle_to_dict(record.workspace)
        ),
        "status": _team_member_status(record.status),
        "created_at": record.created_at,
    }


def team_member_record_from_dict(data: JsonDict) -> TeamMemberRecord:
    """Deserialize TeamMemberRecord."""

    workspace = data.get("workspace")
    return TeamMemberRecord(
        team_id=str(data["team_id"]),
        agent_id=str(data["agent_id"]),
        role=_team_member_role(data["role"]),
        session_id=(
            None if data.get("session_id") is None else str(data["session_id"])
        ),
        capabilities=tuple(str(item) for item in data.get("capabilities", [])),
        workspace=(
            None
            if workspace is None
            else workspace_handle_from_dict(cast(JsonDict, workspace))
        ),
        status=_team_member_status(data.get("status", "active")),
        created_at=float(data.get("created_at", 0)),
    )


def task_request_to_dict(request: TaskRequest) -> JsonDict:
    """Serialize TaskRequest."""

    return {
        "task_id": request.task_id,
        "instruction": request.instruction,
        "required_capabilities": list(request.required_capabilities),
        "allowed_tool_names": list(request.allowed_tool_names),
        "timeout_seconds": request.timeout_seconds,
        "trace_context": (
            None if request.trace_context is None else dict(request.trace_context)
        ),
    }


def task_request_from_dict(data: JsonDict) -> TaskRequest:
    """Deserialize TaskRequest."""

    trace_context = data.get("trace_context")
    return TaskRequest(
        task_id=str(data["task_id"]),
        instruction=str(data["instruction"]),
        required_capabilities=tuple(
            str(name) for name in data.get("required_capabilities", [])
        ),
        allowed_tool_names=tuple(
            str(name) for name in data.get("allowed_tool_names", [])
        ),
        timeout_seconds=float(data.get("timeout_seconds", 300)),
        trace_context=(
            None
            if trace_context is None
            else {str(key): str(value) for key, value in dict(trace_context).items()}
        ),
    )


def task_result_to_dict(result: TaskResult) -> JsonDict:
    """Serialize TaskResult."""

    return {
        "task_id": result.task_id,
        "status": _task_status(result.status),
        "summary": result.summary,
        "artifacts": _json_safe_dict(
            result.artifacts,
            error_message="artifacts must be JSON serializable",
        ),
        "error": result.error,
        "elapsed_seconds": result.elapsed_seconds,
    }


def task_result_from_dict(data: JsonDict) -> TaskResult:
    """Deserialize TaskResult."""

    return TaskResult(
        task_id=str(data["task_id"]),
        status=_task_status(data["status"]),
        summary=str(data["summary"]),
        artifacts=_json_safe_dict(
            dict(data.get("artifacts", {})),
            error_message="artifacts must be JSON serializable",
        ),
        error=None if data.get("error") is None else str(data["error"]),
        elapsed_seconds=float(data.get("elapsed_seconds", 0)),
    )


def team_message_to_dict(message: TeamMessage) -> JsonDict:
    """Serialize TeamMessage."""

    return {
        "message_id": message.message_id,
        "team_id": message.team_id,
        "from_agent_id": message.from_agent_id,
        "to_agent_id": message.to_agent_id,
        "content": message.content,
        "kind": _team_message_kind(message.kind),
        "created_at": message.created_at,
        "correlation_id": message.correlation_id,
        "artifact_handles": list(message.artifact_handles),
        "metadata": {
            str(key): str(value)
            for key, value in dict(message.metadata).items()
        },
    }


def team_message_from_dict(data: JsonDict) -> TeamMessage:
    """Deserialize TeamMessage."""

    return TeamMessage(
        message_id=str(data["message_id"]),
        team_id=str(data["team_id"]),
        from_agent_id=str(data["from_agent_id"]),
        to_agent_id=(
            None if data.get("to_agent_id") is None else str(data["to_agent_id"])
        ),
        content=str(data["content"]),
        kind=_team_message_kind(data.get("kind", "observation")),
        created_at=float(data["created_at"]),
        correlation_id=(
            None if data.get("correlation_id") is None else str(data["correlation_id"])
        ),
        artifact_handles=tuple(
            str(handle) for handle in data.get("artifact_handles", [])
        ),
        metadata={
            str(key): str(value)
            for key, value in dict(data.get("metadata", {})).items()
        },
    )


def team_ui_event_to_dict(event: TeamUiEvent) -> JsonDict:
    """Serialize TeamUiEvent."""

    return {
        "event_id": event.event_id,
        "team_id": event.team_id,
        "kind": _team_ui_event_kind(event.kind),
        "payload": _json_safe_dict(
            dict(event.payload),
            error_message="team UI event payload must be JSON serializable",
        ),
        "created_at": event.created_at,
    }


def team_ui_event_from_dict(data: JsonDict) -> TeamUiEvent:
    """Deserialize TeamUiEvent."""

    return TeamUiEvent(
        event_id=int(data["event_id"]),
        team_id=str(data["team_id"]),
        kind=_team_ui_event_kind(data["kind"]),
        payload=_json_safe_dict(
            dict(data.get("payload", {})),
            error_message="team UI event payload must be JSON serializable",
        ),
        created_at=float(data["created_at"]),
    )


def task_record_to_dict(record: TaskRecord) -> JsonDict:
    """Serialize TaskRecord."""

    return {
        "task_id": record.task_id,
        "mode": _coordination_mode(record.mode),
        "parent_agent_id": record.parent_agent_id,
        "target_agent_id": record.target_agent_id,
        "request": task_request_to_dict(record.request),
        "status": _task_status(record.status),
        "created_at": record.created_at,
        "deadline_at": record.deadline_at,
        "result": None if record.result is None else task_result_to_dict(record.result),
        "late_result": (
            None
            if record.late_result is None
            else task_result_to_dict(record.late_result)
        ),
        "completed_at": record.completed_at,
        "consumed_at": record.consumed_at,
        "worker_id": record.worker_id,
        "lease_expires_at": record.lease_expires_at,
        "attempt": record.attempt,
        "updated_at": record.updated_at,
        "version": record.version,
        "cancel_requested_at": record.cancel_requested_at,
        "result_notified_at": record.result_notified_at,
    }


def task_record_from_dict(data: JsonDict) -> TaskRecord:
    """Deserialize TaskRecord."""

    return TaskRecord(
        task_id=str(data["task_id"]),
        mode=_coordination_mode(data["mode"]),
        parent_agent_id=str(data["parent_agent_id"]),
        target_agent_id=str(data["target_agent_id"]),
        request=task_request_from_dict(data["request"]),
        status=_task_status(data["status"]),
        created_at=float(data["created_at"]),
        deadline_at=float(data["deadline_at"]),
        result=(
            None if data.get("result") is None else task_result_from_dict(data["result"])
        ),
        late_result=(
            None
            if data.get("late_result") is None
            else task_result_from_dict(data["late_result"])
        ),
        completed_at=(
            None if data.get("completed_at") is None else float(data["completed_at"])
        ),
        consumed_at=(
            None if data.get("consumed_at") is None else float(data["consumed_at"])
        ),
        worker_id=None if data.get("worker_id") is None else str(data["worker_id"]),
        lease_expires_at=(
            None
            if data.get("lease_expires_at") is None
            else float(data["lease_expires_at"])
        ),
        attempt=int(data.get("attempt", 0)),
        updated_at=None if data.get("updated_at") is None else float(data["updated_at"]),
        version=int(data.get("version", 0)),
        cancel_requested_at=(
            None
            if data.get("cancel_requested_at") is None
            else float(data["cancel_requested_at"])
        ),
        result_notified_at=(
            None
            if data.get("result_notified_at") is None
            else float(data["result_notified_at"])
        ),
    )


def envelope_to_dict(envelope: AgentEnvelope) -> JsonDict:
    """Serialize AgentEnvelope."""

    envelope_type = _envelope_type(envelope.type)
    if envelope_type == "task_request":
        if not isinstance(envelope.payload, TaskRequest):
            raise TypeError("task_request envelope payload must be TaskRequest")
        payload = task_request_to_dict(envelope.payload)
    elif envelope_type == "task_result":
        if not isinstance(envelope.payload, TaskResult):
            raise TypeError("task_result envelope payload must be TaskResult")
        payload = task_result_to_dict(envelope.payload)
    else:
        if not isinstance(envelope.payload, TeamMessage):
            raise TypeError("team_message envelope payload must be TeamMessage")
        payload = team_message_to_dict(envelope.payload)
    return {
        "envelope_id": envelope.envelope_id,
        "from_agent_id": envelope.from_agent_id,
        "to_agent_id": envelope.to_agent_id,
        "type": envelope_type,
        "payload": payload,
        "created_at": envelope.created_at,
        "correlation_id": envelope.correlation_id,
    }


def envelope_from_dict(data: JsonDict) -> AgentEnvelope:
    """Deserialize AgentEnvelope."""

    envelope_type = _envelope_type(data["type"])
    payload: TaskRequest | TaskResult | TeamMessage
    if envelope_type == "task_request":
        if not _is_task_request_payload(data["payload"]):
            raise TypeError("task_request envelope payload must be TaskRequest data")
        payload = task_request_from_dict(data["payload"])
    elif envelope_type == "task_result":
        if not _is_task_result_payload(data["payload"]):
            raise TypeError("task_result envelope payload must be TaskResult data")
        payload = task_result_from_dict(data["payload"])
    else:
        if not _is_team_message_payload(data["payload"]):
            raise TypeError("team_message envelope payload must be TeamMessage data")
        payload = team_message_from_dict(data["payload"])
    return AgentEnvelope(
        envelope_id=str(data["envelope_id"]),
        from_agent_id=str(data["from_agent_id"]),
        to_agent_id=str(data["to_agent_id"]),
        type=envelope_type,
        payload=payload,
        created_at=float(data["created_at"]),
        correlation_id=(
            None if data.get("correlation_id") is None else str(data["correlation_id"])
        ),
    )


def _task_status(value: object) -> TaskStatus:
    status = str(value)
    if status not in _TASK_STATUSES:
        raise ValueError(f"invalid task status: {status}")
    return cast(TaskStatus, status)


def _coordination_mode(value: object) -> CoordinationMode:
    mode = str(value)
    if mode not in _COORDINATION_MODES:
        raise ValueError(f"invalid coordination mode: {mode}")
    return cast(CoordinationMode, mode)


def _envelope_type(value: object) -> AgentEnvelopeType:
    envelope_type = str(value)
    if envelope_type not in _ENVELOPE_TYPES:
        raise ValueError(f"invalid envelope type: {envelope_type}")
    return cast(AgentEnvelopeType, envelope_type)


def _team_message_kind(value: object) -> TeamMessageKind:
    kind = str(value)
    if kind not in _TEAM_MESSAGE_KINDS:
        raise ValueError(f"invalid team message kind: {kind}")
    return cast(TeamMessageKind, kind)


def _team_status(value: object) -> TeamStatus:
    status = str(value)
    if status not in _TEAM_STATUSES:
        raise ValueError(f"invalid team status: {status}")
    return cast(TeamStatus, status)


def _team_member_role(value: object) -> TeamMemberRole:
    role = str(value)
    if role not in _TEAM_MEMBER_ROLES:
        raise ValueError(f"invalid team member role: {role}")
    return cast(TeamMemberRole, role)


def _team_member_status(value: object) -> TeamMemberStatus:
    status = str(value)
    if status not in _TEAM_MEMBER_STATUSES:
        raise ValueError(f"invalid team member status: {status}")
    return cast(TeamMemberStatus, status)


def _team_ui_event_kind(value: object) -> TeamUiEventKind:
    kind = str(value)
    if kind not in _TEAM_UI_EVENT_KINDS:
        raise ValueError(f"invalid team UI event kind: {kind}")
    return cast(TeamUiEventKind, kind)


def _workspace_scope(value: object) -> WorkspaceScope:
    scope = str(value)
    if scope not in _WORKSPACE_SCOPES:
        raise ValueError(f"invalid workspace scope: {scope}")
    return cast(WorkspaceScope, scope)


def _json_safe_dict(value: dict[str, object], *, error_message: str) -> JsonDict:
    try:
        return cast(
            JsonDict,
            json.loads(json.dumps(dict(value), ensure_ascii=False, allow_nan=False)),
        )
    except (TypeError, ValueError) as error:
        raise TypeError(error_message) from error


def _is_task_request_payload(value: object) -> bool:
    return _payload_has_keys(value, ("task_id", "instruction")) and not _payload_has_keys(
        value,
        ("status", "summary"),
    )


def _is_task_result_payload(value: object) -> bool:
    return _payload_has_keys(value, ("task_id", "status", "summary")) and not (
        isinstance(value, dict) and "instruction" in value
    )


def _is_team_message_payload(value: object) -> bool:
    return _payload_has_keys(
        value,
        ("message_id", "team_id", "from_agent_id", "content"),
    )


def _payload_has_keys(value: object, keys: tuple[str, ...]) -> bool:
    if not isinstance(value, dict):
        return False
    return all(key in value for key in keys)
