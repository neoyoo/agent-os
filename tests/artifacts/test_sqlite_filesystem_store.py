import asyncio
import os
import sqlite3
import threading
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest

from agentos.artifacts.sqlite_filesystem import (
    ArtifactContentMissingError,
    ArtifactMetadataCorruptedError,
    SqliteFilesystemArtifactStore,
)
from agentos.artifacts.types import (
    ArtifactError,
    ArtifactNotFoundError,
    ArtifactValidationError,
)
from tests.artifacts._async import async_test


@asynccontextmanager
async def _store(tmp_path, *, clock=None, id_factory=None):
    store = await SqliteFilesystemArtifactStore.open(
        database_path=tmp_path / "state.db",
        artifact_root=tmp_path / "artifacts",
        clock=clock,
        id_factory=id_factory,
    )
    try:
        yield store
    finally:
        await store.close()


async def _put(store, *, session_id="session-1", data=b"drawing"):
    return await store.put(
        session_id=session_id,
        data=data,
        filename="drawing.png",
        media_type="image/png",
    )


def _block_after_commit(store, monkeypatch):  # type: ignore[no-untyped-def]
    committed = asyncio.Event()
    release = asyncio.Event()
    connection = store._connection
    assert connection is not None
    original_commit = connection.commit

    async def blocked_commit() -> None:
        await original_commit()
        committed.set()
        await release.wait()

    monkeypatch.setattr(connection, "commit", blocked_commit)
    return committed, release


@async_test
async def test_restart_reads_metadata_content_and_stable_page(tmp_path) -> None:
    created_at = datetime(2026, 7, 17, 8, tzinfo=UTC)
    timestamps = iter((created_at, created_at + timedelta(seconds=1)))
    async with _store(tmp_path, clock=lambda: next(timestamps)) as first_store:
        first = await _put(first_store, data=b"first")
        second = await _put(first_store, data=b"second")

    async with _store(tmp_path) as restarted:
        assert await restarted.get("session-1", first.id) == first
        assert await restarted.read("session-1", first.id) == b"first"
        first_page = await restarted.list("session-1", limit=1)
        assert first_page.items == (second,)
        assert first_page.next_cursor is not None
        assert (
            await restarted.list("session-1", cursor=first_page.next_cursor)
        ).items == (first,)


@async_test
async def test_duplicate_content_gets_independent_ids_and_no_path_is_exposed(
    tmp_path,
) -> None:
    async with _store(tmp_path) as store:
        payload = b"artifact-bytes-must-stay-outside-sqlite"
        first = await _put(store, data=payload)
        second = await _put(store, data=payload)

        assert first.id != second.id
        assert not hasattr(first, "blob_key")
        assert str(tmp_path) not in repr(first)
        with sqlite3.connect(tmp_path / "state.db") as connection:
            blob_keys = connection.execute(
                "SELECT blob_key FROM durable_artifacts ORDER BY artifact_id"
            ).fetchall()
        assert all(str(tmp_path) not in blob_key for (blob_key,) in blob_keys)
        database_bytes = (tmp_path / "state.db").read_bytes()
        assert payload not in database_bytes
        assert str(tmp_path).encode() not in database_bytes


@async_test
async def test_concurrent_puts_are_atomic_and_distinct(tmp_path) -> None:
    async with _store(tmp_path) as store:
        records = await asyncio.gather(
            *(_put(store, data=str(index).encode()) for index in range(8))
        )

        assert len({record.id for record in records}) == 8
        assert len((await store.list("session-1", limit=100)).items) == 8


@pytest.mark.parametrize("operation", ["get", "read", "delete"])
@async_test
async def test_unknown_and_cross_session_are_indistinguishable(
    tmp_path,
    operation: str,
) -> None:
    async with _store(tmp_path) as store:
        record = await _put(store, session_id="session-1")
        method = getattr(store, operation)
        unknown_id = "art_00000000-0000-4000-8000-000000000001"

        errors = []
        for artifact_id in (record.id, unknown_id):
            with pytest.raises(ArtifactNotFoundError) as error:
                await method("session-2", artifact_id)
            errors.append(str(error.value))

        assert errors == ["artifact not found", "artifact not found"]


@async_test
async def test_missing_blob_fails_closed_without_exposing_storage_path(
    tmp_path,
) -> None:
    async with _store(tmp_path) as store:
        record = await _put(store)
        blob_path = next((tmp_path / "artifacts").rglob(f"{record.id}.blob"))
        blob_path.unlink()

        with pytest.raises(
            ArtifactContentMissingError,
            match="^artifact content missing$",
        ) as error:
            await store.read("session-1", record.id)

        assert str(tmp_path) not in str(error.value)
        assert await store.get("session-1", record.id) == record


