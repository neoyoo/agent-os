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
from agentos.distributed.errors import DistributedBackendUnavailableError
from agentos.distributed.models import ArtifactContent, RequestScope
from agentos.distributed._model_validation import require_identifier
from agentos.distributed.postgres._artifact_blobs import (
    cleanup_blob,
    put_upload_candidate,
)
from agentos.distributed.postgres._artifact_records import (
    advisory_lock,
    decode_cursor,
    duplicate_upload,
    encode_cursor,
    ensure_session,
    placeholder_time,
    record_from_row,
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
        artifact_id = self._id_factory()
        digest = sha256(data).hexdigest()
        candidate = ArtifactRecord(
            artifact_id,
            session_id,
            filename,
            media_type,
            len(data),
            placeholder_time(),
        )
        created = False
        try:
            created = await put_upload_candidate(
                self._blobs,
                artifact_id=artifact_id,
                data=data,
            )
            if not created:
                raise ArtifactValidationError("artifact id collision")
            async with self._database.transaction() as connection:
                await advisory_lock(connection, scope.tenant_id, upload_id)
                duplicate = await fetchone(
                    connection,
                    """
                    SELECT * FROM agentos_distributed_artifacts
                    WHERE tenant_id = %s AND upload_id = %s
                    FOR UPDATE
                    """,
                    (scope.tenant_id, upload_id),
                )
                if duplicate is not None:
                    record = duplicate_upload(
                        duplicate,
                        candidate,
                        digest=digest,
                    )
                else:
                    await ensure_session(connection, scope.tenant_id, session_id)
                    row = await fetchone(
                        connection,
                        """
                        INSERT INTO agentos_distributed_artifacts
                            (tenant_id, session_id, artifact_id, upload_id,
                             filename, media_type, size_bytes, content_digest,
                             blob_key, lifecycle)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'active')
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
                    record = record_from_row(row)
            if record.id != artifact_id:
                await cleanup_blob(self._blobs, artifact_id)
            return record
        except BaseException:
            if created:
                await cleanup_blob(self._blobs, artifact_id)
            raise

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
            row = await fetchone(
                connection,
                """
                SELECT * FROM agentos_distributed_artifacts
                WHERE tenant_id = %s AND session_id = %s AND artifact_id = %s
                FOR UPDATE
                """,
                (scope.tenant_id, session_id, artifact_id),
            )
            if row is None or (
                row["lifecycle"] != "active" and row["deletion_id"] != deletion_id
            ):
                raise ArtifactNotFoundError()
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
