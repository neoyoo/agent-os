import pytest

from agentos._waiting import WaitReason, WaitRequest
from agentos.capabilities.invocation import ToolCompensationInvocation, ToolInvocation
from agentos.capabilities.tools import (
    RegisteredTool,
    SideEffectPolicy,
    ToolConcurrencyPolicy,
)


def _handler(_invocation: ToolInvocation) -> str:
    return "ok"


@pytest.mark.parametrize("policy", tuple(SideEffectPolicy))
def test_registered_tool_requires_an_explicit_side_effect_policy(
    policy: SideEffectPolicy,
) -> None:
    tool = RegisteredTool(
        name=f"tool_{policy.value}",
        description="Test tool.",
        parameters={"type": "object"},
        handler=_handler,
        side_effect_policy=policy,
        compensation_handler=(
            (lambda _invocation: None)
            if policy is SideEffectPolicy.COMPENSATABLE
            else None
        ),
    )

    assert tool.side_effect_policy is policy


def test_registered_tool_has_no_implicit_side_effect_policy() -> None:
    with pytest.raises(TypeError, match="side_effect_policy"):
        RegisteredTool(  # type: ignore[call-arg]
            name="missing_policy",
            description="Test tool.",
            parameters={"type": "object"},
            handler=_handler,
        )


def test_compensatable_requires_compensation_handler() -> None:
    with pytest.raises(ValueError, match="compensation"):
        RegisteredTool(
            name="charge",
            description="Charge an account.",
            parameters={"type": "object"},
            handler=_handler,
            side_effect_policy=SideEffectPolicy.COMPENSATABLE,
        )


def test_other_policies_reject_compensation_handler() -> None:
    def compensate(_invocation: ToolCompensationInvocation) -> None:
        return None

    with pytest.raises(ValueError, match="compensation"):
        RegisteredTool(
            name="lookup",
            description="Read data.",
            parameters={"type": "object"},
            handler=_handler,
            side_effect_policy=SideEffectPolicy.PURE,
            compensation_handler=compensate,
        )


def test_wait_capable_requires_pure_exclusive_policy() -> None:
    with pytest.raises(ValueError, match="wait_capable"):
        RegisteredTool(
            name="wait_for_approval",
            description="Wait for approval.",
            parameters={"type": "object"},
            handler=lambda _invocation: WaitRequest(
                WaitReason("human_input", "approval_1"),
            ),
            side_effect_policy=SideEffectPolicy.NON_RETRYABLE,
            wait_capable=True,
        )

    with pytest.raises(ValueError, match="wait_capable"):
        RegisteredTool(
            name="wait_in_parallel",
            description="Wait for approval.",
            parameters={"type": "object"},
            handler=lambda _invocation: WaitRequest(
                WaitReason("human_input", "approval_1"),
            ),
            side_effect_policy=SideEffectPolicy.PURE,
            concurrency_policy=ToolConcurrencyPolicy.PARALLEL_SAFE,
            wait_capable=True,
        )


def test_concurrency_and_side_effect_policies_are_independent() -> None:
    tool = RegisteredTool(
        name="parallel_lookup",
        description="Read data in parallel.",
        parameters={"type": "object"},
        handler=_handler,
        side_effect_policy=SideEffectPolicy.PURE,
        concurrency_policy=ToolConcurrencyPolicy.PARALLEL_SAFE,
    )

    assert tool.side_effect_policy is SideEffectPolicy.PURE
    assert tool.concurrency_policy is ToolConcurrencyPolicy.PARALLEL_SAFE


def test_registered_tool_rejects_invalid_runtime_policy_values() -> None:
    with pytest.raises(TypeError, match="concurrency_policy"):
        RegisteredTool(
            name="lookup",
            description="Read data.",
            parameters={"type": "object"},
            handler=_handler,
            side_effect_policy=SideEffectPolicy.PURE,
            concurrency_policy="parallel",  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="kind"):
        RegisteredTool(
            name="lookup",
            description="Read data.",
            parameters={"type": "object"},
            handler=_handler,
            side_effect_policy=SideEffectPolicy.PURE,
            kind="unknown",  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError, match="compensation_handler"):
        RegisteredTool(
            name="charge",
            description="Charge an account.",
            parameters={"type": "object"},
            handler=_handler,
            side_effect_policy=SideEffectPolicy.COMPENSATABLE,
            compensation_handler=42,  # type: ignore[arg-type]
        )
