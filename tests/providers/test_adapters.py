from types import SimpleNamespace

from agentos.providers import (
    AnthropicProvider,
    FilePart,
    ImagePart,
    ProviderFunctionSpec,
    ProviderInputItem,
    ProviderRequest,
    ProviderToolCall,
    ProviderToolSpec,
    ProviderUsage,
    TextPart,
)
from tests._provider_binary import binary_payload


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
            messages=[ProviderInputItem.business_user("read project name")],
            tools=[
                ProviderToolSpec(
                    function=ProviderFunctionSpec(
                        name="read_file",
                        description="Read file.",
                        parameters={"type": "object"},
                    ),
                ),
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
                ProviderInputItem.business_user("read project name"),
                ProviderInputItem.business_assistant(
                    "",
                    (
                        ProviderToolCall(
                            id="call_1",
                            name="read_file",
                            arguments={"path": "pyproject.toml"},
                        ),
                    ),
                ),
                ProviderInputItem.tool_result("call_1", "project = agent-os"),
                ProviderInputItem.tool_result("call_2", "version = 0.1"),
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


def test_anthropic_provider_maps_image_and_pdf_parts_to_content_blocks() -> None:
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
    image = binary_payload(
        handle="att_1",
        filename="diagram.png",
        media_type="image/png",
        data=b"image-bytes",
    )
    pdf = binary_payload(
        handle="att_2",
        filename="doc.pdf",
        media_type="application/pdf",
        data=b"pdf data",
    )

    provider.complete(
        ProviderRequest(
            system="system",
            messages=[
                ProviderInputItem.context_mount(
                    (
                        TextPart("分析附件"),
                        ImagePart(image),
                        FilePart(pdf),
                    ),
                ),
            ],
        ),
    )

    assert messages.kwargs is not None
    assert messages.kwargs["messages"] == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "分析附件"},
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": "aW1hZ2UtYnl0ZXM=",
                    },
                },
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": "cGRmIGRhdGE=",
                    },
                },
            ],
        },
    ]
