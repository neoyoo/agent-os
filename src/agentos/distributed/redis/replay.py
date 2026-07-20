from __future__ import annotations

import asyncio
from collections import deque
from urllib.parse import quote

from agentos.distributed.errors import (
    DeliveryUnavailableError,
    DistributedStoreClosedError,
)
from agentos.distributed.models import (
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
    stream_id_key,
    stream_rows,
)
from agentos.distributed.redis._event_codec import (
    decode_scoped_envelope,
    encode_envelope,
)


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
    ) -> None:
        require_identifier(key_prefix, "key_prefix")
        require_positive(max_events, "max_events")
        require_positive(block_ms, "block_ms")
        require_positive(follow_batch_size, "follow_batch_size")
        self._redis = AsyncRedisClient(url, client)
        self._key_prefix = key_prefix.rstrip(":")
        self._max_events = max_events
        self._block_ms = block_ms
        self._follow_batch_size = follow_batch_size
        self._subscriptions: set[_RedisEventSubscription] = set()
        self._closed = False

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
        oldest_rows = stream_rows(
            await self._redis.call("xrange", key, min="-", max="+", count=1),
        )
        if after is not None and not oldest_rows:
            return StreamGap(
                scope.tenant_id,
                session_id,
                run_id,
                after,
                None,
                "unavailable",
            )
        if after is not None and after != "0-0":
            oldest = oldest_rows[0][0]
            if stream_id_key(after) < stream_id_key(oldest):
                return StreamGap(
                    scope.tenant_id,
                    session_id,
                    run_id,
                    after,
                    oldest,
                    "trimmed",
                )
        minimum = "-" if after is None else f"({after}"
        rows = stream_rows(
            await self._redis.call("xrange", key, min=minimum, max="+", count=limit),
        )
        items = tuple(
            ReplayItem(
                cursor,
                decode_scoped_envelope(fields, scope, session_id, run_id),
            )
            for cursor, fields in rows
        )
        return ReplayBatch(items, items[-1].cursor if items else after)

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
        if self._closed:
            return
        self._closed = True
        subscriptions = tuple(self._subscriptions)
        if subscriptions:
            await asyncio.gather(*(item.aclose() for item in subscriptions))
        await self._redis.close()

    async def _tail(
        self,
        scope: RequestScope,
        session_id: str,
        run_id: str,
        after: str,
    ) -> list[tuple[str, object]]:
        response = await self._redis.call(
            "xread",
            {self._stream_key(scope, session_id, run_id): after},
            count=self._follow_batch_size,
            block=self._block_ms,
        )
        return stream_rows(response)

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
        if self._closed:
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
        self._replay_complete = False
        self._gap_delivered = False
        self._read_task: asyncio.Task[list[tuple[str, object]]] | None = None
        self._closed = False

    def __aiter__(self) -> _RedisEventSubscription:
        return self

    async def __anext__(self) -> ReplayItem | StreamGap:
        while True:
            if self._closed or self._gap_delivered:
                raise StopAsyncIteration
            if self._buffer:
                return self._buffer.popleft()
            if not self._replay_complete:
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
                self._replay_complete = True
            rows = await self._read_tail()
            if rows:
                items = [
                    ReplayItem(
                        cursor,
                        decode_scoped_envelope(
                            fields,
                            self._scope,
                            self._session_id,
                            self._run_id,
                        ),
                    )
                    for cursor, fields in rows
                ]
                self._cursor = items[-1].cursor
                self._buffer.extend(items[1:])
                return items[0]

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        task = self._read_task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._adapter._remove(self)

    async def _read_tail(self) -> list[tuple[str, object]]:
        if self._closed:
            raise StopAsyncIteration
        task = asyncio.create_task(
            self._adapter._tail(
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
