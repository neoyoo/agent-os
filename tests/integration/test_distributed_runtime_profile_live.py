from __future__ import annotations

import asyncio
from dataclasses import dataclass
from importlib import import_module
import os
from uuid import uuid4

import pytest

from agentos import AgentBuilder
from agentos.distributed import DistributedRuntimeProfile
from agentos.distributed.authorization import DenySideEffectResolutionAuthorizer
from agentos.distributed.blobs.s3 import S3BlobStore
from agentos.distributed.errors import RunNotFoundError
from agentos.distributed.migrations.service import (
    DistributedMigrationService,
    canonical_migration_plan,
)
from agentos.distributed.models import RequestScope, RunSubmission
from agentos.distributed.postgres._database import PostgresPool, fetchall
from agentos.distributed.postgres._outbox_records import (
    EXECUTION_TOPIC,
    STATUS_TOPIC,
)
from agentos.distributed.postgres.migrations import PostgresMigrationPort
from agentos.providers import FakeProvider, FilePart, ProviderResponse
from agentos.runtime.run_state import RunStatus


pytestmark = pytest.mark.integration


@dataclass(frozen=True, slots=True)
class _LiveSettings:
    postgres_dsn: str
    redis_url: str
    s3_endpoint_url: str
    s3_access_key_id: str
    s3_secret_access_key: str
    s3_bucket: str
    s3_region: str


def _live_settings() -> _LiveSettings:
    if os.environ.get("AGENTOS_RUN_INTEGRATION") != "1":
        pytest.skip("set AGENTOS_RUN_INTEGRATION=1 to run live integration tests")
    names = (
        "AGENTOS_TEST_POSTGRES_DSN",
        "AGENTOS_TEST_REDIS_URL",
        "AGENTOS_TEST_S3_ENDPOINT_URL",
        "AGENTOS_TEST_S3_ACCESS_KEY_ID",
        "AGENTOS_TEST_S3_SECRET_ACCESS_KEY",
        "AGENTOS_TEST_S3_BUCKET",
        "AGENTOS_TEST_S3_REGION",
    )
    values = {name: os.environ.get(name) for name in names}
    missing = tuple(name for name, value in values.items() if not value)
    if missing:
        pytest.skip(f"missing live integration settings: {', '.join(missing)}")
    return _LiveSettings(
        postgres_dsn=str(values["AGENTOS_TEST_POSTGRES_DSN"]),
        redis_url=str(values["AGENTOS_TEST_REDIS_URL"]),
        s3_endpoint_url=str(values["AGENTOS_TEST_S3_ENDPOINT_URL"]),
        s3_access_key_id=str(values["AGENTOS_TEST_S3_ACCESS_KEY_ID"]),
        s3_secret_access_key=str(values["AGENTOS_TEST_S3_SECRET_ACCESS_KEY"]),
        s3_bucket=str(values["AGENTOS_TEST_S3_BUCKET"]),
        s3_region=str(values["AGENTOS_TEST_S3_REGION"]),
    )


def _s3_session(settings: _LiveSettings) -> object:
    aioboto3 = import_module("aioboto3")
    return aioboto3.Session(
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=settings.s3_secret_access_key,
        region_name=settings.s3_region,
    )


async def _prepare_postgres(settings: _LiveSettings) -> None:
    pool = await PostgresPool.open(settings.postgres_dsn, min_size=0, max_size=2)
    try:
        await DistributedMigrationService(
            port=PostgresMigrationPort(pool),
            plan=canonical_migration_plan(),
        ).apply()
    finally:
        await pool.close()


async def _create_bucket(settings: _LiveSettings, bucket_name: str) -> None:
    session = _s3_session(settings)
    async with session.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        region_name=settings.s3_region,
    ) as client:
        await client.create_bucket(Bucket=bucket_name)


async def _open_blob_store(
    settings: _LiveSettings,
    bucket_name: str,
) -> S3BlobStore:
    return await S3BlobStore.open(
        bucket_name=bucket_name,
        endpoint_url=settings.s3_endpoint_url,
        region_name=settings.s3_region,
        session=_s3_session(settings),
    )