@async_test
async def test_failed_metadata_insert_removes_temporary_and_final_blobs(
    tmp_path,
) -> None:
    artifact_id = "art_00000000-0000-4000-8000-000000000001"
    async with _store(tmp_path, id_factory=lambda: artifact_id) as store:
        with sqlite3.connect(tmp_path / "state.db") as connection:
            connection.execute(
                """
                CREATE TRIGGER reject_artifact_insert
                BEFORE INSERT ON durable_artifacts
                BEGIN
                  SELECT RAISE(ABORT, 'injected failure');
                END
                """
            )

        with pytest.raises(sqlite3.IntegrityError, match="injected failure"):
            await _put(store)

        blob_files = tuple((tmp_path / "artifacts").rglob("*"))
        assert all(not path.is_file() for path in blob_files)
        with sqlite3.connect(tmp_path / "state.db") as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM durable_artifacts"
            ).fetchone() == (0,)


@async_test
async def test_write_os_error_is_stable_and_does_not_expose_storage_path(
    tmp_path,
    monkeypatch,
) -> None:
    async with _store(tmp_path) as store:
        private_path = str(tmp_path / "artifacts" / "private")

        def fail_link(_source, _target) -> None:  # type: ignore[no-untyped-def]
            raise PermissionError(private_path)

        monkeypatch.setattr(os, "link", fail_link)

        with pytest.raises(
            ArtifactError,
            match="^artifact content write failed$",
        ) as error:
            await _put(store)

        assert private_path not in str(error.value)
        assert (await store.list("session-1")).items == ()
        assert all(
            not path.is_file()
            for path in (tmp_path / "artifacts").rglob("*")
        )


@async_test
async def test_delete_and_delete_session_remove_only_scoped_controlled_blobs(
    tmp_path,
) -> None:
    async with _store(tmp_path) as store:
        one = await _put(store, session_id="session-1", data=b"one")
        two = await _put(store, session_id="session-1", data=b"two")
        other = await _put(store, session_id="session-2", data=b"other")
        outside = tmp_path / "outside.txt"
        outside.write_bytes(b"keep")

        with sqlite3.connect(tmp_path / "state.db") as connection:
            connection.execute(
                "UPDATE durable_artifacts SET blob_key = ? WHERE artifact_id = ?",
                ("../outside.txt", one.id),
            )

        await store.delete("session-1", one.id)
        await store.delete_session("session-1")

        assert outside.read_bytes() == b"keep"
        assert (await store.list("session-1")).items == ()
        assert await store.read("session-2", other.id) == b"other"
        with pytest.raises(ArtifactNotFoundError):
            await store.get("session-1", two.id)


@async_test
async def test_delete_session_is_idempotent_and_missing_blob_can_be_cleaned_up(
    tmp_path,
) -> None:
    async with _store(tmp_path) as store:
        record = await _put(store)
        next((tmp_path / "artifacts").rglob(f"{record.id}.blob")).unlink()

        await store.delete_session("session-1")
        await store.delete_session("session-1")

        assert (await store.list("session-1")).items == ()


