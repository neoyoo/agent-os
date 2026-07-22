from __future__ import annotations

from collections.abc import Mapping
import json
import unicodedata

from agentos._json_values import (
    FrozenJsonObject,
    freeze_json_mapping,
    thaw_json_value,
)


MAX_INTERNAL_START_PAYLOAD_BYTES = 4096
_PAYLOAD_FIELDS = frozenset(
    {"team_id", "message_id", "recipient_agent_id", "action"},
)


def normalize_internal_start_payload(
    value: Mapping[str, object] | FrozenJsonObject,
) -> FrozenJsonObject:
    """校验并冻结 internal-start 的 canonical Team source payload。"""

    if not isinstance(value, Mapping):
        raise TypeError("source_payload must be a JSON object")
    if set(value) != _PAYLOAD_FIELDS:
        raise ValueError("source_payload fields are invalid")
    for field_name in ("team_id", "message_id", "recipient_agent_id"):
        _require_identifier(value[field_name], field_name)
    if value["action"] != "team_read_messages":
        raise ValueError("source_payload action is invalid")
    frozen = freeze_json_mapping(value)
    canonical_internal_start_payload(frozen)
    return frozen


def canonical_internal_start_payload(value: FrozenJsonObject) -> str:
    """返回用于持久化和 Context 投影的 canonical JSON。"""

    if type(value) is not FrozenJsonObject:
        raise TypeError("source_payload must be FrozenJsonObject")
    encoded = json.dumps(
        thaw_json_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if len(encoded) > MAX_INTERNAL_START_PAYLOAD_BYTES:
        raise ValueError("source_payload must not exceed 4096 UTF-8 bytes")
    return encoded.decode("utf-8")


def _require_identifier(value: object, field_name: str) -> None:
    if (
        type(value) is not str
        or not 1 <= len(value) <= 255
        or value != value.strip()
        or any(
            character.isspace() or unicodedata.category(character).startswith("C")
            for character in value
        )
    ):
        raise ValueError(f"{field_name} must be an identifier")


__all__ = [
    "MAX_INTERNAL_START_PAYLOAD_BYTES",
    "canonical_internal_start_payload",
    "normalize_internal_start_payload",
]
