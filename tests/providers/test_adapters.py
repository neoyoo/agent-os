from types import SimpleNamespace

from agentos.providers import (
    AssistantMessage,
    AnthropicProvider,
    OpenAIProvider,
    ProviderRequest,
    ProviderToolCall,
    ProviderUsage,
    ToolResultMessage,
    UserMessage,
)


def test_openai_provider_normalizes_chat_completion_tool_calls() -> None:
    class FakeCompletions:
        def __init__(self) -> None:
            self.kwargs: dict[str, object] | None = None

        def create(self, **kwargs: object) -> object:
            self.kwargs = kwargs
            return SimpleNamespace(
                id="chatcmpl_1",
                model="gpt-test",
                usage=SimpleNamespace(
                    prompt_tokens=10,
                    completion_tokens=5,
                    total_tokens=15,
                    prompt_tokens_details=SimpleNamespace(cached_tokens=2),
                    completion_tokens_details=SimpleNamespace(reasoning_tokens=1),
                ),
                choices=[
                    SimpleNamespace(
                        finish_reason="tool_calls",
                        message=SimpleNamespace(
                            content="Need file.",
                            tool_calls=[
                                SimpleNamespace(
                                    id="call_1",
                                    function=SimpleNamespace(
                                        name="read_file",
                                        arguments='{"path": "pyproject.toml"}',
                                    ),
                                ),
                            ],
                        ),
                    ),
                ],
            )

    completions = FakeCompletions()
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
    )
    provider = OpenAIProvider(client=client, model="gpt-test")

    response = provider.complete(
        ProviderRequest(
            system="system text",
            messages=[{"role": "user", "content": "read project name"}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "description": "Read file.",
                        "parameters": {"type": "object"},
                    },
                },
            ],
        ),
    )

    assert completions.kwargs is not None
    assert completions.kwargs["model"] == "gpt-test"
    assert completions.kwargs["messages"] == [
        {"role": "system", "content": "system text"},
        {"role": "user", "content": "read project name"},
    ]
    assert response.content == "Need file."
    assert response.stop_reason == "tool_calls"
    assert response.model == "gpt-test"
    assert response.provider_name == "openai"
    assert response.response_id == "chatcmpl_1"
    assert response.usage == ProviderUsage(
        input_tokens=10,
        output_tokens=5,
        total_tokens=15,
        cached_input_tokens=2,
        reasoning_output_tokens=1,
    )
    assert response.tool_calls == (
        ProviderToolCall(
            id="call_1",
            name="read_file",
            arguments={"path": "pyproject.toml"},
        ),
    )


def test_anthropic_provider_normalizes_messages_tool_calls() -> None:
    class FakeMessages:
        def __init__(self) -> None:
            self.kwargs: dict[str, object] | None = None

        def create(self, **kwargs: object) -> object:
            self.kwargs = kwargs
            return SimpleNamespace(
                id="msg_1",
                model="claude-test",
                stop_reason="tool_use",
                usage=SimpleNamespace(
                    input_tokens=10,
                    output_tokens=5,
                    cache_creation_input_tokens=3,
                    cache_read_input_tokens=2,
                ),
                content=[
                    SimpleNamespace(type="text", text="Need file."),
                    SimpleNamespace(
                        type="tool_use",
                        id="call_1",
                        name="read_file",
                        input={"path": "pyproject.toml"},
                    ),
                ],
            )

    messages = FakeMessages()
    client = SimpleNamespace(messages=messages)
    provider = AnthropicProvider(client=client, model="claude-test")

    response = provider.complete(
        ProviderRequest(
            system="system text",
            messages=[{"role": "user", "content": "read project name"}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "description": "Read file.",
                        "parameters": {"type": "object"},
                    },
                },
            ],
        ),
    )

    assert messages.kwargs is not None
    assert messages.kwargs["model"] == "claude-test"
    assert messages.kwargs["max_tokens"] == 4096
    assert messages.kwargs["system"] == "system text"
    assert messages.kwargs["messages"] == [
        {"role": "user", "content": "read project name"},
    ]
    assert messages.kwargs["tools"] == [
        {
            "name": "read_file",
            "description": "Read file.",
            "input_schema": {"type": "object"},
        },
    ]
    assert response.content == "Need file."
    assert response.stop_reason == "tool_use"
    assert response.model == "claude-test"
    assert response.provider_name == "anthropic"
    assert response.response_id == "msg_1"
    assert response.usage == ProviderUsage(
        input_tokens=10,
        output_tokens=5,
        cached_input_tokens=2,
        cache_creation_input_tokens=3,
    )
    assert response.tool_calls == (
        ProviderToolCall(
            id="call_1",
            name="read_file",
            arguments={"path": "pyproject.toml"},
        ),
    )


def test_anthropic_provider_converts_tool_messages_to_anthropic_blocks() -> None:
    class FakeMessages:
        def __init__(self) -> None:
            self.kwargs: dict[str, object] | None = None

        def create(self, **kwargs: object) -> object:
            self.kwargs = kwargs
            return SimpleNamespace(
                id="msg_1",
                model="claude-test",
                stop_reason="end_turn",
                usage=None,
                content=[SimpleNamespace(type="text", text="done")],
            )

    messages = FakeMessages()
    provider = AnthropicProvider(
        client=SimpleNamespace(messages=messages),
        model="claude-test",
    )

    provider.complete(
        ProviderRequest(
            system="system text",
            messages=[
                UserMessage(content="read project name"),
                AssistantMessage(
                    content="",
                    tool_calls=(
                        ProviderToolCall(
                            id="call_1",
                            name="read_file",
                            arguments={"path": "pyproject.toml"},
                        ),
                    ),
                ),
                ToolResultMessage(tool_call_id="call_1", content="project = agent-os"),
                ToolResultMessage(tool_call_id="call_2", content="version = 0.1"),
            ],
        ),
    )

    assert messages.kwargs is not None
    assert messages.kwargs["messages"] == [
        {"role": "user", "content": "read project name"},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "call_1",
                    "name": "read_file",
                    "input": {"path": "pyproject.toml"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "call_1",
                    "content": "project = agent-os",
                },
                {
                    "type": "tool_result",
                    "tool_use_id": "call_2",
                    "content": "version = 0.1",
                },
            ],
        },
    ]
