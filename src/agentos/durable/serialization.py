from __future__ import annotations

import json
from collections.abc import Mapping
from typing import cast

from agentos._json_values import thaw_json_value
from agentos.artifacts import ArtifactRef
from agentos.context import WorkingStateField
from agentos.context.state import CompressedSegment
from agentos.durable.safety import validate_durable_data
from agentos.runtime.checkpoint import (
    CheckpointStoredMessage,
    CheckpointToolCall,
    ContextCheckpoint,
)
from agentos.runtime.execution import (
    PendingToolInvocation,
    RunExecutionCursor,
)
from agentos.runtime.payloads import ProtectedPayloadRef
from agentos.runtime.errors import CheckpointCorruptedError


def dump_json(value: object) -> str:
    """生成确定性的 SQLite JSON 文本。"""

    validate_durable_data(value)
    return _encode_json(value)


def _encode_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def load_json_object(value: str) -> dict[str, object]:
    """严格读取 JSON object，并把损坏数据映射为领域错误。"""

    loaded = _parse_json_object(value)
    validate_durable_data(loaded)
    return loaded


def _parse_json_object(value: str) -> dict[str, object]:
    try:
        loaded = json.loads(value, parse_constant=_reject_json_constant)
    except (TypeError, ValueError, json.JSONDecodeError):
        raise CheckpointCorruptedError("durable JSON record is corrupted") from None
    if type(loaded) is not dict:
        raise CheckpointCorruptedError("durable JSON record is corrupted")
    return cast(dict[str, object], loaded)


def message_to_json(message: CheckpointStoredMessage) -> str:
    payload = _message_payload(message, redact_protected_refs=False)
    validate_durable_data(
        _message_payload(message, redact_protected_refs=True),
    )
    return _encode_json(payload)


def _message_payload(
    message: CheckpointStoredMessage,
    *,
    redact_protected_refs: bool,
) -> dict[str, object]:
    return {
        "artifact_refs": [
            {
                "artifact_id": item.artifact_id,
                "filename": item.filename,
                "media_type": item.media_type,
            }
            for item in message.artifact_refs
        ],
        "content": message.content,
        "id": message.id,
        "role": message.role,
        "tool_call_id": message.tool_call_id,
        "tool_calls": [
            {
                "id": item.id,
                "invocation_id": item.invocation_id,
                "invocation_ref": {
                    "digest": (
                        "protected-integrity-tag"
                        if redact_protected_refs
                        else item.invocation_ref.digest
                    ),
                    "token": (
                        "protected-payload-token"
                        if redact_protected_refs
                        else item.invocation_ref.token
                    ),
                },
                "name": item.name,
                "run_id": item.run_id,
                "turn_id": item.turn_id,
            }
            for item in message.tool_calls
        ],
    }


def message_from_json(value: str) -> CheckpointStoredMessage:
    data = _parse_json_object(value)
    try:
        _require_keys(
            data,
            {"artifact_refs", "content", "id", "role", "tool_call_id", "tool_calls"},
        )
        role = _string(data, "role")
        if role not in {"user", "assistant", "tool"}:
            raise ValueError("invalid stored message role")
        artifact_refs = _object_list(data, "artifact_refs")
        tool_calls = _object_list(data, "tool_calls")
        for item in artifact_refs:
            _require_keys(item, {"artifact_id", "filename", "media_type"})
        for item in tool_calls:
            _require_keys(
                item,
                {
                    "id",
                    "invocation_id",
                    "invocation_ref",
                    "name",
                    "run_id",
                    "turn_id",
                },
            )
            _require_keys(_object(item, "invocation_ref"), {"digest", "token"})
        tool_call_id = _optional_string(data, "tool_call_id")
        if role == "tool":
            if not tool_call_id or tool_calls or artifact_refs:
                raise ValueError("invalid tool result message")
        elif tool_call_id is not None or (tool_calls and role != "assistant"):
            raise ValueError("invalid stored message tool fields")
        message = CheckpointStoredMessage(
            id=_non_empty_string(data, "id"),
            role=cast(object, role),  # type: ignore[arg-type]
            content=_string(data, "content"),
            artifact_refs=tuple(
                ArtifactRef(
                    artifact_id=_string(item, "artifact_id"),
                    filename=_optional_string(item, "filename"),
                    media_type=_string(item, "media_type"),
                )
                for item in artifact_refs
            ),
            tool_calls=tuple(
                CheckpointToolCall(
                    id=_non_empty_string(item, "id"),
                    name=_non_empty_string(item, "name"),
                    run_id=_non_empty_string(item, "run_id"),
                    turn_id=_non_empty_string(item, "turn_id"),
                    invocation_id=_non_empty_string(item, "invocation_id"),
                    invocation_ref=ProtectedPayloadRef(
                        token=_non_empty_string(
                            _object(item, "invocation_ref"),
                            "token",
                        ),
                        digest=_non_empty_string(
                            _object(item, "invocation_ref"),
                            "digest",
                        ),
                    ),
                )
                for item in tool_calls
            ),
            tool_call_id=tool_call_id,
        )
        validate_durable_data(
            _message_payload(message, redact_protected_refs=True),
        )
        return message
    except (KeyError, TypeError, ValueError):
        raise CheckpointCorruptedError("stored message record is corrupted") from None


def context_to_json(context: ContextCheckpoint) -> str:
    return dump_json(
        {
            "compressed_history": [
                {"id": item.id, "summary": item.summary, "topic": item.topic}
                for item in context.compressed_history
            ],
            "inherited_state": list(context.inherited_state),
            "schema": [
                {"name": item.name, "purpose": item.purpose, "type": item.type}
                for item in context.schema
            ],
            "working_state": thaw_json_value(context.working_state),
        },
    )


