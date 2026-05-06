from threading import Event

from agentos.multi import SpawnExecutor, TaskResult


def test_spawn_executor_respects_max_workers_queueing() -> None:
    executor = SpawnExecutor(max_workers=1)
    first_started = Event()
    release_first = Event()
    second_started = Event()

    def first() -> TaskResult:
        first_started.set()
        release_first.wait(timeout=1)
        return TaskResult(task_id="task_1", status="completed", summary="first")

    def second() -> TaskResult:
        second_started.set()
        return TaskResult(task_id="task_2", status="completed", summary="second")

    first_future = executor.submit("task_1", first)
    second_future = executor.submit("task_2", second)

    assert first_started.wait(timeout=1)
    assert not second_started.wait(timeout=0.05)

    release_first.set()

    assert first_future.result(timeout=1).summary == "first"
    assert second_future.result(timeout=1).summary == "second"
    assert second_started.is_set()

    executor.shutdown()
