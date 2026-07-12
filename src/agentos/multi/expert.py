from __future__ import annotations

import time
from threading import Event, Timer

from agentos.multi.coordinator import AgentCoordinator
from agentos.multi.message_queue import QueueDelivery
from agentos.multi.types import TaskRequest


class ExpertAgentRunner:
    """常驻 expert agent 的 inbox 消费循环。"""

    def __init__(
        self,
        *,
        coordinator: AgentCoordinator,
        agent_id: str,
        worker_id: str | None = None,
        capabilities: tuple[str, ...] = (),
        lease_ttl_seconds: float = 300.0,
        requeue_backoff_seconds: float = 1.0,
    ) -> None:
        """绑定 coordinator 和 expert agent id。"""

        self.coordinator = coordinator
        self.agent_id = agent_id
        self.worker_id = worker_id or agent_id
        registry = getattr(coordinator, "registry", None)
        resolve = None if registry is None else getattr(registry, "resolve", None)
        card = resolve(agent_id) if callable(resolve) else None
        self.capabilities = tuple(
            capabilities or (() if card is None else card.capabilities),
        )
        self.lease_ttl_seconds = lease_ttl_seconds
        self.requeue_backoff_seconds = float(requeue_backoff_seconds)
        if self.requeue_backoff_seconds < 0:
            raise ValueError("requeue_backoff_seconds must be >= 0")
        self._stopped = Event()
        self._idle = Event()
        self._idle.set()

    def run_once(self, timeout: float | None = None) -> bool:
        """等待并处理当前 inbox 中的一批 task_request。"""

        if self._stopped.is_set():
            return False
        self._idle.clear()
        try:
            if self._stopped.is_set():
                return False
            wait_matching = getattr(self.coordinator.inbox, "wait_matching", None)
            if callable(wait_matching):
                has_work = wait_matching(
                    self.agent_id,
                    "task_request",
                    timeout=timeout,
                )
            else:
                has_work = self.coordinator.inbox.wait(self.agent_id, timeout)
            if not has_work or self._stopped.is_set():
                return False
            handled = False
            for delivery in self.coordinator.inbox.collect(
                self.agent_id,
                envelope_types=("task_request",),
            ):
                request = delivery.envelope.payload
                if not isinstance(request, TaskRequest):
                    self.coordinator.inbox.ack(self.agent_id, delivery.delivery_id)
                    continue
                if delivery.envelope.to_agent_id != self.agent_id:
                    self.coordinator.inbox.ack(self.agent_id, delivery.delivery_id)
                    continue
                record = self.coordinator.task_table.get(request.task_id)
                if (
                    record is not None
                    and delivery.envelope.to_agent_id != record.target_agent_id
                ):
                    self.coordinator.inbox.ack(self.agent_id, delivery.delivery_id)
                    continue
                now = time.time()
                claim = self.coordinator.task_table.claim_task(
                    request.task_id,
                    worker_id=self.worker_id,
                    target_agent_id=self.agent_id,
                    capabilities=self.capabilities,
                    lease_expires_at=now + self.lease_ttl_seconds,
                    now=now,
                )
                if claim is None:
                    if self._terminal_result_saved(request.task_id):
                        self.coordinator.inbox.ack(self.agent_id, delivery.delivery_id)
                        handled = True
                    else:
                        self._requeue_delivery(delivery, delay=True)
                    continue
                try:
                    result = self.coordinator.execute_expert_envelope(
                        delivery.envelope,
                        claim=claim,
                    )
                except Exception:
                    self._requeue_delivery(delivery)
                    self._release_worker_leases()
                    continue
                if result is not None and self._terminal_result_saved(
                    request.task_id,
                ):
                    self.coordinator.inbox.ack(self.agent_id, delivery.delivery_id)
                    handled = True
                elif result is not None:
                    self._requeue_delivery(delivery)
            return handled
        finally:
            self._idle.set()

    def run_forever(self, timeout: float = 0.1) -> None:
        """持续消费 inbox，直到 stop 被调用。"""

        while not self._stopped.is_set():
            self.run_once(timeout=timeout)

    def stop(self, timeout_seconds: float | None = None) -> bool:
        """请求 runner 停止。"""

        self._stopped.set()
        drained = self._idle.wait(timeout=timeout_seconds)
        if drained:
            self._release_worker_leases()
        return drained

    def _terminal_result_saved(self, task_id: str) -> bool:
        record = self.coordinator.task_table.get(task_id)
        return (
            record is not None
            and record.status in {"completed", "failed", "cancelled", "timeout"}
            and record.result is not None
            and record.result.status == record.status
        )

    def _requeue_delivery(
        self,
        delivery: QueueDelivery,
        *,
        delay: bool = False,
    ) -> None:
        # Durable queues keep unacked deliveries pending; AgentInbox drains in memory.
        requeue = getattr(self.coordinator.inbox, "requeue", None)
        if not callable(requeue):
            return
        delay_seconds = self.requeue_backoff_seconds if delay else 0.0
        if delay_seconds <= 0:
            requeue(self.agent_id, delivery)
            return
        timer = Timer(delay_seconds, requeue, args=(self.agent_id, delivery))
        timer.daemon = True
        timer.start()

    def _release_worker_leases(self) -> None:
        task_table = getattr(self.coordinator, "task_table", None)
        if task_table is None:
            return
        release_running_leases = getattr(
            task_table,
            "release_running_leases",
            None,
        )
        if callable(release_running_leases):
            release_running_leases(worker_id=self.worker_id, now=time.time())
