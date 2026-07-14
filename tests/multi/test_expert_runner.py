import time
from threading import Event, Thread

from agentos.multi import (
    AgentCard,
    AgentCoordinator,
    AgentEnvelope,
    AgentInbox,
    ExpertAgentRunner,
    InMemoryRegistry,
    QueueDelivery,
    SpawnExecutor,
    TaskResult,
    TaskTable,
)
from agentos.runtime import AgentResult
from tests.multi.helpers import build_sync_agent_with_response
from tests.multi.test_coordinator_spawn import StaticSubagentFactory


class RecordingInbox(AgentInbox):
    def __init__(self) -> None:
        super().__init__()
        self.acked: list[tuple[str, str]] = []

    def ack(self, agent_id: str, delivery_id: str) -> bool:
        self.acked.append((agent_id, delivery_id))
        return super().ack(agent_id, delivery_id)


class CorruptDeliveryInbox(RecordingInbox):
    def __init__(self) -> None:
        super().__init__()
        self.corrupt_deliveries: dict[str, list[QueueDelivery]] = {}

    def wait(self, agent_id: str, timeout: float | None = None) -> bool:
        if self.corrupt_deliveries.get(agent_id):
            return True
        return super().wait(agent_id, timeout)

    def collect(
        self,
        agent_id: str,
        *,
        envelope_types: tuple[str, ...] | None = None,
    ) -> list[QueueDelivery]:
        deliveries = self.corrupt_deliveries.pop(agent_id, [])
        if deliveries:
            return deliveries
        return super().collect(agent_id, envelope_types=envelope_types)


class RejectTerminalTaskTable(TaskTable):
    def mark_completed(self, *args: object, **kwargs: object) -> bool:
        return False


class CancellingAgent:
    def __init__(self, coordinator: AgentCoordinator) -> None:
        self.coordinator = coordinator
        self.task_id: str | None = None
        self.agent = self

    def bind_task(self, task_id: str) -> None:
        self.task_id = task_id

    def interrupt(self) -> bool:
        return True

    def run(self, instruction: str) -> AgentResult:
        if self.task_id is None:
            raise RuntimeError("cancelling agent has no task")
        self.coordinator.cancel(self.task_id)
        return AgentResult("expert result")


def wait_for_parent_result(coordinator: AgentCoordinator):
    deadline = time.time() + 2
    while time.time() < deadline:
        results = coordinator.collect_results("parent")
        if results:
            return results[0]
        time.sleep(0.01)
    raise AssertionError("timed out waiting for expert result")


def test_expert_runner_processes_one_task_request_and_returns_result() -> None:
    coordinator = AgentCoordinator(
        registry=InMemoryRegistry(),
        inbox=AgentInbox(),
        task_table=TaskTable(),
        spawn_executor=SpawnExecutor(max_workers=1),
        subagent_factory=StaticSubagentFactory(),
    )
    coordinator.attach_agent(
        AgentCard(
            agent_id="parent",
            name="Parent",
            description="Parent agent.",
            capabilities=("coordinate",),
        ),
        build_sync_agent_with_response("parent"),
    )
    coordinator.attach_agent(
        AgentCard(
            agent_id="expert",
            name="Expert",
            description="Expert agent.",
            capabilities=("code-review",),
        ),
        build_sync_agent_with_response("expert result"),
    )
    handle = coordinator.dispatch(
        instruction="Review this",
        required_capabilities=("code-review",),
        parent_agent_id="parent",
    )

    runner = ExpertAgentRunner(coordinator=coordinator, agent_id="expert")

    assert runner.run_once(timeout=0.1) is True

    result = wait_for_parent_result(coordinator)
    assert result.task_id == handle.task_id
    assert result.status == "completed"
    assert result.summary == "expert result"
    assert coordinator.task_table.get(handle.task_id).status == "completed"  # type: ignore[union-attr]

    coordinator.spawn_executor.shutdown()


def test_expert_runner_run_once_does_not_accept_new_work_after_stop() -> None:
    inbox = RecordingInbox()
    coordinator = AgentCoordinator(
        registry=InMemoryRegistry(),
        inbox=inbox,
        task_table=TaskTable(),
        spawn_executor=SpawnExecutor(max_workers=1),
        subagent_factory=StaticSubagentFactory(),
    )
    coordinator.attach_agent(
        AgentCard(
            agent_id="parent",
            name="Parent",
            description="Parent agent.",
            capabilities=("coordinate",),
        ),
        build_sync_agent_with_response("parent"),
    )
    coordinator.attach_agent(
        AgentCard(
            agent_id="expert",
            name="Expert",
            description="Expert agent.",
            capabilities=("code-review",),
        ),
        build_sync_agent_with_response("expert result"),
    )
    handle = coordinator.dispatch(
        instruction="Review this",
        required_capabilities=("code-review",),
        parent_agent_id="parent",
    )
    runner = ExpertAgentRunner(coordinator=coordinator, agent_id="expert")

    assert runner.stop(timeout_seconds=0.1)
    assert runner.run_once(timeout=0.1) is False

    stored = coordinator.task_table.get(handle.task_id)
    assert stored is not None
    assert stored.status == "queued"
    assert inbox.has_pending("expert")
    assert inbox.acked == []

    coordinator.spawn_executor.shutdown()


