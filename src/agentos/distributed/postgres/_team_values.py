from __future__ import annotations

from datetime import UTC, datetime

from agentos.distributed.postgres._database import Row


def row_text(row: Row, field_name: str) -> str:
    value = row[field_name]
    if type(value) is not str:
        raise TypeError(f"{field_name} must be str")
    return value


def row_datetime(row: Row, field_name: str) -> datetime:
    value = row[field_name]
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise TypeError(f"{field_name} must be timezone-aware datetime")
    return value.astimezone(UTC)


__all__ = ["row_datetime", "row_text"]
