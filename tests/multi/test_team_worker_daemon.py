from __future__ import annotations

import threading
from pathlib import Path

from agentos.multi.team import (
    InMemoryTeamWorkerCancellationStore,
    InMemoryTeamWorkerRetryStore,
    TeamWorkerCancellationRecord,
    TeamWorkerDaemon,
    TeamWorkerRetryRecord,
    TeamWorkerRunError,
    TeamWorkerRunResult,
)


class RecordingRunner:
    def __init__(
        self,
        batches: list[list[TeamWorkerRunResult]] | None = None,
        errors: tuple[TeamWorkerRunError, ...] = (),
    ) -> None:
        self.batches = batches or []
        self.recorded_errors = errors
        self.team_ids: list[str | None] = []
        self.calls = 0
        self.second_call = threading.Event()

    def run_pending(
        self,
        team_id: str | None = None,
    ) -> list[TeamWorkerRunResult]:
        self.calls += 1
        self.team_ids.append(team_id)
        if self.calls >= 2:
            self.second_call.set()
        if self.batches:
            return self.batches.pop(0)
        return []

    def errors(self) -> tuple[TeamWorkerRunError, ...]:
        return self.recorded_errors

    def retry_records(self) -> tuple[TeamWorkerRetryRecord, ...]:
        return ()

    def cancellation_records(self) -> tuple[TeamWorkerCancellationRecord, ...]:
        return ()


def completed_result(delivery_id: str = "delivery_1") -> TeamWorkerRunResult:
    return TeamWorkerRunResult(
        team_id="team_1",
        agent_id="worker",
        session_id="session_worker",
        delivery_id=delivery_id,
        status="completed",
        message_id="message_1",
    )


def failed_result() -> TeamWorkerRunResult:
    return TeamWorkerRunResult(
        team_id="team_1",
        agent_id="worker",
        session_id="session_worker",
        delivery_id="delivery_failed",
        status="failed",
        message_id="message_failed",
        error="worker failed",
    )


def run_error() -> TeamWorkerRunError:
    return TeamWorkerRunError(
        team_id="team_1",
        agent_id="worker",
        session_id="session_worker",
        delivery_id="delivery_failed",
        error="worker failed",
    )


def test_team_worker_daemon_run_once_records_results_and_errors() -> None:
    runner = RecordingRunner(
        batches=[[completed_result()]],
        errors=(run_error(),),
    )
    daemon = TeamWorkerDaemon(
        runner=runner,
        team_id="team_1",
        poll_interval_seconds=0.01,
    )

    results = daemon.run_once()
    state = daemon.state()

    assert results == [completed_result()]
    assert runner.team_ids == ["team_1"]
    assert state.status == "idle"
    assert state.team_id == "team_1"
    assert state.iterations == 1
    assert state.last_results == (completed_result(),)
    assert state.errors == (run_error(),)
    assert state.last_run_at is not None


def test_team_worker_daemon_start_stop_and_join_runs_until_stopped() -> None:
    runner = RecordingRunner(
        batches=[[completed_result("delivery_1")], [completed_result("delivery_2")]],
    )
    daemon = TeamWorkerDaemon(
        runner=runner,
        team_id="team_1",
        poll_interval_seconds=0.001,
    )

    daemon.start()
    assert runner.second_call.wait(timeout=1.0)
    assert daemon.is_running() is True
    daemon.stop()

    assert daemon.join(timeout=1.0) is True
    state = daemon.state()
    assert state.status == "stopped"
    assert state.iterations >= 2
    assert runner.team_ids[:2] == ["team_1", "team_1"]
    assert daemon.is_running() is False


def test_team_worker_daemon_exposes_failed_worker_results() -> None:
    error = run_error()
    runner = RecordingRunner(
        batches=[[failed_result()]],
        errors=(error,),
    )
    daemon = TeamWorkerDaemon(runner=runner)

    daemon.run_once()
    state = daemon.state()

    assert state.last_results == (failed_result(),)
    assert state.errors == (error,)


def test_team_worker_daemon_state_exposes_retry_records() -> None:
    retry_store = InMemoryTeamWorkerRetryStore()
    retry_store.record_failure(
        retry_record := TeamWorkerRetryRecord(
            team_id="team_1",
            agent_id="worker",
            session_id="session_worker",
            delivery_id="delivery_failed",
            message_id="message_failed",
            attempts=1,
            status="scheduled",
            next_run_at=10.0,
            last_error="worker failed",
        ),
    )

    class RetryRunner(RecordingRunner):
        def retry_records(self) -> tuple[TeamWorkerRetryRecord, ...]:
            return retry_store.list_records()

    daemon = TeamWorkerDaemon(runner=RetryRunner())

    daemon.run_once()

    assert daemon.state().retry_records == (retry_record,)


def test_team_worker_daemon_state_exposes_cancellation_records() -> None:
    cancellation_store = InMemoryTeamWorkerCancellationStore()
    cancellation_store.request_cancel(
        cancellation_record := TeamWorkerCancellationRecord(
            team_id="team_1",
            agent_id="worker",
            session_id="session_worker",
            reason="worker paused",
            requested_at=3.0,
        ),
    )

    class CancellationRunner(RecordingRunner):
        def cancellation_records(self) -> tuple[TeamWorkerCancellationRecord, ...]:
            return cancellation_store.list_records()

    daemon = TeamWorkerDaemon(runner=CancellationRunner())

    daemon.run_once()

    assert daemon.state().cancellation_records == (cancellation_record,)


def test_runtime_loops_do_not_import_team_worker_daemon() -> None:
    project_root = Path(__file__).resolve().parents[2]

    for path in [
        project_root / "src" / "agentos" / "runtime" / "query_loop.py",
        project_root / "src" / "agentos" / "runtime" / "async_query_loop.py",
    ]:
        text = path.read_text(encoding="utf-8")
        assert "TeamWorkerDaemon" not in text
        assert "TeamWorkerDaemonState" not in text
        assert "TeamWorkerDaemonStatus" not in text
