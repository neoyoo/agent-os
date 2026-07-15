from agentos.artifacts.in_memory import InMemoryArtifactStore
from agentos.artifacts.runtime import ArtifactRuntime
from agentos.artifacts.types import ArtifactPage, ArtifactRecord


class RecordingArtifactStore:
    def __init__(self) -> None:
        self.delegate = InMemoryArtifactStore()
        self.get_calls = 0
        self.read_calls = 0

    def put(
        self,
        *,
        session_id: str,
        data: bytes,
        filename: str | None,
        media_type: str,
    ) -> ArtifactRecord:
        return self.delegate.put(
            session_id=session_id,
            data=data,
            filename=filename,
            media_type=media_type,
        )

    def get(self, session_id: str, artifact_id: str) -> ArtifactRecord:
        self.get_calls += 1
        return self.delegate.get(session_id, artifact_id)

    def read(self, session_id: str, artifact_id: str) -> bytes:
        self.read_calls += 1
        return self.delegate.read(session_id, artifact_id)

    def list(
        self,
        session_id: str,
        cursor: str | None = None,
        limit: int = 20,
    ) -> ArtifactPage:
        return self.delegate.list(session_id, cursor, limit)

    def delete(self, session_id: str, artifact_id: str) -> None:
        self.delegate.delete(session_id, artifact_id)

    def delete_session(self, session_id: str) -> None:
        self.delegate.delete_session(session_id)


def test_resolve_mount_rereads_metadata_and_content_every_time() -> None:
    store = RecordingArtifactStore()
    runtime = ArtifactRuntime(session_id="session-1", store=store)
    record = runtime.upload(
        data=b"image",
        filename="drawing.png",
        media_type="image/png",
    )
    mount = runtime.mount_user_upload(record.id)
    baseline_gets = store.get_calls

    assert runtime.resolve_mount(mount) == (record, b"image")
    assert runtime.resolve_mount(mount) == (record, b"image")
    assert store.get_calls == baseline_gets + 2
    assert store.read_calls == 2
