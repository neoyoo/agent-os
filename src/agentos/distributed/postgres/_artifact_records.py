from __future__ import annotations

import base64
from datetime import UTC, datetime
import json
from typing import cast

from agentos.artifacts.types import (
    ArtifactRecord,
    ArtifactValidationError,
    validate_artifact_id,
)
from agentos.distributed.errors import DistributedBackendUnavailableError
from agentos.distributed.postgres._database import AsyncConnection, Row


def record_from_row(row: Row) -> ArtifactRecord:
    try:
        return ArtifactRecord(
            cast(str, row["artifact_id"]),
            cast(str, row["session_id"]),
            cast(str | None, row["filename"]),
            cast(str, row["media_type"]),
            cast(int, row["size_bytes"]),
            cast(datetime, row["created_at"]),
        )
    except (KeyError, TypeError, ValueError):
        raise DistributedBackendUnavailableError() from None


def duplicate_upload(
    row: Row,
    candidate: ArtifactRecord,
    *,
    digest: str,
) -> ArtifactRecord:
    record = record_from_row(row)
    if (
        row["lifecycle"] != "active"
        or record.session_id != candidate.session_id
        or record.filename != candidate.filename
        or record.media_type != candidate.media_type
        or record.size_bytes != candidate.size_bytes
        or row["content_digest"] != digest
    ):
        raise ArtifactValidationError("artifact upload conflicts with existing request")
    return record


async def ensure_session(
    connection: AsyncConnection,
    tenant_id: str,
    session_id: str,
) -> None:
    await connection.execute(
        """
        INSERT INTO agentos_distributed_sessions
            (tenant_id, session_id, status, next_turn_number)
        VALUES (%s, %s, 'new', 1)
        ON CONFLICT (tenant_id, session_id) DO NOTHING
        """,
        (tenant_id, session_id),
    )


async def advisory_lock(
    connection: AsyncConnection,
    tenant_id: str,
    request_id: str,
) -> None:
    await connection.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (f"artifact\x1f{tenant_id}\x1f{request_id}",),
    )


def encode_cursor(artifact_id: str) -> str:
    raw = json.dumps(
        {"artifact_id": artifact_id, "version": 1},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> str:
    if type(cursor) is not str or not cursor:
        raise ArtifactValidationError("invalid artifact cursor")
    try:
        padding = b"=" * (-len(cursor) % 4)
        data = json.loads(
            base64.b64decode(
                cursor.encode("ascii") + padding,
                altchars=b"-_",
                validate=True,
            ).decode("utf-8"),
        )
        if (
            type(data) is not dict
            or set(data) != {"artifact_id", "version"}
            or data["version"] != 1
            or encode_cursor(data["artifact_id"]) != cursor
        ):
            raise ValueError
        validate_artifact_id(data["artifact_id"])
        return cast(str, data["artifact_id"])
    except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        raise ArtifactValidationError("invalid artifact cursor") from None


def placeholder_time() -> datetime:
    return datetime.min.replace(tzinfo=UTC)


__all__ = [
    "advisory_lock",
    "decode_cursor",
    "duplicate_upload",
    "encode_cursor",
    "ensure_session",
    "placeholder_time",
    "record_from_row",
]