def _profile(
    settings: _LiveSettings,
    *,
    blob_store: S3BlobStore,
    suffix: str,
    worker_id: str,
    provider: FakeProvider | None = None,
) -> DistributedRuntimeProfile:
    return DistributedRuntimeProfile(
        agent_builder=AgentBuilder().provider(
            provider
            or FakeProvider([ProviderResponse(content="live distributed result")]),
        ),
        postgres_dsn=settings.postgres_dsn,
        redis_url=settings.redis_url,
        blob_store=blob_store,
        worker_id=worker_id,
        relay_id=f"relay_{suffix}",
        side_effect_resolution_authorizer=DenySideEffectResolutionAuthorizer(),
        key_prefix=f"agentos-live-{suffix}",
        postgres_min_size=0,
        postgres_max_size=4,
    )


async def _wait_for_terminal(
    profile: DistributedRuntimeProfile,
    scope: RequestScope,
    session_id: str,
    run_id: str,
):  # type: ignore[no-untyped-def]
    async def poll():  # type: ignore[no-untyped-def]
        while True:
            run = await profile.queries.get(scope, session_id, run_id)
            if run.status in {
                RunStatus.COMPLETED,
                RunStatus.FAILED,
                RunStatus.CANCELLED,
                RunStatus.WAITING,
            }:
                return run
            await asyncio.sleep(0.02)

    return await asyncio.wait_for(poll(), timeout=15)


async def _terminal_event_kinds(
    profile: DistributedRuntimeProfile,
    scope: RequestScope,
    session_id: str,
    run_id: str,
) -> tuple[str, ...]:
    subscription = await profile.events.subscribe(scope, session_id, run_id)
    kinds: list[str] = []
    try:
        while True:
            item = await asyncio.wait_for(anext(subscription), timeout=5)
            event_kind = item.event.event_kind  # type: ignore[union-attr]
            kinds.append(event_kind)
            if event_kind in {
                "turn_completed",
                "turn_failed",
                "turn_cancelled",
                "turn_waiting",
            }:
                return tuple(kinds)
    finally:
        await subscription.aclose()


async def _cleanup_postgres(settings: _LiveSettings, tenant_id: str) -> None:
    pool = await PostgresPool.open(settings.postgres_dsn, min_size=0, max_size=1)
    try:
        async with pool.transaction() as connection:
            for table in (
                "agentos_distributed_side_effects",
                "agentos_distributed_checkpoints",
                "agentos_distributed_outbox",
                "agentos_distributed_accepted_inputs",
                "agentos_distributed_submissions",
                "agentos_distributed_runs",
                "agentos_distributed_artifact_deletions",
                "agentos_distributed_artifacts",
                "agentos_distributed_sessions",
            ):
                await connection.execute(
                    f"DELETE FROM {table} WHERE tenant_id = %s",
                    (tenant_id,),
                )
    finally:
        await pool.close()


async def _published_outbox_topics(
    settings: _LiveSettings,
    tenant_id: str,
    run_id: str,
) -> tuple[str, ...]:
    pool = await PostgresPool.open(settings.postgres_dsn, min_size=0, max_size=1)
    try:
        async with pool.connection() as connection:
            rows = await fetchall(
                connection,
                """
                SELECT topic FROM agentos_distributed_outbox
                WHERE tenant_id = %s AND run_id = %s AND published_at IS NOT NULL
                ORDER BY topic
                """,
                (tenant_id, run_id),
            )
        return tuple(str(row["topic"]) for row in rows)
    finally:
        await pool.close()


async def _cleanup_redis(settings: _LiveSettings, key_prefix: str) -> None:
    redis_asyncio = import_module("redis.asyncio")
    client = redis_asyncio.Redis.from_url(settings.redis_url)
    try:
        keys = [key async for key in client.scan_iter(match=f"{key_prefix}:*")]
        if keys:
            await client.delete(*keys)
    finally:
        await client.aclose()


async def _cleanup_bucket(settings: _LiveSettings, bucket_name: str) -> None:
    session = _s3_session(settings)
    async with session.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        region_name=settings.s3_region,
    ) as client:
        response = await client.list_objects_v2(Bucket=bucket_name)
        objects = tuple(response.get("Contents", ()))
        if objects:
            await client.delete_objects(
                Bucket=bucket_name,
                Delete={"Objects": [{"Key": item["Key"]} for item in objects]},
            )
        await client.delete_bucket(Bucket=bucket_name)


