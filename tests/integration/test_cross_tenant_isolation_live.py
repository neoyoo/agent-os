from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest

from agentos import AgentBuilder
from agentos.artifacts.types import ArtifactNotFoundError
from agentos.distributed.authorization import DenySideEffectResolutionAuthorizer
from agentos.distributed.errors import RunNotFoundError
from agentos.distributed.models import RequestScope, RunSubmission
from agentos.distributed.postgres.state import PostgresStateStore
from agentos.distributed.profile import DistributedRuntimeProfile
from agentos.providers import FakeProvider, ProviderResponse
from agentos.runtime.durable_commands import DurableRunCommand
from agentos.runtime.run_state import RunStatus
from tests.integration._backend_restart_support import cleanup_redis
from tests.integration._distributed_failure_support import (
    cleanup_tenant,
    live_postgres_settings,
)


pytestmark = pytest.mark.integration


class _MemoryBlobStore:
    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}

    async def put_if_absent(self, *, artifact_id: str, data: bytes) -> bool:
        if artifact_id in self.data:
            return False
        self.data[artifact_id] = data
        return True

    async def read(self, *, artifact_id: str) -> bytes | None:
        return self.data.get(artifact_id)

    async def delete(self, *, artifact_id: str) -> None:
        self.data.pop(artifact_id, None)

    async def close(self) -> None:
        return None


def test_live_cross_tenant_ids_and_cursors_remain_isolated() -> None:
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(_verify_cross_tenant_isolation())


async def _verify_cross_tenant_isolation() -> None:
    settings = live_postgres_settings()
    redis_url = _redis_url()
    suffix = uuid4().hex
    scope = RequestScope(f"tenant_isolation_{suffix}", "principal_1")
    other = RequestScope(f"tenant_other_{suffix}", "principal_1")
    session_id = f"session_{suffix}"
    key_prefix = f"agentos-tenant-isolation-{suffix}"
    profile = DistributedRuntimeProfile(
        agent_builder=AgentBuilder().provider(
            FakeProvider([ProviderResponse("isolated result")]),
        ),
        postgres_dsn=settings.dsn,
        redis_url=redis_url,
        blob_store=_MemoryBlobStore(),
        worker_id=f"worker_{suffix}",
        relay_id=f"relay_{suffix}",
        side_effect_resolution_authorizer=(
            DenySideEffectResolutionAuthorizer()
        ),
        key_prefix=key_prefix,
        queue_group_name="workers",
        postgres_min_size=0,
        postgres_max_size=3,
    )
    await profile.open()
    state = profile.runs.port
    assert isinstance(state, PostgresStateStore)
    try:
        first = await profile.artifacts.upload(
            scope,
            session_id,
            f"upload_1_{suffix}",
            b"%PDF-1.7\nfirst",
            "first.pdf",
            "application/pdf",
        )
        await profile.artifacts.upload(
            scope,
            session_id,
            f"upload_2_{suffix}",
            b"%PDF-1.7\nsecond",
            "second.pdf",
            "application/pdf",
        )
        page = await profile.artifacts.list(scope, session_id, None, 1)
        assert page.next_cursor is not None

        receipt = await profile.runs.submit(
            scope,
            RunSubmission(
                session_id,
                f"submission_{suffix}",
                "verify tenant isolation",
            ),
        )
        assert await profile.relay.relay_once() >= 1
        await profile.worker.start()
        await _wait_for_status(
            profile,
            scope=scope,
            session_id=session_id,
            run_id=receipt.run_id,
            status=RunStatus.COMPLETED,
        )
        await profile.worker.drain(timeout=2)
        own_cursor = await profile.events.capture_high_water(
            scope,
            session_id,
            receipt.run_id,
        )
        assert own_cursor is not None

        with pytest.raises(RunNotFoundError, match="^run not found$"):
            await profile.queries.get(other, session_id, receipt.run_id)
        assert await profile.queries.get_active(other, session_id) is None
        with pytest.raises(RunNotFoundError, match="^run not found$"):
            await profile.commands.submit(
                other,
                session_id,
                DurableRunCommand(
                    receipt.run_id,
                    f"command_{suffix}",
                    "cancel",
                ),
            )
        assert await state.bind(other).load_checkpoint(session_id) is None

        with pytest.raises(ArtifactNotFoundError):
            await profile.artifacts.read(
                other,
                session_id,
                first.id,
            )
        with pytest.raises(ArtifactNotFoundError):
            await profile.artifacts.list(
                other,
                session_id,
                page.next_cursor,
                1,
            )

        with pytest.raises(RunNotFoundError, match="^run not found$"):
            await profile.events.subscribe(
                other,
                session_id,
                receipt.run_id,
                own_cursor,
            )
        other_cursor = await profile.events.replay_port.high_water(
            scope=other,
            session_id=session_id,
            run_id=receipt.run_id,
        )
        assert other_cursor is None
    finally:
        await cleanup_tenant(state._database, scope.tenant_id)
        await cleanup_tenant(state._database, other.tenant_id)
        await profile.close()
        await cleanup_redis(redis_url, key_prefix)


async def _wait_for_status(
    profile: DistributedRuntimeProfile,
    *,
    scope: RequestScope,
    session_id: str,
    run_id: str,
    status: RunStatus,
) -> None:
    async def poll() -> None:
        while True:
            run = await profile.queries.get(scope, session_id, run_id)
            if run.status is status:
                return
            await asyncio.sleep(0)

    await asyncio.wait_for(poll(), timeout=3)


def _redis_url() -> str:
    redis_url = os.environ.get("AGENTOS_TEST_REDIS_URL")
    if not redis_url:
        pytest.skip("set AGENTOS_TEST_REDIS_URL to run live Redis tests")
    return redis_url
