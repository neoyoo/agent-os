from __future__ import annotations

import asyncio

import pytest

from agentos.multi import AgentInbox
from agentos.multi.message_queue import QueueDelivery
from agentos.multi.team import (
    InMemoryTeamWorkerCancellationStore,
    InMemoryTeamUiStreamStore,
    InMemoryTeamWorkerSessionProvider,
    InMemoryTeamWorkerRetryStore,
    TeamMessage,
    TeamError,
    TeamWorkerCancellationRecord,
    TeamWorkerRetryPolicy,
    TeamWorkerRunner,
    TeamWorkerSessionRequest,
)
from agentos.multi.types import AgentEnvelope, TaskRequest


class RecordingAgent:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.continuations = 0
        self.inputs: list[object] = []
        self.outcomes: list[object] = []

    async def run(self, input: object) -> object:
        self.inputs.append(input)
        self.continuations += 1
        if self.fail:
            raise RuntimeError("worker failed")
        from agentos.runtime import AgentResult

        outcome = AgentResult("continued")
        self.outcomes.append(outcome)
        return outcome


class MapAgentProvider:
    def __init__(self, agents: dict[str, RecordingAgent]) -> None:
        self.agents = agents

    def get_worker_agent(self, session) -> RecordingAgent:
        return self.agents[session.session_id]


class MutableClock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class TypeOnlyQueue:
    def __init__(self, delivery: QueueDelivery) -> None:
        self.delivery = delivery

    def create_inbox(self, agent_id: str) -> None:
        pass

    def remove_inbox(self, agent_id: str) -> None:
        pass

    def send(self, envelope: AgentEnvelope) -> str:
        return envelope.envelope_id

    def collect(
        self,
        agent_id: str,
        *,
        envelope_types=None,
    ) -> list[QueueDelivery]:
        return [self.delivery]

    def wait(self, agent_id: str, timeout: float | None = None) -> bool:
        return False

    def ack(self, agent_id: str, delivery_id: str) -> bool:
        return False


def make_session_provider() -> InMemoryTeamWorkerSessionProvider:
    provider = InMemoryTeamWorkerSessionProvider()
    provider.create_worker_session(
        TeamWorkerSessionRequest(
            team_id="team_1",
            agent_id="worker",
            role="worker",
            capabilities=("research",),
            requested_session_id="session_worker",
            created_at=1.0,
        ),
    )
    return provider


def team_message() -> TeamMessage:
    return TeamMessage(
        message_id="message_1",
        team_id="team_1",
        from_agent_id="leader",
        to_agent_id="worker",
        content="Find source A.",
        kind="instruction",
        created_at=2.0,
    )


def send_team_message(inbox: AgentInbox) -> str:
    return inbox.send(
        AgentEnvelope(
            envelope_id="env_team_1",
            from_agent_id="leader",
            to_agent_id="worker",
            type="team_message",
            payload=team_message(),
            created_at=2.0,
            correlation_id="message_1",
        ),
    )


def run_pending(
    runner: TeamWorkerRunner,
    *,
    team_id: str | None = None,
):
    return asyncio.run(runner.run_pending(team_id=team_id))


def test_team_worker_runner_scopes_shared_worker_delivery_to_team() -> None:
    provider = make_session_provider()
    provider.create_worker_session(
        TeamWorkerSessionRequest(
            team_id="team_2",
            agent_id="worker",
            role="worker",
            capabilities=("research",),
            requested_session_id="session_worker_team_2",
            created_at=1.0,
        ),
    )
    inbox = AgentInbox()
    inbox.create_inbox("worker")
    team_2_delivery_id = inbox.send(
        AgentEnvelope(
            envelope_id="env_team_2",
            from_agent_id="leader_2",
            to_agent_id="worker",
            type="team_message",
            payload=TeamMessage(
                message_id="message_2",
                team_id="team_2",
                from_agent_id="leader_2",
                to_agent_id="worker",
                content="Find source B.",
                kind="instruction",
                created_at=2.0,
            ),
            created_at=2.0,
            correlation_id="message_2",
        ),
    )
    team_1_delivery_id = send_team_message(inbox)
    team_1_agent = RecordingAgent()
    team_2_agent = RecordingAgent()
    runner = TeamWorkerRunner(
        session_provider=provider,
        agent_provider=MapAgentProvider(
            {
                "session_worker": team_1_agent,
                "session_worker_team_2": team_2_agent,
            },
        ),
        message_queue=inbox,
    )

    team_1_results = run_pending(runner, team_id="team_1")
    team_2_results = run_pending(runner, team_id="team_2")

    assert [result.delivery_id for result in team_1_results] == [
        team_1_delivery_id,
    ]
    assert team_1_agent.continuations == 1
    assert [result.delivery_id for result in team_2_results] == [
        team_2_delivery_id,
    ]
    assert team_2_agent.continuations == 1
    assert inbox.ack("worker", team_1_delivery_id) is False
    assert inbox.ack("worker", team_2_delivery_id) is False


