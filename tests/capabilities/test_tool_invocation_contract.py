import dataclasses

import pytest

from agentos._json_values import FrozenJsonObject
from agentos.capabilities.invocation import (
    ToolCompensationContext,
    ToolCompensationInvocation,
    ToolInvocation,
    ToolInvocationContext,
)


def _context(*, attempt: int = 1) -> ToolInvocationContext:
    return ToolInvocationContext(
        invocation_id="invocation_ea91f27fdf596bceb7c90d2578c5e988",
        operation_id="operation_d340f3861e0c6a7eefbaf707fdc69d3d",
        tenant_id=None,
        session_id="session_1",
        run_id="run_1",
        turn_id="turn_1",
        tool_call_id="call_1",
        attempt=attempt,
    )


def test_tool_invocation_copies_and_freezes_arguments() -> None:
    arguments = {"query": "shaft", "filters": {"limit": 3}}

    invocation = ToolInvocation(
        tool_name="lookup",
        arguments=arguments,
        context=_context(),
    )
    arguments["query"] = "changed"
    arguments["filters"]["limit"] = 9  # type: ignore[index]

    assert type(invocation.arguments) is FrozenJsonObject
    assert invocation.arguments == {"query": "shaft", "filters": {"limit": 3}}
    with pytest.raises(dataclasses.FrozenInstanceError):
        invocation.tool_name = "other"  # type: ignore[misc]


@pytest.mark.parametrize("attempt", [0, -1, True])
def test_tool_invocation_context_rejects_invalid_attempt(attempt: object) -> None:
    with pytest.raises(ValueError, match="attempt"):
        _context(attempt=attempt)  # type: ignore[arg-type]


def test_tool_invocation_context_allows_absent_local_tenant() -> None:
    assert _context().tenant_id is None


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("invocation_id", "invocation_1"),
        ("operation_id", "operation_ABCDEF0123456789abcdef0123456789"),
    ],
)
def test_tool_invocation_context_rejects_noncanonical_stable_id(
    field_name: str,
    value: str,
) -> None:
    with pytest.raises(ValueError, match=field_name):
        dataclasses.replace(_context(), **{field_name: value})


@pytest.mark.parametrize("session_id", [" session_1", "session\u200b_1", "x" * 256])
def test_tool_invocation_context_rejects_noncanonical_identifier(
    session_id: str,
) -> None:
    with pytest.raises(ValueError, match="session_id"):
        dataclasses.replace(_context(), session_id=session_id)


def test_compensation_invocation_has_independent_stable_operation_identity() -> None:
    context = ToolCompensationContext(
        original_operation_id="operation_d340f3861e0c6a7eefbaf707fdc69d3d",
        compensation_operation_id="operation_bf3249c6dee281bc70712f707141d93a",
        tenant_id=None,
        session_id="session_1",
        run_id="run_1",
        turn_id="turn_1",
        invocation_id="invocation_ea91f27fdf596bceb7c90d2578c5e988",
        attempt=1,
    )

    compensation = ToolCompensationInvocation(
        arguments={"query": "shaft"},
        context=context,
        result_ref=None,
    )

    assert compensation.context.original_operation_id != (
        compensation.context.compensation_operation_id
    )
    assert type(compensation.arguments) is FrozenJsonObject
