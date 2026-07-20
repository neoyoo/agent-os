from __future__ import annotations

import asyncio

import pytest

import agentos.distributed.profile as profile_module
from agentos import AgentBuilder
from agentos.distributed import DistributedRuntimeProfile
from agentos.distributed.errors import DistributedStoreClosedError
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
    async def open(cls, dsn: str, *, min_size: int, max_size: int) -> _Pool:
        assert dsn == "postgresql://agentos"
        assert (min_size, max_size) == (1, 10)
        cls.trace.append("postgres.open")
        return cls.pool


class _StateStore:
    instances: list[_StateStore] = []
    initialize_error: BaseException | None = None

    def __init__(self, pool: _Pool) -> None:
        self.pool = pool
        self.instances.append(self)

    async def initialize(self) -> None:
        self.pool.trace.append("postgres.initialize")
        if self.initialize_error is not None:
            raise self.initialize_error


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
    _StateStore.initialize_error = None
    _Queue.instances = []
    _Queue.trace = trace
    _ClosableRedis.instances = []
    _ClosableRedis.trace = trace
    blob = _BlobStore(trace)
    monkeypatch.setattr(profile_module, "PostgresPool", _OpeningPool)
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
) -> DistributedRuntimeProfile:
    return DistributedRuntimeProfile(
        agent_builder=AgentBuilder().provider(FakeProvider([])),
        postgres_dsn="postgresql://agentos",
        redis_url="redis://agentos",
        blob_store=blob_store,  # type: ignore[arg-type]
        worker_id="worker_1",
        relay_id="relay_1",
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
    assert trace == ["postgres.open", "postgres.initialize"]
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
async def test_profile_open_failure_closes_acquired_postgres_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace: list[str] = []
    _, blob = _patch_adapters(monkeypatch, trace)
    _StateStore.initialize_error = RuntimeError("schema unavailable")
    profile = _profile(blob)

    with pytest.raises(RuntimeError, match="schema unavailable"):
        await profile.open()

    assert trace == [
        "postgres.open",
        "postgres.initialize",
        "postgres.close",
    ]
    assert not profile.is_open
