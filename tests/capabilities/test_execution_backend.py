from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from agentos.capabilities import InProcessExecutionBackend, ToolCallRouter
from agentos.capabilities.executor import ToolExecutionError, ToolExecutor
from agentos.capabilities.registry import ToolRegistry
from agentos.capabilities.tools import RegisteredTool
from agentos.policies import ResourcePolicy, SecurityPolicy, SecurityPolicyError
from agentos.providers import ProviderToolCall


@dataclass(slots=True)
class FakeSandboxBackend:
    calls: list[tuple[RegisteredTool, dict[str, object], ResourcePolicy]] = field(
        default_factory=list,
    )

    def run(
        self,
        tool: RegisteredTool,
        arguments: dict[str, object],
        *,
        resource_policy: ResourcePolicy,
    ) -> str:
        self.calls.append((tool, arguments, resource_policy))
        return "sandbox result"

    async def async_run(
        self,
        tool: RegisteredTool,
        arguments: dict[str, object],
        *,
        resource_policy: ResourcePolicy,
    ) -> str:
        self.calls.append((tool, arguments, resource_policy))
        return "async sandbox result"


def test_tool_executor_delegates_to_injected_backend_with_resource_policy() -> None:
    registry = _registry()
    backend = FakeSandboxBackend()
    resource_policy = ResourcePolicy(deadline_seconds=1.0, memory_limit_mb=128)
    executor = ToolExecutor(
        registry=registry,
        security_policy=SecurityPolicy(),
        backend=backend,
        resource_policy=resource_policy,
    )

    result = executor.execute(
        ProviderToolCall(id="call_1", name="echo", arguments={"text": "hello"}),
    )

    assert result.content == "sandbox result"
    assert len(backend.calls) == 1
    tool, arguments, policy = backend.calls[0]
    assert tool.name == "echo"
    assert arguments == {"text": "hello"}
    assert policy is resource_policy


def test_tool_executor_keeps_security_check_before_backend_call() -> None:
    registry = _registry()
    backend = FakeSandboxBackend()
    executor = ToolExecutor(
        registry=registry,
        security_policy=SecurityPolicy(denied_tools={"echo"}),
        backend=backend,
    )

    with pytest.raises(SecurityPolicyError):
        executor.execute(
            ProviderToolCall(id="call_1", name="echo", arguments={"text": "hello"}),
        )

    assert backend.calls == []


def test_tool_executor_keeps_argument_validation_before_backend_call() -> None:
    registry = _registry()
    backend = FakeSandboxBackend()
    executor = ToolExecutor(
        registry=registry,
        security_policy=SecurityPolicy(),
        backend=backend,
    )

    with pytest.raises(ToolExecutionError, match="invalid tool argument text"):
        executor.execute(
            ProviderToolCall(id="call_1", name="echo", arguments={"text": 42}),
        )

    assert backend.calls == []


def test_in_process_backend_accepts_and_ignores_resource_policy() -> None:
    registry = _registry()
    executor = ToolExecutor(
        registry=registry,
        security_policy=SecurityPolicy(),
        backend=InProcessExecutionBackend(),
        resource_policy=ResourcePolicy(deadline_seconds=0.1, memory_limit_mb=64),
    )

    result = executor.execute(
        ProviderToolCall(id="call_1", name="echo", arguments={"text": "hello"}),
    )

    assert result.content == "echo: hello"


@pytest.mark.parametrize("deadline_seconds", [0.0, -1.0])
def test_resource_policy_rejects_non_positive_deadline(deadline_seconds: float) -> None:
    with pytest.raises(ValueError, match="deadline_seconds must be positive"):
        ResourcePolicy(deadline_seconds=deadline_seconds)


def test_resource_policy_rejects_zero_memory_limit() -> None:
    with pytest.raises(ValueError, match="memory_limit_mb must be at least 1"):
        ResourcePolicy(memory_limit_mb=0)


def test_tool_call_router_default_construction_still_routes_external_tools() -> None:
    router = ToolCallRouter(tool_registry=_registry())

    result = router.execute_tool_call(
        ProviderToolCall(id="call_1", name="echo", arguments={"text": "hello"}),
    )

    assert result.content == "echo: hello"


def test_tool_call_router_passes_backend_and_resource_policy_to_executor() -> None:
    backend = FakeSandboxBackend()
    resource_policy = ResourcePolicy(deadline_seconds=1.0)
    router = ToolCallRouter(
        tool_registry=_registry(),
        backend=backend,
        resource_policy=resource_policy,
    )

    result = router.execute_tool_call(
        ProviderToolCall(id="call_1", name="echo", arguments={"text": "hello"}),
    )

    assert result.content == "sandbox result"
    assert len(backend.calls) == 1
    assert backend.calls[0][2] is resource_policy


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        RegisteredTool(
            name="echo",
            description="Echo text.",
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            handler=lambda arguments: f"echo: {arguments['text']}",
        ),
    )
    return registry
