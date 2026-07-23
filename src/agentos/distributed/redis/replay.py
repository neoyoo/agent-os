from __future__ import annotations

import asyncio
from collections import deque
from datetime import timedelta
from urllib.parse import quote

from agentos.distributed.errors import (
    DeliveryUnavailableError,
    DistributedStoreClosedError,
)
from agentos.distributed.models import (
    LiveTurnCancelled,
    LiveTurnCompleted,
    LiveTurnFailed,
    LiveTurnWaiting,
    ReplayBatch,
    ReplayItem,
    RequestScope,
    RunEventEnvelope,
    StreamGap,
)
from agentos.distributed._model_validation import require_identifier, require_positive
from agentos.distributed.redis._client import (
    AsyncRedisClient,
    decode_text,
)
from agentos.distributed.redis._event_codec import (
    decode_scoped_envelope,
    encode_envelope,
)
from agentos.distributed.redis._replay_snapshot import read_replay_snapshot
from agentos.distributed.redis._terminal_replay import ensure_terminal
from agentos.distributed._stream_models import TERMINAL_EVENT_SEQUENCE


_TERMINAL_EVENT_TYPES = {
    LiveTurnCancelled,
    LiveTurnCompleted,
    LiveTurnFailed,
    LiveTurnWaiting,
}


class RedisEventReplayAdapter:
    """使用短期 Redis Stream 提供 typed live event Replay + Tail。"""

    def __init__(
        self,
        url: str | None = None,
        *,
        client: object | None = None,
        key_prefix: str = "agentos",
        max_events: int = 1_000,
        block_ms: int = 1_000,
        follow_batch_size: int = 256,
        operation_timeout: timedelta = timedelta(seconds=5),
    ) -> None:
        require_identifier(key_prefix, "key_prefix")
        require_positive(max_events, "max_events")
        require_positive(block_ms, "block_ms")
        require_positive(follow_batch_size, "follow_batch_size")
        self._redis = AsyncRedisClient(
            url,
            client,
            operation_timeout=operation_timeout,
        )
        self._key_prefix = key_prefix.rstrip(":")
        self._max_events = max_events
        self._block_ms = block_ms
        self._follow_batch_size = follow_batch_size
        self._subscriptions: set[_RedisEventSubscription] = set()
        self._closed = False
        self._closing = False
        self._close_lock = asyncio.Lock()

    async def append(
        self,
        *,
        scope: RequestScope,
        event: RunEventEnvelope,
    ) -> ReplayItem:
        self._ensure_open()
        if type(event) is not RunEventEnvelope:
            raise TypeError("event must be RunEventEnvelope")
        if event.tenant_id != scope.tenant_id:
            raise ValueError("event tenant does not match request scope")
        cursor = decode_text(
            await self._redis.call(
                "xadd",
                self._stream_key(scope, event.session_id, event.run_id),
                encode_envelope(event),
                id="*",
                maxlen=self._max_events,
                approximate=False,
            ),
        )
        if cursor is None:
            raise DeliveryUnavailableError()
        return ReplayItem(cursor, event)

    async def ensure_terminal(
        self,
        *,
        scope: RequestScope,
        event: RunEventEnvelope,
    ) -> ReplayItem:
        self._ensure_open()
        if type(event) is not RunEventEnvelope:
            raise TypeError("event must be RunEventEnvelope")
        if event.tenant_id != scope.tenant_id:
            raise ValueError("event tenant does not match request scope")
        if event.event_sequence != TERMINAL_EVENT_SEQUENCE:
            raise ValueError("terminal event must use the reserved sequence")
        if type(event.event) not in _TERMINAL_EVENT_TYPES:
            raise ValueError("event must be terminal or waiting")
        return await ensure_terminal(
            self._redis,
            stream=self._stream_key(scope, event.session_id, event.run_id),
            event=event,
            max_events=self._max_events,
        )

    async def replay(
        self,
        *,
        scope: RequestScope,
        session_id: str,
        run_id: str,
        after: str | None,
        limit: int,
    ) -> ReplayBatch | StreamGap:
        self._validate_query(session_id, run_id, after, limit)
        key = self._stream_key(scope, session_id, run_id)
        snapshot = await read_replay_snapshot(
            self._redis,
            stream=key,
            after=after,
            limit=limit,
        )
        if snapshot.gap_reason is not None:
            if after is None:
                raise DeliveryUnavailableError()
            return StreamGap(
                scope.tenant_id,
                session_id,
                run_id,
                after,
                snapshot.oldest,
                snapshot.gap_reason,
            )
        items = tuple(
            ReplayItem(
                cursor,
                decode_scoped_envelope(fields, scope, session_id, run_id),
            )
            for cursor, fields in snapshot.rows
        )
        return ReplayBatch(items, items[-1].cursor if items else after)

    async def high_water(
        self,
        *,
        scope: RequestScope,
        session_id: str,
        run_id: str,
    ) -> str | None:
        """Capture the latest stream cursor without consuming an event."""

        self._validate_query(session_id, run_id, None, 1)
        rows = await self._redis.call(
            "xrevrange",
            self._stream_key(scope, session_id, run_id),
            max="+",
            min="-",
            count=1,
        )
        if not isinstance(rows, (list, tuple)):
            raise DeliveryUnavailableError()
        if not rows:
            return None
        row = rows[0]
        if not isinstance(row, (list, tuple)) or len(row) != 2:
            raise DeliveryUnavailableError()
        cursor = decode_text(row[0])
        if cursor is None:
            raise DeliveryUnavailableError()
        return cursor

    def follow(
        self,
        *,
        scope: RequestScope,
        session_id: str,
        run_id: str,
        after: str | None,
    ) -> _RedisEventSubscription:
        self._validate_query(session_id, run_id, after, 1)
        subscription = _RedisEventSubscription(
            self,
            scope,
            session_id,
            run_id,
            after,
        )
        self._subscriptions.add(subscription)
        return subscription

    async def close(self) -> None:
        async with self._close_lock:
            if self._closed:
                return
            self._closing = True
            try:
                subscriptions = tuple(self._subscriptions)
                if subscriptions:
                    await asyncio.gather(*(item.aclose() for item in subscriptions))
                await self._redis.close()
                self._closed = True
            finally:
                self._closing = False

    async def _wait_for_tail(
        self,
        scope: RequestScope,
        session_id: str,
        run_id: str,
        after: str,
    ) -> None:
        await self._redis.blocking_call(
            self._block_ms,
            "xread",
            {self._stream_key(scope, session_id, run_id): after},
            count=self._follow_batch_size,
            block=self._block_ms,
        )

    def _validate_query(
        self,
        session_id: str,
        run_id: str,
        after: str | None,
        limit: int,
    ) -> None:
        self._ensure_open()
        require_identifier(session_id, "session_id")
        require_identifier(run_id, "run_id")
        if after is not None:
            require_identifier(after, "after")
        require_positive(limit, "limit")

    def _stream_key(
        self,
        scope: RequestScope,
        session_id: str,
        run_id: str,
    ) -> str:
        identity = ":".join(
            quote(value, safe="")
            for value in (scope.tenant_id, session_id, run_id)
        )
        return f"{self._key_prefix}:events:{identity}"

    def _remove(self, subscription: _RedisEventSubscription) -> None:
        self._subscriptions.discard(subscription)

    def _ensure_open(self) -> None:
        if self._closed or self._closing:
            raise DistributedStoreClosedError()


