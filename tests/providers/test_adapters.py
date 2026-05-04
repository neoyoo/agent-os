from types import SimpleNamespace

from agentos.providers import (
    AnthropicProvider,
    OpenAIProvider,
    ProviderRequest,
    ProviderToolCall,
)


def test_openai_provider_normalizes_chat_completion_tool_calls() -> None:
    class FakeCompletions:
        def __init__(self) -> None:
            self.kwargs: dict[str, object] | None = None

        def create(self, **kwargs: object) -> object:
            self.kwargs = kwargs
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
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
    assert response.tool_calls == [
        ProviderToolCall(
            id="call_1",
            name="read_file",
            arguments={"path": "pyproject.toml"},
        ),
    ]


def test_anthropic_provider_normalizes_messages_tool_calls() -> None:
    class FakeMessages:
        def __init__(self) -> None:
            self.kwargs: dict[str, object] | None = None

        def create(self, **kwargs: object) -> object:
            self.kwargs = kwargs
            return SimpleNamespace(
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
    assert response.tool_calls == [
        ProviderToolCall(
            id="call_1",
            name="read_file",
            arguments={"path": "pyproject.toml"},
        ),
    ]
