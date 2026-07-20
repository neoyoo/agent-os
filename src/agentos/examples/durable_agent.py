from pathlib import Path

from agentos import AgentBuilder
from agentos.capabilities import (
    RegisteredTool,
    SideEffectPolicy,
    ToolConcurrencyPolicy,
    ToolInvocation,
    WaitRequest,
)
from agentos.durable import DurableRuntimeProfile
from agentos.providers import Provider
from agentos.runtime import WaitReason
from agentos.runtime.payloads import PayloadProtector


def _approval_tool() -> RegisteredTool:
    async def wait_for_approval(_invocation: ToolInvocation) -> WaitRequest:
        return WaitRequest(WaitReason("human_input", "approval_1"))

    return RegisteredTool(
        name="wait_for_approval",
        description="Pause the current run until a human answers.",
        parameters={"type": "object", "properties": {}},
        handler=wait_for_approval,
        side_effect_policy=SideEffectPolicy.PURE,
        concurrency_policy=ToolConcurrencyPolicy.EXCLUSIVE,
        wait_capable=True,
    )


def build_durable_profile(
    provider: Provider,
    state_root: str | Path,
    *,
    payload_protector: PayloadProtector,
) -> DurableRuntimeProfile:
    """构建由调用方通过 context manager 管理的单机 Durable Profile。"""

    root = Path(state_root)
    return DurableRuntimeProfile(
        agent_builder=(
            AgentBuilder().provider(provider).tools([_approval_tool()])
        ),
        database_path=root / "state.db",
        artifact_root=root / "artifacts",
        payload_protector=payload_protector,
    )


__all__ = ["build_durable_profile"]