class _RedisEventSubscription:
    def __init__(
        self,
        adapter: RedisEventReplayAdapter,
        scope: RequestScope,
        session_id: str,
        run_id: str,
        after: str | None,
    ) -> None:
        self._adapter = adapter
        self._scope = scope
        self._session_id = session_id
        self._run_id = run_id
        self._cursor = after
        self._buffer: deque[ReplayItem] = deque()
        self._gap_delivered = False
        self._read_task: asyncio.Task[None] | None = None
        self._closed = False

    def __aiter__(self) -> _RedisEventSubscription:
        return self

    async def __anext__(self) -> ReplayItem | StreamGap:
        while True:
            if self._closed or self._gap_delivered:
                raise StopAsyncIteration
            if self._buffer:
                return self._buffer.popleft()
            replayed = await self._adapter.replay(
                scope=self._scope,
                session_id=self._session_id,
                run_id=self._run_id,
                after=self._cursor,
                limit=self._adapter._follow_batch_size,
            )
            if type(replayed) is StreamGap:
                self._gap_delivered = True
                return replayed
            if replayed.items:
                self._buffer.extend(replayed.items)
                self._cursor = replayed.next_cursor
                return self._buffer.popleft()
            await self._read_tail()

    async def aclose(self) -> None:
        if self._closed:
            self._adapter._remove(self)
            return
        self._closed = True
        try:
            task = self._read_task
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        finally:
            self._adapter._remove(self)

    async def _read_tail(self) -> None:
        if self._closed:
            raise StopAsyncIteration
        task = asyncio.create_task(
            self._adapter._wait_for_tail(
                self._scope,
                self._session_id,
                self._run_id,
                self._cursor or "0-0",
            ),
        )
        self._read_task = task
        try:
            return await task
        except asyncio.CancelledError:
            if self._closed:
                raise StopAsyncIteration from None
            raise
        finally:
            if self._read_task is task:
                self._read_task = None
