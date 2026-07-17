from pathlib import Path

from agentos import AgentBuilder
from agentos.capabilities import RegisteredTool, WaitRequest
from agentos.durable import DurableRuntimeProfile
from agentos.providers import Provider
from agentos.runtime import WaitReason


def _approval_tool() -> RegisteredTool:
    async def wait_for_approval(_arguments: dict[str, object]) -> WaitRequest:
        return WaitRequest(WaitReason("human_input", "approval_1"))

    return RegisteredTool(
        name="wait_for_approval",
        description="Pause the current run until a human answers.",
        parameters={"type": "object", "properties": {}},
        handler=wait_for_approval,
    )


def build_durable_profile(
    provider: Provider,
    state_root: str | Path,
) -> DurableRuntimeProfile:
    """构建由调用方通过 context manager 管理的单机 Durable Profile。"""

    root = Path(state_root)
    return DurableRuntimeProfile(
        agent_builder=(
            AgentBuilder().provider(provider).tools([_approval_tool()])
        ),
        database_path=root / "state.db",
        artifact_root=root / "artifacts",
    )


__all__ = ["build_durable_profile"]