@async_test
async def test_delete_session_rejects_corrupted_id_before_touching_files(
    tmp_path,
) -> None:
    async with _store(tmp_path) as store:
        outside = tmp_path / "outside.blob"
        outside.write_bytes(b"keep")
        with sqlite3.connect(tmp_path / "state.db") as connection:
            connection.execute(
                """
                INSERT INTO durable_artifacts (
                    artifact_id, session_id, filename, media_type,
                    size_bytes, created_at, blob_key
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "../../outside",
                    "corrupted-session",
                    None,
                    "image/png",
                    4,
                    "2026-07-17T08:00:00.000000+00:00",
                    "../../outside.blob",
                ),
            )

        with pytest.raises(ArtifactValidationError, match="artifact id is invalid"):
            await store.delete_session("corrupted-session")

        assert outside.read_bytes() == b"keep"
        with sqlite3.connect(tmp_path / "state.db") as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM durable_artifacts WHERE session_id = ?",
                ("corrupted-session",),
            ).fetchone() == (1,)


@async_test
async def test_delete_failure_keeps_metadata_for_safe_retry(
    tmp_path,
    monkeypatch,
) -> None:
    async with _store(tmp_path) as store:
        record = await _put(store)

        def fail_delete(_artifact_id: str) -> None:
            raise ArtifactError("artifact content delete failed")

        monkeypatch.setattr(store._blobs, "stage_delete", fail_delete)

        with pytest.raises(ArtifactError, match="content delete failed"):
            await store.delete("session-1", record.id)

        assert await store.get("session-1", record.id) == record


@async_test
async def test_cancelled_delete_restores_staged_blob_and_metadata(
    tmp_path,
    monkeypatch,
) -> None:
    async with _store(tmp_path) as store:
        record = await _put(store, data=b"keep")
        staged = threading.Event()
        release = threading.Event()
        original_stage = store._blobs.stage_delete

        def block_after_staging(artifact_id: str):  # type: ignore[no-untyped-def]
            deletion = original_stage(artifact_id)
            staged.set()
            if not release.wait(timeout=5):
                raise AssertionError("delete release timed out")
            return deletion

        monkeypatch.setattr(store._blobs, "stage_delete", block_after_staging)
        delete_task = asyncio.create_task(store.delete("session-1", record.id))
        assert await asyncio.to_thread(staged.wait, 5)
        delete_task.cancel()
        release.set()

        with pytest.raises(asyncio.CancelledError):
            await delete_task

        assert await store.get("session-1", record.id) == record
        assert await store.read("session-1", record.id) == b"keep"
        assert not any(
            path.name.startswith(".delete-")
            for path in (tmp_path / "artifacts").rglob("*")
        )


@async_test
async def test_cancelled_put_after_filesystem_write_removes_uncommitted_blob(
    tmp_path,
    monkeypatch,
) -> None:
    artifact_id = "art_00000000-0000-4000-8000-000000000001"
    async with _store(tmp_path, id_factory=lambda: artifact_id) as store:
        written = threading.Event()
        release = threading.Event()
        original_write = store._blobs.write_exclusive

        def block_after_write(handle: str, data: bytes) -> None:
            original_write(handle, data)
            written.set()
            if not release.wait(timeout=5):
                raise AssertionError("write release timed out")

        monkeypatch.setattr(store._blobs, "write_exclusive", block_after_write)
        uploading = asyncio.create_task(_put(store, data=b"cancelled"))
        assert await asyncio.to_thread(written.wait, 5)
        uploading.cancel()
        release.set()

        with pytest.raises(asyncio.CancelledError):
            await uploading
        assert (await store.list("session-1", limit=20)).items == ()
        assert store._blobs.exists(artifact_id) is False


@async_test
async def test_cancelled_put_after_commit_keeps_committed_blob_and_metadata(
    tmp_path,
    monkeypatch,
) -> None:
    artifact_id = "art_00000000-0000-4000-8000-000000000001"
    async with _store(tmp_path, id_factory=lambda: artifact_id) as store:
        committed, release = _block_after_commit(store, monkeypatch)
        uploading = asyncio.create_task(_put(store, data=b"committed"))
        await asyncio.wait_for(committed.wait(), timeout=5)
        uploading.cancel()
        release.set()

        with pytest.raises(asyncio.CancelledError):
            await uploading
        assert await store.read("session-1", artifact_id) == b"committed"


@pytest.mark.parametrize("operation", ["delete", "delete_session"])
@async_test
async def test_cancelled_delete_after_commit_keeps_metadata_deleted(
    tmp_path,
    monkeypatch,
    operation: str,
) -> None:
    async with _store(tmp_path) as store:
        record = await _put(store, data=b"deleted")
        committed, release = _block_after_commit(store, monkeypatch)
        deleting = asyncio.create_task(
            store.delete("session-1", record.id)
            if operation == "delete"
            else store.delete_session("session-1")
        )
        await asyncio.wait_for(committed.wait(), timeout=5)
        deleting.cancel()
        release.set()

        with pytest.raises(asyncio.CancelledError):
            await deleting
        with pytest.raises(ArtifactNotFoundError):
            await store.get("session-1", record.id)


@async_test
async def test_restart_restores_staged_blob_when_metadata_is_still_live(
    tmp_path,
) -> None:
    async with _store(tmp_path) as store:
        record = await _put(store, data=b"recover")
        staged = store._blobs.stage_delete(record.id)
        assert staged is not None

    async with _store(tmp_path) as restarted:
        assert await restarted.read("session-1", record.id) == b"recover"


@async_test
async def test_restart_finishes_staged_blob_when_metadata_was_deleted(
    tmp_path,
) -> None:
    async with _store(tmp_path) as store:
        record = await _put(store, data=b"delete")
        staged = store._blobs.stage_delete(record.id)
        assert staged is not None
        with sqlite3.connect(tmp_path / "state.db") as connection:
            connection.execute(
                "DELETE FROM durable_artifacts WHERE artifact_id = ?",
                (record.id,),
            )

    async with _store(tmp_path) as restarted:
        with pytest.raises(ArtifactNotFoundError):
            await restarted.get("session-1", record.id)
        assert not any(
            path.name.startswith(".delete-")
            for path in (tmp_path / "artifacts").rglob("*")
        )


@pytest.mark.parametrize("operation", ["delete", "delete_session"])
@async_test
async def test_post_commit_cleanup_failure_keeps_committed_delete_for_recovery(
    tmp_path,
    monkeypatch,
    operation: str,
) -> None:
    async with _store(tmp_path) as store:
        record = await _put(store, data=b"delete")

        def fail_cleanup(_deletion) -> None:  # type: ignore[no-untyped-def]
            raise ArtifactError("artifact content delete failed")

        monkeypatch.setattr(store._blobs, "commit_delete", fail_cleanup)

        if operation == "delete":
            await store.delete("session-1", record.id)
        else:
            await store.delete_session("session-1")

        with pytest.raises(ArtifactNotFoundError):
            await store.get("session-1", record.id)
        assert any(
            path.name.startswith(".delete-")
            for path in (tmp_path / "artifacts").rglob("*")
        )

    async with _store(tmp_path) as restarted:
        assert (await restarted.list("session-1")).items == ()
        assert not any(
            path.name.startswith(".delete-")
            for path in (tmp_path / "artifacts").rglob("*")
        )


@async_test
async def test_pending_staging_blocks_artifact_id_reuse_until_recovery(
    tmp_path,
    monkeypatch,
) -> None:
    artifact_id = "art_00000000-0000-4000-8000-000000000001"
    async with _store(tmp_path, id_factory=lambda: artifact_id) as store:
        old_record = await _put(store, data=b"old")
        original_cleanup = store._blobs.commit_delete

        def fail_cleanup(_deletion) -> None:  # type: ignore[no-untyped-def]
            raise ArtifactError("artifact content delete failed")

        monkeypatch.setattr(store._blobs, "commit_delete", fail_cleanup)
        await store.delete("session-1", old_record.id)
        monkeypatch.setattr(store._blobs, "commit_delete", original_cleanup)

        with pytest.raises(ArtifactValidationError, match="artifact id collision"):
            await _put(store, data=b"NEW-CONTENT")

    async with _store(tmp_path, id_factory=lambda: artifact_id) as restarted:
        new_record = await _put(restarted, data=b"NEW-CONTENT")

        assert await restarted.get("session-1", new_record.id) == new_record
        assert await restarted.read("session-1", new_record.id) == b"NEW-CONTENT"
        assert not any(
            path.name.startswith(".delete-")
            for path in (tmp_path / "artifacts").rglob("*")
        )


@async_test
async def test_restart_preserves_staging_when_final_blob_is_not_a_regular_file(
    tmp_path,
) -> None:
    async with _store(tmp_path) as store:
        record = await _put(store, data=b"authoritative")
        deletion = store._blobs.stage_delete(record.id)
        assert deletion is not None
        blob_root = next(
            path for path in (tmp_path / "artifacts").rglob("blobs") if path.is_dir()
        )
        (blob_root / f"{record.id}.blob").mkdir()

    with pytest.raises(
        ArtifactError,
        match="^artifact content restore failed$",
    ) as error:
        async with _store(tmp_path):
            pass

    assert str(tmp_path) not in str(error.value)
    assert (blob_root / deletion.staged_name).read_bytes() == b"authoritative"


@async_test
async def test_recovery_waits_for_in_progress_delete_transaction(
    tmp_path,
    monkeypatch,
) -> None:
    async with _store(tmp_path) as deleting_store:
        record = await _put(deleting_store, data=b"delete")
        staged = threading.Event()
        allow_delete = threading.Event()
        original_stage = deleting_store._blobs.stage_delete

        def block_staged_delete(artifact_id: str):  # type: ignore[no-untyped-def]
            deletion = original_stage(artifact_id)
            staged.set()
            if not allow_delete.wait(timeout=5):
                raise AssertionError("delete release timed out")
            return deletion

        monkeypatch.setattr(
            deleting_store._blobs,
            "stage_delete",
            block_staged_delete,
        )
        delete_task = asyncio.create_task(
            deleting_store.delete("session-1", record.id)
        )
        assert await asyncio.to_thread(staged.wait, 5)
        recovery_task = asyncio.create_task(
            SqliteFilesystemArtifactStore.open(
                database_path=tmp_path / "state.db",
                artifact_root=tmp_path / "artifacts",
            )
        )
        await asyncio.sleep(0.05)
        assert not recovery_task.done()

        allow_delete.set()
        await asyncio.wait_for(delete_task, timeout=5)
        recovered = await asyncio.wait_for(recovery_task, timeout=5)
        await recovered.close()

        with sqlite3.connect(tmp_path / "state.db") as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM durable_artifacts WHERE artifact_id = ?",
                (record.id,),
            ).fetchone() == (0,)
        assert not any(
            path.name == f"{record.id}.blob"
            for path in (tmp_path / "artifacts").rglob("*")
        )
        assert not any(
            path.name.startswith(".delete-")
            for path in (tmp_path / "artifacts").rglob("*")
        )


@async_test
async def test_delete_database_failure_restores_blob_and_metadata(tmp_path) -> None:
    async with _store(tmp_path) as store:
        record = await _put(store, data=b"keep")
        with sqlite3.connect(tmp_path / "state.db") as connection:
            connection.execute(
                """
                CREATE TRIGGER reject_artifact_delete
                BEFORE DELETE ON durable_artifacts
                BEGIN
                  SELECT RAISE(ABORT, 'injected delete failure');
                END
                """
            )

        with pytest.raises(sqlite3.IntegrityError, match="injected delete failure"):
            await store.delete("session-1", record.id)

        assert await store.get("session-1", record.id) == record
        assert await store.read("session-1", record.id) == b"keep"


@async_test
async def test_delete_session_database_failure_restores_every_blob(tmp_path) -> None:
    async with _store(tmp_path) as store:
        first = await _put(store, data=b"first")
        second = await _put(store, data=b"second")
        with sqlite3.connect(tmp_path / "state.db") as connection:
            connection.execute(
                """
                CREATE TRIGGER reject_session_artifact_delete
                BEFORE DELETE ON durable_artifacts
                BEGIN
                  SELECT RAISE(ABORT, 'injected session delete failure');
                END
                """
            )

        with pytest.raises(
            sqlite3.IntegrityError,
            match="injected session delete failure",
        ):
            await store.delete_session("session-1")

        assert await store.read("session-1", first.id) == b"first"
        assert await store.read("session-1", second.id) == b"second"


@async_test
async def test_corrupted_metadata_raises_stable_error_without_value_echo(
    tmp_path,
) -> None:
    async with _store(tmp_path) as store:
        record = await _put(store)
        secret_path = str(tmp_path / "private" / "secret.txt")
        with sqlite3.connect(tmp_path / "state.db") as connection:
            connection.execute(
                "UPDATE durable_artifacts SET created_at = ? WHERE artifact_id = ?",
                (secret_path, record.id),
            )

        with pytest.raises(
            ArtifactMetadataCorruptedError,
            match="^artifact metadata corrupted$",
        ) as error:
            await store.get("session-1", record.id)
        assert secret_path not in str(error.value)


@async_test
async def test_two_store_id_collision_never_overwrites_committed_blob(
    tmp_path,
) -> None:
    artifact_id = "art_00000000-0000-4000-8000-000000000001"
    async with (
        _store(tmp_path, id_factory=lambda: artifact_id) as first,
        _store(tmp_path, id_factory=lambda: artifact_id) as second,
    ):
        async def upload(store, payload: bytes):
            try:
                record = await _put(store, data=payload)
                return record, payload
            except ArtifactValidationError:
                return None

        outcomes = await asyncio.gather(
            upload(first, b"first"),
            upload(second, b"second"),
        )

        succeeded = [item for item in outcomes if item is not None]
        assert len(succeeded) == 1
        record, payload = succeeded[0]
        assert await first.get("session-1", record.id) == record
        assert await first.read("session-1", record.id) == payload


@async_test
async def test_blob_symlink_cannot_read_outside_artifact_root(tmp_path) -> None:
    async with _store(tmp_path) as store:
        record = await _put(store)
        blob_path = next((tmp_path / "artifacts").rglob(f"{record.id}.blob"))
        outside = tmp_path / "outside-secret.txt"
        outside.write_bytes(b"outside-secret")
        blob_path.unlink()
        try:
            blob_path.symlink_to(outside)
        except OSError:
            pytest.skip("symlink creation is unavailable")

        with pytest.raises(ArtifactContentMissingError):
            await store.read("session-1", record.id)
