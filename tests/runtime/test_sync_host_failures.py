import asyncio
from collections.abc import Callable
from concurrent.futures import Future
import inspect
import threading

import pytest

from agentos.runtime._sync_host import SyncHost
from agentos.runtime.errors import SyncAdapterReentryError, SyncAgentClosedError


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


def test_sync_host_rejects_owner_thread_submit_reentry_and_closes_coroutine(
) -> None:
    rejected_coroutines: list[object] = []

    async def reenter(host: SyncHost) -> None:
        coroutine = _never_started()
        rejected_coroutines.append(coroutine)
        host.submit(coroutine)

    with SyncHost() as host:
        with pytest.raises(SyncAdapterReentryError):
            host.submit(reenter(host)).result(timeout=2)

    _assert_coroutine_closed(rejected_coroutines[0])


def test_sync_host_rejects_owner_thread_close_reentry() -> None:
    async def reenter_close(host: SyncHost) -> None:
        host.close()

    with SyncHost() as host:
        with pytest.raises(SyncAdapterReentryError):
            host.submit(reenter_close(host)).result(timeout=2)
        assert host.submit(_owner_identity()).result(timeout=2)


def test_sync_host_isolates_base_exception_from_future_callback() -> None:
    first_started = threading.Event()
    second_started = threading.Event()
    release_first = threading.Event()
    release_second = threading.Event()
    close_returned = threading.Event()
    close_errors: list[BaseException] = []
    loop_errors: list[dict[str, object]] = []
    host = SyncHost()

    async def install_exception_handler() -> None:
        asyncio.get_running_loop().set_exception_handler(
            lambda _loop, context: loop_errors.append(context),
        )

    async def blocked(
        started: threading.Event,
        release: threading.Event,
        result: str,
    ) -> str:
        started.set()
        await asyncio.to_thread(release.wait)
        return result

    def raise_from_callback(_future: Future[str]) -> None:
        raise SystemExit("future callback failed")

    def close_host() -> None:
        try:
            host.close()
        except BaseException as error:
            close_errors.append(error)
        finally:
            close_returned.set()

    host.submit(install_exception_handler()).result(timeout=2)
    first = host.submit(blocked(first_started, release_first, "first"))
    second = host.submit(blocked(second_started, release_second, "second"))
    assert first_started.wait(timeout=2)
    assert second_started.wait(timeout=2)
    first.add_done_callback(raise_from_callback)

    closer = threading.Thread(target=close_host)
    closer.start()
    late_submissions: list[Future[None]] = []
    try:
        late_submissions = _wait_for_submission_rejection(host)
        release_first.set()
        assert first.result(timeout=2) == "first"
        assert not close_returned.is_set()
        release_second.set()
        assert second.result(timeout=2) == "second"
        closer.join(timeout=2)
    finally:
        release_first.set()
        release_second.set()
        closer.join(timeout=2)
        if host._thread.is_alive():
            _close_host_bounded(host)

    assert [future.result(timeout=2) for future in late_submissions] == [
        None,
    ] * len(late_submissions)
    assert not closer.is_alive()
    assert close_returned.is_set()
    assert close_errors == []
    assert loop_errors == []
    assert not host._thread.is_alive()


@pytest.mark.parametrize("settlement", ["cancel", "complete"])
def test_sync_host_continues_after_native_coroutine_close_raises(
    settlement: str,
) -> None:
    owner_blocked = threading.Event()
    release_owner = threading.Event()
    close_attempted = threading.Event()
    host = SyncHost()

    class SuspendOnce:
        def __await__(self):
            yield None

    async def fail_on_close() -> None:
        try:
            await SuspendOnce()
        finally:
            close_attempted.set()
            raise SystemExit("coroutine close failed")

    def block_owner() -> None:
        owner_blocked.set()
        release_owner.wait()

    coroutine = fail_on_close()
    coroutine.send(None)
    host._loop.call_soon_threadsafe(block_owner)
    assert owner_blocked.wait(timeout=2)
    rejected_target = host.submit(coroutine)
    following = host.submit(_owner_identity())
    if settlement == "cancel":
        assert rejected_target.cancel()
    else:
        rejected_target.set_result(None)

    try:
        release_owner.set()
        assert following.result(timeout=2)
        assert close_attempted.wait(timeout=2)
    finally:
        release_owner.set()
        _close_host_bounded(host)

    _assert_coroutine_closed(coroutine)
    assert not host._thread.is_alive()


