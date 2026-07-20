from __future__ import annotations

import asyncio

import pytest

from agentos.capabilities import (
    RegisteredTool,
    SideEffectPolicy,
    ToolCallRouter,
    ToolRegistry,
)
from agentos.capabilities.executor import ToolExecutionError
from tests.tool_invocation import make_tool_invocation


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
            handler=lambda invocation: str(invocation.arguments["text"]),
            side_effect_policy=SideEffectPolicy.PURE,
        ),
    )
    router = ToolCallRouter(tool_registry=registry)

    with pytest.raises(ToolExecutionError, match="missing required tool argument"):
        asyncio.run(router.execute(make_tool_invocation("echo", {})))


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
            handler=lambda _invocation: "ok",
            side_effect_policy=SideEffectPolicy.PURE,
        ),
    )
    router = ToolCallRouter(tool_registry=registry)

    with pytest.raises(ToolExecutionError) as error:
        asyncio.run(
            router.execute(
                make_tool_invocation("connect", {"api_key": "sk-secret"}),
            ),
        )

    assert "sk-secret" not in str(error.value)


def test_tool_executor_redacts_secret_named_arguments_from_validation_errors() -> None:
    registry = ToolRegistry()
    registry.register(
        RegisteredTool(
            name="login",
            description="Login.",
            parameters={
                "type": "object",
                "properties": {"password": {"type": "string"}},
            },
            handler=lambda _invocation: "ok",
            side_effect_policy=SideEffectPolicy.PURE,
        ),
    )
    router = ToolCallRouter(tool_registry=registry)

    with pytest.raises(ToolExecutionError) as error:
        asyncio.run(
            router.execute(
                make_tool_invocation("login", {"password": 123456}),
            ),
        )

    message = str(error.value)
    assert message == "invalid tool argument password: expected string"
    assert "123456" not in message


@pytest.mark.parametrize(
    "value",
    [
        "postgresql://admin:s3cr3t@db/app",
        "Bearer top-secret-token",
        {"customer_secret": "value"},
    ],
)
def test_tool_validation_errors_never_echo_argument_values(value: object) -> None:
    registry = ToolRegistry()
    registry.register(
        RegisteredTool(
            name="submit",
            description="Submit.",
            parameters={
                "type": "object",
                "properties": {"payload": {"type": "integer"}},
            },
            handler=lambda _invocation: "ok",
            side_effect_policy=SideEffectPolicy.PURE,
        ),
    )
    router = ToolCallRouter(tool_registry=registry)

    with pytest.raises(ToolExecutionError) as error:
        asyncio.run(
            router.execute(make_tool_invocation("submit", {"payload": value})),
        )

    assert str(error.value) == "invalid tool argument payload: expected integer"
