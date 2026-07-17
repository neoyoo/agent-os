from __future__ import annotations

from collections.abc import Callable
from threading import RLock
from typing import Protocol
from uuid import uuid4

from agentos._waiting import WaitReason
from agentos.runtime.run_state import (
    RunAlreadyExistsError,
    RunNotFoundError,
    RunState,
    RunStatus,
)


class RunStore(Protocol):
    """RunRuntime 使用的最小权威状态 Store 边界。"""

    def create(self, state: RunState) -> RunState: ...

    def get(self, *, session_id: str, run_id: str) -> RunState | None: ...

    def transition(
        self,
        *,
        session_id: str,
        run_id: str,
        status: RunStatus,
        wait_reason: WaitReason | None = None,
    ) -> RunState: ...


class InMemoryRunStore:
    """按 Session 隔离并原子更新 RunState 的进程内 Store。"""

    def __init__(self) -> None:
        self._states: dict[tuple[str, str], RunState] = {}
        self._lock = RLock()

    def create(self, state: RunState) -> RunState:
        key = (state.session_id, state.run_id)
        with self._lock:
            if key in self._states:
                raise RunAlreadyExistsError(
                    f"run already exists: {state.run_id}",
                )
            self._states[key] = state
            return state

    def get(self, *, session_id: str, run_id: str) -> RunState | None:
        with self._lock:
            return self._states.get((session_id, run_id))

    def transition(
        self,
        *,
        session_id: str,
        run_id: str,
        status: RunStatus,
        wait_reason: WaitReason | None = None,
    ) -> RunState:
        key = (session_id, run_id)
        with self._lock:
            current = self._states.get(key)
            if current is None:
                raise RunNotFoundError(f"run not found: {run_id}")
            updated = current.transition(status, wait_reason=wait_reason)
            self._states[key] = updated
            return updated


class RunRuntime:
    """协调单个 Session 内 Run 的进程内生命周期。"""

    def __init__(
        self,
        *,
        session_id: str,
        store: RunStore,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        if not session_id.strip():
            raise ValueError("session_id must not be empty")
        self._session_id = session_id
        self._store = store
        self._id_factory = id_factory or _new_run_id

    @property
    def session_id(self) -> str:
        """返回该 Runtime 绑定的 Session ID。"""

        return self._session_id

    def create_run(self, *, run_id: str | None = None) -> RunState:
        """创建一个 CREATED Run。"""

        state = RunState(
            run_id=self._id_factory() if run_id is None else run_id,
            session_id=self._session_id,
        )
        return self._store.create(state)

    def get_run(self, run_id: str) -> RunState:
        """读取当前 Session 的 Run，不存在时 fail-closed。"""

        state = self._store.get(session_id=self._session_id, run_id=run_id)
        if state is None:
            raise RunNotFoundError(f"run not found: {run_id}")
        return state

    def queue(self, run_id: str) -> RunState:
        """执行 CREATED/WAITING -> QUEUED。"""

        return self._transition(run_id, RunStatus.QUEUED)

    def start(self, run_id: str) -> RunState:
        """执行 QUEUED -> RUNNING。"""

        return self._transition(run_id, RunStatus.RUNNING)

    def complete(self, run_id: str) -> RunState:
        """执行 RUNNING -> COMPLETED。"""

        return self._transition(run_id, RunStatus.COMPLETED)

    def fail(self, run_id: str) -> RunState:
        """执行 RUNNING -> FAILED。"""

        return self._transition(run_id, RunStatus.FAILED)

    def cancel(self, run_id: str) -> RunState:
        """把任意非终态 Run 转为 CANCELLED。"""

        return self._transition(run_id, RunStatus.CANCELLED)

    def wait(self, run_id: str, *, reason: WaitReason) -> RunState:
        """执行 RUNNING -> WAITING 并保存类型化原因。"""

        return self._transition(
            run_id,
            RunStatus.WAITING,
            wait_reason=reason,
        )

    def _transition(
        self,
        run_id: str,
        status: RunStatus,
        *,
        wait_reason: WaitReason | None = None,
    ) -> RunState:
        return self._store.transition(
            session_id=self._session_id,
            run_id=run_id,
            status=status,
            wait_reason=wait_reason,
        )


def _new_run_id() -> str:
    return f"run_{uuid4().hex}"
