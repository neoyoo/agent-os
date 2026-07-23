from __future__ import annotations

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest

from agentos import AgentBuilder
from agentos.distributed.blobs.s3 import S3BlobStore
from agentos.distributed.models import RequestScope, RunSubmission
from agentos.distributed.postgres._database import PostgresPool
from agentos.distributed.postgres.artifacts import PostgresArtifactStore
from agentos.distributed.postgres.claims import PostgresClaimStore
from agentos.distributed.postgres.state import PostgresStateStore
from agentos.providers import ImagePart, ProviderRequest, ProviderResponse, TextPart
from agentos.runtime import AgentResult
from agentos.runtime.execution import RestoreAcceptedTurn
from agentos.security import FernetPayloadProtector
from tests.integration._artifact_live_support import (
    ArtifactLiveSettings,
    artifact_live_settings,
    cleanup_bucket,
    create_bucket,
    s3_session,
)
from tests.integration._distributed_failure_support import (
    ProcessCrash,
    claim_scoped_agent_factory,
    cleanup_tenant,
    execution_outbox_id,
    expire_and_reclaim,
    open_migrated_pool,
)


pytestmark = pytest.mark.integration
_DATA = b"\x89PNG\r\n\x1a\nfull-restart-artifact"
_CONTENT = "recover checkpoint and inspect the uploaded drawing"


