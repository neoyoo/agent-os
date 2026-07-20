import pytest

from agentos.capabilities import (
    SideEffectPolicy,
    ToolConcurrencyPolicy,
)
from agentos.capabilities.tools import ToolExecutionContract
from agentos.providers import ProviderToolCall
from agentos.runtime.tool_invocations import (
    build_tool_invocation_plan,
    prepare_tool_invocation_batch,
)


def _plan(*names: str):
    return build_tool_invocation_plan(
        tenant_id=None,
        session_id="session_1",
        run_id="run_1",
        turn_id="turn_1",
        provider_call_index=0,
        assistant_message_id="message_1",
        calls=tuple(
            ProviderToolCall(f"call_{index}", name, {})
            for index, name in enumerate(names)
        ),
    )


def test_single_wait_capable_invocation_passes_preflight() -> None:
    batch = prepare_tool_invocation_batch(
        _plan("wait_for_input"),
        lambda _invocation: ToolExecutionContract(
            SideEffectPolicy.PURE,
            ToolConcurrencyPolicy.EXCLUSIVE,
            wait_capable=True,
        ),
    )

    assert batch.contracts[0].wait_capable is True


@pytest.mark.parametrize(
    "names",
    [("wait_for_input", "lookup"), ("wait_a", "wait_b")],
)
def test_wait_capable_mixed_batch_fails_before_execution(names: tuple[str, ...]) -> None:
    touched = []

    def contract_for(invocation):  # type: ignore[no-untyped-def]
        touched.append(invocation.tool_name)
        return ToolExecutionContract(
            SideEffectPolicy.PURE,
            ToolConcurrencyPolicy.EXCLUSIVE,
            wait_capable=invocation.tool_name.startswith("wait"),
        )

    with pytest.raises(ValueError, match="wait-capable"):
        prepare_tool_invocation_batch(_plan(*names), contract_for)

    assert touched == list(names)


def test_wait_capable_contract_requires_pure_exclusive() -> None:
    with pytest.raises(ValueError, match="wait_capable"):
        ToolExecutionContract(
            SideEffectPolicy.NON_RETRYABLE,
            ToolConcurrencyPolicy.EXCLUSIVE,
            wait_capable=True,
        )
