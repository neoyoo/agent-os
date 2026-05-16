from agentos.multi import TaskRecord, TaskRequest, TaskResult, TaskTable


def record(
    task_id: str = "task_1",
    *,
    allowed_tool_names: tuple[str, ...] = ("code",),
) -> TaskRecord:
    return TaskRecord(
        task_id=task_id,
        mode="dispatch",
        parent_agent_id="parent",
        target_agent_id="code-worker",
        request=TaskRequest(
            task_id=task_id,
            instruction="Do work",
            allowed_tool_names=allowed_tool_names,
        ),
        status="queued",
        created_at=1.0,
        deadline_at=30.0,
    )


def result(task_id: str = "task_1", status: str = "completed") -> TaskResult:
    return TaskResult(
        task_id=task_id,
        status=status,  # type: ignore[arg-type]
        summary=f"{status} result",
    )


def test_task_table_claims_queued_task_with_worker_lease() -> None:
    store = TaskTable()
    store.create(record())

    claims = store.claim_queued(
        worker_id="worker-instance-1",
        capabilities=("code",),
        limit=1,
        lease_expires_at=20.0,
        now=2.0,
    )

    assert len(claims) == 1
    claimed = store.get("task_1")
    assert claimed is not None
    assert claimed.status == "running"
    assert claimed.worker_id == "worker-instance-1"
    assert claimed.lease_expires_at == 20.0
    assert claimed.attempt == 1
    assert claimed.updated_at == 2.0
    assert claimed.version == 1


def test_task_table_reclaims_expired_running_lease() -> None:
    store = TaskTable()
    store.create(record())
    store.claim_queued(
        worker_id="worker-instance-1",
        capabilities=("code",),
        limit=1,
        lease_expires_at=3.0,
        now=2.0,
    )

    claims = store.claim_queued(
        worker_id="worker-instance-2",
        capabilities=("code",),
        limit=1,
        lease_expires_at=8.0,
        now=4.0,
    )

    assert len(claims) == 1
    assert claims[0].worker_id == "worker-instance-2"
    assert claims[0].attempt == 2
    claimed = store.get("task_1")
    assert claimed is not None
    assert claimed.status == "running"
    assert claimed.worker_id == "worker-instance-2"
    assert claimed.lease_expires_at == 8.0
    assert claimed.attempt == 2
    assert claimed.updated_at == 4.0
    assert claimed.version == 2


def test_task_table_does_not_claim_when_capabilities_do_not_match() -> None:
    store = TaskTable()
    store.create(record(allowed_tool_names=("code", "web")))

    claims = store.claim_queued(
        worker_id="worker-instance-1",
        capabilities=("code",),
        limit=1,
        lease_expires_at=20.0,
        now=2.0,
    )

    assert claims == []
    assert store.get("task_1") == record(allowed_tool_names=("code", "web"))


def test_task_table_rejects_stale_worker_completion_after_reclaim() -> None:
    store = TaskTable()
    store.create(record())
    store.claim_queued(
        worker_id="worker-instance-1",
        capabilities=("code",),
        limit=1,
        lease_expires_at=3.0,
        now=2.0,
    )
    store.claim_queued(
        worker_id="worker-instance-2",
        capabilities=("code",),
        limit=1,
        lease_expires_at=8.0,
        now=4.0,
    )

    assert (
        store.mark_completed("task_1", result(), now=5.0)
        is False
    )
    assert (
        store.mark_completed(
            "task_1",
            result(),
            now=5.0,
            worker_id="worker-instance-1",
            attempt=1,
        )
        is False
    )
    assert (
        store.mark_completed(
            "task_1",
            result(),
            now=6.0,
            worker_id="worker-instance-2",
            attempt=2,
        )
        is True
    )


def test_task_table_does_not_reclaim_cancel_requested_running_task() -> None:
    store = TaskTable()
    store.create(record())
    store.claim_queued(
        worker_id="worker-instance-1",
        capabilities=("code",),
        limit=1,
        lease_expires_at=3.0,
        now=2.0,
    )
    assert store.request_cancel("task_1", now=2.5) is True

    claims = store.claim_queued(
        worker_id="worker-instance-2",
        capabilities=("code",),
        limit=1,
        lease_expires_at=8.0,
        now=4.0,
    )

    assert claims == []
    stored = store.get("task_1")
    assert stored is not None
    assert stored.worker_id == "worker-instance-1"
    assert stored.cancel_requested_at == 2.5


def test_task_table_running_cancel_is_request_then_ack() -> None:
    store = TaskTable()
    store.create(record())
    store.claim_queued(
        worker_id="worker-instance-1",
        capabilities=("code",),
        limit=1,
        lease_expires_at=20.0,
        now=2.0,
    )

    assert store.request_cancel("task_1", now=3.0) is True
    requested = store.get("task_1")
    assert requested is not None
    assert requested.status == "running"
    assert requested.cancel_requested_at == 3.0

    assert (
        store.ack_cancelled(
            "task_1",
            result(status="cancelled"),
            now=4.0,
            worker_id="worker-instance-1",
            attempt=1,
        )
        is True
    )
    cancelled = store.get("task_1")
    assert cancelled is not None
    assert cancelled.status == "cancelled"
    assert cancelled.result == result(status="cancelled")


def test_task_table_request_cancel_rejects_non_cancelled_terminal_tasks() -> None:
    store = TaskTable()
    store.create(record())
    store.claim_queued(
        worker_id="worker-instance-1",
        capabilities=("code",),
        limit=1,
        lease_expires_at=20.0,
        now=2.0,
    )
    store.mark_completed(
        "task_1",
        result(),
        now=3.0,
        worker_id="worker-instance-1",
        attempt=1,
    )

    assert store.request_cancel("task_1", now=4.0) is False


def test_task_table_marks_result_notified_once() -> None:
    store = TaskTable()
    store.create(record())
    store.claim_queued(
        worker_id="worker-instance-1",
        capabilities=("code",),
        limit=1,
        lease_expires_at=20.0,
        now=2.0,
    )
    store.mark_completed(
        "task_1",
        result(),
        now=3.0,
        worker_id="worker-instance-1",
        attempt=1,
    )

    assert store.mark_result_notified("task_1", now=4.0) is True
    assert store.mark_result_notified("task_1", now=5.0) is False
