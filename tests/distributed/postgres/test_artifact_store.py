from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from hashlib import sha256

import pytest

from agentos.artifacts import ArtifactNotFoundError, ArtifactValidationError
from agentos.distributed.errors import (
    ArtifactConflictError,
    DistributedBackendUnavailableError,
)
from agentos.distributed.models import RequestScope
from agentos.distributed.postgres._artifact_records import (
    decode_cursor,
    encode_cursor,
    record_from_row,
)
from agentos.distributed.postgres.artifacts import PostgresArtifactStore
from tests.planning._async import async_test


CANDIDATE_ID = "art_00000000-0000-4000-8000-000000000001"
EXISTING_ID = "art_00000000-0000-4000-8000-000000000002"
SCOPE = RequestScope("tenant_1", "principal_1")


class RecordingBlobs:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.puts: list[tuple[str, bytes]] = []
        self.reads: list[str] = []
        self.deleted: list[str] = []
        self.put_failure: BaseException | None = None

    async def put_if_absent(self, *, artifact_id: str, data: bytes) -> bool:
        self.puts.append((artifact_id, data))
        if self.put_failure is not None:
            failure = self.put_failure
            self.put_failure = None
            raise failure
        if artifact_id in self.objects:
            return False
        self.objects[artifact_id] = data
        return True

    async def read(self, *, artifact_id: str) -> bytes | None:
        self.reads.append(artifact_id)
        return self.objects.get(artifact_id)

    async def delete(self, *, artifact_id: str) -> None:
        self.deleted.append(artifact_id)
        self.objects.pop(artifact_id, None)

    async def close(self) -> None:
        return None


class Cursor:
    def __init__(
        self,
        row: dict[str, object] | None = None,
        rows: list[dict[str, object]] | None = None,
    ) -> None:
        self._row = row
        self._rows = rows or []

    async def fetchone(self) -> dict[str, object] | None:
        return self._row

    async def fetchall(self) -> list[dict[str, object]]:
        return self._rows


class InjectedCrash(BaseException):
    pass


class ArtifactDatabase:
    def __init__(self) -> None:
        self.row: dict[str, object] | None = None
        self.mutations: list[str] = []
        self.fail_activation_once = False

    async def execute(
        self,
        query: str,
        params: tuple[object, ...] = (),
    ) -> Cursor:
        normalized = " ".join(query.split())
        if "pg_advisory_xact_lock" in normalized:
            return Cursor()
        if normalized.startswith("INSERT INTO agentos_distributed_sessions"):
            self.mutations.append("ensure_session")
            return Cursor()
        if (
            normalized.startswith("SELECT * FROM agentos_distributed_artifacts")
            and "upload_id = %s" in normalized
        ):
            return Cursor(self.row)
        if normalized.startswith("INSERT INTO agentos_distributed_artifacts"):
            (
                tenant_id,
                session_id,
                artifact_id,
                upload_id,
                filename,
                media_type,
                size_bytes,
                content_digest,
                blob_key,
            ) = params
            self.row = {
                "tenant_id": tenant_id,
                "session_id": session_id,
                "artifact_id": artifact_id,
                "upload_id": upload_id,
                "filename": filename,
                "media_type": media_type,
                "size_bytes": size_bytes,
                "content_digest": content_digest,
                "blob_key": blob_key,
                "lifecycle": "staging",
                "created_at": datetime(2026, 7, 20, tzinfo=UTC),
            }
            self.mutations.append("stage")
            return Cursor(self.row)
        if normalized.startswith("UPDATE agentos_distributed_artifacts"):
            if self.fail_activation_once:
                self.fail_activation_once = False
                raise InjectedCrash
            assert self.row is not None
            self.row["lifecycle"] = "active"
            self.mutations.append("activate")
            return Cursor(self.row)
        if "lifecycle = 'active'" in normalized:
            active = self.row is not None and self.row["lifecycle"] == "active"
            if "ORDER BY" in normalized:
                return Cursor(rows=[self.row] if active else [])
            return Cursor(self.row if active else None)
        raise AssertionError(f"unexpected query: {normalized}")

    @asynccontextmanager
    async def transaction(self):  # type: ignore[no-untyped-def]
        yield self

    @asynccontextmanager
    async def connection(self):  # type: ignore[no-untyped-def]
        yield self


def _store(database: ArtifactDatabase, blobs: RecordingBlobs) -> PostgresArtifactStore:
    return PostgresArtifactStore(  # type: ignore[arg-type]
        database,
        blobs,
        id_factory=lambda: CANDIDATE_ID,
    )


async def _upload(
    store: PostgresArtifactStore,
    *,
    data: bytes = b"content",
):  # type: ignore[no-untyped-def]
    return await store.upload(
        scope=SCOPE,
        session_id="session_1",
        upload_id="upload_1",
        data=data,
        filename="drawing.png",
        media_type="image/png",
    )


