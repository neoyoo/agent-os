from __future__ import annotations

from agentos.transports.a2a._json_codec import require_string
from agentos.transports.a2a._protojson import proto_int32, required_field


def _tenant(value: str | None) -> dict[str, object]:
    return {} if value is None else {"tenant": value}


def _optional(payload: dict[str, object], name: str, value: object) -> None:
    if value is not None:
        payload[name] = value


def _required_string(fields: dict[str, object], name: str) -> str:
    return require_string(required_field(fields, name, name), name)


def _optional_string(value: object, field_name: str) -> str | None:
    return None if value is None or value == "" else require_string(value, field_name)


def _optional_int(
    value: object, field_name: str, *, minimum: int, maximum: int | None = None
) -> int | None:
    return (
        None
        if value is None
        else proto_int32(value, field_name, minimum=minimum, maximum=maximum)
    )


def _optional_object(value: object, field_name: str) -> dict[str, object] | None:
    if value is None:
        return None
    if type(value) is not dict:
        raise ValueError(f"{field_name} must be an object")
    return value
