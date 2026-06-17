from __future__ import annotations

import threading
from pathlib import Path

from agentos.channels.a2a_operations import (
    A2APushNotificationConfig,
    A2APushNotificationDaemonError,
    A2APushNotificationDaemonState,
    A2APushNotificationDeliveryRecord,
    A2ATask,
    A2ATaskSubscriptionEvent,
)


def push_record(delivery_id: str = "delivery_1") -> A2APushNotificationDeliveryRecord:
    return A2APushNotificationDeliveryRecord(
        delivery_id=delivery_id,
        task_id="task_1",
        config=A2APushNotificationConfig(
            config_id="cfg_1",
            url="https://client.example/webhook",
        ),
        event=A2ATaskSubscriptionEvent(
            event_id="7",
            task=A2ATask(task_id="task_1", context_id="ctx_1", state="completed"),
        ),
        created_at=1.0,
        next_run_at=1.0,
        status="delivered",
    )


class RecordingPushWorker:
    def __init__(
        self,
        batches: list[tuple[A2APushNotificationDeliveryRecord, ...]] | None = None,
    ) -> None:
        self.batches = batches or []
        self.calls = 0
        self.now_values: list[float | None] = []
        self.worker_ids: list[str] = []
        self.limits: list[int] = []
        self.second_call = threading.Event()

    def run_pending(
        self,
        *,
        now: float | None = None,
        worker_id: str = "a2a-push-worker",
        limit: int = 10,
    ) -> tuple[A2APushNotificationDeliveryRecord, ...]:
        self.calls += 1
        self.now_values.append(now)
        self.worker_ids.append(worker_id)
        self.limits.append(limit)
        if self.calls >= 2:
            self.second_call.set()
        if self.batches:
            return self.batches.pop(0)
        return ()


class FailingThenRecoveringPushWorker(RecordingPushWorker):
    def run_pending(
        self,
        *,
        now: float | None = None,
        worker_id: str = "a2a-push-worker",
        limit: int = 10,
    ) -> tuple[A2APushNotificationDeliveryRecord, ...]:
        self.calls += 1
        self.now_values.append(now)
        self.worker_ids.append(worker_id)
        self.limits.append(limit)
        if self.calls == 1:
            raise RuntimeError("transient backend outage")
        if self.calls >= 2:
            self.second_call.set()
        return (push_record("delivery_recovered"),)


def test_a2a_push_notification_daemon_run_once_records_results() -> None:
    from agentos.channels.a2a_operations import A2APushNotificationDaemon

    worker = RecordingPushWorker(batches=[(push_record(),)])
    daemon = A2APushNotificationDaemon(
        worker=worker,
        worker_id="push-worker-a",
        poll_interval_seconds=0.01,
        batch_limit=3,
        clock=lambda: 10.0,
    )

    results = daemon.run_once()
    state = daemon.state()

    assert results == (push_record(),)
    assert worker.now_values == [10.0]
    assert worker.worker_ids == ["push-worker-a"]
    assert worker.limits == [3]
    assert state.status == "idle"
    assert state.worker_id == "push-worker-a"
    assert state.iterations == 1
    assert state.last_run_at == 10.0
    assert state.last_results == (push_record(),)
    assert state.errors == ()


def test_a2a_push_notification_daemon_start_stop_and_join_runs_until_stopped() -> None:
    from agentos.channels.a2a_operations import A2APushNotificationDaemon

    worker = RecordingPushWorker(
        batches=[(push_record("delivery_1"),), (push_record("delivery_2"),)],
    )
    daemon = A2APushNotificationDaemon(
        worker=worker,
        worker_id="push-worker-a",
        poll_interval_seconds=0.001,
    )

    daemon.start()
    assert worker.second_call.wait(timeout=1.0)
    assert daemon.is_running() is True
    daemon.stop()

    assert daemon.join(timeout=1.0) is True
    state = daemon.state()
    assert state.status == "stopped"
    assert state.iterations >= 2
    assert worker.worker_ids[:2] == ["push-worker-a", "push-worker-a"]
    assert daemon.is_running() is False


def test_a2a_push_notification_daemon_records_errors_and_keeps_polling() -> None:
    from agentos.channels.a2a_operations import A2APushNotificationDaemon

    worker = FailingThenRecoveringPushWorker()
    daemon = A2APushNotificationDaemon(
        worker=worker,
        worker_id="push-worker-a",
        poll_interval_seconds=0.001,
    )

    daemon.start()
    assert worker.second_call.wait(timeout=1.0)
    daemon.stop()
    assert daemon.join(timeout=1.0) is True
    state = daemon.state()

    assert state.iterations >= 2
    assert state.last_results == (push_record("delivery_recovered"),)
    assert len(state.errors) == 1
    assert state.errors[0].worker_id == "push-worker-a"
    assert state.errors[0].error == "transient backend outage"
    assert state.errors[0].raised_at is not None


