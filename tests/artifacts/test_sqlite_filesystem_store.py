import os
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
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


def _store(tmp_path, *, clock=None, id_factory=None):
    return SqliteFilesystemArtifactStore(
        database_path=tmp_path / "state.db",
        artifact_root=tmp_path / "artifacts",
        clock=clock,
        id_factory=id_factory,
    )


def _put(store, *, session_id="session-1", data=b"drawing"):
    return store.put(
        session_id=session_id,
        data=data,
        filename="drawing.png",
        media_type="image/png",
    )


def test_restart_reads_metadata_content_and_stable_page(tmp_path) -> None:
    created_at = datetime(2026, 7, 17, 8, tzinfo=UTC)
    timestamps = iter((created_at, created_at + timedelta(seconds=1)))
    first_store = _store(tmp_path, clock=lambda: next(timestamps))
    first = _put(first_store, data=b"first")
    second = _put(first_store, data=b"second")

    restarted = _store(tmp_path)

    assert restarted.get("session-1", first.id) == first
    assert restarted.read("session-1", first.id) == b"first"
    assert restarted.list("session-1", limit=1).items == (second,)
    cursor = restarted.list("session-1", limit=1).next_cursor
    assert cursor is not None
    assert restarted.list("session-1", cursor=cursor).items == (first,)


def test_duplicate_content_gets_independent_ids_and_no_path_is_exposed(
    tmp_path,
) -> None:
    store = _store(tmp_path)
    payload = b"artifact-bytes-must-stay-outside-sqlite"

    first = _put(store, data=payload)
    second = _put(store, data=payload)

    assert first.id != second.id
    assert not hasattr(first, "blob_key")
    assert str(tmp_path) not in repr(first)
    with sqlite3.connect(tmp_path / "state.db") as connection:
        blob_keys = connection.execute(
            "SELECT blob_key FROM durable_artifacts ORDER BY artifact_id"
        ).fetchall()
    assert all(not str(tmp_path) in blob_key for (blob_key,) in blob_keys)
    database_bytes = (tmp_path / "state.db").read_bytes()
    assert payload not in database_bytes
    assert str(tmp_path).encode() not in database_bytes


def test_concurrent_puts_are_atomic_and_distinct(tmp_path) -> None:
    store = _store(tmp_path)
    barrier = threading.Barrier(8)

    def upload(index: int) -> str:
        barrier.wait()
        return _put(store, data=str(index).encode()).id

    with ThreadPoolExecutor(max_workers=8) as executor:
        artifact_ids = tuple(executor.map(upload, range(8)))

    assert len(set(artifact_ids)) == 8
    assert len(store.list("session-1", limit=100).items) == 8


@pytest.mark.parametrize("operation", ["get", "read", "delete"])
def test_unknown_and_cross_session_are_indistinguishable(
    tmp_path,
    operation: str,
) -> None:
    store = _store(tmp_path)
    record = _put(store, session_id="session-1")
    method = getattr(store, operation)
    unknown_id = "art_00000000-0000-4000-8000-000000000001"

    errors = []
    for artifact_id in (record.id, unknown_id):
        with pytest.raises(ArtifactNotFoundError) as error:
            method("session-2", artifact_id)
        errors.append(str(error.value))

    assert errors == ["artifact not found", "artifact not found"]


def test_missing_blob_fails_closed_without_exposing_storage_path(tmp_path) -> None:
    store = _store(tmp_path)
    record = _put(store)
    blob_path = next((tmp_path / "artifacts").rglob(f"{record.id}.blob"))
    blob_path.unlink()

    with pytest.raises(
        ArtifactContentMissingError,
        match="^artifact content missing$",
    ) as error:
        store.read("session-1", record.id)

    assert str(tmp_path) not in str(error.value)
    assert store.get("session-1", record.id) == record


