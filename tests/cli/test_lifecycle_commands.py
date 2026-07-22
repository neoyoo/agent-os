from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest

from agentos.cli.application import CliInterruptedError
from agentos.cli.commands import run_relay_start, run_serve, run_worker_start


class _Signal:
    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.observed = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def __call__(self) -> None:
        try:
            await self.release.wait()
            self.observed.set()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise


class _Worker:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.stopped = asyncio.Event()
        self.failure: BaseException | None = None
        self.wait_cancelled = False
        self.drain_calls: list[float] = []

    async def start(self) -> None:
        self.started.set()

    async def wait(self) -> None:
        try:
            await self.stopped.wait()
        except asyncio.CancelledError:
            self.wait_cancelled = True
            raise
        if self.failure is not None:
            raise self.failure

    async def drain(self, timeout: float) -> None:
        self.drain_calls.append(timeout)


class _Relay:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = False
        self.failure: BaseException | None = None
        self.results: list[int] = []
        self.calls = 0

    async def relay_once(self) -> int:
        self.calls += 1
        self.started.set()
        if self.failure is not None:
            raise self.failure
        if self.results:
            return self.results.pop(0)
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return 1


class _Server:
    def __init__(self) -> None:
        self.calls = 0

    async def serve(self) -> None:
        self.calls += 1


class _HostFactory:
    def __init__(self) -> None:
        self.worker = _Worker()
        self.relay = _Relay()
        self.server = _Server()
        self.entered: list[str] = []
        self.exited: list[str] = []

    @asynccontextmanager
    async def open_worker_host(self):  # type: ignore[no-untyped-def]
        self.entered.append("worker")
        try:
            yield self
        finally:
            self.exited.append("worker")

    @asynccontextmanager
    async def open_relay_host(self):  # type: ignore[no-untyped-def]
        self.entered.append("relay")
        try:
            yield self
        finally:
            self.exited.append("relay")

    @asynccontextmanager
    async def open_server_host(self):  # type: ignore[no-untyped-def]
        self.entered.append("server")
        try:
            yield self
        finally:
            self.exited.append("server")


def test_worker_signal_drains_and_leaves_close_to_host_context() -> None:
    async def scenario() -> None:
        factory = _HostFactory()
        signal = _Signal()
        command = asyncio.create_task(
            run_worker_start(
                factory,  # type: ignore[arg-type]
                drain_timeout=3.5,
                wait_for_signal=signal,
            )
        )
        await factory.worker.started.wait()

        signal.release.set()
        with pytest.raises(CliInterruptedError):
            await command

        assert factory.worker.wait_cancelled
        assert factory.worker.drain_calls == [3.5]
        assert factory.entered == ["worker"]
        assert factory.exited == ["worker"]

    asyncio.run(scenario())


def test_worker_failure_skips_drain_and_preserves_exception_identity() -> None:
    async def scenario() -> None:
        factory = _HostFactory()
        signal = _Signal()
        failure = RuntimeError("worker failed")
        factory.worker.failure = failure
        command = asyncio.create_task(
            run_worker_start(
                factory,  # type: ignore[arg-type]
                drain_timeout=3,
                wait_for_signal=signal,
            )
        )
        await factory.worker.started.wait()

        factory.worker.stopped.set()
        with pytest.raises(RuntimeError) as raised:
            await command

        assert raised.value is failure
        assert signal.cancelled.is_set()
        assert factory.worker.drain_calls == []
        assert factory.exited == ["worker"]

    asyncio.run(scenario())


def test_worker_unexpected_normal_stop_is_not_success() -> None:
    async def scenario() -> None:
        factory = _HostFactory()
        signal = _Signal()
        command = asyncio.create_task(
            run_worker_start(
                factory,  # type: ignore[arg-type]
                drain_timeout=3,
                wait_for_signal=signal,
            )
        )
        await factory.worker.started.wait()

        factory.worker.stopped.set()
        with pytest.raises(RuntimeError, match="worker stopped unexpectedly"):
            await command

        assert factory.worker.drain_calls == []
        assert factory.exited == ["worker"]

    asyncio.run(scenario())


