from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING
from weakref import WeakValueDictionary

from agentos._builder_durable import build_durable_agent, validate_durable_builder
from agentos.artifacts.sqlite_filesystem import SqliteFilesystemArtifactStore
from agentos.capabilities.skill_activation import SQLiteSkillActivationStore
from agentos.durable.sqlite_store import SQLiteDurableStore
from agentos.memory.sqlite import SQLiteMemoryStore
from agentos.planning.sqlite import SQLitePlanStore
from agentos.runtime.agent import Agent
from agentos.runtime.durable_runtime import DurableCommandRuntime
from agentos.runtime.errors import DurableStoreClosedError

if TYPE_CHECKING:
    from agentos.builder import AgentBuilder
    from agentos.runtime.query_loop import QueryLoop


class DurableRuntimeProfile:
    """组合单机 SQLite/Filesystem Durable Runtime，不执行 Provider Loop。"""

    name = "durable"

    def __init__(
        self,
        *,
        agent_builder: AgentBuilder,
        database_path: str | Path,
        artifact_root: str | Path,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        validate_durable_builder(agent_builder)
        self._builder = agent_builder
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = RLock()
        self._query_loops: WeakValueDictionary[str, QueryLoop] = (
            WeakValueDictionary()
        )
        self._closed = False
        database = Path(database_path)
        database.parent.mkdir(parents=True, exist_ok=True)
        self._store = SQLiteDurableStore(database, clock=self._clock)
        try:
            self._plan_store = SQLitePlanStore(database)
            self._memory_store = SQLiteMemoryStore(database)
            self._skill_activation_store = SQLiteSkillActivationStore(database)
            self._artifact_store = SqliteFilesystemArtifactStore(
                database_path=database,
                artifact_root=artifact_root,
                clock=self._clock,
            )
        except BaseException:
            if hasattr(self, "_artifact_store"):
                self._artifact_store.close()
            if hasattr(self, "_skill_activation_store"):
                self._skill_activation_store.close()
            if hasattr(self, "_memory_store"):
                self._memory_store.close()
            if hasattr(self, "_plan_store"):
                self._plan_store.close()
            self._store.close()
            raise

    def __enter__(self) -> DurableRuntimeProfile:
        self._ensure_open()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        """幂等关闭 Profile 持有的 Durable connection。"""

        with self._lock:
            if self._closed:
                return
            reservations = []
            try:
                for query_loop in self._query_loops.values():
                    lease = query_loop._execution_lease
                    reservations.append((lease, lease.reserve_close()))
            except BaseException:
                for lease, reservation in reservations:
                    lease.cancel_close_reservation(reservation)
                raise
            try:
                self._closed = True
                self._query_loops.clear()
                try:
                    self._artifact_store.close()
                finally:
                    try:
                        self._skill_activation_store.close()
                    finally:
                        try:
                            self._memory_store.close()
                        finally:
                            try:
                                self._plan_store.close()
                            finally:
                                self._store.close()
            finally:
                for lease, reservation in reservations:
                    lease.cancel_close_reservation(reservation)

    @property
    def plan_store(self) -> SQLitePlanStore:
        """返回由 Profile 管理的 SQLite Plan Store。"""

        self._ensure_open()
        return self._plan_store

    @property
    def memory_store(self) -> SQLiteMemoryStore:
        """返回由 Profile 管理的 SQLite Memory Store。"""

        self._ensure_open()
        return self._memory_store

    @property
    def skill_activation_store(self) -> SQLiteSkillActivationStore:
        """返回由 Profile 管理的 SQLite Skill Activation Store。"""

        self._ensure_open()
        return self._skill_activation_store

    async def build_agent(self, session_id: str | None = None) -> Agent:
        """为指定 Session 水合或创建标准 Agent。"""

        with self._lock:
            self._ensure_open()
            if session_id is None or not session_id.strip():
                raise ValueError("durable profile requires a non-empty session_id")
            query_loop = self._query_loops.get(session_id)
            if query_loop is not None:
                return Agent(
                    query_loop=query_loop,
                    durable_command_runtime=DurableCommandRuntime(
                        session_id,
                        self._store,
                        self._clock,
                    ),
                )
            agent = await build_durable_agent(
                builder=self._builder,
                store=self._store,
                artifact_store=self._artifact_store,
                session_id=session_id,
                clock=self._clock,
            )
            self._query_loops[session_id] = agent.query_loop
            return agent

    def _ensure_open(self) -> None:
        if self._closed:
            raise DurableStoreClosedError("durable profile is closed")


__all__ = ["DurableRuntimeProfile"]
