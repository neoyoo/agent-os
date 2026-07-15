"""session snapshot 持久化边界。"""

from agentos.persistence.base import (
    SNAPSHOT_VERSION,
    SessionPersistence,
    SessionSnapshot,
    SessionSnapshotRecord,
    SnapshotConflictError,
    SnapshotLoadError,
    SnapshotVersionError,
)
from agentos.persistence.filesystem import FileSystemPersistence
from agentos.persistence.memory import MemoryPersistence
from agentos.persistence.in_memory_session import (
    InMemoryDurableSessionStore,
    InMemoryHotSessionStore,
)
from agentos.persistence.postgres import (
    BackendUnavailableError,
    PostgresDurableSessionStore,
    PostgresSessionSnapshotPersistence,
)
from agentos.persistence.protocols import PostgresConnection, PostgresCursor
from agentos.persistence.redis_session import RedisHotSessionStore
from agentos.persistence.session_store import (
    DurableSessionStore,
    HotSessionState,
    HotSessionStore,
)
from agentos.persistence.sqlite import SQLitePersistence

__all__ = [
    "FileSystemPersistence",
    "DurableSessionStore",
    "HotSessionState",
    "HotSessionStore",
    "InMemoryDurableSessionStore",
    "InMemoryHotSessionStore",
    "MemoryPersistence",
    "PostgresConnection",
    "PostgresCursor",
    "PostgresDurableSessionStore",
    "PostgresSessionSnapshotPersistence",
    "RedisHotSessionStore",
    "BackendUnavailableError",
    "SNAPSHOT_VERSION",
    "SessionPersistence",
    "SessionSnapshot",
    "SessionSnapshotRecord",
    "SQLitePersistence",
    "SnapshotConflictError",
    "SnapshotLoadError",
    "SnapshotVersionError",
]
