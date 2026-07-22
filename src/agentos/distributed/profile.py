from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Self

from agentos._builder_distributed import (
    ClaimScopedAgentFactory,
    validate_distributed_builder,
)
from agentos.distributed.blobs.protocol import BlobStore
from agentos.distributed.errors import DistributedStoreClosedError
from agentos.distributed.migrations.service import (
    DistributedMigrationService,
    canonical_migration_plan,
)
from agentos.distributed.postgres._database import PostgresPool
from agentos.distributed.postgres._outbox_records import EXECUTION_TOPIC
from agentos.distributed.postgres.artifacts import PostgresArtifactStore
from agentos.distributed.postgres.claims import PostgresClaimStore
from agentos.distributed.postgres.migrations import PostgresMigrationPort
from agentos.distributed.postgres.outbox import PostgresOutboxStore
from agentos.distributed.postgres.resume_validation import (
    PostgresSideEffectResumeValidator,
)
from agentos.distributed.postgres.side_effects import PostgresSideEffectStore
from agentos.distributed.postgres.state import PostgresStateStore
from agentos.distributed.redis.leases import RedisLeaseAdapter
from agentos.distributed.redis.queue import RedisQueueAdapter
from agentos.distributed.redis.replay import RedisEventReplayAdapter
from agentos.distributed.services import (
    ArtifactService,
    RunCommandService,
    RunEventStream,
    RunQueryService,
    RunSubmissionService,
)
from agentos.distributed.worker.relay import OutboxRelay
from agentos.distributed.worker.runner import WorkerRunner
from agentos.distributed.worker.supervisor import DistributedWorker
from agentos.runtime._async_bridge import _await_cleanup_preserving_cancellation
from agentos.runtime.payloads import PayloadProtector

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentos.builder import AgentBuilder


Clock = Callable[[], datetime]


@dataclass(slots=True)
class _ProfileResources:
    pool: PostgresPool | None = None
    state: PostgresStateStore | None = None
    artifacts: PostgresArtifactStore | None = None
    worker_queue: RedisQueueAdapter | None = None
    relay_queue: RedisQueueAdapter | None = None
    leases: RedisLeaseAdapter | None = None
    replay: RedisEventReplayAdapter | None = None
    worker: DistributedWorker | None = None
    relay: OutboxRelay | None = None
    runs: RunSubmissionService | None = None
    commands: RunCommandService | None = None
    queries: RunQueryService | None = None
    events: RunEventStream | None = None
    artifact_service: ArtifactService | None = None


