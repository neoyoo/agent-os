import pytest

from agentos import AgentBuilder
from agentos.capabilities import (
    RegisteredTool,
    ToolCallRouter,
    ToolConcurrencyPolicy,
    ToolRegistry,
)
from agentos.providers import (
    FakeProvider,
    ProviderRequest,
    ProviderResponse,
    ProviderToolCall,
)


def _tool(
    *,
    policy: ToolConcurrencyPolicy = ToolConcurrencyPolicy.EXCLUSIVE,
) -> RegisteredTool:
    return RegisteredTool(
        name="lookup",
        description="lookup",
        parameters={"type": "object", "properties": {"keys": {"type": "array"}}},
        handler=lambda _arguments: "ok",
        concurrency_policy=policy,
        metadata={"labels": ["read"]},
    )


def test_registered_tool_freezes_nested_contract_and_defaults_exclusive() -> None:
    parameters = {"type": "object", "properties": {"keys": {"type": "array"}}}
    metadata = {"labels": ["read"], "parallel": True}
    tool = RegisteredTool(
        name="lookup",
        description="lookup",
        parameters=parameters,
        handler=lambda _arguments: "ok",
        metadata=metadata,
    )

    parameters["properties"]["keys"]["type"] = "string"  # type: ignore[index]
    metadata["labels"].append("changed")  # type: ignore[union-attr]

    assert tool.concurrency_policy is ToolConcurrencyPolicy.EXCLUSIVE
    assert tool.parameters["properties"]["keys"]["type"] == "array"  # type: ignore[index]
    assert tool.metadata["labels"] == ("read",)
    with pytest.raises(TypeError):
        tool.parameters["type"] = "array"  # type: ignore[index]


def test_router_is_conservative_and_preserves_explicit_tool_policy() -> None:
    registry = ToolRegistry()
    registry.register(_tool(policy=ToolConcurrencyPolicy.PARALLEL_SAFE))
    router = ToolCallRouter(tool_registry=registry)

    assert router.concurrency_policy_for(
        ProviderToolCall("call_1", "lookup", {}),
    ) is ToolConcurrencyPolicy.PARALLEL_SAFE
    assert router.concurrency_policy_for(
        ProviderToolCall("call_2", "unknown", {}),
    ) is ToolConcurrencyPolicy.EXCLUSIVE


@pytest.mark.parametrize("invalid", [True, 0, -1])
def test_builder_rejects_invalid_parallel_limit(invalid: object) -> None:
    builder = AgentBuilder().provider(FakeProvider([ProviderResponse("ok")]))
    with pytest.raises(ValueError):
        builder.max_parallel_calls(invalid)  # type: ignore[arg-type]


def test_builder_defaults_to_eight_and_rejects_duplicate_limit() -> None:
    agent = AgentBuilder().provider(
        FakeProvider([ProviderResponse("ok")]),
    ).build()
    assert agent.query_loop.tool_scheduler.max_parallel_calls == 8

    builder = AgentBuilder().max_parallel_calls(2)
    with pytest.raises(ValueError):
        builder.max_parallel_calls(3)


def test_provider_request_carries_parallel_tool_call_intent() -> None:
    request = ProviderRequest(
        system="system",
        messages=(),
        parallel_tool_calls=True,
    )

    assert request.parallel_tool_calls is True
