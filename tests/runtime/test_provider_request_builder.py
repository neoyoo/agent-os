from agentos.context import ContextRenderer, ContextRuntime, WorkingStateField
from agentos.messages import MessageRuntime
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
        tools=[{"name": "read_file", "input_schema": {"type": "object"}}],
    ).build(context)

    assert "# Runtime Contract" in request.system
    assert "Build request builder." in request.system
    assert request.messages == [{"role": "user", "content": "Please build it."}]
    assert request.tools == [{"name": "read_file", "input_schema": {"type": "object"}}]


def test_provider_request_builder_does_not_render_tool_schema_into_system() -> None:
    context = ContextRuntime()
    messages = MessageRuntime()
    tool_schema = {
        "name": "dangerous_schema_marker",
        "input_schema": {"type": "object", "properties": {"secret": {"type": "string"}}},
    }

    request = ProviderRequestBuilder(
        context_renderer=ContextRenderer(),
        message_runtime=messages,
        tools=[tool_schema],
    ).build(context)

    assert request.tools == [tool_schema]
    assert "dangerous_schema_marker" not in request.system
    assert "secret" not in request.system
