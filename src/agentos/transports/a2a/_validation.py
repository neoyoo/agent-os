from __future__ import annotations

import json
import unicodedata
from collections.abc import Iterable, Mapping
from urllib.parse import urlsplit

from agentos._json_values import (
    FrozenJsonObject,
    freeze_json_mapping,
    thaw_json_value,
)


MAX_IDENTIFIER_CHARACTERS = 255
MAX_METADATA_BYTES = 16 * 1024


def require_identifier(value: object, field_name: str) -> str:
    if (
        type(value) is not str
        or not 1 <= len(value) <= MAX_IDENTIFIER_CHARACTERS
        or value != value.strip()
        or any(
            character.isspace() or unicodedata.category(character).startswith("C")
            for character in value
        )
    ):
        raise ValueError(f"{field_name} is invalid")
    return value


def require_optional_identifier(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return require_identifier(value, field_name)


def require_text(value: object, field_name: str, *, allow_empty: bool = True) -> str:
    if type(value) is not str or (not allow_empty and not value):
        raise ValueError(f"{field_name} is invalid")
    return value


def require_url_syntax(value: object, field_name: str) -> str:
    text = require_text(value, field_name, allow_empty=False)
    try:
        parsed = urlsplit(text)
        port = parsed.port
    except ValueError as error:
        raise ValueError(f"{field_name} is invalid") from error
    if not parsed.scheme or not parsed.hostname or port is not None and port < 1:
        raise ValueError(f"{field_name} is invalid")
    return text


def freeze_metadata(
    value: Mapping[str, object] | FrozenJsonObject,
    *,
    field_name: str = "metadata",
) -> FrozenJsonObject:
    frozen = freeze_json_mapping(value)
    try:
        size = len(
            json.dumps(
                thaw_json_value(frozen),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
                allow_nan=False,
            ).encode("utf-8"),
        )
    except UnicodeEncodeError as error:
        raise ValueError(f"{field_name} must be valid UTF-8") from error
    if size > MAX_METADATA_BYTES:
        raise ValueError(f"{field_name} exceeds the maximum size")
    return frozen


def freeze_json_object(
    value: Mapping[str, object] | FrozenJsonObject,
    *,
    field_name: str,
) -> FrozenJsonObject:
    try:
        return freeze_json_mapping(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field_name} must be a JSON object") from error


def freeze_string_tuple(
    values: Iterable[str],
    field_name: str,
    *,
    allow_empty: bool = True,
) -> tuple[str, ...]:
    if type(values) is str:
        raise TypeError(f"{field_name} must contain str values")
    result = tuple(values)
    if not allow_empty and not result:
        raise ValueError(f"{field_name} must not be empty")
    for value in result:
        require_identifier(value, field_name)
    if len(set(result)) != len(result):
        raise ValueError(f"{field_name} must not contain duplicates")
    return result


__all__ = [
    "MAX_IDENTIFIER_CHARACTERS",
    "MAX_METADATA_BYTES",
    "freeze_metadata",
    "freeze_json_object",
    "freeze_string_tuple",
    "require_identifier",
    "require_optional_identifier",
    "require_text",
    "require_url_syntax",
]
