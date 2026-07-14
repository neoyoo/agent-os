from collections.abc import Callable, Iterator
from threading import Event as ThreadEvent, Lock, Thread

import pytest

from agentos.sync import SyncAgent


class RegisteredStreamProbe:
    def __init__(
        self,
        name: str,
        attempts: list[str],
        *,
        error: BaseException | None = None,
        entered: ThreadEvent | None = None,
        release: ThreadEvent | None = None,
        reenter: Callable[[], None] | None = None,
    ) -> None:
        self.name = name
        self.attempts = attempts
        self.error = error
        self.entered = entered
        self.release = release
        self.reenter = reenter
        self.calls = 0
        self._lock = Lock()

    def close(self) -> None:
        with self._lock:
            self.calls += 1
        self.attempts.append(self.name)
        if self.entered is not None:
            self.entered.set()
        if self.release is not None:
            assert self.release.wait(2)
        if self.reenter is not None:
            self.reenter()
        if self.error is not None:
            raise self.error


class OrderedStreamRegistry:
    def __init__(self, *streams: RegisteredStreamProbe) -> None:
        self._streams = list(streams)

    def __iter__(self) -> Iterator[RegisteredStreamProbe]:
        return iter(self._streams)

    def add(self, stream: RegisteredStreamProbe) -> None:
        self._streams.append(stream)

    def discard(self, stream: RegisteredStreamProbe) -> None:
        if stream in self._streams:
            self._streams.remove(stream)


def register_blocking_stream(
    sync_agent: SyncAgent,
    attempts: list[str],
    *,
    error: BaseException | None = None,
) -> tuple[RegisteredStreamProbe, ThreadEvent, ThreadEvent]:
    cleanup_entered = ThreadEvent()
    release_cleanup = ThreadEvent()
    blocking = RegisteredStreamProbe(
        "blocking",
        attempts,
        error=error,
        entered=cleanup_entered,
        release=release_cleanup,
    )
    sync_agent._streams.add(blocking)  # type: ignore[arg-type]
    return blocking, cleanup_entered, release_cleanup


def close_agent_thread(
    sync_agent: SyncAgent,
    errors: list[BaseException],
    *,
    returned: ThreadEvent | None = None,
) -> Thread:
    def close_agent() -> None:
        try:
            sync_agent.close()
        except BaseException as error:
            errors.append(error)
        finally:
            if returned is not None:
                returned.set()

    return Thread(target=close_agent)


def observe_condition_wait(
    monkeypatch: pytest.MonkeyPatch,
    sync_agent: SyncAgent,
    waiting: ThreadEvent,
) -> None:
    original_condition_wait = sync_agent._stream_condition.wait

    def observed_condition_wait(timeout: float | None = None) -> bool:
        waiting.set()
        return original_condition_wait(timeout)

    monkeypatch.setattr(
        sync_agent._stream_condition,
        "wait",
        observed_condition_wait,
    )
