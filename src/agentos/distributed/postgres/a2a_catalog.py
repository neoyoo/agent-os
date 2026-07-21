from __future__ import annotations

import base64
from collections.abc import Mapping
from datetime import UTC, datetime
from hashlib import sha256
import json
from typing import cast

from agentos.distributed.a2a_models import (
    A2ATaskBinding,
    A2ATaskListItem,
    A2ATaskListPage,
    A2ATaskListQuery,
    A2ATaskState,
)
from agentos.distributed.errors import (
    CheckpointConflictError,
    DistributedBackendUnavailableError,
)
from agentos.distributed.models import RequestScope
from agentos.distributed.postgres._database import PostgresPool, Row, fetchall
from agentos.distributed.postgres._state_records import run_read_model


_STATUS_FILTERS = {
    A2ATaskState.SUBMITTED: "run.status IN ('created', 'queued')",
    A2ATaskState.WORKING: (
        "(run.status = 'running' OR "
        "(run.status = 'waiting' AND run.wait_kind <> 'human_input'))"
    ),
    A2ATaskState.INPUT_REQUIRED: (
        "run.status = 'waiting' AND run.wait_kind = 'human_input'"
    ),
    A2ATaskState.COMPLETED: "run.status = 'completed'",
    A2ATaskState.FAILED: "run.status = 'failed'",
    A2ATaskState.CANCELED: "run.status = 'cancelled'",
    A2ATaskState.REJECTED: "FALSE",
    A2ATaskState.AUTH_REQUIRED: "FALSE",
}


class PostgresA2ATaskCatalogStore:
    """Single-query tenant-scoped A2A task catalog."""

    def __init__(self, database: PostgresPool) -> None:
        self._database = database

    async def list(
        self,
        *,
        scope: RequestScope,
        query: A2ATaskListQuery,
    ) -> A2ATaskListPage:
        if type(scope) is not RequestScope:
            raise TypeError("scope must be RequestScope")
        if type(query) is not A2ATaskListQuery:
            raise TypeError("query must be A2ATaskListQuery")
        cursor = (
            None
            if query.page_token is None
            else _decode_page_token(query.page_token, scope, query)
        )
        sql, params = _catalog_query(scope, query, cursor)
        async with self._database.connection() as connection:
            rows = await fetchall(connection, sql, params)
        return _page_from_rows(rows, scope, query)


def _catalog_query(
    scope: RequestScope,
    query: A2ATaskListQuery,
    cursor: tuple[datetime, str] | None,
) -> tuple[str, tuple[object, ...]]:
    clauses = ["binding.tenant_id = %s"]
    params: list[object] = [scope.tenant_id]
    if query.context_id is not None:
        clauses.append("binding.session_id = %s")
        params.append(query.context_id)
    if query.status is not None:
        clauses.append(_STATUS_FILTERS[query.status])
    if query.status_timestamp_after is not None:
        clauses.append("run.updated_at > %s")
        params.append(query.status_timestamp_after)
    cursor_clause = ""
    if cursor is not None:
        cursor_clause = """
            WHERE status_updated_at < %s
               OR (status_updated_at = %s AND task_id < %s)
        """
        params.extend((cursor[0], cursor[0], cursor[1]))
    params.append(query.page_size + 1)
    sql = f"""
        WITH filtered AS (
            SELECT binding.tenant_id, binding.task_id, binding.session_id,
                   binding.run_id, run.status, run.wait_kind, run.wait_handle,
                   run.wait_detail, run.wait_not_before, run.aggregate_version,
                   run.result_content, run.updated_at AS status_updated_at
            FROM agentos_distributed_a2a_tasks AS binding
            JOIN agentos_distributed_runs AS run
              ON run.tenant_id = binding.tenant_id
             AND run.session_id = binding.session_id
             AND run.run_id = binding.run_id
            WHERE {' AND '.join(clauses)}
        ), page AS (
            SELECT * FROM filtered
            {cursor_clause}
            ORDER BY status_updated_at DESC, task_id DESC
            LIMIT %s
        ), totals AS (
            SELECT COUNT(*) AS total_size FROM filtered
        )
        SELECT totals.total_size, page.tenant_id, page.task_id,
               page.session_id, page.run_id, page.status, page.wait_kind,
               page.wait_handle, page.wait_detail, page.wait_not_before,
               page.aggregate_version, page.result_content,
               page.status_updated_at
        FROM totals
        LEFT JOIN page ON TRUE
        ORDER BY status_updated_at DESC, task_id DESC
    """
    return sql, tuple(params)