class DistributedRuntimeProfile:
    """组合分布式 Adapter，并保持执行过程由权威 Claim 驱动。"""

    name = "distributed"

    def __init__(
        self,
        *,
        agent_builder: AgentBuilder,
        postgres_dsn: str,
        redis_url: str,
        blob_store: BlobStore,
        worker_id: str,
        relay_id: str,
        key_prefix: str = "agentos",
        queue_group_name: str = "agentos-workers",
        worker_max_concurrency: int = 1,
        postgres_min_size: int = 1,
        postgres_max_size: int = 10,
        claim_ttl: timedelta = timedelta(minutes=1),
        lease_ttl: timedelta = timedelta(seconds=30),
        heartbeat_interval: timedelta = timedelta(seconds=10),
        relay_batch_size: int = 100,
        relay_claim_ttl: timedelta = timedelta(minutes=1),
        payload_protector: PayloadProtector | None = None,
        clock: Clock | None = None,
    ) -> None:
        validate_distributed_builder(agent_builder)
        _validate_config(
            postgres_dsn=postgres_dsn,
            redis_url=redis_url,
            worker_id=worker_id,
            relay_id=relay_id,
            key_prefix=key_prefix,
            queue_group_name=queue_group_name,
            worker_max_concurrency=worker_max_concurrency,
            postgres_min_size=postgres_min_size,
            postgres_max_size=postgres_max_size,
            claim_ttl=claim_ttl,
            lease_ttl=lease_ttl,
            heartbeat_interval=heartbeat_interval,
            relay_batch_size=relay_batch_size,
            relay_claim_ttl=relay_claim_ttl,
            blob_store=blob_store,
        )
        self._builder = agent_builder
        self._postgres_dsn = postgres_dsn
        self._redis_url = redis_url
        self._blob_store = blob_store
        self._worker_id = worker_id
        self._relay_id = relay_id
        self._key_prefix = key_prefix
        self._queue_group_name = queue_group_name
        self._worker_max_concurrency = worker_max_concurrency
        self._postgres_min_size = postgres_min_size
        self._postgres_max_size = postgres_max_size
        self._claim_ttl = claim_ttl
        self._lease_ttl = lease_ttl
        self._heartbeat_interval = heartbeat_interval
        self._relay_batch_size = relay_batch_size
        self._relay_claim_ttl = relay_claim_ttl
        self._payload_protector = payload_protector
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lifecycle_lock = asyncio.Lock()
        self._state = "new"
        self._resources = _ProfileResources()

    @property
    def is_open(self) -> bool:
        return self._state == "open"

    async def open(self) -> Self:
        async with self._lifecycle_lock:
            if self._state == "open":
                return self
            if self._state != "new":
                raise DistributedStoreClosedError()
            self._state = "opening"
            try:
                await self._open_resources()
            except BaseException:
                try:
                    await _await_cleanup_preserving_cancellation(
                        self._close_resources,
                    )
                except BaseException:
                    self._state = "close_failed"
                    raise
                self._resources = _ProfileResources()
                self._state = "new"
                raise
            self._state = "open"
            return self

    async def __aenter__(self) -> Self:
        return await self.open()

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    async def close(self) -> None:
        async with self._lifecycle_lock:
            if self._state == "closed":
                return
            if self._state == "new":
                self._state = "closed"
                return
            if self._state not in {"open", "close_failed"}:
                raise DistributedStoreClosedError()
            self._state = "closing"
            try:
                await _await_cleanup_preserving_cancellation(
                    self._close_resources,
                )
            except BaseException:
                self._state = "close_failed"
                raise
            self._resources = _ProfileResources()
            self._state = "closed"

    @property
    def runs(self) -> RunSubmissionService:
        return self._require("runs")

    @property
    def commands(self) -> RunCommandService:
        return self._require("commands")

    @property
    def queries(self) -> RunQueryService:
        return self._require("queries")

    @property
    def events(self) -> RunEventStream:
        return self._require("events")

    @property
    def artifacts(self) -> ArtifactService:
        return self._require("artifact_service")

    @property
    def worker(self) -> DistributedWorker:
        return self._require("worker")

    @property
    def relay(self) -> OutboxRelay:
        return self._require("relay")

    async def _open_resources(self) -> None:
        resources = self._resources
        resources.pool = await PostgresPool.open(
            self._postgres_dsn,
            min_size=self._postgres_min_size,
            max_size=self._postgres_max_size,
        )
        await DistributedMigrationService(
            port=PostgresMigrationPort(resources.pool),
            plan=canonical_migration_plan(),
        ).check()
        resources.state = PostgresStateStore(resources.pool)
        claims = PostgresClaimStore(resources.pool)
        outbox = PostgresOutboxStore(resources.pool)
        side_effects = PostgresSideEffectStore(resources.pool)
        resume_validator = PostgresSideEffectResumeValidator(resources.pool)
        resources.artifacts = PostgresArtifactStore(
            resources.pool,
            self._blob_store,
        )
        queue_options = {
            "key_prefix": self._key_prefix,
            "group_name": self._queue_group_name,
        }
        resources.worker_queue = RedisQueueAdapter(
            self._redis_url,
            **queue_options,
        )
        resources.relay_queue = RedisQueueAdapter(
            self._redis_url,
            **queue_options,
        )
        resources.leases = RedisLeaseAdapter(
            self._redis_url,
            key_prefix=self._key_prefix,
        )
        resources.replay = RedisEventReplayAdapter(
            self._redis_url,
            key_prefix=self._key_prefix,
        )
        agent_factory = ClaimScopedAgentFactory(
            builder=self._builder,
            state_store=resources.state,
            artifact_store=resources.artifacts,
            side_effect_store=side_effects,
            side_effect_resume_validator=resume_validator,
            payload_protector=self._payload_protector,
        )
        runner = WorkerRunner(
            claims=claims,
            queue=resources.worker_queue,
            leases=resources.leases,
            agent_factory=agent_factory,
            event_sink=resources.replay,
            worker_id=self._worker_id,
            topic=EXECUTION_TOPIC,
            claim_ttl=self._claim_ttl,
            lease_ttl=self._lease_ttl,
            heartbeat_interval=self._heartbeat_interval,
            clock=self._clock,
        )
        resources.worker = DistributedWorker(
            runner=runner,
            queue=resources.worker_queue,
            worker_id=self._worker_id,
            topic=EXECUTION_TOPIC,
            max_concurrency=self._worker_max_concurrency,
            clock=self._clock,
        )
        resources.relay = OutboxRelay(
            outbox=outbox,
            queue=resources.relay_queue,
            owner_id=self._relay_id,
            batch_size=self._relay_batch_size,
            claim_ttl=self._relay_claim_ttl,
        )
        resources.runs = RunSubmissionService(resources.state)
        resources.commands = RunCommandService(resources.state)
        resources.queries = RunQueryService(resources.state)
        resources.events = RunEventStream(resources.state, resources.replay)
        resources.artifact_service = ArtifactService(resources.artifacts)

    async def _close_resources(self) -> None:
        resources = self._resources
        first_error: BaseException | None = None

        async def close(resource: object | None) -> None:
            nonlocal first_error
            if resource is None:
                return
            try:
                await resource.close()  # type: ignore[attr-defined]
            except BaseException as error:
                first_error = first_error or error

        await close(resources.relay)
        await close(resources.worker)
        if resources.worker is None:
            await close(resources.worker_queue)
        await close(resources.replay)
        await close(resources.leases)
        await close(resources.relay_queue)
        await close(resources.artifacts)
        await close(resources.pool)
        if first_error is not None:
            raise first_error

    def _require(self, attribute: str):  # type: ignore[no-untyped-def]
        if self._state != "open":
            raise DistributedStoreClosedError()
        value = getattr(self._resources, attribute)
        assert value is not None
        return value