@async_test
async def test_conflicting_upload_id_fails_before_blob_io_and_database_writes() -> None:
    database = ArtifactDatabase()
    blobs = RecordingBlobs()
    store = _store(database, blobs)
    record = await _upload(store)
    replayed = await _upload(store)
    mutations = list(database.mutations)

    with pytest.raises(
        ArtifactConflictError,
        match="^artifact operation conflicts with an existing request$",
    ):
        await _upload(store, data=b"different")

    assert replayed == record
    assert blobs.puts == [(CANDIDATE_ID, b"content")]
    assert database.mutations == mutations


@async_test
async def test_retry_after_staging_before_blob_reuses_artifact_id() -> None:
    database = ArtifactDatabase()
    blobs = RecordingBlobs()
    blobs.put_failure = InjectedCrash()
    store = _store(database, blobs)

    with pytest.raises(InjectedCrash):
        await _upload(store)
    assert database.row is not None
    assert database.row["lifecycle"] == "staging"
    assert blobs.objects == {}

    record = await _upload(store)

    assert record.id == CANDIDATE_ID
    assert database.row["lifecycle"] == "active"
    assert [artifact_id for artifact_id, _ in blobs.puts] == [
        CANDIDATE_ID,
        CANDIDATE_ID,
    ]


@async_test
async def test_retry_after_blob_before_activation_validates_and_activates() -> None:
    database = ArtifactDatabase()
    database.fail_activation_once = True
    blobs = RecordingBlobs()
    store = _store(database, blobs)

    with pytest.raises(InjectedCrash):
        await _upload(store)
    assert database.row is not None
    assert database.row["lifecycle"] == "staging"
    assert blobs.objects == {CANDIDATE_ID: b"content"}

    record = await _upload(store)

    assert record.id == CANDIDATE_ID
    assert database.row["lifecycle"] == "active"
    assert blobs.reads == [CANDIDATE_ID]
    assert blobs.deleted == []


@async_test
async def test_existing_staged_blob_must_match_metadata_before_activation() -> None:
    database = ArtifactDatabase()
    database.row = {
        "tenant_id": SCOPE.tenant_id,
        "session_id": "session_1",
        "artifact_id": CANDIDATE_ID,
        "upload_id": "upload_1",
        "filename": "drawing.png",
        "media_type": "image/png",
        "size_bytes": len(b"content"),
        "content_digest": sha256(b"content").hexdigest(),
        "blob_key": CANDIDATE_ID,
        "lifecycle": "staging",
        "created_at": datetime(2026, 7, 20, tzinfo=UTC),
    }
    blobs = RecordingBlobs()
    blobs.objects[CANDIDATE_ID] = b"corrupt"
    store = _store(database, blobs)

    with pytest.raises(DistributedBackendUnavailableError):
        await _upload(store)

    assert database.row["lifecycle"] == "staging"
    assert "activate" not in database.mutations


@async_test
async def test_staging_artifact_is_invisible_to_read_and_list() -> None:
    database = ArtifactDatabase()
    blobs = RecordingBlobs()
    blobs.put_failure = InjectedCrash()
    store = _store(database, blobs)
    with pytest.raises(InjectedCrash):
        await _upload(store)

    with pytest.raises(ArtifactNotFoundError):
        await store.read(
            scope=SCOPE,
            session_id="session_1",
            artifact_id=CANDIDATE_ID,
        )
    page = await store.list(
        scope=SCOPE,
        session_id="session_1",
        cursor=None,
        limit=20,
    )

    assert page.items == ()
    assert blobs.reads == []


@async_test
async def test_cancellation_preserves_staging_and_completed_blob_for_retry() -> None:
    class BlockingPutBlobs(RecordingBlobs):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def put_if_absent(self, *, artifact_id: str, data: bytes) -> bool:
            self.started.set()
            await self.release.wait()
            return await super().put_if_absent(artifact_id=artifact_id, data=data)

    blobs = BlockingPutBlobs()
    database = ArtifactDatabase()
    store = _store(database, blobs)
    uploading = asyncio.create_task(_upload(store))
    await blobs.started.wait()

    uploading.cancel()
    blobs.release.set()
    with pytest.raises(asyncio.CancelledError):
        await uploading

    assert database.row is not None
    assert database.row["lifecycle"] == "staging"
    assert blobs.objects == {CANDIDATE_ID: b"content"}
    assert blobs.deleted == []


def test_artifact_cursor_codec_round_trips_and_rejects_noncanonical_input() -> None:
    cursor = encode_cursor(EXISTING_ID)

    assert decode_cursor(cursor) == EXISTING_ID
    with pytest.raises(ArtifactValidationError, match="invalid artifact cursor"):
        decode_cursor(cursor + "=")


def test_artifact_metadata_codec_redacts_corrupt_database_row() -> None:
    row = {
        "artifact_id": EXISTING_ID,
        "session_id": "session_1",
        "filename": "drawing.png",
        "media_type": "image/png",
        "size_bytes": len(b"content"),
        "created_at": "postgresql://user:secret@example",
    }

    with pytest.raises(DistributedBackendUnavailableError) as captured:
        record_from_row(row)

    assert str(captured.value) == "distributed backend is unavailable"
    assert "secret" not in str(captured.value)
