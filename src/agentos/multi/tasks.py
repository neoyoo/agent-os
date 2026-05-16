from __future__ import annotations

import time
from dataclasses import replace
from threading import RLock
from typing import Sequence

from agentos.multi.task_store import TaskClaim
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

    def claim_queued(
        self,
        *,
        worker_id: str,
        capabilities: Sequence[str],
        limit: int,
        lease_expires_at: float,
        now: float,
    ) -> list[TaskClaim]:
        """领取 queued task，并写入 worker lease。"""

        if limit < 1:
            return []
        available_capabilities = set(capabilities)
        claims: list[TaskClaim] = []
        with self._lock:
            for task_id, record in self._records.items():
                if len(claims) >= limit:
                    break
                if not (
                    record.status == "queued"
                    or (
                        record.status == "running"
                        and record.lease_expires_at is not None
                        and record.lease_expires_at <= now
                        and record.cancel_requested_at is None
                    )
                ):
                    continue
                if record.deadline_at <= now:
                    continue
                required_capabilities = set(record.request.allowed_tool_names)
                if (
                    required_capabilities
                    and not required_capabilities.issubset(available_capabilities)
                ):
                    continue
                attempt = record.attempt + 1
                self._records[task_id] = replace(
                    record,
                    status="running",
                    worker_id=worker_id,
                    lease_expires_at=lease_expires_at,
                    attempt=attempt,
                    updated_at=now,
                    version=record.version + 1,
                )
                claims.append(
                    TaskClaim(
                        task_id=task_id,
                        worker_id=worker_id,
                        lease_expires_at=lease_expires_at,
                        attempt=attempt,
                    ),
                )
        return claims

    def mark_running(self, task_id: str, *, now: float | None = None) -> bool:
        """queued -> running。"""

        return self._transition(
            task_id,
            allowed={"queued"},
            status="running",
            now=now,
        )

    def mark_completed(
        self,
        task_id: str,
        result: TaskResult,
        *,
        now: float | None = None,
        worker_id: str | None = None,
        attempt: int | None = None,
    ) -> bool:
        """running -> completed。"""

        return self._transition(
            task_id,
            allowed={"running"},
            status="completed",
            result=result,
            now=now,
            worker_id=worker_id,
            attempt=attempt,
        )

    def mark_failed(
        self,
        task_id: str,
        result: TaskResult,
        *,
        now: float | None = None,
        worker_id: str | None = None,
        attempt: int | None = None,
    ) -> bool:
        """running -> failed。"""

        return self._transition(
            task_id,
            allowed={"running"},
            status="failed",
            result=result,
            now=now,
            worker_id=worker_id,
            attempt=attempt,
        )

    def request_cancel(self, task_id: str, *, now: float) -> bool:
        """queued 直接取消，running 写入 cancel intent。"""

        with self._lock:
            record = self._records.get(task_id)
            if record is None:
                return False
            if record.status == "queued":
                result = TaskResult(
                    task_id=task_id,
                    status="cancelled",
                    summary="task cancelled",
                )
                self._records[task_id] = replace(
                    record,
                    status="cancelled",
                    result=result,
                    completed_at=now,
                    consumed_at=None,
                    updated_at=now,
                    version=record.version + 1,
                )
                return True
            if record.status == "running":
                if record.cancel_requested_at is not None:
                    return True
                self._records[task_id] = replace(
                    record,
                    cancel_requested_at=now,
                    updated_at=now,
                    version=record.version + 1,
                )
                return True
            return record.status == "cancelled"

    def ack_cancelled(
        self,
        task_id: str,
        result: TaskResult,
        *,
        now: float,
        worker_id: str | None = None,
        attempt: int | None = None,
    ) -> bool:
        """running task 响应 cancel intent 后进入 cancelled。"""

        with self._lock:
            record = self._records.get(task_id)
            if (
                record is None
                or record.status != "running"
                or record.cancel_requested_at is None
                or not self._claim_matches(
                    record,
                    worker_id=worker_id,
                    attempt=attempt,
                )
            ):
                return False
            self._records[task_id] = replace(
                record,
                status="cancelled",
                result=result,
                completed_at=now,
                consumed_at=None,
                updated_at=now,
                version=record.version + 1,
            )
            return True

    def mark_cancelled(
        self,
        task_id: str,
        result: TaskResult,
        *,
        now: float | None = None,
        worker_id: str | None = None,
        attempt: int | None = None,
    ) -> bool:
        """queued/running -> cancelled。"""

        return self._transition(
            task_id,
            allowed={"queued", "running"},
            status="cancelled",
            result=result,
            now=now,
            worker_id=worker_id,
            attempt=attempt,
        )

    def mark_timed_out(
        self,
        task_id: str,
        result: TaskResult,
        *,
        now: float | None = None,
    ) -> bool:
        """queued/running -> timeout。"""

        return self._transition(
            task_id,
            allowed={"queued", "running"},
            status="timeout",
            result=result,
            now=now,
        )

    def store_late_result(self, task_id: str, result: TaskResult) -> bool:
        """在 timeout/cancelled 之后保存 late result。"""

        with self._lock:
            record = self._records.get(task_id)
            if record is None or record.status not in {"cancelled", "timeout"}:
                return False
            self._records[task_id] = replace(
                record,
                late_result=result,
                updated_at=time.time(),
                version=record.version + 1,
            )
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
                        updated_at=now,
                        version=record.version + 1,
                    )
            return results

    def mark_result_notified(self, task_id: str, *, now: float) -> bool:
        """标记 terminal result 已发送 result-ready 通知。"""

        with self._lock:
            record = self._records.get(task_id)
            if (
                record is None
                or record.result is None
                or record.result_notified_at is not None
            ):
                return False
            self._records[task_id] = replace(
                record,
                result_notified_at=now,
                updated_at=now,
                version=record.version + 1,
            )
            return True

    def release_running_leases(
        self,
        *,
        worker_id: str | None = None,
        now: float | None = None,
    ) -> int:
        """shutdown 时释放 running lease，使任务可被其它 worker 重新领取。"""

        updated_at = time.time() if now is None else now
        released = 0
        with self._lock:
            for task_id, record in list(self._records.items()):
                if record.status != "running":
                    continue
                if worker_id is not None and record.worker_id != worker_id:
                    continue
                self._records[task_id] = replace(
                    record,
                    status="queued",
                    worker_id=None,
                    lease_expires_at=None,
                    updated_at=updated_at,
                    version=record.version + 1,
                )
                released += 1
        return released

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
        now: float | None = None,
        worker_id: str | None = None,
        attempt: int | None = None,
    ) -> bool:
        with self._lock:
            record = self._records.get(task_id)
            if (
                record is None
                or record.status not in allowed
                or not self._claim_matches(
                    record,
                    worker_id=worker_id,
                    attempt=attempt,
                )
            ):
                return False
            updated_at = time.time() if now is None else now
            self._records[task_id] = replace(
                record,
                status=status,
                result=result,
                completed_at=None if result is None else updated_at,
                consumed_at=None,
                updated_at=updated_at,
                version=record.version + 1,
            )
            return True

    def _handle(self, record: TaskRecord) -> TaskHandle:
        return TaskHandle(
            task_id=record.task_id,
            mode=record.mode,
            target_agent_id=record.target_agent_id,
            status=record.status,
        )

    def _claim_matches(
        self,
        record: TaskRecord,
        *,
        worker_id: str | None,
        attempt: int | None,
    ) -> bool:
        if record.worker_id is not None and worker_id is None:
            return False
        if record.attempt > 0 and attempt is None:
            return False
        if worker_id is not None and record.worker_id != worker_id:
            return False
        if attempt is not None and record.attempt != attempt:
            return False
        return True