def context_from_json(value: str) -> ContextCheckpoint:
    data = load_json_object(value)
    try:
        from agentos._json_values import freeze_json_mapping

        _require_keys(
            data,
            {"compressed_history", "inherited_state", "schema", "working_state"},
        )
        return ContextCheckpoint(
            schema=tuple(
                WorkingStateField(
                    name=_string(item, "name"),
                    type=_string(item, "type"),
                    purpose=_string(item, "purpose"),
                )
                for item in _object_list(data, "schema")
            ),
            working_state=freeze_json_mapping(_object(data, "working_state")),
            compressed_history=tuple(
                CompressedSegment(
                    id=_string(item, "id"),
                    topic=_string(item, "topic"),
                    summary=_string(item, "summary"),
                )
                for item in _object_list(data, "compressed_history")
            ),
            inherited_state=_string_tuple(data, "inherited_state"),
        )
    except (KeyError, TypeError, ValueError):
        raise CheckpointCorruptedError("context checkpoint is corrupted") from None


def execution_cursor_to_json(cursor: RunExecutionCursor) -> str:
    """序列化唯一 running execution cursor。"""

    payload = _execution_cursor_payload(cursor, redact_protected_refs=False)
    validate_durable_data(
        _execution_cursor_payload(cursor, redact_protected_refs=True),
    )
    return _encode_json(payload)


def _execution_cursor_payload(
    cursor: RunExecutionCursor,
    *,
    redact_protected_refs: bool,
) -> dict[str, object]:
    return {
        "assistant_message_id": cursor.assistant_message_id,
        "pending_tools": [
            {
                "invocation_id": item.invocation_id,
                "invocation_ref": {
                    "digest": (
                        "protected-integrity-tag"
                        if redact_protected_refs
                        else item.invocation_ref.digest
                    ),
                    "token": (
                        "protected-payload-token"
                        if redact_protected_refs
                        else item.invocation_ref.token
                    ),
                },
                "provider_tool_call_id": item.provider_tool_call_id,
                "tool_name": item.tool_name,
            }
            for item in cursor.pending_tools
        ],
        "provider_call_index": cursor.provider_call_index,
        "stage": cursor.stage,
        "turn_id": cursor.turn_id,
    }


def execution_cursor_from_json(value: str) -> RunExecutionCursor:
    """严格恢复 running execution cursor。"""

    data = _parse_json_object(value)
    try:
        _require_keys(
            data,
            {
                "assistant_message_id",
                "pending_tools",
                "provider_call_index",
                "stage",
                "turn_id",
            },
        )
        pending = _object_list(data, "pending_tools")
        for item in pending:
            _require_keys(
                item,
                {
                    "invocation_id",
                    "invocation_ref",
                    "provider_tool_call_id",
                    "tool_name",
                },
            )
            _require_keys(_object(item, "invocation_ref"), {"digest", "token"})
        provider_call_index = data["provider_call_index"]
        if type(provider_call_index) is not int:
            raise TypeError("provider_call_index")
        cursor = RunExecutionCursor(
            turn_id=_non_empty_string(data, "turn_id"),
            stage=cast(object, _non_empty_string(data, "stage")),  # type: ignore[arg-type]
            provider_call_index=provider_call_index,
            assistant_message_id=_optional_string(data, "assistant_message_id"),
            pending_tools=tuple(
                PendingToolInvocation(
                    invocation_id=_non_empty_string(item, "invocation_id"),
                    provider_tool_call_id=_non_empty_string(
                        item,
                        "provider_tool_call_id",
                    ),
                    tool_name=_non_empty_string(item, "tool_name"),
                    invocation_ref=ProtectedPayloadRef(
                        token=_non_empty_string(
                            _object(item, "invocation_ref"),
                            "token",
                        ),
                        digest=_non_empty_string(
                            _object(item, "invocation_ref"),
                            "digest",
                        ),
                    ),
                )
                for item in pending
            ),
        )
        validate_durable_data(
            _execution_cursor_payload(cursor, redact_protected_refs=True),
        )
        return cursor
    except (KeyError, TypeError, ValueError):
        raise CheckpointCorruptedError(
            "execution cursor is corrupted",
        ) from None


def _string(data: Mapping[str, object], key: str) -> str:
    value = data[key]
    if type(value) is not str:
        raise TypeError(key)
    return value


def _non_empty_string(data: Mapping[str, object], key: str) -> str:
    value = _string(data, key)
    if not value:
        raise ValueError(key)
    return value


def _optional_string(data: Mapping[str, object], key: str) -> str | None:
    value = data.get(key)
    if value is not None and type(value) is not str:
        raise TypeError(key)
    return cast(str | None, value)


def _object(data: Mapping[str, object], key: str) -> dict[str, object]:
    value = data[key]
    if type(value) is not dict:
        raise TypeError(key)
    return cast(dict[str, object], value)


def _object_list(
    data: Mapping[str, object],
    key: str,
) -> tuple[dict[str, object], ...]:
    value = data[key]
    if type(value) is not list or any(type(item) is not dict for item in value):
        raise TypeError(key)
    return tuple(cast(list[dict[str, object]], value))


def _string_tuple(data: Mapping[str, object], key: str) -> tuple[str, ...]:
    value = data[key]
    if type(value) is not list or any(type(item) is not str for item in value):
        raise TypeError(key)
    return tuple(cast(list[str], value))


def _require_keys(data: Mapping[str, object], expected: set[str]) -> None:
    if set(data) != expected:
        raise ValueError("record fields are invalid")


def _reject_json_constant(value: str) -> None:
    raise ValueError("invalid JSON constant")