def test_expert_runner_stop_during_wait_prevents_late_collect() -> None:
    wait_entered = Event()
    allow_wait_return = Event()

    class BlockingInbox(RecordingInbox):
        def wait(self, agent_id: str, timeout: float | None = None) -> bool:
            wait_entered.set()
            allow_wait_return.wait(timeout=1)
            return True

        def collect(
            self,
            agent_id: str,
            *,
            envelope_types: tuple[str, ...] | None = None,
        ) -> list[QueueDelivery]:
            raise AssertionError("stopped runner must not collect new deliveries")

    inbox = BlockingInbox()
    coordinator = AgentCoordinator(
        registry=InMemoryRegistry(),
        inbox=inbox,
        task_table=TaskTable(),
        spawn_executor=SpawnExecutor(max_workers=1),
        subagent_factory=StaticSubagentFactory(),
    )
    runner = ExpertAgentRunner(coordinator=coordinator, agent_id="expert")

    result: list[bool] = []
    thread = Thread(target=lambda: result.append(runner.run_once(timeout=5)))
    thread.start()
    assert wait_entered.wait(timeout=1)

    assert runner.stop(timeout_seconds=0.01) is False
    allow_wait_return.set()
    thread.join(timeout=1)

    assert result == [False]
    assert not thread.is_alive()

    coordinator.spawn_executor.shutdown()


def test_expert_runner_claims_task_before_execution_and_acks_after_terminal_save() -> None:
    inbox = RecordingInbox()
    coordinator = AgentCoordinator(
        registry=InMemoryRegistry(),
        inbox=inbox,
        task_table=TaskTable(),
        spawn_executor=SpawnExecutor(max_workers=1),
        subagent_factory=StaticSubagentFactory(),
    )
    coordinator.attach_agent(
        AgentCard(
            agent_id="parent",
            name="Parent",
            description="Parent agent.",
            capabilities=("coordinate",),
        ),
        build_sync_agent_with_response("parent"),
    )
    coordinator.attach_agent(
        AgentCard(
            agent_id="expert",
            name="Expert",
            description="Expert agent.",
            capabilities=("code-review",),
        ),
        build_sync_agent_with_response("expert result"),
    )
    handle = coordinator.dispatch(
        instruction="Review this",
        required_capabilities=("code-review",),
        parent_agent_id="parent",
    )

    runner = ExpertAgentRunner(
        coordinator=coordinator,
        agent_id="expert",
        worker_id="expert-worker-1",
        capabilities=("code-review",),
        lease_ttl_seconds=30.0,
    )

    assert runner.run_once(timeout=0.1) is True

    stored = coordinator.task_table.get(handle.task_id)
    assert stored is not None
    assert stored.status == "completed"
    assert stored.worker_id == "expert-worker-1"
    assert stored.attempt == 1
    assert len(inbox.acked) == 1
    assert inbox.acked[0][0] == "expert"

    coordinator.spawn_executor.shutdown()


def test_expert_runner_requeues_claimed_delivery_and_releases_lease_on_outer_failure() -> None:
    inbox = RecordingInbox()
    coordinator = AgentCoordinator(
        registry=InMemoryRegistry(),
        inbox=inbox,
        task_table=TaskTable(),
        spawn_executor=SpawnExecutor(max_workers=1),
        subagent_factory=StaticSubagentFactory(),
    )
    coordinator.attach_agent(
        AgentCard(
            agent_id="parent",
            name="Parent",
            description="Parent agent.",
            capabilities=("coordinate",),
        ),
        build_sync_agent_with_response("parent"),
    )
    coordinator.attach_agent(
        AgentCard(
            agent_id="expert",
            name="Expert",
            description="Expert agent.",
            capabilities=("code-review",),
        ),
        build_sync_agent_with_response("expert result"),
    )
    handle = coordinator.dispatch(
        instruction="Review this",
        required_capabilities=("code-review",),
        parent_agent_id="parent",
    )

    def raise_outer_failure(*args: object, **kwargs: object) -> None:
        raise RuntimeError("runner boundary unavailable")

    coordinator.execute_expert_envelope = raise_outer_failure  # type: ignore[method-assign]
    runner = ExpertAgentRunner(
        coordinator=coordinator,
        agent_id="expert",
        worker_id="expert-worker-1",
        capabilities=("code-review",),
        lease_ttl_seconds=30.0,
        requeue_backoff_seconds=0.0,
    )

    assert runner.run_once(timeout=0.1) is False

    stored = coordinator.task_table.get(handle.task_id)
    assert stored is not None
    assert stored.status == "queued"
    assert stored.worker_id is None
    assert stored.lease_expires_at is None
    assert inbox.has_pending("expert")
    assert inbox.acked == []
    assert coordinator.collect_results("parent") == []

    coordinator.spawn_executor.shutdown()


