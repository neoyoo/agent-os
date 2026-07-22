from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from agentos.cli.application import CliHostFactory, CliInterruptedError


_SignalWaiter = Callable[[], Awaitable[None]]
_IdleWaiter = Callable[[float], Awaitable[None]]


async def run_relay_start(
    host_factory: CliHostFactory,
    *,
    idle_interval: float,
    wait_for_signal: _SignalWaiter,
    wait_for_idle: _IdleWaiter = asyncio.sleep,
) -> None:
    """循环发布 Outbox；停止信号不取消活动批次。"""

    interval = _validate_idle_interval(idle_interval)
    async with host_factory.open_relay_host() as host:
        signal_observer = asyncio.ensure_future(wait_for_signal())
        batch: asyncio.Task[int] | None = None
        idle_timer: asyncio.Task[None] | None = None
        try:
            while True:
                if signal_observer.done():
                    await signal_observer
                    raise CliInterruptedError()
                batch = asyncio.create_task(host.relay.relay_once())
                completed, _ = await asyncio.wait(
                    (signal_observer, batch),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if signal_observer in completed:
                    await signal_observer
                    await batch
                    raise CliInterruptedError()
                published = await batch
                batch = None
                if published > 0:
                    continue
                idle_timer = asyncio.ensure_future(wait_for_idle(interval))
                completed, _ = await asyncio.wait(
                    (signal_observer, idle_timer),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if signal_observer in completed:
                    await signal_observer
                    await _cancel_task(idle_timer)
                    idle_timer = None
                    raise CliInterruptedError()
                await idle_timer
                idle_timer = None
        finally:
            if batch is not None:
                await _cancel_task(batch)
            if idle_timer is not None:
                await _cancel_task(idle_timer)
            await _cancel_task(signal_observer)


def _validate_idle_interval(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("idle_interval must be a number")
    if not value > 0:
        raise ValueError("idle_interval must be positive")
    return float(value)


async def _cancel_task(task: asyncio.Future[object]) -> None:
    if not task.done():
        task.cancel()
    await asyncio.gather(task, return_exceptions=True)


__all__ = ["run_relay_start"]