@pytest.mark.parametrize("timeout", [True, -0.1, "1"])
def test_worker_rejects_invalid_drain_timeout_before_opening_host(
    timeout: object,
) -> None:
    async def scenario() -> None:
        factory = _HostFactory()

        with pytest.raises((TypeError, ValueError)):
            await run_worker_start(
                factory,  # type: ignore[arg-type]
                drain_timeout=timeout,  # type: ignore[arg-type]
                wait_for_signal=_Signal(),
            )

        assert factory.entered == []

    asyncio.run(scenario())


def test_relay_signal_does_not_cancel_active_batch() -> None:
    async def scenario() -> None:
        factory = _HostFactory()
        signal = _Signal()
        command = asyncio.create_task(
            run_relay_start(
                factory,  # type: ignore[arg-type]
                idle_interval=2,
                wait_for_signal=signal,
            )
        )
        await factory.relay.started.wait()

        signal.release.set()
        await signal.observed.wait()
        factory.relay.release.set()
        with pytest.raises(CliInterruptedError):
            await command

        assert not factory.relay.cancelled
        assert factory.relay.calls == 1
        assert factory.exited == ["relay"]

    asyncio.run(scenario())


def test_relay_signal_cancels_idle_timer() -> None:
    async def scenario() -> None:
        factory = _HostFactory()
        factory.relay.results.append(0)
        signal = _Signal()
        idle_started = asyncio.Event()
        idle_cancelled = asyncio.Event()

        async def wait_for_idle(_: float) -> None:
            idle_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                idle_cancelled.set()
                raise

        command = asyncio.create_task(
            run_relay_start(
                factory,  # type: ignore[arg-type]
                idle_interval=2,
                wait_for_signal=signal,
                wait_for_idle=wait_for_idle,
            )
        )
        await idle_started.wait()

        signal.release.set()
        with pytest.raises(CliInterruptedError):
            await command

        assert idle_cancelled.is_set()
        assert factory.relay.calls == 1
        assert factory.exited == ["relay"]

    asyncio.run(scenario())


def test_relay_failure_propagates_without_entering_idle() -> None:
    async def scenario() -> None:
        factory = _HostFactory()
        signal = _Signal()
        failure = RuntimeError("relay failed")
        factory.relay.failure = failure
        idle_calls: list[float] = []

        async def wait_for_idle(interval: float) -> None:
            idle_calls.append(interval)

        with pytest.raises(RuntimeError) as raised:
            await run_relay_start(
                factory,  # type: ignore[arg-type]
                idle_interval=2,
                wait_for_signal=signal,
                wait_for_idle=wait_for_idle,
            )

        assert raised.value is failure
        assert signal.cancelled.is_set()
        assert idle_calls == []
        assert factory.exited == ["relay"]

    asyncio.run(scenario())


@pytest.mark.parametrize("interval", [True, 0, -0.1, "1"])
def test_relay_rejects_invalid_idle_interval_before_opening_host(
    interval: object,
) -> None:
    async def scenario() -> None:
        factory = _HostFactory()

        with pytest.raises((TypeError, ValueError)):
            await run_relay_start(
                factory,  # type: ignore[arg-type]
                idle_interval=interval,  # type: ignore[arg-type]
                wait_for_signal=_Signal(),
            )

        assert factory.entered == []

    asyncio.run(scenario())


def test_serve_delegates_only_to_server_host() -> None:
    async def scenario() -> None:
        factory = _HostFactory()

        await run_serve(factory)  # type: ignore[arg-type]

        assert factory.server.calls == 1
        assert factory.entered == ["server"]
        assert factory.exited == ["server"]

    asyncio.run(scenario())
