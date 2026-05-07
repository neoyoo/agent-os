from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Condition, RLock
from typing import Protocol

from agentos.events import AgentContinuationFailedEvent, EventBus
from agentos.runtime import Agent


class ContinuationTrigger(Protocol):
    """multi-agent 任务终态后的 parent continuation 触发边界。"""

    def on_task_completed(self, parent_agent_id: str, task_id: str) -> None:
        """通知 parent agent 有任务进入终态。"""


class AgentTaskNoticeProvider:
    """绑定单个 parent agent 的一次性 task notice provider。"""

    def __init__(self, store: "AgentTaskNoticeStore", agent_id: str) -> None:
        """绑定 notice store 和 parent agent id。"""

        self._store = store
        self._agent_id = agent_id

    def consume_notices(self) -> tuple[str, ...]:
        """返回并消费当前 parent agent 的 runtime notices。"""

        return self._store.consume_notices(self._agent_id)


class AgentTaskNoticeStore:
    """保存 parent agent 待注入 continuation turn 的 task notices。"""

    def __init__(self) -> None:
        """创建空 notice store。"""

        self._notices: dict[str, deque[str]] = defaultdict(deque)
        self._lock = RLock()

    def provider_for(self, agent_id: str) -> AgentTaskNoticeProvider:
        """返回绑定 parent agent 的 notice provider。"""

        return AgentTaskNoticeProvider(self, agent_id)

    def add_task_completed(self, agent_id: str, task_id: str) -> None:
        """记录一个 task terminal notice。"""

        notice = (
            f"Task {task_id} completed. "
            "Call check_agent_tasks to retrieve results."
        )
        with self._lock:
            self._notices[agent_id].append(notice)

    def consume_notices(self, agent_id: str) -> tuple[str, ...]:
        """返回并清空指定 parent agent 的 notices。"""

        with self._lock:
            notices = tuple(self._notices[agent_id])
            self._notices[agent_id].clear()
            return notices


@dataclass(frozen=True, slots=True)
class ContinuationErrorRecord:
    """parent continuation 执行失败记录。"""

    parent_agent_id: str
    error: str


class LocalContinuationTrigger:
    """本地线程池驱动的 parent continuation trigger。"""

    def __init__(
        self,
        *,
        agents: Mapping[str, Agent],
        notice_store: AgentTaskNoticeStore,
        event_bus: EventBus | None = None,
        max_workers: int = 1,
    ) -> None:
        """绑定本地 agent 映射和 notice store。"""

        self._agents = agents
        self._notice_store = notice_store
        self._event_bus = event_bus
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._lock = RLock()
        self._condition = Condition(self._lock)
        self._running: set[str] = set()
        self._rerun_requested: set[str] = set()
        self._errors: list[ContinuationErrorRecord] = []

    def on_task_completed(self, parent_agent_id: str, task_id: str) -> None:
        """记录 notice，并在 parent idle 时启动 continuation。"""

        self._notice_store.add_task_completed(parent_agent_id, task_id)
        with self._condition:
            if parent_agent_id in self._running:
                self._rerun_requested.add(parent_agent_id)
                return
            self._running.add(parent_agent_id)
        self._executor.submit(self._run_parent_until_idle, parent_agent_id)

    def wait_idle(self, parent_agent_id: str, timeout: float | None = None) -> bool:
        """等待指定 parent continuation 队列进入 idle。"""

        deadline = None if timeout is None else time.time() + timeout
        with self._condition:
            while parent_agent_id in self._running:
                if deadline is None:
                    remaining = None
                else:
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        return False
                self._condition.wait(timeout=remaining)
            return True

    def continuation_errors(
        self,
        parent_agent_id: str | None = None,
    ) -> tuple[ContinuationErrorRecord, ...]:
        """返回已记录的 parent continuation 失败。"""

        with self._condition:
            errors = tuple(self._errors)
        if parent_agent_id is None:
            return errors
        return tuple(
            error for error in errors if error.parent_agent_id == parent_agent_id
        )

    def shutdown(self) -> None:
        """关闭 continuation 执行线程池。"""

        self._executor.shutdown(wait=True)

    def _run_parent_until_idle(self, parent_agent_id: str) -> None:
        while True:
            try:
                agent = self._agents.get(parent_agent_id)
                if agent is not None:
                    agent.run_continuation()
            except Exception as error:
                self._record_error(parent_agent_id, error)
            finally:
                with self._condition:
                    if parent_agent_id in self._rerun_requested:
                        self._rerun_requested.remove(parent_agent_id)
                        continue
                    self._running.remove(parent_agent_id)
                    self._condition.notify_all()
                    return

    def _record_error(self, parent_agent_id: str, error: Exception) -> None:
        """记录 continuation 失败，并发布 observation-only event。"""

        error_text = str(error)
        record = ContinuationErrorRecord(
            parent_agent_id=parent_agent_id,
            error=error_text,
        )
        with self._condition:
            self._errors.append(record)
        if self._event_bus is not None:
            self._event_bus.emit(
                AgentContinuationFailedEvent(
                    parent_agent_id=parent_agent_id,
                    error=error_text,
                ),
            )
