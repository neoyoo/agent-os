import inspect
import asyncio

from agentos.runtime import WaitReason
from agentos.runtime.run_commit import RunCommitRuntime
from agentos.runtime.run_driver import RunDriver
from agentos.runtime.run_runtime import RunWriteGuard
from agentos.runtime.waiting import WaitingCommit
from agentos.runtime.side_effect_types import WaitingToolCompletion


def test_run_driver_uses_run_commit_runtime_for_execution_outcomes() -> None:
    source = inspect.getsource(RunDriver)

    assert "self.commits.commit_waiting(" in source
    assert "self.commits.commit_terminal(" in source
    for direct_call in (
        "self.runs.wait(",
        "self.runs.complete(",
        "self.runs.fail(",
        "self.runs.cancel(",
    ):
        assert direct_call not in source


def test_waiting_commit_uses_atomic_version_without_reading_run_store() -> None:
    class AtomicWaitingRuntime:
        async def commit_waiting(
            self,
            *,
            run_id: str,
            turn_id: str,
            reason: WaitReason,
            guard: RunWriteGuard,
            completion: WaitingToolCompletion | None = None,
        ) -> WaitingCommit:
            return WaitingCommit(run_id, reason, aggregate_version=9)

    async def run() -> None:
        reason = WaitReason("human_input", "approval_1")
        runtime = RunCommitRuntime(
            runs=object(),  # type: ignore[arg-type]
            waiting=AtomicWaitingRuntime(),
        )

        guard = await runtime.commit_waiting(
            run_id="run_1",
            turn_id="turn_1",
            reason=reason,
            guard=RunWriteGuard(8),
        )

        assert guard == RunWriteGuard(9)

    asyncio.run(run())