def test_expert_runner_fences_cancelled_claim_before_ack() -> None:
    inbox = RecordingInbox()
    coordinator = AgentCoordinator(
        registry=InMemoryRegistry(),
        inbox=inbox,
        task_table=TaskTable(),
        spawn_executor=SpawnExecutor(max_workers=1),
        subagent_factory=StaticSubagentFactory(),
    )
    coordinator.attach_agent(
        AgentCard(
            agent_id="parent",
            name="Parent",
            description="Parent agent.",
            capabilities=("coordinate",),
        ),
        build_sync_agent_with_response("parent"),
    )
    cancelling_agent = CancellingAgent(coordinator)
    coordinator.attach_agent(
        AgentCard(
            agent_id="expert",
            name="Expert",
            description="Expert agent.",
            capabilities=("code-review",),
        ),
        cancelling_agent,  # type: ignore[arg-type]
    )
    handle = coordinator.dispatch(
        instruction="Review this",
        required_capabilities=("code-review",),
        parent_agent_id="parent",
    )
    cancelling_agent.bind_task(handle.task_id)

    runner = ExpertAgentRunner(
        coordinator=coordinator,
        agent_id="expert",
        worker_id="expert-worker-1",
        capabilities=("code-review",),
        lease_ttl_seconds=30.0,
    )

    assert runner.run_once(timeout=0.1) is True

    stored = coordinator.task_table.get(handle.task_id)
    assert stored is not None
    assert stored.status == "cancelled"
    assert stored.result is not None
    assert stored.result.status == "cancelled"
    assert len(inbox.acked) == 1

    coordinator.spawn_executor.shutdown()


def test_expert_runner_does_not_ack_when_terminal_save_is_rejected() -> None:
    inbox = RecordingInbox()
    coordinator = AgentCoordinator(
        registry=InMemoryRegistry(),
        inbox=inbox,
        task_table=RejectTerminalTaskTable(),
        spawn_executor=SpawnExecutor(max_workers=1),
        subagent_factory=StaticSubagentFactory(),
    )
    coordinator.attach_agent(
        AgentCard(
            agent_id="parent",
            name="Parent",
            description="Parent agent.",
            capabilities=("coordinate",),
        ),
        build_sync_agent_with_response("parent"),
    )
    coordinator.attach_agent(
        AgentCard(
            agent_id="expert",
            name="Expert",
            description="Expert agent.",
            capabilities=("code-review",),
        ),
        build_sync_agent_with_response("expert result"),
    )
    handle = coordinator.dispatch(
        instruction="Review this",
        required_capabilities=("code-review",),
        parent_agent_id="parent",
    )

    runner = ExpertAgentRunner(
        coordinator=coordinator,
        agent_id="expert",
        worker_id="expert-worker-1",
        capabilities=("code-review",),
        lease_ttl_seconds=30.0,
    )

    assert runner.run_once(timeout=0.1) is False

    stored = coordinator.task_table.get(handle.task_id)
    assert stored is not None
    assert stored.status == "running"
    assert stored.worker_id == "expert-worker-1"
    assert stored.attempt == 1
    assert inbox.acked == []
    assert inbox.has_pending("expert")
    assert coordinator.collect_results("parent") == []

    coordinator.spawn_executor.shutdown()