def test_failed_metadata_insert_removes_temporary_and_final_blobs(tmp_path) -> None:
    artifact_id = "art_00000000-0000-4000-8000-000000000001"
    store = _store(tmp_path, id_factory=lambda: artifact_id)
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
        _put(store)

    blob_files = tuple((tmp_path / "artifacts").rglob("*"))
    assert all(not path.is_file() for path in blob_files)
    with sqlite3.connect(tmp_path / "state.db") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM durable_artifacts"
        ).fetchone() == (0,)


def test_write_os_error_is_stable_and_does_not_expose_storage_path(
    tmp_path,
    monkeypatch,
) -> None:
    store = _store(tmp_path)
    private_path = str(tmp_path / "artifacts" / "private")

    def fail_link(_source, _target) -> None:  # type: ignore[no-untyped-def]
        raise PermissionError(private_path)

    monkeypatch.setattr(os, "link", fail_link)

    with pytest.raises(
        ArtifactError,
        match="^artifact content write failed$",
    ) as error:
        _put(store)

    assert private_path not in str(error.value)
    assert store.list("session-1").items == ()
    assert all(
        not path.is_file()
        for path in (tmp_path / "artifacts").rglob("*")
    )


def test_delete_and_delete_session_remove_only_scoped_controlled_blobs(
    tmp_path,
) -> None:
    store = _store(tmp_path)
    one = _put(store, session_id="session-1", data=b"one")
    two = _put(store, session_id="session-1", data=b"two")
    other = _put(store, session_id="session-2", data=b"other")
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"keep")

    with sqlite3.connect(tmp_path / "state.db") as connection:
        connection.execute(
            "UPDATE durable_artifacts SET blob_key = ? WHERE artifact_id = ?",
            ("../outside.txt", one.id),
        )

    store.delete("session-1", one.id)
    store.delete_session("session-1")

    assert outside.read_bytes() == b"keep"
    assert store.list("session-1").items == ()
    assert store.read("session-2", other.id) == b"other"
    with pytest.raises(ArtifactNotFoundError):
        store.get("session-1", two.id)


def test_delete_session_is_idempotent_and_missing_blob_can_be_cleaned_up(
    tmp_path,
) -> None:
    store = _store(tmp_path)
    record = _put(store)
    next((tmp_path / "artifacts").rglob(f"{record.id}.blob")).unlink()

    store.delete_session("session-1")
    store.delete_session("session-1")

    assert store.list("session-1").items == ()


def test_delete_session_rejects_corrupted_id_before_touching_files(tmp_path) -> None:
    store = _store(tmp_path)
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
        store.delete_session("corrupted-session")

    assert outside.read_bytes() == b"keep"
    with sqlite3.connect(tmp_path / "state.db") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM durable_artifacts WHERE session_id = ?",
            ("corrupted-session",),
        ).fetchone() == (1,)


def test_delete_failure_keeps_metadata_for_safe_retry(tmp_path, monkeypatch) -> None:
    store = _store(tmp_path)
    record = _put(store)

    def fail_delete(_artifact_id: str) -> None:
        raise ArtifactError("artifact content delete failed")

    monkeypatch.setattr(store._blobs, "stage_delete", fail_delete)

    with pytest.raises(ArtifactError, match="content delete failed"):
        store.delete("session-1", record.id)

    assert store.get("session-1", record.id) == record


def test_restart_restores_staged_blob_when_metadata_is_still_live(tmp_path) -> None:
    store = _store(tmp_path)
    record = _put(store, data=b"recover")
    staged = store._blobs.stage_delete(record.id)
    assert staged is not None

    restarted = _store(tmp_path)

    assert restarted.read("session-1", record.id) == b"recover"