def test_team_worker_runner_requires_team_scoped_queue_collection() -> None:
    delivery = QueueDelivery(
        delivery_id="env_team_2",
        envelope=AgentEnvelope(
            envelope_id="env_team_2",
            from_agent_id="leader_2",
            to_agent_id="worker",
            type="team_message",
            payload=TeamMessage(
                message_id="message_2",
                team_id="team_2",
                from_agent_id="leader_2",
                to_agent_id="worker",
                content="Find source B.",
                kind="instruction",
                created_at=2.0,
            ),
            created_at=2.0,
            correlation_id="message_2",
        ),
    )
    agent = RecordingAgent()
    runner = TeamWorkerRunner(
        session_provider=make_session_provider(),
        agent_provider=MapAgentProvider({"session_worker": agent}),
        message_queue=TypeOnlyQueue(delivery),
    )

    with pytest.raises(TeamError, match="team-scoped"):
        run_pending(runner, team_id="team_1")
    assert agent.continuations == 0


def test_team_worker_runner_runs_continuation_and_acks_team_message() -> None:
    inbox = AgentInbox()
    inbox.create_inbox("worker")
    delivery_id = send_team_message(inbox)
    agent = RecordingAgent()
    runner = TeamWorkerRunner(
        session_provider=make_session_provider(),
        agent_provider=MapAgentProvider({"session_worker": agent}),
        message_queue=inbox,
    )

    results = run_pending(runner, team_id="team_1")

    assert len(results) == 1
    assert results[0].agent_id == "worker"
    assert results[0].session_id == "session_worker"
    assert results[0].delivery_id == delivery_id
    assert results[0].status == "completed"
    assert agent.continuations == 1
    assert inbox.ack("worker", delivery_id) is False


def test_team_worker_runner_publishes_completed_ui_event() -> None:
    inbox = AgentInbox()
    inbox.create_inbox("worker")
    delivery_id = send_team_message(inbox)
    agent = RecordingAgent()
    ui_stream = InMemoryTeamUiStreamStore()
    runner = TeamWorkerRunner(
        session_provider=make_session_provider(),
        agent_provider=MapAgentProvider({"session_worker": agent}),
        message_queue=inbox,
        ui_stream=ui_stream,
    )

    results = run_pending(runner, team_id="team_1")

    events = ui_stream.list_events("team_1")
    assert len(results) == 1
    assert [event.kind for event in events] == ["worker_run_completed"]
    assert events[0].payload["delivery_id"] == delivery_id
    assert events[0].payload["agent_id"] == "worker"
    assert events[0].payload["session_id"] == "session_worker"
    assert events[0].payload["status"] == "completed"


def test_team_worker_runner_ignores_non_team_message_deliveries() -> None:
    inbox = AgentInbox()
    inbox.create_inbox("worker")
    delivery_id = inbox.send(
        AgentEnvelope(
            envelope_id="env_task_1",
            from_agent_id="leader",
            to_agent_id="worker",
            type="task_request",
            payload=TaskRequest(task_id="task_1", instruction="Do work"),
            created_at=2.0,
        ),
    )
    agent = RecordingAgent()
    runner = TeamWorkerRunner(
        session_provider=make_session_provider(),
        agent_provider=MapAgentProvider({"session_worker": agent}),
        message_queue=inbox,
    )

    assert run_pending(runner, team_id="team_1") == []
    assert agent.continuations == 0
    assert inbox.ack("worker", delivery_id) is True


def test_team_worker_runner_records_failure_and_leaves_delivery_unacked() -> None:
    inbox = AgentInbox()
    inbox.create_inbox("worker")
    delivery_id = send_team_message(inbox)
    agent = RecordingAgent(fail=True)
    runner = TeamWorkerRunner(
        session_provider=make_session_provider(),
        agent_provider=MapAgentProvider({"session_worker": agent}),
        message_queue=inbox,
    )

    results = run_pending(runner, team_id="team_1")

    assert len(results) == 1
    assert results[0].status == "failed"
    assert results[0].error == "worker failed"
    assert runner.errors()[0].delivery_id == delivery_id
    assert agent.continuations == 1
    assert inbox.ack("worker", delivery_id) is True