def test_expert_runner_rejects_wrong_target_delivery_before_claiming_task() -> None:
    inbox = RecordingInbox()
    coordinator = AgentCoordinator(
        registry=InMemoryRegistry(),
        inbox=inbox,
        task_table=TaskTable(),
        spawn_executor=SpawnExecutor(max_workers=1),
        subagent_factory=StaticSubagentFactory(),
    )
    coordinator.attach_agent(
        AgentCard(
            agent_id="parent",
            name="Parent",
            description="Parent agent.",
            capabilities=("coordinate",),
        ),
        build_sync_agent_with_response("parent"),
    )
    coordinator.attach_agent(
        AgentCard(
            agent_id="expert",
            name="Expert",
            description="Expert agent.",
            capabilities=("code-review",),
        ),
        build_sync_agent_with_response("expert result"),
    )
    coordinator.attach_agent(
        AgentCard(
            agent_id="other_expert",
            name="Other Expert",
            description="Other expert agent.",
            capabilities=("code-review",),
        ),
        build_sync_agent_with_response("wrong expert result"),
    )
    handle = coordinator.dispatch(
        instruction="Review this",
        required_capabilities=("code-review",),
        parent_agent_id="parent",
        target_agent_id="expert",
    )
    record = coordinator.task_table.get(handle.task_id)
    assert record is not None
    inbox.send(
        AgentEnvelope(
            envelope_id="env_wrong_target",
            from_agent_id="parent",
            to_agent_id="other_expert",
            type="task_request",
            payload=record.request,
            created_at=time.time(),
        ),
    )

    runner = ExpertAgentRunner(
        coordinator=coordinator,
        agent_id="other_expert",
        worker_id="other-worker-1",
        capabilities=("code-review",),
        lease_ttl_seconds=30.0,
    )

    assert runner.run_once(timeout=0.1) is False

    stored = coordinator.task_table.get(handle.task_id)
    assert stored is not None
    assert stored.status == "queued"
    assert stored.worker_id is None
    assert stored.attempt == 0
    assert ("other_expert", "env_wrong_target") in inbox.acked
    assert coordinator.collect_results("parent") == []

    coordinator.spawn_executor.shutdown()


def test_expert_runner_rejects_delivery_not_addressed_to_runner() -> None:
    inbox = CorruptDeliveryInbox()
    coordinator = AgentCoordinator(
        registry=InMemoryRegistry(),
        inbox=inbox,
        task_table=TaskTable(),
        spawn_executor=SpawnExecutor(max_workers=1),
        subagent_factory=StaticSubagentFactory(),
    )
    coordinator.attach_agent(
        AgentCard(
            agent_id="parent",
            name="Parent",
            description="Parent agent.",
            capabilities=("coordinate",),
        ),
        build_sync_agent_with_response("parent"),
    )
    coordinator.attach_agent(
        AgentCard(
            agent_id="expert",
            name="Expert",
            description="Expert agent.",
            capabilities=("code-review",),
        ),
        build_sync_agent_with_response("expert result"),
    )
    coordinator.attach_agent(
        AgentCard(
            agent_id="other_expert",
            name="Other Expert",
            description="Other expert agent.",
            capabilities=("code-review",),
        ),
        build_sync_agent_with_response("wrong expert result"),
    )
    handle = coordinator.dispatch(
        instruction="Review this",
        required_capabilities=("code-review",),
        parent_agent_id="parent",
        target_agent_id="expert",
    )
    record = coordinator.task_table.get(handle.task_id)
    assert record is not None
    inbox.corrupt_deliveries["other_expert"] = [
        QueueDelivery(
            delivery_id="env_wrong_queue",
            envelope=AgentEnvelope(
                envelope_id="env_wrong_queue",
                from_agent_id="parent",
                to_agent_id="expert",
                type="task_request",
                payload=record.request,
                created_at=time.time(),
            ),
        ),
    ]

    runner = ExpertAgentRunner(
        coordinator=coordinator,
        agent_id="other_expert",
        worker_id="other-worker-1",
        capabilities=("code-review",),
        lease_ttl_seconds=30.0,
    )

    assert runner.run_once(timeout=0.1) is False

    stored = coordinator.task_table.get(handle.task_id)
    assert stored is not None
    assert stored.status == "queued"
    assert stored.worker_id is None
    assert stored.attempt == 0
    assert inbox.acked == [("other_expert", "env_wrong_queue")]

    coordinator.spawn_executor.shutdown()


