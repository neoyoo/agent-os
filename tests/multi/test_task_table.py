from agentos.multi import TaskRecord, TaskRequest, TaskResult, TaskTable


def record(
    task_id: str = "task_1",
    *,
    status: str = "queued",
    deadline_at: float = 10.0,
) -> TaskRecord:
    return TaskRecord(
        task_id=task_id,
        mode="spawn",
        parent_agent_id="parent",
        target_agent_id="child",
        request=TaskRequest(
            task_id=task_id,
            instruction="Do work",
        ),
        status=status,  # type: ignore[arg-type]
        created_at=1.0,
        deadline_at=deadline_at,
    )


def result(task_id: str = "task_1", status: str = "completed") -> TaskResult:
    return TaskResult(
        task_id=task_id,
        status=status,  # type: ignore[arg-type]
        summary=f"{status} result",
    )


def test_task_table_tracks_queued_running_and_completed() -> None:
    table = TaskTable()

    handle = table.create(record())

    assert handle.status == "queued"
    assert table.mark_running("task_1") is True
    assert table.mark_completed("task_1", result()) is True
    stored = table.get("task_1")
    assert stored is not None
    assert stored.completed_at is not None
    assert stored.completed_at >= stored.created_at
    assert table.active_for_agent("parent") == [
        handle.__class__(
            task_id="task_1",
            mode="spawn",
            target_agent_id="child",
            status="completed",
        ),
    ]
    assert table.completed_for_agent("parent") == [result()]


def test_task_table_consumes_completed_results_once() -> None:
    table = TaskTable()
    table.create(record())
    table.mark_running("task_1")
    table.mark_completed("task_1", result())

    assert table.consume_results_for_agent("parent") == [result()]
    assert table.consume_results_for_agent("parent") == []

    stored = table.get("task_1")
    assert stored is not None
    assert stored.consumed_at is not None
    assert stored.consumed_at >= stored.completed_at  # type: ignore[operator]


def test_task_table_cancels_queued_task_and_stores_late_result() -> None:
    table = TaskTable()
    table.create(record())

    assert table.mark_cancelled("task_1", result(status="cancelled")) is True
    assert table.mark_completed("task_1", result(status="completed")) is False
    assert table.store_late_result("task_1", result(status="completed")) is True

    stored = table.get("task_1")
    assert stored is not None
    assert stored.status == "cancelled"
    assert stored.result == result(status="cancelled")
    assert stored.late_result == result(status="completed")


def test_task_table_finds_and_marks_due_timeouts() -> None:
    table = TaskTable()
    table.create(record("task_due", deadline_at=5.0))
    table.create(record("task_later", deadline_at=15.0))

    due = table.due_timeouts(now=10.0)

    assert [task.task_id for task in due] == ["task_due"]
    assert table.mark_timed_out("task_due", result("task_due", "timeout")) is True
    assert table.mark_completed("task_due", result("task_due", "completed")) is False

    stored = table.get("task_due")
    assert stored is not None
    assert stored.status == "timeout"
    assert stored.result == result("task_due", "timeout")
