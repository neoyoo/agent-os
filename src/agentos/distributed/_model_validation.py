from __future__ import annotations

import unicodedata
from datetime import UTC, datetime


_MAX_IDENTIFIER_LENGTH = 255


def require_identifier(value: object, field_name: str) -> None:
    if (
        type(value) is not str
        or not 1 <= len(value) <= _MAX_IDENTIFIER_LENGTH
        or value != value.strip()
        or any(
            character.isspace()
            or unicodedata.category(character).startswith("C")
            for character in value
        )
    ):
        raise ValueError(f"{field_name} is invalid")


def require_non_negative(value: object, field_name: str) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")


def require_positive(value: object, field_name: str) -> None:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")


def normalize_utc(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def normalize_optional_utc(
    value: datetime | None,
    field_name: str,
) -> datetime | None:
    if value is None:
        return None
    return normalize_utc(value, field_name)
