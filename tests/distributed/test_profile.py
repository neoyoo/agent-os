from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

import agentos.distributed.profile as profile_module
from agentos import AgentBuilder
from agentos.distributed import DistributedRuntimeProfile
from agentos.distributed.authorization import DenySideEffectResolutionAuthorizer
from agentos.distributed.errors import DistributedStoreClosedError
from agentos.distributed.errors import DistributedShutdownTimeoutError
from agentos.providers import FakeProvider
from tests.planning._async import async_test


class _Pool:
    def __init__(self, trace: list[str]) -> None:
        self.trace = trace

    async def close(self) -> None:
        self.trace.append("postgres.close")


class _OpeningPool:
    trace: list[str]
    pool: _Pool

    @classmethod
    async def open(
        cls,
        dsn: str,
        *,
        min_size: int,
        max_size: int,
        operation_timeout: timedelta,
    ) -> _Pool:
        assert dsn == "postgresql://agentos"
        assert (min_size, max_size) == (1, 10)
        assert operation_timeout == timedelta(seconds=5)
        cls.trace.append("postgres.open")
        return cls.pool


class _StateStore:
    instances: list[_StateStore] = []

    def __init__(self, pool: _Pool) -> None:
        self.pool = pool
        self.instances.append(self)


class _MigrationPort:
    check_error: BaseException | None = None

    def __init__(self, pool: _Pool) -> None:
        self.pool = pool

    async def check(self, plan: object) -> None:
        del plan
        self.pool.trace.append("postgres.migration.check")
        if self.check_error is not None:
            raise self.check_error


class _OwnedArtifactStore:
    def __init__(self, pool: _Pool, blobs: object, **kwargs: object) -> None:
        assert kwargs.get("owns_blobs", False) is False
        self.pool = pool
        self.blobs = blobs

    async def close(self) -> None:
        self.pool.trace.append("artifacts.close")


class _Adapter:
    def __init__(self, pool: _Pool) -> None:
        self.pool = pool


class _Queue:
    instances: list[_Queue] = []
    trace: list[str]

    def __init__(self, url: str, **kwargs: object) -> None:
        assert url == "redis://agentos"
        assert kwargs["key_prefix"] == "agentos"
        self.name = f"queue_{len(self.instances)}"
        self.closed = 0
        self.receive_started = asyncio.Event()
        self.instances.append(self)

    async def reclaim(self, **kwargs: object):  # type: ignore[no-untyped-def]
        del kwargs
        return ()

    async def receive(self, **kwargs: object):  # type: ignore[no-untyped-def]
        del kwargs
        self.receive_started.set()
        await asyncio.Event().wait()

    async def close(self) -> None:
        self.trace.append(f"{self.name}.close")
        self.closed += 1


class _ClosableRedis:
    instances: list[_ClosableRedis] = []
    trace: list[str]

    def __init__(self, url: str, **kwargs: object) -> None:
        assert url == "redis://agentos"
        assert kwargs["key_prefix"] == "agentos"
        self.name = f"redis_{len(self.instances)}"
        self.closed = 0
        self.instances.append(self)

    async def close(self) -> None:
        self.trace.append(f"{self.name}.close")
        self.closed += 1


class _BlobStore:
    def __init__(self, trace: list[str]) -> None:
        self.trace = trace
        self.closed = 0

    async def put_if_absent(self, *, artifact_id: str, data: bytes) -> bool:
        del artifact_id, data
        return True

    async def read(self, *, artifact_id: str) -> bytes | None:
        del artifact_id
        return None

    async def delete(self, *, artifact_id: str) -> None:
        del artifact_id

    async def close(self) -> None:
        self.trace.append("blob.close")
        self.closed += 1


def _patch_adapters(
    monkeypatch: pytest.MonkeyPatch,
    trace: list[str],
) -> tuple[_Pool, _BlobStore]:
    pool = _Pool(trace)
    _OpeningPool.trace = trace
    _OpeningPool.pool = pool
    _StateStore.instances = []
    _MigrationPort.check_error = None
    _Queue.instances = []
    _Queue.trace = trace
    _ClosableRedis.instances = []
    _ClosableRedis.trace = trace
    blob = _BlobStore(trace)
    monkeypatch.setattr(profile_module, "PostgresPool", _OpeningPool)
    monkeypatch.setattr(profile_module, "PostgresMigrationPort", _MigrationPort)
    monkeypatch.setattr(profile_module, "PostgresStateStore", _StateStore)
    monkeypatch.setattr(profile_module, "PostgresArtifactStore", _OwnedArtifactStore)
    monkeypatch.setattr(profile_module, "PostgresClaimStore", _Adapter)
    monkeypatch.setattr(profile_module, "PostgresOutboxStore", _Adapter)
    monkeypatch.setattr(profile_module, "PostgresSideEffectStore", _Adapter)
    monkeypatch.setattr(profile_module, "PostgresSideEffectResumeValidator", _Adapter)
    monkeypatch.setattr(profile_module, "RedisQueueAdapter", _Queue)
    monkeypatch.setattr(profile_module, "RedisLeaseAdapter", _ClosableRedis)
    monkeypatch.setattr(profile_module, "RedisEventReplayAdapter", _ClosableRedis)
    return pool, blob


def _profile(
    blob_store: object,
    *,
    shutdown_cleanup_timeout: timedelta = timedelta(seconds=5),
    relay_batch_timeout: timedelta = timedelta(seconds=30),
) -> DistributedRuntimeProfile:
    return DistributedRuntimeProfile(
        agent_builder=AgentBuilder().provider(FakeProvider([])),
        postgres_dsn="postgresql://agentos",
        redis_url="redis://agentos",
        blob_store=blob_store,  # type: ignore[arg-type]
        worker_id="worker_1",
        relay_id="relay_1",
        side_effect_resolution_authorizer=DenySideEffectResolutionAuthorizer(),
        shutdown_cleanup_timeout=shutdown_cleanup_timeout,
        relay_batch_timeout=relay_batch_timeout,
    )


