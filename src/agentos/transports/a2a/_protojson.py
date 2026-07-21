from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import IntEnum
from typing import TypeVar


class _Unset:
    __slots__ = ()

    def __repr__(self) -> str:
        return "A2A_UNSET"


A2A_UNSET = _Unset()
TEnum = TypeVar("TEnum", bound=IntEnum)


def proto_fields(
    value: object,
    aliases: Mapping[str, str],
    *,
    field_name: str,
) -> dict[str, object]:
    """Resolve ProtoJSON camel/snake aliases while ignoring future fields."""

    if type(value) is not dict:
        raise ValueError(f"{field_name} must be an object")
    result: dict[str, object] = {}
    consumed: set[str] = set()
    for json_name, proto_name in aliases.items():
        names = (json_name,) if json_name == proto_name else (json_name, proto_name)
        present = tuple(name for name in names if name in value)
        if len(present) > 1:
            raise ValueError(f"{field_name} contains duplicate field aliases")
        if present:
            result[json_name] = value[present[0]]
            consumed.add(present[0])
    return result


def required_field(fields: Mapping[str, object], name: str, field_name: str) -> object:
    if name not in fields or fields[name] is None:
        raise ValueError(f"{field_name} is required")
    return fields[name]


def repeated(value: object, field_name: str) -> list[object]:
    if value is None:
        return []
    if type(value) is not list or any(item is None for item in value):
        raise ValueError(f"{field_name} must be an array without null elements")
    return value


def proto_enum(value: object, enum_type: type[TEnum], field_name: str) -> TEnum | int:
    if type(value) is str:
        try:
            return enum_type[value]
        except KeyError as error:
            raise ValueError(f"{field_name} has an unknown symbolic value") from error
    if type(value) is not int:
        raise ValueError(f"{field_name} must be an enum name or integer")
    try:
        return enum_type(value)
    except ValueError:
        return value


def proto_enum_json(value: IntEnum | int) -> str | int:
    return value.name if isinstance(value, IntEnum) else value


def decode_proto_bytes(value: object, field_name: str) -> bytes:
    if type(value) is not str:
        raise ValueError(f"{field_name} must be a base64 string")
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError as error:
        raise ValueError(f"{field_name} must be ASCII base64") from error
    if any(character in b" \t\r\n" for character in encoded):
        raise ValueError(f"{field_name} must not contain whitespace")
    padding_at = encoded.find(b"=")
    if padding_at >= 0:
        if encoded[padding_at:] not in {b"=", b"=="}:
            raise ValueError(f"{field_name} has invalid base64 padding")
        body = encoded[:padding_at]
    else:
        body = encoded
    if len(body) % 4 == 1:
        raise ValueError(f"{field_name} has invalid base64 length")
    padded = body + b"=" * (-len(body) % 4)
    try:
        return base64.b64decode(padded, altchars=b"-_", validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError(f"{field_name} is invalid base64") from error


def encode_proto_bytes(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def proto_int32(
    value: object,
    field_name: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if type(value) is str:
        if not value or value != value.strip():
            raise ValueError(f"{field_name} must be an integer")
        try:
            value = int(value, 10)
        except ValueError as error:
            raise ValueError(f"{field_name} must be an integer") from error
    if type(value) is not int or not -(2**31) <= value < 2**31:
        raise ValueError(f"{field_name} must be an int32")
    if minimum is not None and value < minimum:
        raise ValueError(f"{field_name} is below its minimum")
    if maximum is not None and value > maximum:
        raise ValueError(f"{field_name} exceeds its maximum")
    return value


def proto_timestamp(value: object, field_name: str) -> datetime:
    if type(value) is not str or not value:
        raise ValueError(f"{field_name} must be a timestamp string")
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as error:
        raise ValueError(f"{field_name} is invalid") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} must include an offset")
    return parsed.astimezone(UTC)


def proto_timestamp_json(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    utc = value.astimezone(UTC)
    if utc.microsecond:
        return utc.isoformat(timespec="microseconds").replace("+00:00", "Z")
    return utc.isoformat(timespec="seconds").replace("+00:00", "Z")


__all__ = [
    "A2A_UNSET",
    "decode_proto_bytes",
    "encode_proto_bytes",
    "proto_enum",
    "proto_enum_json",
    "proto_fields",
    "proto_int32",
    "proto_timestamp",
    "proto_timestamp_json",
    "repeated",
    "required_field",
]
