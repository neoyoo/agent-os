from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from agentos.cli.application import CliHostFactory, CliInterruptedError


_SignalWaiter = Callable[[], Awaitable[None]]


async def run_worker_start(
    host_factory: CliHostFactory,
    *,
    drain_timeout: float,
    wait_for_signal: _SignalWaiter,
) -> None:
    """运行 Worker，收到停止信号后排空当前执行。"""

    timeout = _validate_drain_timeout(drain_timeout)
    async with host_factory.open_worker_host() as host:
        await host.worker.start()
        signal_observer = asyncio.ensure_future(wait_for_signal())
        worker_observer = asyncio.create_task(host.worker.wait())
        try:
            completed, _ = await asyncio.wait(
                (signal_observer, worker_observer),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if worker_observer in completed:
                await worker_observer
                raise RuntimeError("worker stopped unexpectedly")
            await signal_observer
            await _cancel_task(worker_observer)
            await host.worker.drain(timeout)
            raise CliInterruptedError()
        finally:
            await _cancel_task(signal_observer)
            await _cancel_task(worker_observer)


def _validate_drain_timeout(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("drain_timeout must be a number")
    if not value >= 0:
        raise ValueError("drain_timeout must not be negative")
    return float(value)


async def _cancel_task(task: asyncio.Future[None]) -> None:
    if not task.done():
        task.cancel()
    await asyncio.gather(task, return_exceptions=True)


__all__ = ["run_worker_start"]
