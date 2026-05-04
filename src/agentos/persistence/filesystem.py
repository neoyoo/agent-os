import json
from json import JSONDecodeError
from pathlib import Path

from agentos.persistence.base import (
    SessionSnapshot,
    SnapshotLoadError,
    SnapshotVersionError,
)
from agentos.persistence.serializers import (
    session_snapshot_from_dict,
    session_snapshot_to_dict,
)


class FileSystemPersistence:
    """以 JSON 文件保存 session snapshot。"""

    def __init__(self, base_dir: Path) -> None:
        """创建文件存储目录。"""

        self._base_dir = Path(base_dir).resolve()
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def save(self, snapshot: SessionSnapshot) -> None:
        """保存一个 session snapshot。"""

        path = self._path(snapshot.session_state.id)
        temp_path = path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(
                session_snapshot_to_dict(snapshot),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temp_path.replace(path)

    def load(self, session_id: str) -> SessionSnapshot:
        """读取一个 session snapshot。"""

        path = self._path(session_id)
        if not path.exists():
            raise KeyError(session_id)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return session_snapshot_from_dict(payload)
        except SnapshotVersionError:
            raise
        except (JSONDecodeError, KeyError, TypeError, ValueError) as error:
            raise SnapshotLoadError(
                f"failed to load snapshot {session_id!r}: {error}",
            ) from error

    def list_ids(self) -> list[str]:
        """列出已保存的 session ids。"""

        return sorted(path.stem for path in self._base_dir.glob("*.json"))

    def delete(self, session_id: str) -> None:
        """删除一个 session snapshot。"""

        self._path(session_id).unlink(missing_ok=True)

    def _path(self, session_id: str) -> Path:
        """返回 session 文件路径，并阻止路径逃逸。"""

        path = (self._base_dir / f"{session_id}.json").resolve()
        if not path.is_relative_to(self._base_dir):
            raise ValueError(f"invalid session id: {session_id!r}")
        return path
