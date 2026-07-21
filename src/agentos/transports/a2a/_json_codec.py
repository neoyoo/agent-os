from __future__ import annotations

import json
from collections.abc import Mapping

from agentos._json_values import FrozenJsonValue, thaw_json_value
from agentos.transports.a2a.operation_types import A2AOperationError


MAX_A2A_JSON_BYTES = 1024 * 1024
MAX_A2A_JSON_DEPTH = 32


class A2AWireDecodeError(ValueError):
    """固定脱敏的 A2A wire decode 失败。"""

    def __init__(
        self,
        error: A2AOperationError | None = None,
        *,
        request_id: str | int | None = None,
    ) -> None:
        if request_id is not None and not (
            (type(request_id) is str and bool(request_id))
            or type(request_id) is int
        ):
            raise TypeError("request_id must be a readable JSON-RPC id or None")
        self.error = error or A2AOperationError(-32600)
        self.request_id = request_id
        super().__init__(self.error.message)


def strict_json_object(
    data: bytes | str,
    *,
    max_bytes: int = MAX_A2A_JSON_BYTES,
) -> dict[str, object]:
    if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_A2A_JSON_BYTES:
        raise ValueError("max_bytes is invalid")
    if type(data) is bytes:
        raw = data
    elif type(data) is str:
        try:
            raw = data.encode("utf-8")
        except UnicodeEncodeError as error:
            raise A2AWireDecodeError(A2AOperationError(-32700)) from error
    else:
        raise TypeError("A2A JSON body must be bytes or str")
    if len(raw) > max_bytes:
        raise A2AWireDecodeError
    try:
        text = raw.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise A2AWireDecodeError(A2AOperationError(-32700)) from error
    if type(value) is not dict:
        raise A2AWireDecodeError
    _require_depth(value, 1)
    return value


def compact_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise ValueError("value is not canonical JSON") from error


def thaw(value: FrozenJsonValue) -> object:
    return thaw_json_value(value)


def require_fields(
    value: object,
    *,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
    field_name: str,
) -> Mapping[str, object]:
    if type(value) is not dict:
        raise ValueError(f"{field_name} must be an object")
    fields = frozenset(value)
    if not required.issubset(fields) or fields - required - optional:
        raise ValueError(f"{field_name} fields are invalid")
    return value


def require_list(value: object, field_name: str) -> list[object]:
    if type(value) is not list:
        raise ValueError(f"{field_name} must be a list")
    return value


def require_string(value: object, field_name: str, *, empty: bool = False) -> str:
    if type(value) is not str or (not empty and not value):
        raise ValueError(f"{field_name} must be a string")
    return value


def optional_string(
    value: object,
    field_name: str,
    *,
    empty: bool = False,
) -> str | None:
    if value is None:
        return None
    return require_string(value, field_name, empty=empty)


def _object_without_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite number is invalid: {value}")


def _require_depth(value: object, depth: int) -> None:
    if depth > MAX_A2A_JSON_DEPTH:
        raise A2AWireDecodeError
    if type(value) is dict:
        for item in value.values():
            _require_depth(item, depth + 1)
    elif type(value) is list:
        for item in value:
            _require_depth(item, depth + 1)


__all__ = [
    "A2AWireDecodeError",
    "MAX_A2A_JSON_BYTES",
    "MAX_A2A_JSON_DEPTH",
    "compact_json_bytes",
    "optional_string",
    "require_fields",
    "require_list",
    "require_string",
    "strict_json_object",
    "thaw",
]
