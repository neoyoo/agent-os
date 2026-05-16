from agentos.context import ContextRenderer, ContextRuntime, WorkingStateField
from agentos.messages import MessageRuntime
from agentos.providers import (
    ProviderFunctionSpec,
    ProviderToolSpec,
    provider_message_to_dict,
)
from agentos.runtime import ProviderRequestBuilder


def test_provider_request_builder_uses_rendered_context_and_active_messages() -> None:
    context = ContextRuntime()
    context.declare_schema(
        [
            WorkingStateField(
                name="task_goal",
                type="str",
                purpose="当前任务目标和完成标准",
            ),
        ],
    )
    context.update_state("task_goal", "Build request builder.")
    messages = MessageRuntime()
    messages.append_user("Please build it.")

    request = ProviderRequestBuilder(
        context_renderer=ContextRenderer(),
        message_runtime=messages,
        tools=[
            ProviderToolSpec(
                function=ProviderFunctionSpec(
                    name="read_file",
                    description="Read file.",
                    parameters={"type": "object"},
                ),
            ),
        ],
    ).build(context)

    assert "# Runtime Contract" in request.system
    assert "Build request builder." in request.system
    assert [provider_message_to_dict(message) for message in request.messages] == [
        {"role": "user", "content": "Please build it."},
    ]
    assert request.tools == [
        ProviderToolSpec(
            function=ProviderFunctionSpec(
                name="read_file",
                description="Read file.",
                parameters={"type": "object"},
            ),
        ),
    ]


def test_provider_request_builder_does_not_render_tool_schema_into_system() -> None:
    context = ContextRuntime()
    messages = MessageRuntime()
    tool_schema = {
        "type": "function",
        "function": {
            "name": "dangerous_schema_marker",
            "description": "Dangerous marker.",
            "parameters": {
                "type": "object",
                "properties": {"secret": {"type": "string"}},
            },
        },
    }

    request = ProviderRequestBuilder(
        context_renderer=ContextRenderer(),
        message_runtime=messages,
        tools=[tool_schema],
    ).build(context)

    assert request.tools == [
        ProviderToolSpec(
            function=ProviderFunctionSpec(
                name="dangerous_schema_marker",
                description="Dangerous marker.",
                parameters={
                    "type": "object",
                    "properties": {"secret": {"type": "string"}},
                },
            ),
        ),
    ]
    assert "dangerous_schema_marker" not in request.system
    assert "secret" not in request.system