def test_restart_finishes_staged_blob_when_metadata_was_deleted(tmp_path) -> None:
    store = _store(tmp_path)
    record = _put(store, data=b"delete")
    staged = store._blobs.stage_delete(record.id)
    assert staged is not None
    with sqlite3.connect(tmp_path / "state.db") as connection:
        connection.execute(
            "DELETE FROM durable_artifacts WHERE artifact_id = ?",
            (record.id,),
        )

    restarted = _store(tmp_path)

    with pytest.raises(ArtifactNotFoundError):
        restarted.get("session-1", record.id)
    assert not any(
        path.name.startswith(".delete-")
        for path in (tmp_path / "artifacts").rglob("*")
    )


@pytest.mark.parametrize("operation", ["delete", "delete_session"])
def test_post_commit_cleanup_failure_keeps_committed_delete_for_recovery(
    tmp_path,
    monkeypatch,
    operation: str,
) -> None:
    store = _store(tmp_path)
    record = _put(store, data=b"delete")

    def fail_cleanup(_deletion) -> None:  # type: ignore[no-untyped-def]
        raise ArtifactError("artifact content delete failed")

    monkeypatch.setattr(store._blobs, "commit_delete", fail_cleanup)

    if operation == "delete":
        store.delete("session-1", record.id)
    else:
        store.delete_session("session-1")

    with pytest.raises(ArtifactNotFoundError):
        store.get("session-1", record.id)
    assert any(
        path.name.startswith(".delete-")
        for path in (tmp_path / "artifacts").rglob("*")
    )

    restarted = _store(tmp_path)

    assert restarted.list("session-1").items == ()
    assert not any(
        path.name.startswith(".delete-")
        for path in (tmp_path / "artifacts").rglob("*")
    )


def test_pending_staging_blocks_artifact_id_reuse_until_recovery(
    tmp_path,
    monkeypatch,
) -> None:
    artifact_id = "art_00000000-0000-4000-8000-000000000001"
    store = _store(tmp_path, id_factory=lambda: artifact_id)
    old_record = _put(store, data=b"old")
    original_cleanup = store._blobs.commit_delete

    def fail_cleanup(_deletion) -> None:  # type: ignore[no-untyped-def]
        raise ArtifactError("artifact content delete failed")

    monkeypatch.setattr(store._blobs, "commit_delete", fail_cleanup)
    store.delete("session-1", old_record.id)
    monkeypatch.setattr(store._blobs, "commit_delete", original_cleanup)

    with pytest.raises(ArtifactValidationError, match="artifact id collision"):
        _put(store, data=b"NEW-CONTENT")

    restarted = _store(tmp_path, id_factory=lambda: artifact_id)
    new_record = _put(restarted, data=b"NEW-CONTENT")

    assert restarted.get("session-1", new_record.id) == new_record
    assert restarted.read("session-1", new_record.id) == b"NEW-CONTENT"
    assert not any(
        path.name.startswith(".delete-")
        for path in (tmp_path / "artifacts").rglob("*")
    )


def test_restart_preserves_staging_when_final_blob_is_not_a_regular_file(
    tmp_path,
) -> None:
    store = _store(tmp_path)
    record = _put(store, data=b"authoritative")
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
        _store(tmp_path)

    assert str(tmp_path) not in str(error.value)
    assert (blob_root / deletion.staged_name).read_bytes() == b"authoritative"


