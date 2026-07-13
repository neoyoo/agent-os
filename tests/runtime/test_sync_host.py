import asyncio
from collections.abc import Callable
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor
import inspect
import threading

import pytest

from agentos.runtime._sync_host import SyncHost
from agentos.runtime.errors import (
    SyncAdapterEventLoopError,
    SyncAgentClosedError,
)


async def _owner_identity() -> tuple[int, int, bool]:
    return (
        threading.get_ident(),
        id(asyncio.get_running_loop()),
        threading.current_thread().daemon,
    )


async def _never_started() -> None:
    return None


def _assert_coroutine_closed(coroutine: object) -> None:
    assert inspect.getcoroutinestate(coroutine) == inspect.CORO_CLOSED


def _wait_for_submission_rejection(host: SyncHost) -> list[Future[None]]:
    accepted: list[Future[None]] = []
    for _ in range(10_000):
        coroutine = _never_started()
        try:
            accepted.append(host.submit(coroutine))
        except SyncAgentClosedError:
            _assert_coroutine_closed(coroutine)
            return accepted
    raise AssertionError("close did not reject a submission")


def _call_bounded(call: Callable[[], object]) -> object | BaseException:
    outcomes: list[object | BaseException] = []
    def run() -> None:
        try:
            outcomes.append(call())
        except BaseException as error:
            outcomes.append(error)
    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(timeout=2)
    assert not worker.is_alive()
    return outcomes[0]


def _close_host_bounded(host: SyncHost) -> None:
    outcome = _call_bounded(host.close)
    if isinstance(outcome, BaseException):
        raise outcome


def test_sync_host_reuses_one_daemon_owner_thread_and_runner() -> None:
    host = SyncHost()
    owner_thread = host._thread
    first = host.submit(_owner_identity()).result(timeout=2)
    second = host.submit(_owner_identity()).result(timeout=2)
    host.close()

    assert first == second
    assert first[2] is True
    assert not owner_thread.is_alive()


def test_sync_host_rejects_submission_from_running_event_loop_thread() -> None:
    host = SyncHost()
    coroutine = _never_started()
    async def submit_from_running_loop() -> None:
        with pytest.raises(SyncAdapterEventLoopError):
            host.submit(coroutine)

    try:
        asyncio.run(submit_from_running_loop())
    finally:
        host.close()

    _assert_coroutine_closed(coroutine)


def test_sync_host_close_is_idempotent_and_closed_submit_closes_coroutine(
) -> None:
    host = SyncHost()
    owner_thread = host._thread
    host.close()
    host.close()
    coroutine = _never_started()
    with pytest.raises(SyncAgentClosedError):
        host.submit(coroutine)

    _assert_coroutine_closed(coroutine)
    assert not owner_thread.is_alive()


def test_sync_host_safely_accepts_submissions_from_multiple_threads() -> None:
    worker_count = 8
    barrier = threading.Barrier(worker_count)
    async def execute(value: int) -> tuple[int, int, int]:
        owner_thread_id, loop_id, _ = await _owner_identity()
        return value * value, owner_thread_id, loop_id

    with SyncHost() as host:
        def submit(value: int) -> tuple[int, int, int]:
            barrier.wait(timeout=2)
            return host.submit(execute(value)).result(timeout=2)

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            results = list(executor.map(submit, range(worker_count)))

    assert [result[0] for result in results] == [
        value * value for value in range(worker_count)
    ]
    assert len({result[1] for result in results}) == 1
    assert len({result[2] for result in results}) == 1


def test_sync_host_preserves_awaitable_exception_and_cancellation() -> None:
    error = ValueError("submission failed")
    async def fail() -> None:
        raise error

    async def cancel() -> None:
        raise asyncio.CancelledError

    with SyncHost() as host:
        with pytest.raises(ValueError) as caught:
            host.submit(fail()).result(timeout=2)

        cancelled = host.submit(cancel())
        with pytest.raises(CancelledError):
            cancelled.result(timeout=2)

    assert caught.value is error
    assert cancelled.cancelled()