def test_a2a_push_notification_health_policy_classifies_unstarted_state() -> None:
    from agentos.channels.a2a_operations import A2APushNotificationHealthPolicy

    report = A2APushNotificationHealthPolicy().evaluate(
        A2APushNotificationDaemonState(
            status="idle",
            worker_id="push-worker-a",
            poll_interval_seconds=0.5,
            batch_limit=10,
        ),
        now=100.0,
    )

    assert report.status == "unstarted"
    assert report.reason == "worker has not run yet"
    assert report.worker_id == "push-worker-a"
    assert report.iterations == 0
    assert report.seconds_since_last_run is None
    assert report.recent_error_count == 0


def test_a2a_push_notification_health_policy_reports_healthy_recent_run() -> None:
    from agentos.channels.a2a_operations import A2APushNotificationHealthPolicy

    report = A2APushNotificationHealthPolicy(
        max_stale_seconds=30.0,
    ).evaluate(
        A2APushNotificationDaemonState(
            status="running",
            worker_id="push-worker-a",
            poll_interval_seconds=0.5,
            batch_limit=10,
            iterations=3,
            last_run_at=95.0,
        ),
        now=100.0,
    )

    assert report.status == "healthy"
    assert report.reason == "worker is polling"
    assert report.seconds_since_last_run == 5.0
    assert report.daemon_status == "running"


def test_a2a_push_notification_health_policy_marks_stale_worker_unhealthy() -> None:
    from agentos.channels.a2a_operations import A2APushNotificationHealthPolicy

    report = A2APushNotificationHealthPolicy(
        max_stale_seconds=10.0,
    ).evaluate(
        A2APushNotificationDaemonState(
            status="running",
            worker_id="push-worker-a",
            poll_interval_seconds=0.5,
            batch_limit=10,
            iterations=3,
            last_run_at=80.0,
        ),
        now=100.0,
    )

    assert report.status == "unhealthy"
    assert report.reason == "worker polling is stale"
    assert report.seconds_since_last_run == 20.0


def test_a2a_push_notification_health_policy_marks_recent_errors_degraded() -> None:
    from agentos.channels.a2a_operations import A2APushNotificationHealthPolicy

    report = A2APushNotificationHealthPolicy(
        degraded_error_threshold=1,
        unhealthy_error_threshold=3,
        error_window_seconds=60.0,
    ).evaluate(
        A2APushNotificationDaemonState(
            status="running",
            worker_id="push-worker-a",
            poll_interval_seconds=0.5,
            batch_limit=10,
            iterations=3,
            last_run_at=99.0,
            errors=(
                A2APushNotificationDaemonError(
                    worker_id="push-worker-a",
                    error="transient backend outage",
                    raised_at=95.0,
                ),
            ),
        ),
        now=100.0,
    )

    assert report.status == "degraded"
    assert report.reason == "worker has recent polling errors"
    assert report.recent_error_count == 1
    assert report.last_error == "transient backend outage"


def test_a2a_push_notification_health_policy_marks_many_errors_unhealthy() -> None:
    from agentos.channels.a2a_operations import A2APushNotificationHealthPolicy

    report = A2APushNotificationHealthPolicy(
        degraded_error_threshold=1,
        unhealthy_error_threshold=2,
        error_window_seconds=60.0,
    ).evaluate(
        A2APushNotificationDaemonState(
            status="running",
            worker_id="push-worker-a",
            poll_interval_seconds=0.5,
            batch_limit=10,
            iterations=4,
            last_run_at=99.0,
            errors=(
                A2APushNotificationDaemonError(
                    worker_id="push-worker-a",
                    error="first failure",
                    raised_at=90.0,
                ),
                A2APushNotificationDaemonError(
                    worker_id="push-worker-a",
                    error="second failure",
                    raised_at=95.0,
                ),
                A2APushNotificationDaemonError(
                    worker_id="push-worker-a",
                    error="old failure",
                    raised_at=1.0,
                ),
            ),
        ),
        now=100.0,
    )

    assert report.status == "unhealthy"
    assert report.reason == "worker has too many recent polling errors"
    assert report.recent_error_count == 2
    assert report.last_error == "second failure"


def test_a2a_push_notification_health_policy_reports_stopped_state() -> None:
    from agentos.channels.a2a_operations import A2APushNotificationHealthPolicy

    report = A2APushNotificationHealthPolicy().evaluate(
        A2APushNotificationDaemonState(
            status="stopped",
            worker_id="push-worker-a",
            poll_interval_seconds=0.5,
            batch_limit=10,
            iterations=2,
            last_run_at=95.0,
        ),
        now=100.0,
    )

    assert report.status == "stopped"
    assert report.reason == "worker is stopped"


