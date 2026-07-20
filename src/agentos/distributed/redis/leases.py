from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from urllib.parse import quote
from uuid import uuid4

from agentos.distributed.errors import ClaimConflictError
from agentos.distributed.models import RequestScope, SessionLease
from agentos.distributed.redis._client import AsyncRedisClient, decode_text


_RENEW_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('PEXPIRE', KEYS[1], ARGV[2])
end
return 0
"""

_RELEASE_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


class RedisLeaseAdapter:
    """使用 Redis 原子 compare-and-renew 管理短期 Session Lease。"""

    def __init__(
        self,
        url: str | None = None,
        *,
        client: object | None = None,
        key_prefix: str = "agentos",
    ) -> None:
        if not key_prefix:
            raise ValueError("key_prefix must not be empty")
        self._redis = AsyncRedisClient(url, client)
        self._key_prefix = key_prefix.rstrip(":")

    async def acquire(
        self,
        *,
        scope: RequestScope,
        session_id: str,
        owner_id: str,
        ttl: timedelta,
    ) -> SessionLease | None:
        self._redis.ensure_open()
        ttl_ms = _ttl_milliseconds(ttl)
        lease = SessionLease(
            scope,
            session_id,
            owner_id,
            f"lease_{uuid4().hex}",
            datetime.now(UTC) + ttl,
        )
        acquired = await self._redis.call(
            "set",
            self._key(scope, session_id),
            _lease_payload(owner_id, lease.lease_id),
            nx=True,
            px=ttl_ms,
        )
        return lease if acquired else None

    async def renew(
        self,
        *,
        scope: RequestScope,
        lease: SessionLease,
        ttl: timedelta,
    ) -> SessionLease:
        self._redis.ensure_open()
        if lease.scope != scope:
            raise ClaimConflictError()
        ttl_ms = _ttl_milliseconds(ttl)
        requested_at = datetime.now(UTC)
        renewed = await self._redis.call(
            "eval",
            _RENEW_SCRIPT,
            1,
            self._key(scope, lease.session_id),
            _lease_payload(lease.owner_id, lease.lease_id),
            ttl_ms,
        )
        if renewed != 1:
            raise ClaimConflictError()
        return SessionLease(
            scope,
            lease.session_id,
            lease.owner_id,
            lease.lease_id,
            requested_at + ttl,
        )

    async def ensure_owned(
        self,
        *,
        scope: RequestScope,
        lease: SessionLease,
    ) -> None:
        self._redis.ensure_open()
        if lease.scope != scope:
            raise ClaimConflictError()
        current = await self._redis.call("get", self._key(scope, lease.session_id))
        if decode_text(current) != _lease_payload(lease.owner_id, lease.lease_id):
            raise ClaimConflictError()

    async def release(
        self,
        *,
        scope: RequestScope,
        lease: SessionLease,
    ) -> None:
        self._redis.ensure_open()
        if lease.scope != scope:
            return
        await self._redis.call(
            "eval",
            _RELEASE_SCRIPT,
            1,
            self._key(scope, lease.session_id),
            _lease_payload(lease.owner_id, lease.lease_id),
        )

    async def close(self) -> None:
        await self._redis.close()

    def _key(self, scope: RequestScope, session_id: str) -> str:
        tenant = quote(scope.tenant_id, safe="")
        session = quote(session_id, safe="")
        return f"{self._key_prefix}:lease:{tenant}:{session}"


def _lease_payload(owner_id: str, lease_id: str) -> str:
    return json.dumps(
        {"lease_id": lease_id, "owner_id": owner_id},
        separators=(",", ":"),
        sort_keys=True,
    )


def _ttl_milliseconds(ttl: timedelta) -> int:
    if not isinstance(ttl, timedelta):
        raise TypeError("ttl must be timedelta")
    milliseconds = int(ttl.total_seconds() * 1000)
    if milliseconds <= 0:
        raise ValueError("ttl must be positive")
    return milliseconds