def test_team_worker_runner_schedules_retry_and_skips_until_due() -> None:
    clock = MutableClock(10.0)
    inbox = AgentInbox()
    inbox.create_inbox("worker")
    delivery_id = send_team_message(inbox)
    agent = RecordingAgent(fail=True)
    retry_store = InMemoryTeamWorkerRetryStore()
    runner = TeamWorkerRunner(
        session_provider=make_session_provider(),
        agent_provider=MapAgentProvider({"session_worker": agent}),
        message_queue=inbox,
        retry_policy=TeamWorkerRetryPolicy(
            max_attempts=3,
            backoff_seconds=5.0,
        ),
        retry_store=retry_store,
        clock=clock,
    )

    failed = run_pending(runner, team_id="team_1")
    skipped = run_pending(runner, team_id="team_1")

    record = retry_store.get("worker", delivery_id)
    assert len(failed) == 1
    assert failed[0].status == "failed"
    assert failed[0].retry_status == "scheduled"
    assert failed[0].attempt == 1
    assert failed[0].next_run_at == 15.0
    assert len(skipped) == 1
    assert skipped[0].status == "retry_skipped"
    assert skipped[0].retry_status == "scheduled"
    assert record is not None
    assert record.attempts == 1
    assert record.next_run_at == 15.0
    assert agent.continuations == 1
    assert inbox.ack("worker", delivery_id) is True


def test_team_worker_runner_retries_due_delivery_and_clears_on_success() -> None:
    clock = MutableClock(10.0)
    inbox = AgentInbox()
    inbox.create_inbox("worker")
    delivery_id = send_team_message(inbox)
    agent = RecordingAgent(fail=True)
    retry_store = InMemoryTeamWorkerRetryStore()
    runner = TeamWorkerRunner(
        session_provider=make_session_provider(),
        agent_provider=MapAgentProvider({"session_worker": agent}),
        message_queue=inbox,
        retry_policy=TeamWorkerRetryPolicy(
            max_attempts=3,
            backoff_seconds=5.0,
        ),
        retry_store=retry_store,
        clock=clock,
    )

    run_pending(runner, team_id="team_1")
    agent.fail = False
    clock.now = 15.0
    retried = run_pending(runner, team_id="team_1")

    assert len(retried) == 1
    assert retried[0].status == "completed"
    assert retried[0].retry_status == "cleared"
    assert retried[0].attempt == 2
    assert retry_store.get("worker", delivery_id) is None
    assert agent.continuations == 2
    assert inbox.ack("worker", delivery_id) is False


def test_team_worker_runner_marks_exhausted_after_max_attempts() -> None:
    clock = MutableClock(10.0)
    inbox = AgentInbox()
    inbox.create_inbox("worker")
    delivery_id = send_team_message(inbox)
    agent = RecordingAgent(fail=True)
    retry_store = InMemoryTeamWorkerRetryStore()
    runner = TeamWorkerRunner(
        session_provider=make_session_provider(),
        agent_provider=MapAgentProvider({"session_worker": agent}),
        message_queue=inbox,
        retry_policy=TeamWorkerRetryPolicy(
            max_attempts=2,
            backoff_seconds=5.0,
            ack_exhausted=True,
        ),
        retry_store=retry_store,
        clock=clock,
    )

    run_pending(runner, team_id="team_1")
    clock.now = 15.0
    exhausted = run_pending(runner, team_id="team_1")

    record = retry_store.get("worker", delivery_id)
    assert len(exhausted) == 1
    assert exhausted[0].status == "failed"
    assert exhausted[0].retry_status == "exhausted"
    assert exhausted[0].attempt == 2
    assert record is not None
    assert record.status == "exhausted"
    assert record.exhausted_at == 15.0
    assert agent.continuations == 2
    assert inbox.ack("worker", delivery_id) is False


def test_team_worker_runner_cancels_queued_delivery_before_continuation() -> None:
    inbox = AgentInbox()
    inbox.create_inbox("worker")
    delivery_id = send_team_message(inbox)
    agent = RecordingAgent()
    cancellation_store = InMemoryTeamWorkerCancellationStore()
    cancellation_store.request_cancel(
        TeamWorkerCancellationRecord(
            team_id="team_1",
            agent_id="worker",
            session_id="session_worker",
            reason="leader cancelled",
            requested_at=3.0,
            delivery_id=delivery_id,
        ),
    )
    runner = TeamWorkerRunner(
        session_provider=make_session_provider(),
        agent_provider=MapAgentProvider({"session_worker": agent}),
        message_queue=inbox,
        cancellation_store=cancellation_store,
    )

    results = run_pending(runner, team_id="team_1")
    record = cancellation_store.list_records()[0]

    assert len(results) == 1
    assert results[0].status == "cancelled"
    assert results[0].cancellation_status == "acknowledged"
    assert results[0].error == "leader cancelled"
    assert record.status == "acknowledged"
    assert record.acknowledged_at is not None
    assert agent.continuations == 0
    assert inbox.ack("worker", delivery_id) is False


