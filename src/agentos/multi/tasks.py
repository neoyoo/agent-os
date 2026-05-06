from __future__ import annotations

import time
from dataclasses import replace
from threading import RLock

from agentos.multi.types import TaskHandle, TaskRecord, TaskResult, TaskStatus


class TaskTable:
    """本地 multi-agent 任务状态机。"""

    def __init__(self) -> None:
        """创建空任务表。"""

        self._records: dict[str, TaskRecord] = {}
        self._lock = RLock()

    def create(self, record: TaskRecord) -> TaskHandle:
        """创建 queued task record。"""

        with self._lock:
            if record.task_id in self._records:
                raise ValueError(f"task already exists: {record.task_id}")
            self._records[record.task_id] = record
            return self._handle(record)

    def get(self, task_id: str) -> TaskRecord | None:
        """返回 task record。"""

        with self._lock:
            return self._records.get(task_id)

    def mark_running(self, task_id: str) -> bool:
        """queued -> running。"""

        return self._transition(
            task_id,
            allowed={"queued"},
            status="running",
        )

    def mark_completed(self, task_id: str, result: TaskResult) -> bool:
        """running -> completed。"""

        return self._transition(
            task_id,
            allowed={"running"},
            status="completed",
            result=result,
        )

    def mark_failed(self, task_id: str, result: TaskResult) -> bool:
        """running -> failed。"""

        return self._transition(
            task_id,
            allowed={"running"},
            status="failed",
            result=result,
        )

    def mark_cancelled(self, task_id: str, result: TaskResult) -> bool:
        """queued/running -> cancelled。"""

        return self._transition(
            task_id,
            allowed={"queued", "running"},
            status="cancelled",
            result=result,
        )

    def mark_timed_out(self, task_id: str, result: TaskResult) -> bool:
        """queued/running -> timeout。"""

        return self._transition(
            task_id,
            allowed={"queued", "running"},
            status="timeout",
            result=result,
        )

    def store_late_result(self, task_id: str, result: TaskResult) -> bool:
        """在 timeout/cancelled 之后保存 late result。"""

        with self._lock:
            record = self._records.get(task_id)
            if record is None or record.status not in {"cancelled", "timeout"}:
                return False
            self._records[task_id] = replace(record, late_result=result)
            return True

    def due_timeouts(self, now: float) -> list[TaskRecord]:
        """返回 deadline 已到且仍可标记 timeout 的任务。"""

        with self._lock:
            return [
                record
                for record in self._records.values()
                if record.status in {"queued", "running"}
                and record.deadline_at <= now
            ]

    def active_for_agent(self, agent_id: str | None = None) -> list[TaskHandle]:
        """返回指定 parent 或全部任务的 handles。"""

        with self._lock:
            return [
                self._handle(record)
                for record in self._records.values()
                if agent_id is None or record.parent_agent_id == agent_id
            ]

    def completed_for_agent(self, agent_id: str) -> list[TaskResult]:
        """返回指定 parent 可见的终态 results。"""

        with self._lock:
            return [
                record.result
                for record in self._records.values()
                if record.parent_agent_id == agent_id
                and record.result is not None
                and record.status
                in {"completed", "failed", "cancelled", "timeout"}
            ]

    def consume_results_for_agent(self, agent_id: str) -> list[TaskResult]:
        """返回并标记指定 parent 尚未消费的终态 results。"""

        with self._lock:
            now = time.time()
            results: list[TaskResult] = []
            for record in self._records.values():
                if (
                    record.parent_agent_id == agent_id
                    and record.result is not None
                    and record.consumed_at is None
                    and record.status in {"completed", "failed", "cancelled", "timeout"}
                ):
                    results.append(record.result)
                    self._records[record.task_id] = replace(
                        record,
                        consumed_at=now,
                    )
            return results

    def active_count_for_target(self, agent_id: str) -> int:
        """返回指定 target agent 的 queued/running 任务数。"""

        with self._lock:
            return sum(
                1
                for record in self._records.values()
                if record.target_agent_id == agent_id
                and record.status in {"queued", "running"}
            )

    def _transition(
        self,
        task_id: str,
        *,
        allowed: set[TaskStatus],
        status: TaskStatus,
        result: TaskResult | None = None,
    ) -> bool:
        with self._lock:
            record = self._records.get(task_id)
            if record is None or record.status not in allowed:
                return False
            self._records[task_id] = replace(
                record,
                status=status,
                result=result,
                completed_at=None if result is None else time.time(),
                consumed_at=None,
            )
            return True

    def _handle(self, record: TaskRecord) -> TaskHandle:
        return TaskHandle(
            task_id=record.task_id,
            mode=record.mode,
            target_agent_id=record.target_agent_id,
            status=record.status,
        )
