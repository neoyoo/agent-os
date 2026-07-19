import asyncio
from collections.abc import Coroutine
from typing import Any, TypeVar

from agentos.runtime.run_runtime import RunRuntime, RunWriteGuard
from agentos.runtime.run_state import RunState


T = TypeVar("T")


def run(awaitable: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(awaitable)


async def create_running(runs: RunRuntime, run_id: str) -> RunState:
    created = await runs.create_run(run_id=run_id)
    queued = await runs.queue(
        run_id,
        guard=RunWriteGuard(expected_version=created.aggregate_version),
    )
    return await runs.start(
        run_id,
        guard=RunWriteGuard(expected_version=queued.aggregate_version),
    )
