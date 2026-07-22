from __future__ import annotations

import asyncio
from collections import deque
from typing import TYPE_CHECKING

from agentos.distributed.models import RequestScope
from agentos.multi.team_event_types import TeamEventReplayItem, TeamStreamGap

if TYPE_CHECKING:
    from agentos.distributed.redis.team_replay import RedisTeamEventReplayAdapter


class _RedisTeamEventSubscription:
    """可显式关闭的 Team replay + tail iterator。"""

    def __init__(
        self,
        adapter: RedisTeamEventReplayAdapter,
        scope: RequestScope,
        team_id: str,
        after: str | None,
    ) -> None:
        self._adapter = adapter
        self._scope = scope
        self._team_id = team_id
        self._cursor = after
        self._buffer: deque[TeamEventReplayItem] = deque()
        self._gap_delivered = False
        self._read_task: asyncio.Task[None] | None = None
        self._closed = False

    def __aiter__(self) -> _RedisTeamEventSubscription:
        return self

    async def __anext__(self) -> TeamEventReplayItem | TeamStreamGap:
        while True:
            if self._closed or self._gap_delivered:
                raise StopAsyncIteration
            if self._buffer:
                return self._buffer.popleft()
            replayed = await self._adapter.replay(
                scope=self._scope,
                team_id=self._team_id,
                after=self._cursor,
                limit=self._adapter._follow_batch_size,
            )
            if self._closed:
                raise StopAsyncIteration
            if type(replayed) is TeamStreamGap:
                self._gap_delivered = True
                return replayed
            if replayed.items:
                self._buffer.extend(replayed.items)
                self._cursor = replayed.next_cursor
                return self._buffer.popleft()
            await self._read_tail()

    async def aclose(self) -> None:
        if self._closed:
            self._adapter._remove_subscription(self)
            return
        self._closed = True
        try:
            task = self._read_task
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        finally:
            self._adapter._remove_subscription(self)

    async def _read_tail(self) -> None:
        if self._closed:
            raise StopAsyncIteration
        task = asyncio.create_task(
            self._adapter._wait_for_tail(
                self._scope,
                self._team_id,
                self._cursor or "0-0",
            ),
        )
        self._read_task = task
        try:
            await task
        except asyncio.CancelledError:
            if self._closed:
                raise StopAsyncIteration from None
            raise
        finally:
            if self._read_task is task:
                self._read_task = None


__all__: list[str] = []
