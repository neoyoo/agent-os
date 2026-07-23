from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from agentos.artifacts.types import ArtifactNotFoundError
from agentos.distributed.blobs.s3 import S3BlobStore
from agentos.distributed.migrations.service import (
    DistributedMigrationService,
    canonical_migration_plan,
)
from agentos.distributed.models import RequestScope
from agentos.distributed.postgres._database import PostgresPool, Row, fetchone
from agentos.distributed.postgres.artifacts import PostgresArtifactStore
from agentos.distributed.postgres.migrations import PostgresMigrationPort
from tests.integration._artifact_live_support import (
    artifact_live_settings,
    cleanup_bucket,
    create_bucket,
    s3_session,
)


pytestmark = pytest.mark.integration
_DATA = b"%PDF-1.7\nlive artifact recovery"


class _GatedBlobStore:
    def __init__(self, delegate: S3BlobStore, *, after_put: bool) -> None:
        self._delegate = delegate
        self._after_put = after_put
        self.reached = asyncio.Event()
        self.release = asyncio.Event()

    async def put_if_absent(self, *, artifact_id: str, data: bytes) -> bool:
        if not self._after_put:
            self.reached.set()
            await self.release.wait()
            raise _InjectedCrash
        created = await self._delegate.put_if_absent(
            artifact_id=artifact_id,
            data=data,
        )
        if self._after_put:
            self.reached.set()
            await self.release.wait()
        return created

    async def read(self, *, artifact_id: str) -> bytes | None:
        return await self._delegate.read(artifact_id=artifact_id)

    async def delete(self, *, artifact_id: str) -> None:
        await self._delegate.delete(artifact_id=artifact_id)

    async def close(self) -> None:
        return None


class _InjectedCrash(BaseException):
    pass


@pytest.mark.parametrize("window", ("before_blob", "after_blob"))
def test_live_artifact_upload_recovers_both_staging_crash_windows(
    window: str,
) -> None:
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(_verify_upload_window(window))


async def _verify_upload_window(window: str) -> None:
    settings = artifact_live_settings()
    suffix = uuid4().hex
    scope = RequestScope(f"tenant_artifact_{suffix}", "principal_1")
    other_scope = RequestScope(f"other_artifact_{suffix}", "principal_1")
    session_id = f"session_{suffix}"
    upload_id = f"upload_{suffix}"
    bucket = f"{settings.bucket}-{suffix}"[:63]
    pool = await PostgresPool.open(settings.postgres_dsn, min_size=0, max_size=2)
    session = s3_session(settings)
    bucket_created = False
    blobs: S3BlobStore | None = None
    try:
        await DistributedMigrationService(
            port=PostgresMigrationPort(pool),
            plan=canonical_migration_plan(),
        ).apply()
        await create_bucket(settings, session, bucket)
        bucket_created = True
        blobs = await S3BlobStore.open(
            bucket_name=bucket,
            endpoint_url=settings.endpoint_url,
            region_name=settings.region,
            session=session,
        )
        gated = _GatedBlobStore(blobs, after_put=window == "after_blob")
        crashing = PostgresArtifactStore(pool, gated)
        upload = asyncio.create_task(
            crashing.upload(
                scope=scope,
                session_id=session_id,
                upload_id=upload_id,
                data=_DATA,
                filename="drawing.pdf",
                media_type="application/pdf",
            ),
        )
        await asyncio.wait_for(gated.reached.wait(), timeout=5)

        row = await _artifact_row(pool, scope.tenant_id, upload_id)
        assert row["lifecycle"] == "staging"
        artifact_id = str(row["artifact_id"])
        page = await crashing.list(
            scope=scope,
            session_id=session_id,
            cursor=None,
            limit=10,
        )
        assert page.items == ()
        assert page.next_cursor is None
        assert await blobs.read(artifact_id=artifact_id) == (
            _DATA if window == "after_blob" else None
        )

        if window == "before_blob":
            gated.release.set()
            with pytest.raises(_InjectedCrash):
                await upload
        else:
            upload.cancel()
            gated.release.set()
            with pytest.raises(asyncio.CancelledError):
                await upload

        recovered = PostgresArtifactStore(pool, blobs)
        record = await recovered.upload(
            scope=scope,
            session_id=session_id,
            upload_id=upload_id,
            data=_DATA,
            filename="drawing.pdf",
            media_type="application/pdf",
        )
        assert record.id == artifact_id
        assert (await _artifact_row(pool, scope.tenant_id, upload_id))[
            "lifecycle"
        ] == "active"
        assert (await recovered.read(
            scope=scope,
            session_id=session_id,
            artifact_id=record.id,
        )).data == _DATA
        with pytest.raises(ArtifactNotFoundError):
            await recovered.read(
                scope=other_scope,
                session_id=session_id,
                artifact_id=record.id,
            )
    finally:
        if blobs is not None:
            await blobs.close()
        await _cleanup_postgres(pool, scope.tenant_id)
        await pool.close()
        if bucket_created:
            await cleanup_bucket(settings, session, bucket)


async def _artifact_row(
    pool: PostgresPool,
    tenant_id: str,
    upload_id: str,
) -> Row:
    async with pool.connection() as connection:
        row = await fetchone(
            connection,
            """
            SELECT artifact_id, lifecycle FROM agentos_distributed_artifacts
            WHERE tenant_id = %s AND upload_id = %s
            """,
            (tenant_id, upload_id),
        )
    assert row is not None
    return row


async def _cleanup_postgres(pool: PostgresPool, tenant_id: str) -> None:
    async with pool.transaction() as connection:
        await connection.execute(
            "DELETE FROM agentos_distributed_artifact_deletions WHERE tenant_id = %s",
            (tenant_id,),
        )
        await connection.execute(
            "DELETE FROM agentos_distributed_artifacts WHERE tenant_id = %s",
            (tenant_id,),
        )
        await connection.execute(
            "DELETE FROM agentos_distributed_sessions WHERE tenant_id = %s",
            (tenant_id,),
        )