def test_live_profile_recovers_artifact_on_another_worker_and_completes_run(
) -> None:
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(_verify_live_profile())


async def _verify_live_profile() -> None:
    settings = _live_settings()
    suffix = uuid4().hex
    tenant_id = f"tenant_{suffix}"
    scope = RequestScope(tenant_id, "principal_1")
    other_scope = RequestScope(f"other_{suffix}", "principal_1")
    session_id = f"session_{suffix}"
    bucket_name = f"{settings.s3_bucket}-{suffix}"[:63]
    key_prefix = f"agentos-live-{suffix}"
    first_blob: S3BlobStore | None = None
    second_blob: S3BlobStore | None = None
    first_profile: DistributedRuntimeProfile | None = None
    second_profile: DistributedRuntimeProfile | None = None
    bucket_created = False
    try:
        await _prepare_postgres(settings)
        await _create_bucket(settings, bucket_name)
        bucket_created = True

        first_blob = await _open_blob_store(settings, bucket_name)
        first_profile = _profile(
            settings,
            blob_store=first_blob,
            suffix=suffix,
            worker_id=f"worker_a_{suffix}",
        )
        await first_profile.open()
        artifact = await first_profile.artifacts.upload(
            scope,
            session_id,
            f"upload_{suffix}",
            b"%PDF-1.7\nshared drawing bytes",
            "drawing.pdf",
            "application/pdf",
        )
        await first_profile.close()
        first_profile = None
        await first_blob.close()
        first_blob = None

        second_blob = await _open_blob_store(settings, bucket_name)
        second_provider = FakeProvider(
            [ProviderResponse(content="live distributed result")],
        )
        second_profile = _profile(
            settings,
            blob_store=second_blob,
            suffix=suffix,
            worker_id=f"worker_b_{suffix}",
            provider=second_provider,
        )
        await second_profile.open()
        restored = await second_profile.artifacts.read(
            scope,
            session_id,
            artifact.id,
        )
        assert restored.data == b"%PDF-1.7\nshared drawing bytes"

        submission = RunSubmission(
            session_id,
            f"submission_{suffix}",
            "Inspect the mounted drawing.",
            (artifact.id,),
        )
        receipt = await second_profile.runs.submit(scope, submission)
        duplicate = await second_profile.runs.submit(scope, submission)
        assert duplicate.run_id == receipt.run_id
        assert duplicate.duplicate is True

        assert await second_profile.relay.relay_once() >= 2
        assert await _published_outbox_topics(
            settings,
            tenant_id,
            receipt.run_id,
        ) == tuple(sorted((EXECUTION_TOPIC, STATUS_TOPIC)))
        await second_profile.worker.start()
        run = await _wait_for_terminal(
            second_profile,
            scope,
            session_id,
            receipt.run_id,
        )
        await second_profile.worker.drain(timeout=5)

        assert run.status is RunStatus.COMPLETED
        assert run.result is not None
        assert run.result.content == "live distributed result"
        assert len(second_provider.requests) == 1
        mounts = tuple(
            item
            for item in second_provider.requests[0].messages
            if item.kind == "context_mount"
        )
        assert len(mounts) == 1
        mounted_files = tuple(
            part for part in mounts[0].content if type(part) is FilePart
        )
        assert len(mounted_files) == 1
        mounted_file = mounted_files[0]
        assert mounted_file.payload.handle == artifact.id
        assert mounted_file.payload.filename == "drawing.pdf"
        assert mounted_file.payload.media_type == "application/pdf"
        assert mounted_file.payload.data == b"%PDF-1.7\nshared drawing bytes"
        with pytest.raises(RunNotFoundError, match="^run not found$"):
            await second_profile.queries.get(
                other_scope,
                session_id,
                receipt.run_id,
            )
        event_kinds = await _terminal_event_kinds(
            second_profile,
            scope,
            session_id,
            receipt.run_id,
        )
        assert event_kinds[-1] == "turn_completed"
    finally:
        if second_profile is not None:
            await second_profile.close()
        if second_blob is not None:
            await second_blob.close()
        if first_profile is not None:
            await first_profile.close()
        if first_blob is not None:
            await first_blob.close()
        await _cleanup_redis(settings, key_prefix)
        await _cleanup_postgres(settings, tenant_id)
        if bucket_created:
            await _cleanup_bucket(settings, bucket_name)