@pytest.mark.parametrize("settlement", ["cancel", "complete"])
def test_sync_host_reclaims_awaitable_when_target_is_already_settled(
    settlement: str,
) -> None:
    owner_blocked = threading.Event()
    release_owner = threading.Event()
    coroutine_started = threading.Event()
    host = SyncHost()
    def block_owner() -> None:
        owner_blocked.set()
        release_owner.wait()

    host._loop.call_soon_threadsafe(block_owner)
    assert owner_blocked.wait(timeout=2)

    async def record_start() -> None:
        coroutine_started.set()

    coroutine = record_start()
    target = host.submit(coroutine)
    callback_submission: list[Future[tuple[int, int, bool]]] = []
    if settlement == "cancel":
        def reenter_and_fail(future: Future[None]) -> None:
            assert future.cancel()
            callback_submission.append(host.submit(_owner_identity()))
            raise SystemExit("cancel callback failed")
        target.add_done_callback(reenter_and_fail)
        assert isinstance(_call_bounded(target.cancel), SystemExit)
    else:
        target.set_result(None)

    release_owner.set()
    try:
        host.submit(_owner_identity()).result(timeout=2)
    finally:
        release_owner.set()
        _close_host_bounded(host)

    _assert_coroutine_closed(coroutine)
    assert not coroutine_started.is_set()
    assert len(callback_submission) == (1 if settlement == "cancel" else 0)
    assert all(future.result(timeout=2) for future in callback_submission)


def test_sync_host_rejects_cancel_after_awaitable_starts() -> None:
    started = threading.Event()
    release_work = threading.Event()
    side_effect_completed = threading.Event()
    host = SyncHost()
    async def work() -> str:
        started.set()
        await asyncio.to_thread(release_work.wait)
        side_effect_completed.set()
        return "completed"

    target = host.submit(work())
    try:
        assert started.wait(timeout=2)
        assert target.cancel() is False
        release_work.set()
        assert target.result(timeout=2) == "completed"
        assert side_effect_completed.is_set()
    finally:
        release_work.set()
        _close_host_bounded(host)


def test_sync_host_close_drains_accepted_work_and_rejects_new_submissions(
) -> None:
    work_count = 3
    closer_count = 3
    started = [threading.Event() for _ in range(work_count)]
    release_work = threading.Event()
    close_entered = [threading.Event() for _ in range(closer_count)]
    close_returned = [threading.Event() for _ in range(closer_count)]
    host = SyncHost()
    owner_thread = host._thread

    async def blocked_work(index: int) -> int:
        started[index].set()
        await asyncio.to_thread(release_work.wait)
        return index

    accepted = [host.submit(blocked_work(index)) for index in range(work_count)]
    assert all(event.wait(timeout=2) for event in started)

    def close_host(index: int) -> None:
        close_entered[index].set()
        host.close()
        close_returned[index].set()

    closers = [
        threading.Thread(target=close_host, args=(index,))
        for index in range(closer_count)
    ]
    for closer in closers:
        closer.start()
    assert all(event.wait(timeout=2) for event in close_entered)
    late_submissions: list[Future[None]] = []
    try:
        late_submissions = _wait_for_submission_rejection(host)
        assert not any(event.is_set() for event in close_returned)
        assert owner_thread.is_alive()

        release_work.set()
        for closer in closers:
            closer.join(timeout=2)
    finally:
        release_work.set()
        for closer in closers:
            closer.join(timeout=2)
        if owner_thread.is_alive():
            _close_host_bounded(host)

    assert not any(closer.is_alive() for closer in closers)
    assert all(event.is_set() for event in close_returned)
    assert [future.result(timeout=2) for future in late_submissions] == [
        None,
    ] * len(late_submissions)
    assert [future.result(timeout=2) for future in accepted] == list(
        range(work_count),
    )
    assert not owner_thread.is_alive()
