from __future__ import annotations

import asyncio
from threading import Event, Thread
import time

from agentos.channels.asgi import AsgiAgentApp
from agentos.channels.session import InMemoryAgentSessionProvider
from agentos.multi import ExpertAgentRunner, SpawnExecutor, TaskRecord, TaskRequest, TaskResult, TaskTable
from tests.channels.test_asgi_app import call_asgi
from tests.multi.helpers import build_agent_with_response


def test_spawn_executor_shutdown_cancels_pending_tasks_after_timeout() -> None:
    executor = SpawnExecutor(max_workers=1)
    started = Event()
    release = Event()

    def slow() -> TaskResult:
        started.set()
        release.wait(timeout=1)
        return TaskResult(task_id="task_1", status="completed", summary="slow")

    def pending() -> TaskResult:
        return TaskResult(task_id="task_2", status="completed", summary="pending")

    first = executor.submit("task_1", slow)
    second = executor.submit("task_2", pending)
    assert started.wait(timeout=1)

    executor.shutdown(timeout_seconds=0.01)
    release.set()

    assert not first.cancelled()
    assert second.cancelled()


def test_expert_runner_stop_waits_for_current_run_once() -> None:
    finished = Event()

    class BlockingInbox:
        def wait(self, agent_id: str, timeout: float | None = None) -> bool:
            return True

        def collect(self, agent_id: str) -> list[object]:
            time.sleep(0.03)
            finished.set()
            return []

        def ack(self, agent_id: str, delivery_id: str) -> bool:
            return True

    class Coordinator:
        inbox = BlockingInbox()

        def execute_expert_envelope(self, envelope: object) -> None:
            return None

    runner = ExpertAgentRunner(coordinator=Coordinator(), agent_id="expert")  # type: ignore[arg-type]
    thread = Thread(target=runner.run_forever, kwargs={"timeout": 0.01})
    thread.start()

    assert runner.stop(timeout_seconds=1)
    thread.join(timeout=1)

    assert finished.is_set()
    assert not thread.is_alive()


def test_asgi_lifespan_shutdown_runs_registered_callbacks() -> None:
    stopped: list[bool] = []
    app = AsgiAgentApp(
        sessions=InMemoryAgentSessionProvider(lambda session_id: build_agent_with_response("ok")),
        shutdown_handlers=[lambda: stopped.append(True)],
    )

    sent = asyncio.run(
        call_asgi(
            app,
            method="GET",
            path="/",
            scope_type="lifespan",
            receive_after_body=[
                {"type": "lifespan.startup"},
                {"type": "lifespan.shutdown"},
            ],
        ),
    )

    assert [message["type"] for message in sent] == [
        "lifespan.startup.complete",
        "lifespan.shutdown.complete",
    ]
    assert stopped == [True]


def test_task_table_releases_running_leases_for_shutdown() -> None:
    table = TaskTable()
    table.create(
        TaskRecord(
            task_id="task_1",
            mode="dispatch",
            parent_agent_id="parent",
            target_agent_id="expert",
            request=TaskRequest(task_id="task_1", instruction="work"),
            status="queued",
            created_at=1,
            deadline_at=100,
        ),
    )
    table.claim_queued(
        worker_id="worker_1",
        capabilities=(),
        limit=1,
        lease_expires_at=50,
        now=2,
    )

    released = table.release_running_leases(worker_id="worker_1", now=3)

    assert released == 1
    record = table.get("task_1")
    assert record is not None
    assert record.status == "queued"
    assert record.worker_id is None
    assert record.lease_expires_at is None


def test_asgi_lifespan_reports_shutdown_failure() -> None:
    app = AsgiAgentApp(
        sessions=InMemoryAgentSessionProvider(lambda session_id: build_agent_with_response("ok")),
        shutdown_handlers=[lambda: (_ for _ in ()).throw(RuntimeError("boom"))],
    )

    sent = asyncio.run(
        call_asgi(
            app,
            method="GET",
            path="/",
            scope_type="lifespan",
            receive_after_body=[
                {"type": "lifespan.shutdown"},
            ],
        ),
    )

    assert sent[0]["type"] == "lifespan.shutdown.failed"
    assert "boom" in str(sent[0]["message"])
