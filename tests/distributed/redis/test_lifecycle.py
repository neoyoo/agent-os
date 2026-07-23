import asyncio
from collections.abc import Callable
from datetime import timedelta

import pytest

from agentos.distributed.errors import (
    DeliveryUnavailableError,
    DistributedShutdownTimeoutError,
)
from agentos.distributed.models import RequestScope
from agentos.distributed.redis import _client as client_module
from agentos.distributed.redis.queue import RedisQueueAdapter
from agentos.distributed.redis.replay import RedisEventReplayAdapter

from _fake_redis import FakeAsyncRedis, FakeRedisError


AdapterFactory = Callable[[str], RedisQueueAdapter | RedisEventReplayAdapter]


class FailOnceCloseRedis(FakeAsyncRedis):
    def __init__(self) -> None:
        super().__init__()
        self.close_calls = 0

    async def aclose(self) -> None:
        self.close_calls += 1
        if self.close_calls == 1:
            raise FakeRedisError("redis://user:secret@example.invalid")
        await super().aclose()


class CancelOnceCloseRedis(FakeAsyncRedis):
    def __init__(self) -> None:
        super().__init__()
        self.close_calls = 0
        self.close_started = asyncio.Event()

    async def aclose(self) -> None:
        self.close_calls += 1
        if self.close_calls == 1:
            self.close_started.set()
            await asyncio.Event().wait()
        await super().aclose()


@pytest.mark.parametrize(
    "adapter_factory",
    [RedisQueueAdapter, RedisEventReplayAdapter],
)
def test_owned_adapter_close_can_retry_after_backend_failure(
    monkeypatch: pytest.MonkeyPatch,
    adapter_factory: AdapterFactory,
) -> None:
    async def scenario() -> None:
        redis = FailOnceCloseRedis()
        monkeypatch.setattr(client_module, "_create_client", lambda url: redis)
        adapter = adapter_factory("redis://example.invalid")

        with pytest.raises(DeliveryUnavailableError) as caught:
            await adapter.close()
        assert "secret" not in str(caught.value)
        await adapter.close()

        assert redis.close_calls == 2
        assert redis.closed is True

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "adapter_factory",
    [RedisQueueAdapter, RedisEventReplayAdapter],
)
def test_owned_adapter_close_can_retry_after_cancellation(
    monkeypatch: pytest.MonkeyPatch,
    adapter_factory: AdapterFactory,
) -> None:
    async def scenario() -> None:
        redis = CancelOnceCloseRedis()
        monkeypatch.setattr(client_module, "_create_client", lambda url: redis)
        adapter = adapter_factory("redis://example.invalid")

        closing = asyncio.create_task(adapter.close())
        await redis.close_started.wait()
        closing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await closing
        await adapter.close()

        assert redis.close_calls == 2
        assert redis.closed is True

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "adapter_factory",
    [RedisQueueAdapter, RedisEventReplayAdapter],
)
def test_owned_adapter_close_timeout_uses_shutdown_error(
    monkeypatch: pytest.MonkeyPatch,
    adapter_factory: AdapterFactory,
) -> None:
    async def scenario() -> None:
        redis = CancelOnceCloseRedis()
        monkeypatch.setattr(client_module, "_create_client", lambda url: redis)
        adapter = adapter_factory(
            "redis://example.invalid",
            operation_timeout=timedelta(milliseconds=10),
        )

        with pytest.raises(DistributedShutdownTimeoutError):
            await adapter.close()
        await adapter.close()

        assert redis.close_calls == 2
        assert redis.closed is True

    asyncio.run(scenario())


def test_replay_close_cancellation_cleans_active_subscription(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        redis = CancelOnceCloseRedis()
        monkeypatch.setattr(client_module, "_create_client", lambda url: redis)
        adapter = RedisEventReplayAdapter("redis://example.invalid", block_ms=30_000)
        subscription = adapter.follow(
            scope=RequestScope("tenant_1", "user_1"),
            session_id="session_1",
            run_id="run_1",
            after=None,
        )
        waiting = asyncio.create_task(anext(subscription))
        await redis.read_started.wait()

        closing = asyncio.create_task(adapter.close())
        await redis.close_started.wait()
        closing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await closing
        with pytest.raises(StopAsyncIteration):
            await waiting

        assert adapter._subscriptions == set()
        await adapter.close()
        assert redis.close_calls == 2
        assert redis.closed is True

    asyncio.run(scenario())
