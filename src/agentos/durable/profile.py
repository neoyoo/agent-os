from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Self
from weakref import WeakValueDictionary

from agentos._builder_durable import build_durable_agent, validate_durable_builder
from agentos.artifacts.sqlite_filesystem import SqliteFilesystemArtifactStore
from agentos.capabilities.skill_activation import SQLiteSkillActivationStore
from agentos.durable.sqlite_store import SQLiteDurableStore
from agentos.memory.sqlite import SQLiteMemoryStore
from agentos.planning.sqlite import SQLitePlanStore
from agentos.runtime._async_bridge import _await_cleanup_preserving_cancellation
from agentos.runtime.agent import Agent
from agentos.runtime.durable_runtime import DurableCommandRuntime
from agentos.runtime.errors import DurableStoreClosedError
from agentos.runtime.payloads import PayloadProtector

if TYPE_CHECKING:
    from agentos.builder import AgentBuilder
    from agentos.runtime._execution_lease import ExecutionLease
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
        payload_protector: PayloadProtector | None = None,
    ) -> None:
        validate_durable_builder(agent_builder)
        self._builder = agent_builder
        self._database_path = Path(database_path)
        self._artifact_root = Path(artifact_root)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._payload_protector = payload_protector
        self._lifecycle_lock = asyncio.Lock()
        self._query_loops: WeakValueDictionary[str, QueryLoop] = WeakValueDictionary()
        self._close_reservations: list[tuple[ExecutionLease, object]] = []
        self._state = "new"
        self._store: SQLiteDurableStore | None = None
        self._plan_store: SQLitePlanStore | None = None
        self._memory_store: SQLiteMemoryStore | None = None
        self._skill_activation_store: SQLiteSkillActivationStore | None = None
        self._artifact_store: SqliteFilesystemArtifactStore | None = None

    @property
    def is_open(self) -> bool:
        return self._state == "open"

    async def open(self) -> Self:
        """异步初始化 Profile 持有的全部 Durable 资源。"""

        async with self._lifecycle_lock:
            if self._state == "open":
                return self
            if self._state != "new":
                raise DurableStoreClosedError("durable profile is closed")
            self._state = "opening"
            try:
                self._store = await SQLiteDurableStore.open(
                    self._database_path,
                    clock=self._clock,
                )
                self._plan_store = await SQLitePlanStore.open(self._database_path)
                self._memory_store = await SQLiteMemoryStore.open(
                    self._database_path,
                )
                self._skill_activation_store = await SQLiteSkillActivationStore.open(
                    self._database_path,
                )
                self._artifact_store = await SqliteFilesystemArtifactStore.open(
                    database_path=self._database_path,
                    artifact_root=self._artifact_root,
                    clock=self._clock,
                )
            except BaseException:
                try:
                    await _await_cleanup_preserving_cancellation(
                        self._close_owned_stores,
                    )
                except BaseException:
                    self._state = "close_failed"
                    raise
                else:
                    self._state = "new"
                raise
            self._state = "open"
            return self

    async def __aenter__(self) -> Self:
        return await self.open()

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    async def close(self) -> None:
        """先阻止新执行，再幂等关闭 Profile 持有的 Durable 资源。"""

        async with self._lifecycle_lock:
            if self._state == "closed":
                return
            if self._state == "new":
                self._state = "closed"
                return
            if self._state not in {"open", "close_failed"}:
                raise DurableStoreClosedError("durable profile lifecycle is busy")

            if self._state == "open":
                reservations: list[tuple[ExecutionLease, object]] = []
                try:
                    for query_loop in tuple(self._query_loops.values()):
                        lease = query_loop._execution_lease
                        reservations.append((lease, lease.reserve_close()))
                except BaseException:
                    for lease, reservation in reservations:
                        lease.cancel_close_reservation(reservation)
                    raise
                self._close_reservations = reservations

            self._state = "closing"
            try:
                await _await_cleanup_preserving_cancellation(self._close_owned_stores)
            except BaseException:
                self._state = "close_failed"
                raise
            else:
                self._query_loops.clear()
                self._state = "closed"

    @property
    def plan_store(self) -> SQLitePlanStore:
        """返回由 Profile 管理的 SQLite Plan Store。"""

        self._ensure_open()
        assert self._plan_store is not None
        return self._plan_store

    @property
    def memory_store(self) -> SQLiteMemoryStore:
        """返回由 Profile 管理的 SQLite Memory Store。"""

        self._ensure_open()
        assert self._memory_store is not None
        return self._memory_store

    @property
    def skill_activation_store(self) -> SQLiteSkillActivationStore:
        """返回由 Profile 管理的 SQLite Skill Activation Store。"""

        self._ensure_open()
        assert self._skill_activation_store is not None
        return self._skill_activation_store

    async def build_agent(self, session_id: str | None = None) -> Agent:
        """为指定 Session 水合或创建标准 Agent。"""

        async with self._lifecycle_lock:
            self._ensure_open()
            if session_id is None or not session_id.strip():
                raise ValueError("durable profile requires a non-empty session_id")
            assert self._store is not None
            assert self._artifact_store is not None
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
                payload_protector=self._payload_protector,
            )
            self._query_loops[session_id] = agent.query_loop
            return agent

    async def _close_owned_stores(self) -> None:
        first_error: BaseException | None = None
        stores = (
            "_artifact_store",
            "_skill_activation_store",
            "_memory_store",
            "_plan_store",
            "_store",
        )
        for attribute in stores:
            store = getattr(self, attribute)
            if store is None:
                continue
            try:
                await store.close()
            except BaseException as error:
                first_error = first_error or error
            else:
                setattr(self, attribute, None)
        if first_error is not None:
            raise first_error

    def _ensure_open(self) -> None:
        if self._state != "open":
            raise DurableStoreClosedError("durable profile is not open")


__all__ = ["DurableRuntimeProfile"]
