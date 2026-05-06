from __future__ import annotations

import time
from typing import Protocol
from uuid import uuid4

from agentos.events import (
    AgentEvent,
    AgentTaskCancelledEvent,
    AgentTaskCompletedEvent,
    AgentTaskDispatchedEvent,
    AgentTaskFailedEvent,
    AgentTaskLateResultReceivedEvent,
    EventBus,
    SubagentSpawnedEvent,
)
from agentos.multi.inbox import AgentInbox, AgentInboxError
from agentos.multi.registry import AgentRegistry
from agentos.multi.spawn import SpawnExecutor
from agentos.multi.tasks import TaskTable
from agentos.multi.types import (
    AgentCard,
    AgentEnvelope,
    SubagentInitRequest,
    TaskHandle,
    TaskRecord,
    TaskRequest,
    TaskResult,
)
from agentos.runtime import Agent


class SubagentFactory(Protocol):
    """根据初始化请求创建 isolated subagent。"""

    def create_subagent(self, request: SubagentInitRequest) -> Agent:
        """创建一个子 agent。"""


class AgentCoordinator:
    """本地单进程 multi-agent 协调器。"""

    def __init__(
        self,
        *,
        registry: AgentRegistry,
        inbox: AgentInbox,
        task_table: TaskTable,
        spawn_executor: SpawnExecutor,
        subagent_factory: SubagentFactory,
        event_bus: EventBus | None = None,
    ) -> None:
        """创建本地协调器。"""

        self.registry = registry
        self.inbox = inbox
        self.task_table = task_table
        self.spawn_executor = spawn_executor
        self.subagent_factory = subagent_factory
        self.event_bus = event_bus
        self._agents: dict[str, Agent] = {}

    def attach_agent(self, card: AgentCard, agent: Agent) -> None:
        """把本地 agent 实例附着到已声明 card。"""

        self.registry.register(card)
        self.inbox.create_inbox(card.agent_id)
        self._agents[card.agent_id] = agent

    def spawn(
        self,
        *,
        instruction: str,
        allowed_tool_names: tuple[str, ...] = (),
        parent_agent_id: str,
        timeout_seconds: float = 300,
    ) -> TaskHandle:
        """创建 ephemeral subagent 并在线程池中执行。"""

        if self.registry.resolve(parent_agent_id) is None:
            raise KeyError(parent_agent_id)

        created_at = time.time()
        task_id = f"task_{uuid4().hex}"
        child_agent_id = f"subagent_{uuid4().hex}"
        request = TaskRequest(
            task_id=task_id,
            instruction=instruction,
            allowed_tool_names=tuple(allowed_tool_names),
            timeout_seconds=timeout_seconds,
        )
        record = TaskRecord(
            task_id=task_id,
            mode="spawn",
            parent_agent_id=parent_agent_id,
            target_agent_id=child_agent_id,
            request=request,
            status="queued",
            created_at=created_at,
            deadline_at=created_at + timeout_seconds,
        )
        handle = self.task_table.create(record)

        init_request = SubagentInitRequest(
            parent_agent_id=parent_agent_id,
            child_agent_id=child_agent_id,
            task=request,
            context_strategy="isolated",
        )
        child_agent = self.subagent_factory.create_subagent(init_request)
        child_card = AgentCard(
            agent_id=child_agent_id,
            name=child_agent_id,
            description="Ephemeral subagent.",
            capabilities=(),
            lifecycle="ephemeral",
            max_concurrent_tasks=1,
        )
        self.registry.register(child_card)
        self.inbox.create_inbox(child_agent_id)
        self._agents[child_agent_id] = child_agent
        self._emit(
            SubagentSpawnedEvent(
                parent_agent_id=parent_agent_id,
                child_agent_id=child_agent_id,
                task_id=task_id,
            ),
        )

        self.spawn_executor.submit(
            task_id,
            lambda: self._run_spawned_subagent(
                parent_agent_id=parent_agent_id,
                child_agent_id=child_agent_id,
                child_agent=child_agent,
                request=request,
            ),
        )
        return handle

    def collect_results(self, agent_id: str) -> list[TaskResult]:
        """drain inbox，并从 TaskTable 返回未消费的终态 results。"""

        self._mark_due_timeouts()
        self.inbox.collect(agent_id)
        return self.task_table.consume_results_for_agent(agent_id)

    def dispatch(
        self,
        *,
        instruction: str,
        required_capabilities: tuple[str, ...],
        parent_agent_id: str,
        allowed_tool_names: tuple[str, ...] = (),
        timeout_seconds: float = 300,
    ) -> TaskHandle:
        """按 capability 发现本地 expert 并派发任务。"""

        self._mark_due_timeouts()
        if self.registry.resolve(parent_agent_id) is None:
            raise KeyError(parent_agent_id)
        target = self._select_available_expert(required_capabilities)
        if target is None:
            raise RuntimeError("no available agent")

        created_at = time.time()
        task_id = f"task_{uuid4().hex}"
        request = TaskRequest(
            task_id=task_id,
            instruction=instruction,
            allowed_tool_names=tuple(allowed_tool_names),
            timeout_seconds=timeout_seconds,
        )
        record = TaskRecord(
            task_id=task_id,
            mode="dispatch",
            parent_agent_id=parent_agent_id,
            target_agent_id=target.agent_id,
            request=request,
            status="queued",
            created_at=created_at,
            deadline_at=created_at + timeout_seconds,
        )
        handle = self.task_table.create(record)
        envelope = AgentEnvelope(
            envelope_id=f"env_{uuid4().hex}",
            from_agent_id=parent_agent_id,
            to_agent_id=target.agent_id,
            type="task_request",
            payload=request,
            created_at=time.time(),
            correlation_id=task_id,
        )
        self.inbox.send(envelope)
        self._emit(
            AgentTaskDispatchedEvent(
                from_agent_id=parent_agent_id,
                to_agent_id=target.agent_id,
                task_id=task_id,
            ),
        )
        return handle

    def active_tasks(self, agent_id: str | None = None) -> list[TaskHandle]:
        """返回 task table 中的任务 handles。"""

        self._mark_due_timeouts()
        return self.task_table.active_for_agent(agent_id)

    def cancel(self, task_id: str) -> bool:
        """取消 queued/running task，并对 running agent 发出 best-effort interrupt。"""

        record = self.task_table.get(task_id)
        if record is None:
            return False
        if record.status in {"completed", "failed", "cancelled", "timeout"}:
            return True
        agent = self._agents.get(record.target_agent_id)
        if agent is not None:
            agent.interrupt()
        result = TaskResult(
            task_id=task_id,
            status="cancelled",
            summary="task cancelled",
        )
        changed = self.task_table.mark_cancelled(task_id, result)
        if changed:
            self._emit(
                AgentTaskCancelledEvent(
                    agent_id=record.target_agent_id,
                    task_id=task_id,
                ),
            )
            self._send_result(record, result)
            return True
        current = self.task_table.get(task_id)
        return current is not None and current.status in {
            "completed",
            "failed",
            "cancelled",
            "timeout",
        }

    def execute_expert_envelope(self, envelope: AgentEnvelope) -> TaskResult | None:
        """执行 expert inbox 中的一条 task_request envelope。"""

        if envelope.type != "task_request" or not isinstance(
            envelope.payload,
            TaskRequest,
        ):
            return None
        request = envelope.payload
        record = self.task_table.get(request.task_id)
        if record is None:
            return TaskResult(
                task_id=request.task_id,
                status="failed",
                summary="task missing",
                error="task missing",
            )
        agent = self._agents.get(record.target_agent_id)
        started_at = time.time()
        try:
            if agent is None:
                raise RuntimeError(f"missing local agent: {record.target_agent_id}")
            if not self.task_table.mark_running(request.task_id):
                current = self.task_table.get(request.task_id)
                return current.result if current is not None else None
            agent_result = agent.run(request.instruction)
            result = TaskResult(
                task_id=request.task_id,
                status="completed",
                summary=agent_result.content,
                elapsed_seconds=time.time() - started_at,
            )
            if self.task_table.mark_completed(request.task_id, result):
                self._emit(
                    AgentTaskCompletedEvent(
                        agent_id=record.target_agent_id,
                        task_id=request.task_id,
                        status="completed",
                        elapsed_seconds=result.elapsed_seconds,
                    ),
                )
                self._send_result(record, result)
            else:
                self._store_late_result(
                    record.target_agent_id,
                    request.task_id,
                    result,
                )
            return result
        except Exception as error:
            result = TaskResult(
                task_id=request.task_id,
                status="failed",
                summary="task failed",
                error=str(error),
                elapsed_seconds=time.time() - started_at,
            )
            if self.task_table.mark_failed(request.task_id, result):
                self._emit(
                    AgentTaskFailedEvent(
                        agent_id=record.target_agent_id,
                        task_id=request.task_id,
                        error=str(error),
                    ),
                )
                self._send_result(record, result)
            else:
                self._store_late_result(
                    record.target_agent_id,
                    request.task_id,
                    result,
                )
            return result

    def _run_spawned_subagent(
        self,
        *,
        parent_agent_id: str,
        child_agent_id: str,
        child_agent: Agent,
        request: TaskRequest,
    ) -> TaskResult:
        started_at = time.time()
        record = self.task_table.get(request.task_id)
        try:
            if record is None:
                return TaskResult(
                    task_id=request.task_id,
                    status="failed",
                    summary="task missing",
                    error="task missing",
                )
            if not self.task_table.mark_running(request.task_id):
                current = self.task_table.get(request.task_id)
                return current.result if current and current.result else TaskResult(
                    task_id=request.task_id,
                    status="cancelled",
                    summary="task already closed",
                )
            agent_result = child_agent.run(request.instruction)
            result = TaskResult(
                task_id=request.task_id,
                status="completed",
                summary=agent_result.content,
                elapsed_seconds=time.time() - started_at,
            )
            self._detach_ephemeral_agent(child_agent_id)
            if self.task_table.mark_completed(request.task_id, result):
                self._emit(
                    AgentTaskCompletedEvent(
                        agent_id=child_agent_id,
                        task_id=request.task_id,
                        status="completed",
                        elapsed_seconds=result.elapsed_seconds,
                    ),
                )
                self._send_result(record, result)
            else:
                self._store_late_result(child_agent_id, request.task_id, result)
            return result
        except Exception as error:
            result = TaskResult(
                task_id=request.task_id,
                status="failed",
                summary="task failed",
                error=str(error),
                elapsed_seconds=time.time() - started_at,
            )
            self._detach_ephemeral_agent(child_agent_id)
            if self.task_table.mark_failed(request.task_id, result):
                self._emit(
                    AgentTaskFailedEvent(
                        agent_id=child_agent_id,
                        task_id=request.task_id,
                        error=str(error),
                    ),
                )
                if record is not None:
                    self._send_result(record, result)
            else:
                self._store_late_result(child_agent_id, request.task_id, result)
            return result
        finally:
            self._detach_ephemeral_agent(child_agent_id)

    def _store_late_result(
        self,
        agent_id: str,
        task_id: str,
        result: TaskResult,
    ) -> None:
        if self.task_table.store_late_result(task_id, result):
            record = self.task_table.get(task_id)
            final_status = "timeout"
            if record is not None and record.status == "cancelled":
                final_status = "cancelled"
            self._emit(
                AgentTaskLateResultReceivedEvent(
                    agent_id=agent_id,
                    task_id=task_id,
                    final_status=final_status,
                ),
            )

    def _send_result(self, record: TaskRecord, result: TaskResult) -> None:
        envelope = AgentEnvelope(
            envelope_id=f"env_{uuid4().hex}",
            from_agent_id=record.target_agent_id,
            to_agent_id=record.parent_agent_id,
            type="task_result",
            payload=result,
            created_at=time.time(),
            correlation_id=record.task_id,
        )
        try:
            self.inbox.send(envelope)
        except AgentInboxError:
            pass

    def _detach_ephemeral_agent(self, agent_id: str) -> None:
        self._agents.pop(agent_id, None)
        self.registry.unregister(agent_id)
        self.inbox.remove_inbox(agent_id)

    def _mark_due_timeouts(self) -> None:
        for record in self.task_table.due_timeouts(time.time()):
            result = TaskResult(
                task_id=record.task_id,
                status="timeout",
                summary="task timed out",
                error="task timed out",
            )
            if self.task_table.mark_timed_out(record.task_id, result):
                self._emit(
                    AgentTaskCompletedEvent(
                        agent_id=record.target_agent_id,
                        task_id=record.task_id,
                        status="timeout",
                    ),
                )
                self._send_result(record, result)

    def _select_available_expert(
        self,
        required_capabilities: tuple[str, ...],
    ) -> AgentCard | None:
        candidates = [
            card
            for card in self.registry.discover(tuple(required_capabilities))
            if card.lifecycle == "persistent" and card.status != "offline"
        ]
        for card in candidates:
            active_count = self.task_table.active_count_for_target(card.agent_id)
            if active_count < card.max_concurrent_tasks:
                return card
        return None

    def _emit(self, event: AgentEvent) -> None:
        if self.event_bus is not None:
            self.event_bus.emit(event)
