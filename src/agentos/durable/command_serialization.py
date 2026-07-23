from __future__ import annotations

import json
from typing import cast

from agentos._json_values import thaw_json_value
from agentos.durable.safety import validate_durable_data
from agentos.runtime.durable_commands import DurableRunCommand
from agentos.runtime.errors import CheckpointCorruptedError, DurableUnsafeDataError


def command_payload_to_json(kind: str, payload: object) -> str:
    """序列化持久命令，并仅在安全校验视图中隐藏加密 token。"""

    command = DurableRunCommand(
        "stored_run",
        "stored_command",
        kind,  # type: ignore[arg-type]
        payload,
    )
    normalized = thaw_json_value(command.payload)
    validate_durable_data(
        _command_payload_validation_view(command.kind, normalized),
    )
    return _encode_json(normalized)


def command_payload_from_json(kind: str, value: str) -> dict[str, object]:
    """严格读取并验证持久命令 payload。"""

    payload = _parse_json_object(value)
    try:
        canonical = command_payload_to_json(kind, payload)
    except (DurableUnsafeDataError, TypeError, ValueError):
        raise CheckpointCorruptedError("durable command record is corrupted") from None
    if canonical != value:
        raise CheckpointCorruptedError("durable command record is corrupted")
    return payload


def _command_payload_validation_view(
    kind: str,
    payload: dict[str, object],
) -> dict[str, object]:
    if kind != "resolve_side_effect" or payload["attestation_ref"] is None:
        return payload
    reference = cast(dict[str, object], payload["attestation_ref"])
    validation_payload = dict(payload)
    validation_payload["attestation_ref"] = {
        "digest": reference["digest"],
        "token": "protected-payload-token",
    }
    return validation_payload


def _parse_json_object(value: str) -> dict[str, object]:
    try:
        payload = json.loads(value, parse_constant=_reject_json_constant)
    except (TypeError, ValueError, json.JSONDecodeError):
        raise CheckpointCorruptedError("durable command record is corrupted") from None
    if type(payload) is not dict:
        raise CheckpointCorruptedError("durable command record is corrupted")
    return cast(dict[str, object], payload)


def _encode_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _reject_json_constant(_value: str) -> None:
    raise ValueError("invalid JSON constant")


__all__ = ["command_payload_from_json", "command_payload_to_json"]