@async_test
async def test_profile_constructor_is_io_free_and_worker_start_is_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace: list[str] = []
    _, blob = _patch_adapters(monkeypatch, trace)

    profile = _profile(blob)

    assert trace == []
    assert not profile.is_open


    with pytest.raises(DistributedStoreClosedError):
        _ = profile.worker

    services = await profile.open()

    assert services is profile
    assert trace == ["postgres.open", "postgres.migration.check"]
    assert profile.worker.state.status == "created"
    assert len(_Queue.instances) == 2
    assert profile.worker._queue is not profile.relay.queue
    assert profile.runs.port is _StateStore.instances[0]

    await profile.close()
    await profile.close()

    assert [queue.closed for queue in _Queue.instances] == [1, 1]
    assert [adapter.closed for adapter in _ClosableRedis.instances] == [1, 1]
    assert blob.closed == 0
    assert trace[-6:] == [
        "queue_0.close",
        "redis_1.close",
        "redis_0.close",
        "queue_1.close",
        "artifacts.close",
        "postgres.close",
    ]
    assert not profile.is_open


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {
                "heartbeat_cycle_timeout": timedelta(seconds=20),
                "lease_ttl": timedelta(seconds=30),
            },
            "lease_ttl",
        ),
        (
            {
                "heartbeat_cycle_timeout": timedelta(seconds=50),
                "claim_ttl": timedelta(seconds=60),
                "lease_ttl": timedelta(seconds=90),
            },
            "claim_ttl",
        ),
    ],
)
def test_profile_rejects_heartbeat_budget_that_can_expire_ownership(
    overrides: dict[str, timedelta],
    message: str,
) -> None:
    trace: list[str] = []
    blob = _BlobStore(trace)

    with pytest.raises(ValueError, match=message):
        DistributedRuntimeProfile(
            agent_builder=AgentBuilder().provider(FakeProvider([])),
            postgres_dsn="postgresql://agentos",
            redis_url="redis://agentos",
            blob_store=blob,
            worker_id="worker_1",
            relay_id="relay_1",
            side_effect_resolution_authorizer=(
                DenySideEffectResolutionAuthorizer()
            ),
            **overrides,
        )


@async_test
async def test_worker_only_starts_when_the_deployment_host_requests_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace: list[str] = []
    _, blob = _patch_adapters(monkeypatch, trace)
    profile = _profile(blob)
    await profile.open()

    assert profile.worker.state.status == "created"
    worker = profile.worker
    await worker.start()
    await _Queue.instances[0].receive_started.wait()
    assert worker.state.status == "running"

    await profile.close()
    assert worker.state.status == "closed"


@async_test
async def test_profile_schema_check_failure_closes_acquired_postgres_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace: list[str] = []
    _, blob = _patch_adapters(monkeypatch, trace)
    _MigrationPort.check_error = RuntimeError("schema unavailable")
    profile = _profile(blob)

    with pytest.raises(RuntimeError, match="schema unavailable"):
        await profile.open()

    assert trace == [
        "postgres.open",
        "postgres.migration.check",
        "postgres.close",
    ]
    assert not profile.is_open


@async_test
async def test_partial_open_closes_worker_queue_before_worker_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingAgentFactory:
        def __init__(self, **kwargs: object) -> None:
            del kwargs
            raise RuntimeError("agent factory failed")

    trace: list[str] = []
    _, blob = _patch_adapters(monkeypatch, trace)
    monkeypatch.setattr(
        profile_module,
        "ClaimScopedAgentFactory",
        FailingAgentFactory,
    )
    profile = _profile(blob)

    with pytest.raises(RuntimeError, match="agent factory failed"):
        await profile.open()

    assert [queue.closed for queue in _Queue.instances] == [1, 1]
    assert "queue_0.close" in trace
    assert "queue_1.close" in trace


@async_test
async def test_profile_close_worker_failure_still_closes_worker_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingClose:
        async def close(self) -> None:
            raise RuntimeError("worker close failed")

    trace: list[str] = []
    _, blob = _patch_adapters(monkeypatch, trace)
    profile = _profile(blob)
    await profile.open()
    profile._resources.worker = FailingClose()  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="worker close failed"):
        await profile.close()

    assert _Queue.instances[0].closed == 1


@pytest.mark.parametrize("blocking_resource", ("relay", "worker"))
@async_test
async def test_profile_close_timeout_still_attempts_later_resources(
    monkeypatch: pytest.MonkeyPatch,
    blocking_resource: str,
) -> None:
    class BlockingClose:
        async def close(self) -> None:
            await asyncio.Event().wait()

    trace: list[str] = []
    _, blob = _patch_adapters(monkeypatch, trace)
    profile = _profile(
        blob,
        shutdown_cleanup_timeout=timedelta(milliseconds=10),
        relay_batch_timeout=timedelta(seconds=10),
    )
    await profile.open()
    setattr(profile._resources, blocking_resource, BlockingClose())

    with pytest.raises(DistributedShutdownTimeoutError):
        await asyncio.wait_for(profile.close(), timeout=0.2)

    assert "queue_0.close" in trace
    assert "redis_1.close" in trace
    assert "redis_0.close" in trace
    assert "queue_1.close" in trace
    assert "artifacts.close" in trace
    assert "postgres.close" in trace
