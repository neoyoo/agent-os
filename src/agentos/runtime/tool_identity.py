from __future__ import annotations

import hashlib
import json
import unicodedata

from agentos._json_values import thaw_json_value
from agentos.capabilities.invocation import ToolInvocation


def invocation_id(
    *,
    tenant_id: str | None,
    session_id: str,
    run_id: str,
    turn_id: str,
    provider_call_index: int,
    tool_index: int,
) -> str:
    """按 agentos.tool-identity v1 派生稳定 invocation ID。"""

    _require_scope(tenant_id, session_id, run_id, turn_id)
    _require_non_negative(provider_call_index, "provider_call_index")
    _require_non_negative(tool_index, "tool_index")
    return _short_id(
        "invocation",
        {
            "provider_call_index": provider_call_index,
            "run_id": run_id,
            "session_id": session_id,
            "tenant_id": tenant_id,
            "tool_index": tool_index,
            "turn_id": turn_id,
            "version": 1,
        },
    )


def operation_id(
    *,
    tenant_id: str | None,
    session_id: str,
    run_id: str,
    turn_id: str,
    invocation_id: str,
) -> str:
    """按 agentos.tool-identity v1 派生稳定 operation ID。"""

    _require_scope(tenant_id, session_id, run_id, turn_id)
    _require_identifier(invocation_id, "invocation_id")
    return _short_id(
        "operation",
        {
            "invocation_id": invocation_id,
            "run_id": run_id,
            "session_id": session_id,
            "tenant_id": tenant_id,
            "turn_id": turn_id,
            "version": 1,
        },
    )


def compensation_operation_id(original_operation_id: str) -> str:
    """为同一原 operation 派生稳定补偿 operation ID。"""

    _require_identifier(original_operation_id, "original_operation_id")
    return _short_id(
        "operation",
        {
            "original_operation_id": original_operation_id,
            "purpose": "compensation",
            "version": 1,
        },
    )


def invocation_digest(invocation: ToolInvocation) -> str:
    """返回覆盖工具名称、稳定 identity 和 frozen arguments 的完整摘要。"""

    return _digest(
        {
            "arguments": thaw_json_value(invocation.arguments),
            "invocation_id": invocation.context.invocation_id,
            "tool_name": invocation.tool_name,
            "version": 1,
        },
    )


def canonical_digest(value: object) -> str:
    """返回 agentos.tool-identity v1 canonical JSON 的完整摘要。"""

    return _digest(value)


def _short_id(prefix: str, value: object) -> str:
    return f"{prefix}_{_digest(value).removeprefix('sha256:')[:32]}"


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _require_scope(
    tenant_id: str | None,
    session_id: str,
    run_id: str,
    turn_id: str,
) -> None:
    if tenant_id is not None:
        _require_identifier(tenant_id, "tenant_id")
    _require_identifier(session_id, "session_id")
    _require_identifier(run_id, "run_id")
    _require_identifier(turn_id, "turn_id")


def _require_identifier(value: object, field_name: str) -> None:
    if (
        type(value) is not str
        or not 1 <= len(value) <= 255
        or value != value.strip()
        or any(
            character.isspace()
            or unicodedata.category(character).startswith("C")
            for character in value
        )
    ):
        raise ValueError(f"{field_name} must be a valid identifier")


def _require_non_negative(value: object, field_name: str) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")


__all__ = [
    "canonical_digest",
    "compensation_operation_id",
    "invocation_digest",
    "invocation_id",
    "operation_id",
]
