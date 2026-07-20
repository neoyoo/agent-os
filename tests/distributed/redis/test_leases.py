import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from agentos.distributed.errors import (
    ClaimConflictError,
    DeliveryUnavailableError,
    DistributedStoreClosedError,
)
from agentos.distributed.models import RequestScope, SessionLease
from agentos.distributed.redis.leases import RedisLeaseAdapter
from agentos.distributed.redis import leases as leases_module

from _fake_redis import FakeAsyncRedis


SCOPE = RequestScope("tenant_1", "user_1")


class AdjustableDateTime(datetime):
    current = datetime(2026, 7, 20, 12, tzinfo=UTC)

    @classmethod
    def now(cls, tz: object = None) -> datetime:
        return cls.current


class DelayedRenewRedis(FakeAsyncRedis):
    async def eval(self, script: str, numkeys: int, *args: object) -> int:
        AdjustableDateTime.current += timedelta(seconds=10)
        return await super().eval(script, numkeys, *args)


def test_lease_acquire_renew_and_release_require_the_exact_owner() -> None:
    async def scenario() -> None:
        redis = FakeAsyncRedis()
        leases = RedisLeaseAdapter(client=redis, key_prefix="test")
        ttl = timedelta(seconds=30)

        lease = await leases.acquire(
            scope=SCOPE,
            session_id="session_1",
            owner_id="worker_1",
            ttl=ttl,
        )
        assert lease is not None
        assert await leases.acquire(
            scope=SCOPE,
            session_id="session_1",
            owner_id="worker_2",
            ttl=ttl,
        ) is None

        renewed = await leases.renew(scope=SCOPE, lease=lease, ttl=ttl)
        assert renewed.lease_id == lease.lease_id
        assert renewed.expires_at >= lease.expires_at
        await leases.ensure_owned(scope=SCOPE, lease=renewed)

        stale = SessionLease(
            SCOPE,
            "session_1",
            "worker_2",
            "lease_stale",
            datetime.now(UTC) + ttl,
        )
        with pytest.raises(ClaimConflictError):
            await leases.renew(scope=SCOPE, lease=stale, ttl=ttl)
        await leases.release(scope=SCOPE, lease=stale)
        await leases.ensure_owned(scope=SCOPE, lease=renewed)

        await leases.release(scope=SCOPE, lease=renewed)
        with pytest.raises(ClaimConflictError):
            await leases.ensure_owned(scope=SCOPE, lease=renewed)

    asyncio.run(scenario())


def test_lease_renew_expiry_is_conservative_under_response_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        redis = DelayedRenewRedis()
        leases = RedisLeaseAdapter(client=redis)
        ttl = timedelta(seconds=30)
        AdjustableDateTime.current = datetime(2026, 7, 20, 12, tzinfo=UTC)
        monkeypatch.setattr(leases_module, "datetime", AdjustableDateTime)
        lease = await leases.acquire(
            scope=SCOPE,
            session_id="session_1",
            owner_id="worker_1",
            ttl=ttl,
        )
        assert lease is not None
        requested_at = AdjustableDateTime.current

        renewed = await leases.renew(scope=SCOPE, lease=lease, ttl=ttl)

        assert renewed.expires_at == requested_at + ttl

    asyncio.run(scenario())


def test_lease_maps_redis_outage_to_safe_delivery_error() -> None:
    async def scenario() -> None:
        redis = FakeAsyncRedis()
        redis.fail = True
        leases = RedisLeaseAdapter(client=redis)

        with pytest.raises(DeliveryUnavailableError) as caught:
            await leases.acquire(
                scope=SCOPE,
                session_id="session_1",
                owner_id="worker_1",
                ttl=timedelta(seconds=30),
            )
        assert "secret" not in str(caught.value)

    asyncio.run(scenario())


def test_lease_close_rejects_new_work_without_closing_injected_client() -> None:
    async def scenario() -> None:
        redis = FakeAsyncRedis()
        leases = RedisLeaseAdapter(client=redis)

        await leases.close()

        assert redis.closed is False
        with pytest.raises(DistributedStoreClosedError):
            await leases.acquire(
                scope=SCOPE,
                session_id="session_1",
                owner_id="worker_1",
                ttl=timedelta(seconds=30),
            )

    asyncio.run(scenario())
