import pytest

from agentos.capabilities.invocation import ToolInvocation, ToolInvocationContext
from agentos.runtime.tool_identity import (
    compensation_operation_id,
    invocation_digest,
    invocation_id,
    operation_id,
)


def test_tool_identity_v1_golden_vectors() -> None:
    stable_invocation_id = invocation_id(
        tenant_id=None,
        session_id="session_1",
        run_id="run_1",
        turn_id="turn_1",
        provider_call_index=0,
        tool_index=0,
    )
    stable_operation_id = operation_id(
        tenant_id=None,
        session_id="session_1",
        run_id="run_1",
        turn_id="turn_1",
        invocation_id=stable_invocation_id,
    )

    assert stable_invocation_id == "invocation_ea91f27fdf596bceb7c90d2578c5e988"
    assert stable_operation_id == "operation_d340f3861e0c6a7eefbaf707fdc69d3d"
    assert compensation_operation_id(stable_operation_id) == (
        "operation_bf3249c6dee281bc70712f707141d93a"
    )


def test_operation_identity_is_stable_across_handler_attempts() -> None:
    stable_invocation_id = invocation_id(
        tenant_id="tenant_1",
        session_id="session_1",
        run_id="run_1",
        turn_id="turn_1",
        provider_call_index=2,
        tool_index=1,
    )
    stable_operation_id = operation_id(
        tenant_id="tenant_1",
        session_id="session_1",
        run_id="run_1",
        turn_id="turn_1",
        invocation_id=stable_invocation_id,
    )
    first = ToolInvocation(
        tool_name="lookup",
        arguments={"query": "shaft"},
        context=ToolInvocationContext(
            invocation_id=stable_invocation_id,
            operation_id=stable_operation_id,
            tenant_id="tenant_1",
            session_id="session_1",
            run_id="run_1",
            turn_id="turn_1",
            tool_call_id="call_1",
            attempt=1,
        ),
    )
    retry = first.with_attempt(2)

    assert retry.context.invocation_id == first.context.invocation_id
    assert retry.context.operation_id == first.context.operation_id
    assert retry.context.tool_call_id == first.context.tool_call_id
    assert retry.context.attempt == 2


def test_invocation_digest_v1_golden_vector() -> None:
    invocation = ToolInvocation(
        tool_name="lookup",
        arguments={"query": "轴类零件", "limit": 3},
        context=ToolInvocationContext(
            invocation_id="invocation_ea91f27fdf596bceb7c90d2578c5e988",
            operation_id="operation_d340f3861e0c6a7eefbaf707fdc69d3d",
            tenant_id=None,
            session_id="session_1",
            run_id="run_1",
            turn_id="turn_1",
            tool_call_id="call_1",
            attempt=1,
        ),
    )

    assert invocation_digest(invocation) == (
        "sha256:18d1cfaaeeac26d400b335613cf0d7f7d3c2b6f58af5dd507bb3b2c51efce473"
    )


@pytest.mark.parametrize("index", [-1, True])
def test_invocation_identity_rejects_invalid_batch_index(index: object) -> None:
    with pytest.raises(ValueError, match="provider_call_index"):
        invocation_id(
            tenant_id=None,
            session_id="session_1",
            run_id="run_1",
            turn_id="turn_1",
            provider_call_index=index,  # type: ignore[arg-type]
            tool_index=0,
        )
