from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from hashlib import sha256

import pytest

from agentos.artifacts import ArtifactValidationError
from agentos.distributed.errors import DistributedBackendUnavailableError
from agentos.distributed.models import RequestScope
from agentos.distributed.postgres._artifact_records import (
    decode_cursor,
    encode_cursor,
    record_from_row,
)
from agentos.distributed.postgres.artifacts import PostgresArtifactStore


CANDIDATE_ID = "art_00000000-0000-4000-8000-000000000001"
EXISTING_ID = "art_00000000-0000-4000-8000-000000000002"
SCOPE = RequestScope("tenant_1", "principal_1")


class RecordingBlobs:
    def __init__(self, *, created: bool = True) -> None:
        self.created = created
        self.deleted: list[str] = []

    async def put_if_absent(self, *, artifact_id: str, data: bytes) -> bool:
        return self.created

    async def read(self, *, artifact_id: str) -> bytes | None:
        return None

    async def delete(self, *, artifact_id: str) -> None:
        self.deleted.append(artifact_id)

    async def close(self) -> None:
        return None


class Cursor:
    def __init__(self, row: dict[str, object] | None = None) -> None:
        self._row = row

    async def fetchone(self) -> dict[str, object] | None:
        return self._row

    async def fetchall(self) -> list[dict[str, object]]:
        return []


class DuplicateConnection:
    def __init__(self, row: dict[str, object]) -> None:
        self._row = row

    async def execute(
        self,
        query: str,
        params: tuple[object, ...] = (),
    ) -> Cursor:
        del params
        return Cursor(self._row if "SELECT *" in query else None)


class FailingConnection:
    async def execute(
        self,
        query: str,
        params: tuple[object, ...] = (),
    ) -> Cursor:
        del query, params
        raise DistributedBackendUnavailableError()


class Database:
    def __init__(self, connection: object) -> None:
        self._connection = connection

    @asynccontextmanager
    async def transaction(self):  # type: ignore[no-untyped-def]
        yield self._connection


def _store(database: object, blobs: RecordingBlobs) -> PostgresArtifactStore:
    return PostgresArtifactStore(  # type: ignore[arg-type]
        database,
        blobs,
        id_factory=lambda: CANDIDATE_ID,
    )


async def _upload(store: PostgresArtifactStore):  # type: ignore[no-untyped-def]
    return await store.upload(
        scope=SCOPE,
        session_id="session_1",
        upload_id="upload_1",
        data=b"content",
        filename="drawing.png",
        media_type="image/png",
    )


@pytest.mark.asyncio
async def test_blob_collision_never_deletes_existing_content() -> None:
    blobs = RecordingBlobs(created=False)
    store = _store(object(), blobs)

    with pytest.raises(ArtifactValidationError, match="collision"):
        await _upload(store)

    assert blobs.deleted == []


@pytest.mark.asyncio
async def test_duplicate_upload_deletes_only_this_candidate_blob() -> None:
    blobs = RecordingBlobs()
    row = {
        "artifact_id": EXISTING_ID,
        "session_id": "session_1",
        "filename": "drawing.png",
        "media_type": "image/png",
        "size_bytes": len(b"content"),
        "content_digest": sha256(b"content").hexdigest(),
        "created_at": datetime(2026, 7, 20, tzinfo=UTC),
        "lifecycle": "active",
    }
    store = _store(Database(DuplicateConnection(row)), blobs)

    record = await _upload(store)

    assert record.id == EXISTING_ID
    assert blobs.deleted == [CANDIDATE_ID]


@pytest.mark.asyncio
async def test_database_failure_cleans_new_candidate_blob() -> None:
    blobs = RecordingBlobs()
    store = _store(Database(FailingConnection()), blobs)

    with pytest.raises(DistributedBackendUnavailableError):
        await _upload(store)

    assert blobs.deleted == [CANDIDATE_ID]


@pytest.mark.asyncio
async def test_cancellation_waits_for_conditional_put_before_cleanup() -> None:
    class BlockingPutBlobs(RecordingBlobs):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def put_if_absent(self, *, artifact_id: str, data: bytes) -> bool:
            self.started.set()
            await self.release.wait()
            return True

    blobs = BlockingPutBlobs()
    store = _store(object(), blobs)
    uploading = asyncio.create_task(_upload(store))
    await blobs.started.wait()

    uploading.cancel()
    blobs.release.set()
    with pytest.raises(asyncio.CancelledError):
        await uploading

    assert blobs.deleted == [CANDIDATE_ID]


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
