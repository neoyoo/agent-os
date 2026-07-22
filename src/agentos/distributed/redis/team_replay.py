from __future__ import annotations

import asyncio
from urllib.parse import quote

from agentos.distributed.errors import (
    DeliveryUnavailableError,
    DistributedStoreClosedError,
)
from agentos.distributed.models import RequestScope
from agentos.distributed._model_validation import require_identifier, require_positive
from agentos.distributed.redis._client import (
    AsyncRedisClient,
    decode_text,
    stream_id_key,
)
from agentos.distributed.redis._replay_snapshot import read_replay_snapshot
from agentos.distributed.redis._team_event_codec import (
    decode_scoped_team_envelope,
)
from agentos.distributed.redis._team_event_replay import (
    MAX_TEAM_REPLAY_EVENTS,
    ensure_team_event,
)
from agentos.distributed.redis._team_replay_subscription import (
    _RedisTeamEventSubscription,
)
from agentos.multi.team_event_types import (
    TeamEventEnvelope,
    TeamEventReplayBatch,
    TeamEventReplayItem,
    TeamStreamGap,
)


class RedisTeamEventReplayAdapter:
    """使用独立 Redis Stream 保存安全的短期 Team event replay。"""

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
        if max_events > MAX_TEAM_REPLAY_EVENTS:
            raise ValueError(
                f"max_events must not exceed {MAX_TEAM_REPLAY_EVENTS}"
            )
        require_positive(block_ms, "block_ms")
        require_positive(follow_batch_size, "follow_batch_size")
        self._redis = AsyncRedisClient(url, client)
        self._key_prefix = key_prefix.rstrip(":")
        self._max_events = max_events
        self._block_ms = block_ms
        self._follow_batch_size = follow_batch_size
        self._subscriptions: set[_RedisTeamEventSubscription] = set()
        self._closed = False
        self._closing = False
        self._close_lock = asyncio.Lock()

    async def append(
        self,
        *,
        scope: RequestScope,
        event: TeamEventEnvelope,
    ) -> TeamEventReplayItem:
        """追加 PostgreSQL 已解析的 typed Team event。"""

        self._ensure_open()
        _require_scope(scope)
        if type(event) is not TeamEventEnvelope:
            raise TypeError("event must be TeamEventEnvelope")
        if event.tenant_id != scope.tenant_id:
            raise ValueError("event tenant does not match request scope")
        return await ensure_team_event(
            self._redis,
            stream=self._stream_key(scope, event.team_id),
            event=event,
            max_events=self._max_events,
        )

    async def replay(
        self,
        *,
        scope: RequestScope,
        team_id: str,
        after: str | None,
        limit: int,
    ) -> TeamEventReplayBatch | TeamStreamGap:
        """读取一个有界 Team event replay snapshot。"""

        self._validate_query(scope, team_id, after, limit)
        snapshot = await read_replay_snapshot(
            self._redis,
            stream=self._stream_key(scope, team_id),
            after=after,
            limit=min(limit, self._max_events),
        )
        if snapshot.gap_reason is not None:
            if after is None:
                raise DeliveryUnavailableError()
            return TeamStreamGap(
                scope.tenant_id,
                team_id,
                after,
                snapshot.oldest,
                snapshot.gap_reason,
            )
        items = tuple(
            _replay_item(
                cursor,
                decode_scoped_team_envelope(fields, scope, team_id),
            )
            for cursor, fields in snapshot.rows
        )
        return TeamEventReplayBatch(items, items[-1].cursor if items else after)

    async def high_water(
        self,
        *,
        scope: RequestScope,
        team_id: str,
    ) -> str | None:
        """读取当前最新 cursor，但不消费事件。"""

        self._validate_query(scope, team_id, None, 1)
        rows = await self._redis.call(
            "xrevrange",
            self._stream_key(scope, team_id),
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
        event = decode_scoped_team_envelope(row[1], scope, team_id)
        cursor = decode_text(row[0])
        if cursor is None:
            raise DeliveryUnavailableError()
        return _replay_item(cursor, event).cursor

    def follow(
        self,
        *,
        scope: RequestScope,
        team_id: str,
        after: str | None,
    ) -> _RedisTeamEventSubscription:
        """创建需要 consumer 显式关闭的 replay + tail subscription。"""

        self._validate_query(scope, team_id, after, 1)
        subscription = _RedisTeamEventSubscription(self, scope, team_id, after)
        self._subscriptions.add(subscription)
        return subscription

    async def close(self) -> None:
        """停止全部 tail 读取并关闭 adapter 生命周期。"""

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
        team_id: str,
        after: str,
    ) -> None:
        await self._redis.call(
            "xread",
            {self._stream_key(scope, team_id): after},
            count=self._follow_batch_size,
            block=self._block_ms,
        )

    def _remove_subscription(
        self,
        subscription: _RedisTeamEventSubscription,
    ) -> None:
        self._subscriptions.discard(subscription)

    def _validate_query(
        self,
        scope: RequestScope,
        team_id: str,
        after: str | None,
        limit: int,
    ) -> None:
        self._ensure_open()
        _require_scope(scope)
        require_identifier(team_id, "team_id")
        if after is not None:
            require_identifier(after, "after")
        require_positive(limit, "limit")

    def _stream_key(self, scope: RequestScope, team_id: str) -> str:
        identity = ":".join(
            quote(value, safe="") for value in (scope.tenant_id, team_id)
        )
        return f"{self._key_prefix}:team-events:{identity}"

    def _ensure_open(self) -> None:
        if self._closed or self._closing:
            raise DistributedStoreClosedError()


def _require_scope(scope: object) -> None:
    if type(scope) is not RequestScope:
        raise TypeError("scope must be RequestScope")


def _replay_item(
    cursor: str,
    event: TeamEventEnvelope,
) -> TeamEventReplayItem:
    stream_id_key(cursor)
    if cursor != f"{event.event_sequence}-0":
        raise DeliveryUnavailableError()
    return TeamEventReplayItem(cursor, event)


__all__ = ["RedisTeamEventReplayAdapter"]