def test_recovery_reserves_sqlite_writer_before_scanning_staged_deletes(
    tmp_path,
    monkeypatch,
) -> None:
    deleting_store = _store(tmp_path)
    recovering_store = _store(tmp_path)
    record = _put(deleting_store, data=b"delete")
    staged = threading.Event()
    allow_delete = threading.Event()
    database_attempted = threading.Event()
    recovery_finished = threading.Event()
    first_statement: list[str] = []
    errors: list[BaseException] = []
    original_stage = deleting_store._blobs.stage_delete

    def block_staged_delete(artifact_id: str):  # type: ignore[no-untyped-def]
        deletion = original_stage(artifact_id)
        staged.set()
        if not allow_delete.wait(timeout=5):
            raise AssertionError("delete release timed out")
        return deletion

    @contextmanager
    def traced_connection():  # type: ignore[no-untyped-def]
        connection = sqlite3.connect(tmp_path / "state.db", timeout=5)

        def trace(statement: str) -> None:
            normalized = " ".join(statement.upper().split())
            if not first_statement and (
                normalized.startswith("BEGIN IMMEDIATE")
                or normalized.startswith("SELECT")
            ):
                first_statement.append(normalized)
                database_attempted.set()

        connection.set_trace_callback(trace)
        try:
            yield connection
        finally:
            connection.close()

    monkeypatch.setattr(deleting_store._blobs, "stage_delete", block_staged_delete)
    monkeypatch.setattr(recovering_store, "_connect", traced_connection)

    def delete() -> None:
        try:
            deleting_store.delete("session-1", record.id)
        except BaseException as error:
            errors.append(error)

    def recover() -> None:
        try:
            recovering_store._recover_staged_deletes()
        except BaseException as error:
            errors.append(error)
        finally:
            recovery_finished.set()

    delete_thread = threading.Thread(target=delete)
    recovery_thread = threading.Thread(target=recover)
    delete_thread.start()
    assert staged.wait(timeout=5)
    recovery_thread.start()
    assert database_attempted.wait(timeout=5)
    if first_statement[0].startswith("SELECT"):
        assert recovery_finished.wait(timeout=5)
    allow_delete.set()
    delete_thread.join(timeout=5)
    recovery_thread.join(timeout=5)

    assert not delete_thread.is_alive()
    assert not recovery_thread.is_alive()
    assert errors == []
    assert first_statement[0].startswith("BEGIN IMMEDIATE")
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


def test_delete_database_failure_restores_blob_and_metadata(tmp_path) -> None:
    store = _store(tmp_path)
    record = _put(store, data=b"keep")
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
        store.delete("session-1", record.id)

    assert store.get("session-1", record.id) == record
    assert store.read("session-1", record.id) == b"keep"


def test_delete_session_database_failure_restores_every_blob(tmp_path) -> None:
    store = _store(tmp_path)
    first = _put(store, data=b"first")
    second = _put(store, data=b"second")
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
        store.delete_session("session-1")

    assert store.read("session-1", first.id) == b"first"
    assert store.read("session-1", second.id) == b"second"


def test_corrupted_metadata_raises_stable_error_without_value_echo(tmp_path) -> None:
    store = _store(tmp_path)
    record = _put(store)
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
        store.get("session-1", record.id)
    assert secret_path not in str(error.value)


def test_two_store_id_collision_never_overwrites_committed_blob(tmp_path) -> None:
    artifact_id = "art_00000000-0000-4000-8000-000000000001"
    first = _store(tmp_path, id_factory=lambda: artifact_id)
    second = _store(tmp_path, id_factory=lambda: artifact_id)
    barrier = threading.Barrier(2)

    def upload(store, payload: bytes):
        barrier.wait()
        try:
            record = _put(store, data=payload)
            return record, payload
        except ArtifactValidationError:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(
            executor.map(
                lambda item: upload(*item),
                ((first, b"first"), (second, b"second")),
            )
        )

    succeeded = [item for item in outcomes if item is not None]
    assert len(succeeded) == 1
    record, payload = succeeded[0]
    assert first.get("session-1", record.id) == record
    assert first.read("session-1", record.id) == payload


def test_blob_symlink_cannot_read_outside_artifact_root(tmp_path) -> None:
    store = _store(tmp_path)
    record = _put(store)
    blob_path = next((tmp_path / "artifacts").rglob(f"{record.id}.blob"))
    outside = tmp_path / "outside-secret.txt"
    outside.write_bytes(b"outside-secret")
    blob_path.unlink()
    try:
        blob_path.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with pytest.raises(ArtifactContentMissingError):
        store.read("session-1", record.id)