def _page_from_rows(
    rows: list[Row],
    scope: RequestScope,
    query: A2ATaskListQuery,
) -> A2ATaskListPage:
    try:
        if not rows:
            raise ValueError
        total_size = rows[0]["total_size"]
        if type(total_size) is not int or total_size < 0:
            raise TypeError
        if any(row["total_size"] != total_size for row in rows):
            raise ValueError
        items = tuple(
            _item_from_row(row)
            for row in rows
            if row["task_id"] is not None
        )
        has_more = len(items) > query.page_size
        selected = items[: query.page_size]
        if any(item.binding.tenant_id != scope.tenant_id for item in selected):
            raise ValueError
        next_page_token = (
            _encode_page_token(scope, query, selected[-1]) if has_more else ""
        )
        return A2ATaskListPage(
            items=selected,
            next_page_token=next_page_token,
            page_size=query.page_size,
            total_size=total_size,
        )
    except (KeyError, TypeError, ValueError, CheckpointConflictError):
        raise DistributedBackendUnavailableError() from None


def _item_from_row(row: Row) -> A2ATaskListItem:
    binding = A2ATaskBinding(
        tenant_id=cast(str, row["tenant_id"]),
        task_id=cast(str, row["task_id"]),
        session_id=cast(str, row["session_id"]),
        run_id=cast(str, row["run_id"]),
    )
    return A2ATaskListItem(
        binding=binding,
        run=run_read_model(row),
        status_updated_at=cast(datetime, row["status_updated_at"]),
    )


def _encode_page_token(
    scope: RequestScope,
    query: A2ATaskListQuery,
    item: A2ATaskListItem,
) -> str:
    payload = {
        "query": _query_digest(query),
        "scope": _scope_digest(scope),
        "status_timestamp": _timestamp(item.status_updated_at),
        "task_id": item.binding.task_id,
        "version": 1,
    }
    raw = _canonical_json(payload).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_page_token(
    token: str,
    scope: RequestScope,
    query: A2ATaskListQuery,
) -> tuple[datetime, str]:
    try:
        padding = b"=" * (-len(token) % 4)
        data = json.loads(
            base64.b64decode(
                token.encode("ascii") + padding,
                altchars=b"-_",
                validate=True,
            ).decode("utf-8"),
        )
        expected = {"query", "scope", "status_timestamp", "task_id", "version"}
        timestamp = _parse_timestamp(data["status_timestamp"])
        if (
            type(data) is not dict
            or set(data) != expected
            or data["query"] != _query_digest(query)
            or data["scope"] != _scope_digest(scope)
            or type(data["task_id"]) is not str
            or not data["task_id"]
            or data["version"] != 1
            or _canonical_token(data) != token
        ):
            raise ValueError
        return timestamp, cast(str, data["task_id"])
    except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        raise ValueError("a2a task page token is invalid") from None


def _canonical_token(data: Mapping[str, object]) -> str:
    raw = _canonical_json(data).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _query_digest(query: A2ATaskListQuery) -> str:
    value = {
        "context_id": query.context_id,
        "history_length": query.history_length,
        "include_artifacts": query.include_artifacts,
        "page_size": query.page_size,
        "status": None if query.status is None else query.status.value,
        "status_timestamp_after": (
            None
            if query.status_timestamp_after is None
            else _timestamp(query.status_timestamp_after)
        ),
        "version": 1,
    }
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _scope_digest(scope: RequestScope) -> str:
    value = {"tenant_id": scope.tenant_id, "version": 1}
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_timestamp(value: object) -> datetime:
    if type(value) is not str:
        raise TypeError
    parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    if _timestamp(parsed) != value:
        raise ValueError
    return parsed


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


__all__ = ["PostgresA2ATaskCatalogStore"]
