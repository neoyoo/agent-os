from __future__ import annotations

import pytest

from agentos.capabilities import RegisteredTool, ToolCallRouter, ToolRegistry
from agentos.capabilities.executor import ToolExecutionError
from agentos.providers import ProviderToolCall


def test_tool_executor_validates_required_json_schema_fields() -> None:
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
            handler=lambda arguments: str(arguments["text"]),
        ),
    )
    router = ToolCallRouter(tool_registry=registry)

    with pytest.raises(ToolExecutionError, match="missing required tool argument"):
        router.execute_tool_call(ProviderToolCall(id="call_1", name="echo", arguments={}))


def test_tool_executor_redacts_sensitive_arguments_from_validation_errors() -> None:
    registry = ToolRegistry()
    registry.register(
        RegisteredTool(
            name="connect",
            description="Connect.",
            parameters={
                "type": "object",
                "properties": {"api_key": {"type": "string"}, "url": {"type": "string"}},
                "required": ["api_key", "url"],
            },
            handler=lambda arguments: "ok",
        ),
    )
    router = ToolCallRouter(tool_registry=registry)

    with pytest.raises(ToolExecutionError) as error:
        router.execute_tool_call(
            ProviderToolCall(
                id="call_1",
                name="connect",
                arguments={"api_key": "sk-secret"},
            ),
        )

    assert "sk-secret" not in str(error.value)