def test_a2a_push_notification_daemon_health_delegates_to_policy() -> None:
    from agentos.channels.a2a_operations import (
        A2APushNotificationDaemon,
        A2APushNotificationHealthPolicy,
    )

    daemon = A2APushNotificationDaemon(
        worker=RecordingPushWorker(),
        worker_id="push-worker-a",
        clock=lambda: 50.0,
    )

    report = daemon.health(
        policy=A2APushNotificationHealthPolicy(max_stale_seconds=10.0),
        now=50.0,
    )

    assert report.status == "unstarted"
    assert report.worker_id == "push-worker-a"


def test_a2a_push_notification_deployment_profile_serializes_health_payload() -> None:
    from agentos.channels.a2a_operations import (
        A2APushNotificationDaemon,
        A2APushNotificationDeploymentProfile,
        A2APushNotificationHealthPolicy,
    )

    daemon = A2APushNotificationDaemon(
        worker=RecordingPushWorker(batches=[(push_record(),)]),
        worker_id="push-worker-a",
        clock=lambda: 100.0,
    )
    daemon.run_once()
    profile = A2APushNotificationDeploymentProfile(
        daemon=daemon,
        policy=A2APushNotificationHealthPolicy(max_stale_seconds=30.0),
        probe_name="a2a_push_worker",
    )

    payload = profile.health_payload(now=105.0)
    health_check = profile.health_check(now=105.0)
    readiness_check = profile.readiness_check(now=105.0)
    metadata = profile.readiness_metadata()

    assert payload == {
        "status": "healthy",
        "ok": True,
        "reason": "worker is polling",
        "worker_id": "push-worker-a",
        "daemon_status": "idle",
        "iterations": 1,
        "last_run_at": 100.0,
        "seconds_since_last_run": 5.0,
        "recent_error_count": 0,
        "last_error": None,
    }
    assert health_check["status"] == "healthy"
    assert health_check["ok"] is True
    assert readiness_check["status"] == "ok"
    assert readiness_check["health_status"] == "healthy"
    assert readiness_check["ok"] is True
    assert metadata["probe_name"] == "a2a_push_worker"
    assert metadata["worker_id"] == "push-worker-a"
    assert metadata["ready_statuses"] == ("healthy",)
    assert "process supervision" in metadata["deployment_owned"]


def test_a2a_push_notification_deployment_profile_maps_degraded_readiness() -> None:
    from agentos.channels.a2a_operations import (
        A2APushNotificationDaemonError,
        A2APushNotificationDaemonState,
        A2APushNotificationDeploymentProfile,
        A2APushNotificationHealthPolicy,
        A2APushNotificationHealthReport,
    )

    class DegradedDaemon:
        worker_id = "push-worker-a"

        def health(
            self,
            *,
            policy: A2APushNotificationHealthPolicy,
            now: float | None = None,
        ) -> A2APushNotificationHealthReport:
            return policy.evaluate(
                A2APushNotificationDaemonState(
                    status="running",
                    worker_id="push-worker-a",
                    poll_interval_seconds=0.5,
                    batch_limit=10,
                    iterations=2,
                    last_run_at=99.0,
                    errors=(
                        A2APushNotificationDaemonError(
                            worker_id="push-worker-a",
                            error="transient backend outage",
                            raised_at=95.0,
                        ),
                    ),
                ),
                now=now,
            )

    daemon = DegradedDaemon()
    policy = A2APushNotificationHealthPolicy(
        degraded_error_threshold=1,
        unhealthy_error_threshold=3,
        error_window_seconds=60.0,
    )
    profile = A2APushNotificationDeploymentProfile(
        daemon=daemon,
        policy=policy,
    )

    default_readiness = profile.readiness_check(now=100.0)
    permissive_profile = A2APushNotificationDeploymentProfile(
        daemon=daemon,
        policy=policy,
        ready_statuses=("healthy", "degraded"),
    )
    permissive_readiness = permissive_profile.readiness_check(now=100.0)

    assert default_readiness["status"] == "failed"
    assert default_readiness["health_status"] == "degraded"
    assert default_readiness["ok"] is False
    assert permissive_readiness["status"] == "ok"
    assert permissive_readiness["health_status"] == "degraded"
    assert permissive_readiness["ok"] is True


def test_runtime_loops_do_not_import_a2a_push_notification_daemon() -> None:
    project_root = Path(__file__).resolve().parents[2]

    for path in [
        project_root / "src" / "agentos" / "runtime" / "query_loop.py",
        project_root / "src" / "agentos" / "runtime" / "async_query_loop.py",
    ]:
        text = path.read_text(encoding="utf-8")
        assert "A2APushNotificationDaemon" not in text
        assert "A2APushNotificationDaemonState" not in text
        assert "A2APushNotificationDaemonStatus" not in text
        assert "A2APushNotificationHealthPolicy" not in text
        assert "A2APushNotificationHealthReport" not in text
