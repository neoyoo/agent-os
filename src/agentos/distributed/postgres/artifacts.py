"""PostgreSQL Artifact metadata orchestration with durable upload staging.

Staging rows and blobs are recovery state: retries with the same upload id converge
them to active. Automatic age-based cleanup is intentionally deferred until an
upload lease can distinguish stale work from slow in-flight blob I/O.
"""

from __future__ import annotations

from collections.abc import Callable
from hashlib import sha256

from agentos.artifacts.types import (
    ArtifactNotFoundError,
    ArtifactPage,
    ArtifactRecord,
    ArtifactValidationError,
    new_artifact_id,
)
from agentos.distributed.blobs.protocol import BlobStore
from agentos.distributed.errors import (
    ArtifactConflictError,
    DistributedBackendUnavailableError,
)
from agentos.distributed.models import ArtifactContent, RequestScope
from agentos.distributed._model_validation import require_identifier
from agentos.distributed.postgres._artifact_blobs import put_upload_candidate
from agentos.distributed.postgres._artifact_references import (
    require_artifact_unreferenced,
)
from agentos.distributed.postgres._artifact_records import (
    advisory_lock,
    decode_cursor,
    encode_cursor,
    ensure_session,
    record_from_row,
    validate_upload_retry,
)
from agentos.distributed.postgres._database import PostgresPool, fetchall, fetchone