def test_team_worker_runner_worker_scope_cancel_stays_requested() -> None:
    inbox = AgentInbox()
    inbox.create_inbox("worker")
    delivery_id = send_team_message(inbox)
    agent = RecordingAgent()
    cancellation_store = InMemoryTeamWorkerCancellationStore()
    cancellation_store.request_cancel(
        TeamWorkerCancellationRecord(
            team_id="team_1",
            agent_id="worker",
            session_id="session_worker",
            reason="worker paused",
            requested_at=3.0,
        ),
    )
    runner = TeamWorkerRunner(
        session_provider=make_session_provider(),
        agent_provider=MapAgentProvider({"session_worker": agent}),
        message_queue=inbox,
        cancellation_store=cancellation_store,
    )

    results = run_pending(runner, team_id="team_1")
    record = cancellation_store.list_records()[0]

    assert len(results) == 1
    assert results[0].status == "cancelled"
    assert results[0].cancellation_status == "requested"
    assert record.status == "requested"
    assert agent.continuations == 0
    assert inbox.ack("worker", delivery_id) is False


def test_team_worker_runner_cancels_due_retry_and_clears_retry_record() -> None:
    clock = MutableClock(10.0)
    inbox = AgentInbox()
    inbox.create_inbox("worker")
    delivery_id = send_team_message(inbox)
    agent = RecordingAgent(fail=True)
    retry_store = InMemoryTeamWorkerRetryStore()
    cancellation_store = InMemoryTeamWorkerCancellationStore()
    runner = TeamWorkerRunner(
        session_provider=make_session_provider(),
        agent_provider=MapAgentProvider({"session_worker": agent}),
        message_queue=inbox,
        retry_policy=TeamWorkerRetryPolicy(
            max_attempts=3,
            backoff_seconds=5.0,
        ),
        retry_store=retry_store,
        cancellation_store=cancellation_store,
        clock=clock,
    )

    run_pending(runner, team_id="team_1")
    cancellation_store.request_cancel(
        TeamWorkerCancellationRecord(
            team_id="team_1",
            agent_id="worker",
            session_id="session_worker",
            reason="retry cancelled",
            requested_at=12.0,
            delivery_id=delivery_id,
        ),
    )
    clock.now = 12.0
    cancelled = run_pending(runner, team_id="team_1")

    assert len(cancelled) == 1
    assert cancelled[0].status == "cancelled"
    assert cancelled[0].cancellation_status == "acknowledged"
    assert retry_store.get("worker", delivery_id) is None
    assert agent.continuations == 1
    assert inbox.ack("worker", delivery_id) is False


def test_team_worker_runner_publishes_failed_retry_skipped_and_cancelled_ui_events() -> None:
    clock = MutableClock(10.0)
    inbox = AgentInbox()
    inbox.create_inbox("worker")
    delivery_id = send_team_message(inbox)
    agent = RecordingAgent(fail=True)
    retry_store = InMemoryTeamWorkerRetryStore()
    cancellation_store = InMemoryTeamWorkerCancellationStore()
    ui_stream = InMemoryTeamUiStreamStore()
    runner = TeamWorkerRunner(
        session_provider=make_session_provider(),
        agent_provider=MapAgentProvider({"session_worker": agent}),
        message_queue=inbox,
        retry_policy=TeamWorkerRetryPolicy(
            max_attempts=3,
            backoff_seconds=5.0,
        ),
        retry_store=retry_store,
        cancellation_store=cancellation_store,
        ui_stream=ui_stream,
        clock=clock,
    )

    failed = run_pending(runner, team_id="team_1")
    skipped = run_pending(runner, team_id="team_1")
    cancellation_store.request_cancel(
        TeamWorkerCancellationRecord(
            team_id="team_1",
            agent_id="worker",
            session_id="session_worker",
            reason="retry cancelled",
            requested_at=12.0,
            delivery_id=delivery_id,
        ),
    )
    cancelled = run_pending(runner, team_id="team_1")

    events = ui_stream.list_events("team_1")
    assert [result.status for result in failed + skipped + cancelled] == [
        "failed",
        "retry_skipped",
        "cancelled",
    ]
    assert [event.kind for event in events] == [
        "worker_run_failed",
        "worker_run_retry_skipped",
        "worker_run_cancelled",
    ]
    assert events[0].payload["retry_status"] == "scheduled"
    assert events[1].payload["next_run_at"] == 15.0
    assert events[2].payload["cancellation_status"] == "acknowledged"
