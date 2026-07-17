from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from agentos.artifacts.types import (
    ArtifactError,
    ArtifactValidationError,
    validate_artifact_id,
)


class ArtifactContentMissingError(ArtifactError):
    """Artifact metadata 存在，但受控内容文件不可读取。"""

    def __init__(self) -> None:
        super().__init__("artifact content missing")


@dataclass(frozen=True, slots=True)
class ArtifactBlobDelete:
    """一次尚未由 SQLite metadata 提交确认的 blob 删除。"""

    artifact_id: str
    staged_name: str


class FilesystemArtifactBlobs:
    """在受控根目录内独占写入、读取和删除 Artifact bytes。"""

    def __init__(self, artifact_root: str | Path) -> None:
        try:
            root = Path(artifact_root).resolve()
            root.mkdir(parents=True, exist_ok=True)
            candidate = root / "blobs"
            candidate.mkdir(parents=True, exist_ok=True)
            if _is_link_or_junction(candidate):
                raise ArtifactValidationError("artifact blob root is unsafe")
            resolved = candidate.resolve(strict=True)
            if not resolved.is_relative_to(root):
                raise ArtifactValidationError("artifact blob root is unsafe")
            self._root = resolved
        except OSError:
            raise ArtifactError("artifact blob storage is unavailable") from None

    @staticmethod
    def key(artifact_id: str) -> str:
        return f"blobs/{artifact_id}.blob"

    def validate_key(self, artifact_id: str, blob_key: object) -> None:
        if blob_key != self.key(artifact_id):
            raise ArtifactContentMissingError()

    def exists(self, artifact_id: str) -> bool:
        self._ensure_root()
        return os.path.lexists(self._path(artifact_id))

    def write_exclusive(self, artifact_id: str, data: bytes) -> None:
        """通过同目录 hard link 把临时文件提升为不可覆盖的最终文件。"""

        self._ensure_root()
        final_path = self._path(artifact_id)
        temporary_path = self._root / f".tmp-{uuid4().hex}"
        try:
            with temporary_path.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError:
            self._discard(temporary_path)
            raise ArtifactError("artifact content write failed") from None
        try:
            os.link(temporary_path, final_path)
        except FileExistsError:
            self._discard(temporary_path)
            raise ArtifactValidationError("artifact id collision") from None
        except OSError:
            self._discard(temporary_path)
            raise ArtifactError("artifact content write failed") from None
        try:
            temporary_path.unlink()
        except OSError:
            self._discard(final_path)
            self._discard(temporary_path)
            raise ArtifactError("artifact content write failed") from None

    def read(self, artifact_id: str, blob_key: object) -> bytes:
        self.validate_key(artifact_id, blob_key)
        self._ensure_root()
        path = self._path(artifact_id)
        try:
            if _is_link_or_junction(path):
                raise OSError
            metadata = path.lstat()
            if not stat.S_ISREG(metadata.st_mode) or path.resolve(strict=True) != path:
                raise OSError
            return path.read_bytes()
        except OSError:
            raise ArtifactContentMissingError() from None

    def delete(self, artifact_id: str) -> None:
        """删除受控文件；缺失文件视为已完成。"""

        try:
            self._ensure_root()
            self._path(artifact_id).unlink(missing_ok=True)
        except OSError:
            raise ArtifactError("artifact content delete failed") from None

    def stage_delete(self, artifact_id: str) -> ArtifactBlobDelete | None:
        """把 blob 原子移入同目录 staging，等待 metadata 提交。"""

        self._ensure_root()
        source = self._path(artifact_id)
        if not os.path.lexists(source):
            return None
        staged_name = f".delete-{artifact_id}--{uuid4().hex}"
        try:
            os.replace(source, self._root / staged_name)
        except OSError:
            raise ArtifactError("artifact content delete failed") from None
        return ArtifactBlobDelete(artifact_id, staged_name)

    def restore_delete(self, deletion: ArtifactBlobDelete) -> None:
        """在 metadata 回滚后把 staging blob 恢复为正式内容。"""

        staged_path = self._staged_path(deletion)
        final_path = self._path(deletion.artifact_id)
        try:
            os.link(staged_path, final_path)
        except FileExistsError:
            try:
                if _is_link_or_junction(final_path):
                    raise OSError
                metadata = final_path.lstat()
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or final_path.resolve(strict=True) != final_path
                ):
                    raise OSError
                self.commit_delete(deletion)
            except (ArtifactError, OSError):
                raise ArtifactError("artifact content restore failed") from None
            return
        except OSError:
            raise ArtifactError("artifact content restore failed") from None
        try:
            staged_path.unlink()
        except OSError:
            raise ArtifactError("artifact content restore failed") from None

    def commit_delete(self, deletion: ArtifactBlobDelete) -> None:
        """在 metadata 提交后永久清理 staging blob。"""

        try:
            self._staged_path(deletion).unlink(missing_ok=True)
        except OSError:
            raise ArtifactError("artifact content delete failed") from None

    def pending_deletes(self) -> tuple[ArtifactBlobDelete, ...]:
        """返回受控根目录中可严格解析的遗留 staging 删除。"""

        self._ensure_root()
        try:
            names = sorted(path.name for path in self._root.iterdir())
        except OSError:
            raise ArtifactError("artifact blob root is unavailable") from None
        pending = tuple(
            deletion
            for name in names
            if (deletion := _parse_delete_name(name)) is not None
        )
        return pending

    def _path(self, artifact_id: str) -> Path:
        return self._root / f"{artifact_id}.blob"

    def _staged_path(self, deletion: ArtifactBlobDelete) -> Path:
        parsed = _parse_delete_name(deletion.staged_name)
        if parsed != deletion:
            raise ArtifactError("artifact staged delete is invalid")
        return self._root / deletion.staged_name

    @staticmethod
    def _discard(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    def _ensure_root(self) -> None:
        try:
            if _is_link_or_junction(self._root):
                raise OSError
            if self._root.resolve(strict=True) != self._root:
                raise OSError
        except OSError:
            raise ArtifactError("artifact blob root is unavailable") from None


def _is_link_or_junction(path: Path) -> bool:
    return path.is_symlink() or (
        hasattr(path, "is_junction") and path.is_junction()
    )


def _parse_delete_name(name: str) -> ArtifactBlobDelete | None:
    if not name.startswith(".delete-"):
        return None
    artifact_id, separator, nonce = name[8:].rpartition("--")
    if separator != "--" or len(nonce) != 32:
        return None
    try:
        int(nonce, 16)
        validate_artifact_id(artifact_id)
    except (ArtifactValidationError, ValueError):
        return None
    return ArtifactBlobDelete(artifact_id, name)


__all__ = [
    "ArtifactBlobDelete",
    "ArtifactContentMissingError",
    "FilesystemArtifactBlobs",
]