def _validate_config(**values: object) -> None:
    for name in (
        "postgres_dsn",
        "redis_url",
        "worker_id",
        "relay_id",
        "key_prefix",
        "queue_group_name",
    ):
        value = values[name]
        if type(value) is not str or not value.strip():
            raise ValueError(f"{name} must not be empty")
    min_size = values["postgres_min_size"]
    max_size = values["postgres_max_size"]
    if (
        type(min_size) is not int
        or type(max_size) is not int
        or min_size < 0
        or max_size < 1
        or min_size > max_size
    ):
        raise ValueError("postgres pool size is invalid")
    for name in ("worker_max_concurrency", "relay_batch_size"):
        value = values[name]
        if type(value) is not int or value < 1:
            raise ValueError(f"{name} must be a positive integer")
    for name in (
        "claim_ttl",
        "lease_ttl",
        "heartbeat_interval",
        "relay_claim_ttl",
    ):
        value = values[name]
        if type(value) is not timedelta or value <= timedelta(0):
            raise ValueError(f"{name} must be positive")
    heartbeat = values["heartbeat_interval"]
    claim_ttl = values["claim_ttl"]
    lease_ttl = values["lease_ttl"]
    assert isinstance(heartbeat, timedelta)
    assert isinstance(claim_ttl, timedelta)
    assert isinstance(lease_ttl, timedelta)
    if heartbeat >= min(claim_ttl, lease_ttl):
        raise ValueError("heartbeat_interval must be less than claim and lease TTL")
    blob_store = values["blob_store"]
    if any(
        not callable(getattr(blob_store, name, None))
        for name in ("put_if_absent", "read", "delete", "close")
    ):
        raise TypeError("blob_store must satisfy BlobStore")


__all__ = ["DistributedRuntimeProfile"]
