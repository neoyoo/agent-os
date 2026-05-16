from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from agentos.multi.types import TaskHandle, TaskRecord, TaskResult


@dataclass(frozen=True, slots=True)
class TaskClaim:
    """worker 对任务的 lease claim 结果。"""

    task_id: str
    worker_id: str
    lease_expires_at: float
    attempt: int


class TaskStore(Protocol):
    """分布式 multi-agent 任务 truth source 边界。"""

    def create(self, record: TaskRecord) -> TaskHandle:
        """创建 task record。"""

    def get(self, task_id: str) -> TaskRecord | None:
        """返回 task record。"""

    def claim_queued(
        self,
        *,
        worker_id: str,
        capabilities: Sequence[str],
        limit: int,
        lease_expires_at: float,
        now: float,
    ) -> list[TaskClaim]:
        """原子领取 queued 或 lease-expired tasks。"""

    def mark_running(self, task_id: str, *, now: float | None = None) -> bool:
        """queued -> running。"""

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

    def request_cancel(self, task_id: str, *, now: float) -> bool:
        """请求取消 queued/running task。"""

    def ack_cancelled(
        self,
        task_id: str,
        result: TaskResult,
        *,
        now: float,
        worker_id: str | None = None,
        attempt: int | None = None,
    ) -> bool:
        """worker 确认 running task 已取消。"""

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

    def mark_timed_out(
        self,
        task_id: str,
        result: TaskResult,
        *,
        now: float | None = None,
    ) -> bool:
        """queued/running -> timeout。"""

    def store_late_result(self, task_id: str, result: TaskResult) -> bool:
        """保存 late result。"""

    def consume_results_for_agent(self, agent_id: str) -> list[TaskResult]:
        """返回并标记指定 parent 尚未消费的终态 results。"""

    def due_timeouts(self, now: float) -> list[TaskRecord]:
        """返回 deadline 已到且仍可标记 timeout 的任务。"""

    def active_for_agent(self, agent_id: str | None = None) -> list[TaskHandle]:
        """返回指定 parent 或全部任务的 handles。"""

    def active_count_for_target(self, agent_id: str) -> int:
        """返回指定 target agent 的 queued/running 任务数。"""

    def mark_result_notified(self, task_id: str, *, now: float) -> bool:
        """标记 terminal result 已发送 result-ready 通知。"""

    def release_running_leases(
        self,
        *,
        worker_id: str | None = None,
        now: float | None = None,
    ) -> int:
        """shutdown 时释放 running lease。"""