class _CrashAfterRequest:
    def __init__(self) -> None:
        self.requests: list[ProviderRequest] = []

    async def async_complete(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        raise ProcessCrash


class _RecoveryProvider:
    def __init__(self) -> None:
        self.requests: list[ProviderRequest] = []

    async def async_complete(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        return ProviderResponse("restart recovered")


def test_live_full_restart_restores_checkpoint_and_artifact_bytes() -> None:
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(_verify_full_restart())


async def _verify_full_restart() -> None:
    settings = artifact_live_settings()
    suffix = uuid4().hex
    scope = RequestScope(f"tenant_full_restart_{suffix}", "principal_1")
    session_id = f"session_{suffix}"
    upload_id = f"upload_{suffix}"
    bucket = f"{settings.bucket}-{suffix}"[:63]
    key = FernetPayloadProtector.generate_key()
    session_a = s3_session(settings)
    session_b = s3_session(settings)
    pool_a: PostgresPool | None = None
    pool_b: PostgresPool | None = None
    blobs_a: S3BlobStore | None = None
    blobs_b: S3BlobStore | None = None
    bucket_created = False
    try:
        pool_a = await open_migrated_pool(settings.postgres_dsn)
        await create_bucket(settings, session_a, bucket)
        bucket_created = True
        blobs_a = await _open_blobs(settings, session_a, bucket)
        artifacts_a = PostgresArtifactStore(pool_a, blobs_a)
        artifact = await artifacts_a.upload(
            scope=scope,
            session_id=session_id,
            upload_id=upload_id,
            data=_DATA,
            filename="drawing.png",
            media_type="image/png",
        )
        receipt = await PostgresStateStore(pool_a).submit(
            scope=scope,
            submission=RunSubmission(
                session_id,
                f"submission_{suffix}",
                _CONTENT,
                (artifact.id,),
            ),
        )
        outbox_id = await execution_outbox_id(
            pool_a,
            scope=scope,
            session_id=session_id,
            run_id=receipt.run_id,
        )
        claimed = await PostgresClaimStore(pool_a).claim_pending_turn(
            scope=scope,
            outbox_id=outbox_id,
            owner_id=f"worker_a_{suffix}",
            ttl=timedelta(minutes=1),
        )
        assert claimed is not None

        crashing = _CrashAfterRequest()
        agent_a = await claim_scoped_agent_factory(
            pool=pool_a,
            builder=AgentBuilder().provider(crashing),
            state=PostgresStateStore(pool_a),
            artifacts=artifacts_a,
            protector=FernetPayloadProtector(key),
        ).hydrate(claimed=claimed)
        with pytest.raises(ProcessCrash):
            await agent_a.run(claimed.execution)
        _assert_request(crashing.requests[0], artifact.id)

        checkpoint = await PostgresStateStore(pool_a).bind(scope).load_checkpoint(
            session_id,
        )
        assert checkpoint is not None
        assert checkpoint.execution_cursor is not None
        assert checkpoint.execution_cursor.stage == "before_provider"
        assert checkpoint.messages[0].artifact_refs[0].artifact_id == artifact.id

        await blobs_a.close()
        blobs_a = None
        await pool_a.close()
        pool_a = None

        pool_b = await open_migrated_pool(settings.postgres_dsn)
        blobs_b = await _open_blobs(settings, session_b, bucket)
        artifacts_b = PostgresArtifactStore(pool_b, blobs_b)
        claims_b = PostgresClaimStore(pool_b)
        recovered = await expire_and_reclaim(
            pool_b,
            claims=claims_b,
            scope=scope,
            session_id=session_id,
            owner_id=f"worker_b_{suffix}",
        )
        preparation = recovered.execution.preparation
        assert type(preparation) is RestoreAcceptedTurn
        assert preparation.cursor.stage == "before_provider"

        provider_b = _RecoveryProvider()
        agent_b = await claim_scoped_agent_factory(
            pool=pool_b,
            builder=AgentBuilder().provider(provider_b),
            state=PostgresStateStore(pool_b),
            artifacts=artifacts_b,
            protector=FernetPayloadProtector(key),
        ).hydrate(claimed=recovered)
        outcome = await agent_b.run(recovered.execution)

        assert outcome == AgentResult("restart recovered")
        assert len(provider_b.requests) == 1
        _assert_request(provider_b.requests[0], artifact.id)
        assert agent_b.artifacts.active_mounts() == ()
        assert (
            await artifacts_b.read(
                scope=scope,
                session_id=session_id,
                artifact_id=artifact.id,
            )
        ).data == _DATA

        final = await PostgresStateStore(pool_b).bind(scope).load_checkpoint(session_id)
        assert final is not None
        assert final.execution_cursor is None
        assert [message.role for message in final.messages] == ["user", "assistant"]
        assert final.messages[0].artifact_refs[0].artifact_id == artifact.id
    finally:
        cleanup_pool = pool_b or pool_a
        owns_cleanup_pool = False
        try:
            if cleanup_pool is None:
                cleanup_pool = await open_migrated_pool(settings.postgres_dsn)
                owns_cleanup_pool = True
            await cleanup_tenant(cleanup_pool, scope.tenant_id)
        finally:
            if blobs_b is not None:
                await blobs_b.close()
            if blobs_a is not None:
                await blobs_a.close()
            if pool_b is not None:
                await pool_b.close()
            if pool_a is not None:
                await pool_a.close()
            if owns_cleanup_pool:
                await cleanup_pool.close()
            if bucket_created:
                await cleanup_bucket(settings, session_b, bucket)


async def _open_blobs(
    settings: ArtifactLiveSettings,
    session: object,
    bucket: str,
) -> S3BlobStore:
    return await S3BlobStore.open(
        bucket_name=bucket,
        endpoint_url=settings.endpoint_url,
        region_name=settings.region,
        session=session,
    )


def _assert_request(request: ProviderRequest, artifact_id: str) -> None:
    assert [item.kind for item in request.messages] == [
        "context_snapshot",
        "business_message",
        "context_mount",
    ]
    business = request.messages[1]
    assert business.content == (TextPart(_CONTENT),)
    mount = request.messages[2]
    assert len(mount.content) == 2
    image = mount.content[1]
    assert type(image) is ImagePart
    assert image.payload.handle == artifact_id
    assert image.payload.filename == "drawing.png"
    assert image.payload.media_type == "image/png"
    assert image.payload.data == _DATA