class PostgresArtifactStore:
    """PostgreSQL metadata plus shared async BlobStore application boundary."""

    def __init__(
        self,
        database: PostgresPool,
        blobs: BlobStore,
        *,
        id_factory: Callable[[], str] | None = None,
        owns_blobs: bool = False,
    ) -> None:
        self._database = database
        self._blobs = blobs
        self._id_factory = id_factory or new_artifact_id
        self._owns_blobs = owns_blobs

    async def close(self) -> None:
        if self._owns_blobs:
            await self._blobs.close()

    async def upload(
        self,
        *,
        scope: RequestScope,
        session_id: str,
        upload_id: str,
        data: bytes,
        filename: str | None,
        media_type: str,
    ) -> ArtifactRecord:
        if type(data) is not bytes:
            raise ArtifactValidationError("artifact data must be bytes")
        require_identifier(upload_id, "upload_id")
        digest = sha256(data).hexdigest()
        async with self._database.transaction() as connection:
            await advisory_lock(connection, scope.tenant_id, upload_id)
            row = await fetchone(
                connection,
                """
                SELECT * FROM agentos_distributed_artifacts
                WHERE tenant_id = %s AND upload_id = %s
                FOR UPDATE
                """,
                (scope.tenant_id, upload_id),
            )
            if row is None:
                artifact_id = self._id_factory()
                await ensure_session(connection, scope.tenant_id, session_id)
                row = await fetchone(
                    connection,
                    """
                    INSERT INTO agentos_distributed_artifacts
                        (tenant_id, session_id, artifact_id, upload_id,
                         filename, media_type, size_bytes, content_digest,
                         blob_key, lifecycle)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'staging')
                    RETURNING *
                    """,
                    (
                        scope.tenant_id,
                        session_id,
                        artifact_id,
                        upload_id,
                        filename,
                        media_type,
                        len(data),
                        digest,
                        artifact_id,
                    ),
                )
                if row is None:
                    raise DistributedBackendUnavailableError()
            record = validate_upload_retry(
                row,
                session_id=session_id,
                filename=filename,
                media_type=media_type,
                size_bytes=len(data),
                digest=digest,
            )
            if row["lifecycle"] == "active":
                return record

        created = await put_upload_candidate(
            self._blobs,
            artifact_id=record.id,
            data=data,
        )
        if not created:
            existing = await self._blobs.read(artifact_id=record.id)
            if (
                existing is None
                or len(existing) != record.size_bytes
                or sha256(existing).hexdigest() != digest
            ):
                raise DistributedBackendUnavailableError()

        async with self._database.transaction() as connection:
            await advisory_lock(connection, scope.tenant_id, upload_id)
            row = await fetchone(
                connection,
                """
                SELECT * FROM agentos_distributed_artifacts
                WHERE tenant_id = %s AND upload_id = %s
                FOR UPDATE
                """,
                (scope.tenant_id, upload_id),
            )
            if row is None:
                raise DistributedBackendUnavailableError()
            record = validate_upload_retry(
                row,
                session_id=session_id,
                filename=filename,
                media_type=media_type,
                size_bytes=len(data),
                digest=digest,
            )
            if row["lifecycle"] == "active":
                return record
            row = await fetchone(
                connection,
                """
                UPDATE agentos_distributed_artifacts
                SET lifecycle = 'active'
                WHERE tenant_id = %s AND upload_id = %s
                  AND lifecycle = 'staging'
                RETURNING *
                """,
                (scope.tenant_id, upload_id),
            )
            if row is None:
                raise DistributedBackendUnavailableError()
            record = record_from_row(row)
            return record

    async def list(
        self,
        *,
        scope: RequestScope,
        session_id: str,
        cursor: str | None,
        limit: int,
    ) -> ArtifactPage:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ArtifactValidationError("artifact limit is invalid")
        async with self._database.connection() as connection:
            anchor = None
            if cursor is not None:
                anchor_id = decode_cursor(cursor)
                anchor = await fetchone(
                    connection,
                    """
                    SELECT created_at, artifact_id
                    FROM agentos_distributed_artifacts
                    WHERE tenant_id = %s AND session_id = %s
                      AND artifact_id = %s AND lifecycle = 'active'
                    """,
                    (scope.tenant_id, session_id, anchor_id),
                )
                if anchor is None:
                    raise ArtifactNotFoundError()
            rows = await fetchall(
                connection,
                """
                SELECT * FROM agentos_distributed_artifacts
                WHERE tenant_id = %s AND session_id = %s AND lifecycle = 'active'
                  AND (%s::timestamptz IS NULL
                       OR (created_at, artifact_id) < (%s, %s))
                ORDER BY created_at DESC, artifact_id DESC
                LIMIT %s
                """,
                (
                    scope.tenant_id,
                    session_id,
                    None if anchor is None else anchor["created_at"],
                    None if anchor is None else anchor["created_at"],
                    None if anchor is None else anchor["artifact_id"],
                    limit + 1,
                ),
            )
        records = tuple(record_from_row(row) for row in rows[:limit])
        next_cursor = None
        if len(rows) > limit:
            next_cursor = encode_cursor(records[-1].id)
        return ArtifactPage(records, next_cursor)

    async def read(
        self,
        *,
        scope: RequestScope,
        session_id: str,
        artifact_id: str,
    ) -> ArtifactContent:
        async with self._database.connection() as connection:
            row = await fetchone(
                connection,
                """
                SELECT * FROM agentos_distributed_artifacts
                WHERE tenant_id = %s AND session_id = %s AND artifact_id = %s
                  AND lifecycle = 'active'
                """,
                (scope.tenant_id, session_id, artifact_id),
            )
        if row is None:
            raise ArtifactNotFoundError()
        record = record_from_row(row)
        data = await self._blobs.read(artifact_id=record.id)
        if (
            data is None
            or len(data) != record.size_bytes
            or sha256(data).hexdigest() != row["content_digest"]
        ):
            raise DistributedBackendUnavailableError()
        return ArtifactContent(record, data)

    async def delete(
        self,
        *,
        scope: RequestScope,
        session_id: str,
        artifact_id: str,
        deletion_id: str,
    ) -> None:
        require_identifier(deletion_id, "deletion_id")
        async with self._database.transaction() as connection:
            await advisory_lock(connection, scope.tenant_id, deletion_id)
            deletion = await fetchone(
                connection,
                """
                SELECT session_id, artifact_id
                FROM agentos_distributed_artifact_deletions
                WHERE tenant_id = %s AND deletion_id = %s
                FOR UPDATE
                """,
                (scope.tenant_id, deletion_id),
            )
            if deletion is not None and (
                deletion["session_id"] != session_id
                or deletion["artifact_id"] != artifact_id
            ):
                raise ArtifactConflictError()
            await connection.execute(
                """
                SELECT session_id FROM agentos_distributed_sessions
                WHERE tenant_id = %s AND session_id = %s
                FOR UPDATE
                """,
                (scope.tenant_id, session_id),
            )
            row = await fetchone(
                connection,
                """
                SELECT * FROM agentos_distributed_artifacts
                WHERE tenant_id = %s AND session_id = %s AND artifact_id = %s
                FOR UPDATE
                """,
                (scope.tenant_id, session_id, artifact_id),
            )
            if row is None:
                raise ArtifactNotFoundError()
            if deletion is None:
                if row["lifecycle"] != "active":
                    raise ArtifactNotFoundError()
                await require_artifact_unreferenced(
                    connection,
                    tenant_id=scope.tenant_id,
                    session_id=session_id,
                    artifact_id=artifact_id,
                )
                await connection.execute(
                    """
                    INSERT INTO agentos_distributed_artifact_deletions
                        (tenant_id, deletion_id, session_id, artifact_id)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (scope.tenant_id, deletion_id, session_id, artifact_id),
                )
            elif row["deletion_id"] not in {None, deletion_id}:
                raise ArtifactConflictError()
            if row["lifecycle"] == "active":
                await connection.execute(
                    """
                    UPDATE agentos_distributed_artifacts
                    SET lifecycle = 'tombstoned', deletion_id = %s
                    WHERE tenant_id = %s AND session_id = %s AND artifact_id = %s
                    """,
                    (deletion_id, scope.tenant_id, session_id, artifact_id),
                )
        await self._blobs.delete(artifact_id=artifact_id)
        async with self._database.transaction() as connection:
            await connection.execute(
                """
                UPDATE agentos_distributed_artifacts
                SET lifecycle = 'deleted', deleted_at = clock_timestamp()
                WHERE tenant_id = %s AND session_id = %s AND artifact_id = %s
                  AND lifecycle = 'tombstoned' AND deletion_id = %s
                """,
                (scope.tenant_id, session_id, artifact_id, deletion_id),
            )

__all__ = ["PostgresArtifactStore"]
