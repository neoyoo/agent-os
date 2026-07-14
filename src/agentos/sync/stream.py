from __future__ import annotations

import asyncio
from threading import Lock, get_ident
from types import TracebackType
from typing import TYPE_CHECKING, Self

from agentos.runtime.agent_stream import AgentStream
from agentos.runtime.errors import SyncStreamConsumerError
from agentos.runtime.stream_events import (
    TurnStreamCancelled,
    TurnStreamCompleted,
    TurnStreamEvent,
    TurnStreamFailed,
    TurnStreamWaiting,
)

if TYPE_CHECKING:
    from agentos.sync.agent import SyncAgent


class _StreamDriver:
    """在 owner loop 的单一 Task 中驱动 AgentStream。"""

    def __init__(self, stream: AgentStream) -> None:
        self.stream = stream
        self.queue: asyncio.Queue[asyncio.Future[TurnStreamEvent]] = asyncio.Queue()
        self.task = asyncio.create_task(self._drive())
        self.closed = False
        self.terminal_error: BaseException | None = None

    async def next_event(self) -> TurnStreamEvent:
        if self.closed:
            if self.terminal_error is not None:
                error = self.terminal_error
                self.terminal_error = None
                raise error
            raise StopAsyncIteration
        target = asyncio.get_running_loop().create_future()
        await self.queue.put(target)
        return await target

    async def close(self) -> None:
        if not self.closed:
            await self.stream.aclose()
        if not self.task.done():
            self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
        self.closed = True

    async def _drive(self) -> None:
        try:
            while True:
                target = await self.queue.get()
                try:
                    event = await anext(self.stream)
                except StopAsyncIteration as error:
                    target.set_exception(error)
                    return
                except asyncio.CancelledError as error:
                    target.set_exception(error)
                    raise
                except BaseException as error:
                    target.set_exception(error)
                    return
                target.set_result(event)
                if isinstance(
                    event,
                    (
                        TurnStreamCompleted,
                        TurnStreamWaiting,
                        TurnStreamCancelled,
                        TurnStreamFailed,
                    ),
                ):
                    if isinstance(event, TurnStreamFailed):
                        self.terminal_error = event.error
                    return
        finally:
            self.closed = True


async def create_stream_driver(stream: AgentStream) -> _StreamDriver:
    """在当前 owner loop 上创建持久消费 Task。"""

    return _StreamDriver(stream)


class SyncAgentStream:
    """同步迭代并确定性关闭一个 AgentStream。"""

    def __init__(self) -> None:
        raise TypeError("SyncAgentStream instances are created by SyncAgent")

    @classmethod
    def _create(
        cls,
        owner: SyncAgent,
        driver: _StreamDriver,
    ) -> Self:
        self = cls.__new__(cls)
        self._owner = owner
        self._driver = driver
        self._close_owner_when_done = False
        self._consumer_thread_id: int | None = None
        self._lock = Lock()
        self._closed = False
        return self

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def __iter__(self) -> Self:
        return self

    def __next__(self) -> TurnStreamEvent:
        thread_id = get_ident()
        with self._lock:
            if self._closed:
                raise StopIteration
            if self._consumer_thread_id is None:
                self._consumer_thread_id = thread_id
            elif self._consumer_thread_id != thread_id:
                raise SyncStreamConsumerError(
                    "sync agent stream already has a different consumer thread",
                )
        try:
            event = self._owner._submit(self._driver.next_event()).result()
        except StopAsyncIteration:
            self._finish()
            raise StopIteration from None
        if isinstance(
            event,
            (
                TurnStreamCompleted,
                TurnStreamWaiting,
                TurnStreamCancelled,
                TurnStreamFailed,
            ),
        ):
            self._finish()
        return event

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
        self._owner._submit(self._driver.close()).result()
        self._finish()

    def _finish(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._owner._detach_stream(self)
        if self._close_owner_when_done:
            self._owner.close()
