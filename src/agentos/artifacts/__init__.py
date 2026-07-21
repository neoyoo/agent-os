"""Session-scoped Artifact domain and Level 1 runtime."""

from typing import TYPE_CHECKING

from agentos.artifacts.in_memory import InMemoryArtifactStore
from agentos.artifacts.runtime import ArtifactPolicy, ArtifactRuntime
from agentos.artifacts.store import ArtifactStore
from agentos.artifacts.types import (
    ArtifactError,
    ArtifactMediaTypeUnsupportedError,
    ArtifactNotFoundError,
    ArtifactRecord,
    ArtifactRef,
    ArtifactTooLargeError,
    ArtifactValidationError,
)

if TYPE_CHECKING:
    from agentos.artifacts.sqlite_filesystem import SqliteFilesystemArtifactStore


def __getattr__(name: str) -> object:
    """惰性导出 Durable Adapter，避免 Kernel import 加载基础设施实现。"""

    if name == "SqliteFilesystemArtifactStore":
        from agentos.artifacts.sqlite_filesystem import (
            SqliteFilesystemArtifactStore,
        )

        return SqliteFilesystemArtifactStore
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "ArtifactError",
    "ArtifactMediaTypeUnsupportedError",
    "ArtifactNotFoundError",
    "ArtifactPolicy",
    "ArtifactRecord",
    "ArtifactRef",
    "ArtifactTooLargeError",
    "ArtifactRuntime",
    "ArtifactStore",
    "ArtifactValidationError",
    "InMemoryArtifactStore",
    "SqliteFilesystemArtifactStore",
]