def test_expert_runner_acks_stale_delivery_for_terminal_task() -> None:
    inbox = RecordingInbox()
    coordinator = AgentCoordinator(
        registry=InMemoryRegistry(),
        inbox=inbox,
        task_table=TaskTable(),
        spawn_executor=SpawnExecutor(max_workers=1),
        subagent_factory=StaticSubagentFactory(),
    )
    coordinator.attach_agent(
        AgentCard(
            agent_id="parent",
            name="Parent",
            description="Parent agent.",
            capabilities=("coordinate",),
        ),
        build_sync_agent_with_response("parent"),
    )
    coordinator.attach_agent(
        AgentCard(
            agent_id="expert",
            name="Expert",
            description="Expert agent.",
            capabilities=("code-review",),
        ),
        build_sync_agent_with_response("expert result"),
    )
    handle = coordinator.dispatch(
        instruction="Review this",
        required_capabilities=("code-review",),
        parent_agent_id="parent",
    )
    claim = coordinator.task_table.claim_task(
        handle.task_id,
        worker_id="other-worker",
        capabilities=("code-review",),
        lease_expires_at=time.time() + 30.0,
        now=time.time(),
    )
    assert claim is not None
    assert coordinator.task_table.mark_completed(
        handle.task_id,
        TaskResult(task_id=handle.task_id, status="completed", summary="done"),
        now=time.time(),
        worker_id="other-worker",
        attempt=1,
    )

    runner = ExpertAgentRunner(
        coordinator=coordinator,
        agent_id="expert",
        worker_id="expert-worker-1",
        capabilities=("code-review",),
        lease_ttl_seconds=30.0,
    )

    assert runner.run_once(timeout=0.1) is True
    assert len(inbox.acked) == 1
    stored = coordinator.task_table.get(handle.task_id)
    assert stored is not None
    assert stored.status == "completed"
    assert stored.worker_id == "other-worker"

    coordinator.spawn_executor.shutdown()


def test_expert_runner_requeues_in_memory_delivery_when_claim_is_missed() -> None:
    inbox = RecordingInbox()
    coordinator = AgentCoordinator(
        registry=InMemoryRegistry(),
        inbox=inbox,
        task_table=TaskTable(),
        spawn_executor=SpawnExecutor(max_workers=1),
        subagent_factory=StaticSubagentFactory(),
    )
    coordinator.attach_agent(
        AgentCard(
            agent_id="parent",
            name="Parent",
            description="Parent agent.",
            capabilities=("coordinate",),
        ),
        build_sync_agent_with_response("parent"),
    )
    coordinator.attach_agent(
        AgentCard(
            agent_id="expert",
            name="Expert",
            description="Expert agent.",
            capabilities=("code-review",),
        ),
        build_sync_agent_with_response("expert result"),
    )
    handle = coordinator.dispatch(
        instruction="Review this",
        required_capabilities=("code-review",),
        parent_agent_id="parent",
    )
    first_claim = coordinator.task_table.claim_task(
        handle.task_id,
        worker_id="other-worker",
        capabilities=("code-review",),
        lease_expires_at=time.time() + 30.0,
        now=time.time(),
    )
    assert first_claim is not None

    runner = ExpertAgentRunner(
        coordinator=coordinator,
        agent_id="expert",
        worker_id="expert-worker-1",
        capabilities=("code-review",),
        lease_ttl_seconds=30.0,
        requeue_backoff_seconds=0.0,
    )

    assert runner.run_once(timeout=0.1) is False
    assert inbox.acked == []
    assert inbox.has_pending("expert")

    coordinator.spawn_executor.shutdown()


def test_expert_runner_backs_off_claim_missed_delivery_by_default() -> None:
    inbox = RecordingInbox()
    coordinator = AgentCoordinator(
        registry=InMemoryRegistry(),
        inbox=inbox,
        task_table=TaskTable(),
        spawn_executor=SpawnExecutor(max_workers=1),
        subagent_factory=StaticSubagentFactory(),
    )
    coordinator.attach_agent(
        AgentCard(
            agent_id="parent",
            name="Parent",
            description="Parent agent.",
            capabilities=("coordinate",),
        ),
        build_sync_agent_with_response("parent"),
    )
    coordinator.attach_agent(
        AgentCard(
            agent_id="expert",
            name="Expert",
            description="Expert agent.",
            capabilities=("code-review",),
        ),
        build_sync_agent_with_response("expert result"),
    )
    handle = coordinator.dispatch(
        instruction="Review this",
        required_capabilities=("code-review",),
        parent_agent_id="parent",
    )
    first_claim = coordinator.task_table.claim_task(
        handle.task_id,
        worker_id="other-worker",
        capabilities=("code-review",),
        lease_expires_at=time.time() + 30.0,
        now=time.time(),
    )
    assert first_claim is not None

    runner = ExpertAgentRunner(
        coordinator=coordinator,
        agent_id="expert",
        worker_id="expert-worker-1",
        capabilities=("code-review",),
        lease_ttl_seconds=30.0,
    )

    assert runner.run_once(timeout=0.1) is False
    assert inbox.acked == []
    assert not inbox.has_pending("expert")

    coordinator.spawn_executor.shutdown()