def test_sync_host_submit_schedule_failure_does_not_publish_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = SyncHost()

    def reject_schedule(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("loop rejected callback")

    monkeypatch.setattr(host._loop, "call_soon_threadsafe", reject_schedule)
    coroutine = _never_started()
    try:
        with pytest.raises(SyncAgentClosedError):
            host.submit(coroutine)
        _assert_coroutine_closed(coroutine)
        monkeypatch.undo()
        assert host.submit(_owner_identity()).result(timeout=2)
    finally:
        monkeypatch.undo()
        with host._lifecycle_lock:
            if host._state == "closed" and host._thread.is_alive():
                host._state = "open"
        _close_host_bounded(host)


def test_sync_host_close_schedule_failure_is_stable_and_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = SyncHost()

    def reject_schedule(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("loop rejected callback")

    monkeypatch.setattr(host._loop, "call_soon_threadsafe", reject_schedule)
    try:
        with pytest.raises(SyncAgentClosedError):
            host.close()
        monkeypatch.undo()
        assert host.submit(_owner_identity()).result(timeout=2)
    finally:
        monkeypatch.undo()
        with host._lifecycle_lock:
            if host._state == "closing":
                host._state = "open"
        _close_host_bounded(host)


@pytest.mark.parametrize("mode", ["cancel", "error", "pending"])
def test_sync_host_settles_target_when_completion_task_breaks(mode: str) -> None:
    marker = ValueError("completion task failed")
    owner_blocked = threading.Event()
    release_owner = threading.Event()
    host = SyncHost()

    async def install_task_factory() -> None:
        loop = asyncio.get_running_loop()
        original = loop.get_task_factory()

        async def replacement() -> None:
            if mode == "error":
                raise marker

        def factory(loop, coroutine, context=None):
            loop.set_task_factory(original)
            if mode == "cancel":
                task = asyncio.Task(coroutine, loop=loop, context=context)
                task.cancel()
                return task
            coroutine.close()
            return asyncio.Task(replacement(), loop=loop, context=context)

        loop.set_task_factory(factory)

    host.submit(install_task_factory()).result(timeout=2)

    def block_owner() -> None:
        owner_blocked.set()
        release_owner.wait()

    host._loop.call_soon_threadsafe(block_owner)
    assert owner_blocked.wait(timeout=2)
    coroutine = _never_started()
    target = host.submit(coroutine)

    def fail_from_target_callback(_future: Future[None]) -> None:
        raise SystemExit("fallback target callback failed")

    target.add_done_callback(fail_from_target_callback)
    following = host.submit(_owner_identity())
    try:
        release_owner.set()
        _close_host_bounded(host)
    finally:
        release_owner.set()
        if host._thread.is_alive():
            _close_host_bounded(host)

    assert following.result(timeout=2)
    _assert_coroutine_closed(coroutine)
    if mode == "cancel":
        assert target.cancelled()
    elif mode == "error":
        with pytest.raises(ValueError) as caught:
            target.result(timeout=2)
        assert caught.value is marker
    else:
        with pytest.raises(RuntimeError, match="completion task did not settle"):
            target.result(timeout=2)


def test_closed_submit_closes_reentrant_coroutine_outside_lifecycle_lock() -> None:
    host = SyncHost()
    host.close()
    inner_errors: list[BaseException] = []
    close_returned = threading.Event()

    class SuspendOnce:
        def __await__(self):
            yield None

    async def reenter_on_close() -> None:
        try:
            await SuspendOnce()
        finally:
            inner = _never_started()
            try:
                host.submit(inner)
            except BaseException as error:
                inner_errors.append(error)
            _assert_coroutine_closed(inner)
            host.close()
            close_returned.set()

    coroutine = reenter_on_close()
    coroutine.send(None)
    outcome = _call_bounded(lambda: host.submit(coroutine))

    assert isinstance(outcome, SyncAgentClosedError)
    _assert_coroutine_closed(coroutine)
    assert len(inner_errors) == 1
    assert isinstance(inner_errors[0], SyncAgentClosedError)
    assert close_returned.is_set()
