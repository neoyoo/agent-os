from pathlib import Path

import pytest

from agentos.compression import CompressionIndex
from agentos.context import ContextState
from agentos.messages import MessageRuntime
from agentos.persistence import (
    FileSystemPersistence,
    SessionSnapshot,
    SnapshotLoadError,
    SnapshotVersionError,
)
from agentos.runtime import SessionState


def make_snapshot(session_id: str = "session_1") -> SessionSnapshot:
    messages = MessageRuntime()
    messages.append_user("hello")
    return SessionSnapshot(
        session_state=SessionState(id=session_id),
        context_state=ContextState(working_state={"task_goal": "Persist."}),
        message_runtime=messages,
        compression_index=CompressionIndex(),
    )


def test_file_system_persistence_round_trips_snapshot(tmp_path: Path) -> None:
    store = FileSystemPersistence(tmp_path)
    store.save(make_snapshot())

    restored = store.load("session_1")

    assert restored.session_state.id == "session_1"
    assert restored.message_runtime.store.get("msg_1").content == "hello"
    assert store.list_ids() == ["session_1"]


def test_file_system_persistence_rejects_path_traversal(tmp_path: Path) -> None:
    store = FileSystemPersistence(tmp_path)

    with pytest.raises(ValueError, match="invalid session id"):
        store.load("../outside")


def test_file_system_persistence_missing_session_raises_key_error(
    tmp_path: Path,
) -> None:
    store = FileSystemPersistence(tmp_path)

    with pytest.raises(KeyError):
        store.load("missing")


def test_file_system_persistence_corrupt_json_raises_snapshot_load_error(
    tmp_path: Path,
) -> None:
    store = FileSystemPersistence(tmp_path)
    (tmp_path / "session_1.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(SnapshotLoadError, match="session_1"):
        store.load("session_1")


def test_file_system_persistence_version_mismatch_raises_version_error(
    tmp_path: Path,
) -> None:
    store = FileSystemPersistence(tmp_path)
    store.save(make_snapshot())
    path = tmp_path / "session_1.json"
    payload = path.read_text(encoding="utf-8").replace('"version": 1', '"version": 999')
    path.write_text(payload, encoding="utf-8")

    with pytest.raises(SnapshotVersionError, match="999"):
        store.load("session_1")
