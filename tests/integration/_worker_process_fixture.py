from __future__ import annotations

import asyncio
from datetime import timedelta
import os
from pathlib import Path

from agentos import AgentBuilder
from agentos.distributed import DistributedRuntimeProfile
from agentos.distributed.authorization import DenySideEffectResolutionAuthorizer
from agentos.providers import ProviderRequest, ProviderResponse
from tests.integration._distributed_failure_support import NoopBlobStore


class _BlockingProvider:
    async def async_complete(self, _request: ProviderRequest) -> ProviderResponse:
        Path(_required("AGENTOS_PROCESS_MARKER")).write_text("ready", encoding="ascii")
        await asyncio.Event().wait()
        raise AssertionError("blocking provider must be terminated with its process")


async def _run() -> None:
    profile = DistributedRuntimeProfile(
        agent_builder=AgentBuilder().provider(_BlockingProvider()),
        postgres_dsn=_required("AGENTOS_TEST_POSTGRES_DSN"),
        redis_url=_required("AGENTOS_TEST_REDIS_URL"),
        blob_store=NoopBlobStore(),
        worker_id=_required("AGENTOS_PROCESS_WORKER_ID"),
        relay_id="unused_process_relay",
        side_effect_resolution_authorizer=DenySideEffectResolutionAuthorizer(),
        key_prefix=_required("AGENTOS_PROCESS_KEY_PREFIX"),
        queue_group_name="workers",
        postgres_min_size=0,
        postgres_max_size=2,
        claim_ttl=timedelta(seconds=5),
        lease_ttl=timedelta(seconds=1),
        heartbeat_interval=timedelta(milliseconds=200),
        heartbeat_cycle_timeout=timedelta(milliseconds=300),
    )
    async with profile:
        await profile.worker.start()
        await profile.worker.wait()


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"missing process setting: {name}")
    return value


if __name__ == "__main__":
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(_run())
