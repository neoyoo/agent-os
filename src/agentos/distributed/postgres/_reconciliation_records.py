from __future__ import annotations

from agentos.distributed.postgres._database import Row
from agentos.durable.serialization import (
    execution_cursor_from_json,
    execution_cursor_to_json,
)


def cursor_from_row(
    row: Row,
    error_type: type[Exception],
    *,
    require_pending: bool = True,
):  # type: ignore[no-untyped-def]
    payload = (
        row["cursor_payload_json"]
        if "cursor_payload_json" in row
        else row["payload_json"]
    )
    if type(payload) is not str:
        raise error_type()
    try:
        cursor = execution_cursor_from_json(payload)
    except Exception:
        raise error_type() from None
    if execution_cursor_to_json(cursor) != payload or (
        require_pending and cursor.stage != "pending_tools"
    ):
        raise error_type()
    return cursor


def cursor_contains_record(cursor, record) -> bool:  # type: ignore[no-untyped-def]
    matches = tuple(
        item
        for item in cursor.pending_tools
        if item.invocation_id == record.invocation_id
    )
    return (
        len(matches) == 1
        and record.invocation_ref is not None
        and matches[0].invocation_ref == record.invocation_ref
        and matches[0].tool_name == record.tool_name
    )


__all__ = ["cursor_contains_record", "cursor_from_row"]
