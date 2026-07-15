from types import SimpleNamespace

import pytest

from agentos.attachments import (
    Attachment,
    BytesSource,
    FilePart,
    ImagePart,
    TextPart,
)
from agentos.providers import (
    AnthropicProvider,
    OpenAIProvider,
    ProviderFunctionSpec,
    ProviderInputItem,
    ProviderRequest,
    ProviderToolCall,
    ProviderToolSpec,
    ProviderUsage,
)


_FORBIDDEN_PROVIDER_METADATA = {
    "origin",
    "authority",
    "persistence",
    "visibility",
}


def _logical_messages() -> tuple[ProviderInputItem, ...]:
    tool_call = ProviderToolCall(
        id="call_1",
        name="lookup",
        arguments={"query": "agentos"},
    )
    return (
        ProviderInputItem.business_user("hello"),
        ProviderInputItem.business_assistant("", (tool_call,)),
        ProviderInputItem.tool_result("call_1", "done"),
        ProviderInputItem.recalled_user("old"),
        ProviderInputItem.recalled_tool("call_1", "old result"),
    )


def _provider_input_with_content(
    role: str,
    content: tuple[TextPart | ImagePart, ...],
) -> ProviderInputItem:
    if role == "assistant":
        return ProviderInputItem(
            role="assistant",
            kind="business_message",
            origin="message_store",
            authority="conversation_data",
            persistence="stored",
            visibility="conversation",
            content=content,
        )
    return ProviderInputItem(
        role="tool",
        kind="tool_result",
        origin="message_store",
        authority="tool_data",
        persistence="stored",
        visibility="internal",
        content=content,
        tool_call_id="call_1",
    )


@pytest.mark.parametrize(
    "provider",
    [
        OpenAIProvider(client=object(), model="gpt-test"),
        AnthropicProvider(client=object(), model="claude-test"),
    ],
    ids=["openai", "anthropic"],
)
@pytest.mark.parametrize("logical", _logical_messages())
def test_provider_input_wire_excludes_internal_metadata(
    provider: OpenAIProvider | AnthropicProvider,
    logical: ProviderInputItem,
) -> None:
    request = ProviderRequest(system="system", messages=(logical,))

    provider._ensure_no_active_system_messages(request)
    wire = provider._message(logical)

    assert _FORBIDDEN_PROVIDER_METADATA.isdisjoint(wire)


@pytest.mark.parametrize(
    "provider",
    [
        OpenAIProvider(client=object(), model="gpt-test"),
        AnthropicProvider(client=object(), model="claude-test"),
    ],
    ids=["openai", "anthropic"],
)
@pytest.mark.parametrize("role", ["assistant", "tool"])
@pytest.mark.parametrize(
    "content",
    [
        pytest.param((), id="empty"),
        pytest.param((TextPart("one"), TextPart("two")), id="multiple"),
        pytest.param((ImagePart(object()),), id="non-text"),
    ],
)
def test_provider_input_rejects_non_single_text_assistant_and_tool_content(
    provider: OpenAIProvider | AnthropicProvider,
    role: str,
    content: tuple[TextPart | ImagePart, ...],
) -> None:
    logical = _provider_input_with_content(role, content)

    with pytest.raises(
        ValueError,
        match=rf"{role} provider input content requires exactly one TextPart",
    ):
        provider._message(logical)


@pytest.mark.parametrize(
    "provider",
    [
        OpenAIProvider(client=object(), model="gpt-test"),
        AnthropicProvider(client=object(), model="claude-test"),
    ],
    ids=["openai", "anthropic"],
)
def test_context_mount_maps_directly_to_multimodal_user(
    provider: OpenAIProvider | AnthropicProvider,
) -> None:
    attachment = Attachment(
        handle="att_1",
        filename="diagram.png",
        mime_type="image/png",
        size_bytes=11,
        source=BytesSource(b"image-bytes"),
    )
    content = (TextPart("inspect"), ImagePart(attachment))

    wire = provider._message(ProviderInputItem.context_mount(content))

    assert wire["role"] == "user"
    assert isinstance(wire["content"], list)
    assert wire["content"][0] == {"type": "text", "text": "inspect"}  # type: ignore[index]


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


def test_openai_provider_forwards_parallel_tool_call_intent_with_tools() -> None:
    class FakeCompletions:
        def __init__(self) -> None:
            self.kwargs: dict[str, object] = {}

        def create(self, **kwargs: object) -> object:
            self.kwargs = kwargs
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(content="ok", tool_calls=[]),
                    ),
                ],
                usage=None,
            )

    completions = FakeCompletions()
    provider = OpenAIProvider(
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
        model="gpt-test",
    )
    tool = ProviderToolSpec(
        function=ProviderFunctionSpec("lookup", "lookup", {"type": "object"}),
    )

    provider.complete(
        ProviderRequest(
            system="system",
            messages=(),
            tools=(tool,),
            parallel_tool_calls=False,
        ),
    )

    assert completions.kwargs["parallel_tool_calls"] is False


def test_openai_provider_rejects_non_object_tool_arguments() -> None:
    class FakeCompletions:
        def create(self, **kwargs: object) -> object:
            return SimpleNamespace(
                id="chatcmpl_1",
                model="gpt-test",
                usage=None,
                choices=[
                    SimpleNamespace(
                        finish_reason="tool_calls",
                        message=SimpleNamespace(
                            content=None,
                            tool_calls=[
                                SimpleNamespace(
                                    id="call_1",
                                    function=SimpleNamespace(
                                        name="read_file",
                                        arguments='["not", "object"]',
                                    ),
                                ),
                            ],
                        ),
                    ),
                ],
            )

    provider = OpenAIProvider(
        client=SimpleNamespace(
            chat=SimpleNamespace(completions=FakeCompletions()),
        ),
        model="gpt-test",
    )

    with pytest.raises(ValueError, match="tool arguments must decode to an object"):
        provider.complete(ProviderRequest(system="system", messages=[]))


def test_openai_provider_rejects_missing_tool_identity() -> None:
    class FakeCompletions:
        def create(self, **kwargs: object) -> object:
            return SimpleNamespace(
                id="chatcmpl_1",
                model="gpt-test",
                usage=None,
                choices=[
                    SimpleNamespace(
                        finish_reason="tool_calls",
                        message=SimpleNamespace(
                            content=None,
                            tool_calls=[
                                SimpleNamespace(
                                    id=None,
                                    function=SimpleNamespace(
                                        name="read_file",
                                        arguments="{}",
                                    ),
                                ),
                            ],
                        ),
                    ),
                ],
            )

    provider = OpenAIProvider(
        client=SimpleNamespace(
            chat=SimpleNamespace(completions=FakeCompletions()),
        ),
        model="gpt-test",
    )

    with pytest.raises(ValueError, match="tool_call requires id"):
        provider.complete(ProviderRequest(system="system", messages=[]))


def test_openai_provider_maps_image_parts_to_chat_image_url() -> None:
    class FakeCompletions:
        def __init__(self) -> None:
            self.kwargs: dict[str, object] | None = None

        def create(self, **kwargs: object) -> object:
            self.kwargs = kwargs
            return SimpleNamespace(
                id="chatcmpl_1",
                model="gpt-test",
                usage=None,
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(content="ok", tool_calls=[]),
                    ),
                ],
            )

    completions = FakeCompletions()
    provider = OpenAIProvider(
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
        model="gpt-test",
    )
    attachment = Attachment(
        handle="att_1",
        filename="diagram.png",
        mime_type="image/png",
        size_bytes=11,
        source=BytesSource(b"image-bytes"),
    )

    provider.complete(
        ProviderRequest(
            system="system",
            messages=[
                ProviderInputItem.context_mount(
                    (
                        TextPart("分析图片"),
                        ImagePart(attachment),
                    ),
                ),
            ],
        ),
    )

    assert completions.kwargs is not None
    assert completions.kwargs["messages"][1] == {
        "role": "user",
        "content": [
            {"type": "text", "text": "分析图片"},
            {
                "type": "image_url",
                "image_url": {
                    "url": "data:image/png;base64,aW1hZ2UtYnl0ZXM=",
                    "detail": "auto",
                },
            },
        ],
    }


def test_openai_provider_rejects_file_parts_for_chat_completions() -> None:
    class FakeCompletions:
        def create(self, **kwargs: object) -> object:
            raise AssertionError("OpenAI client should not be called")

    provider = OpenAIProvider(
        client=SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions())),
        model="gpt-test",
    )
    attachment = Attachment(
        handle="att_1",
        filename="doc.pdf",
        mime_type="application/pdf",
        size_bytes=8,
        source=BytesSource(b"pdf data"),
    )

    with pytest.raises(ValueError, match="does not support file attachments"):
        provider.complete(
            ProviderRequest(
                system="system",
                messages=[ProviderInputItem.context_mount((FilePart(attachment),))],
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
    image = Attachment(
        handle="att_1",
        filename="diagram.png",
        mime_type="image/png",
        size_bytes=11,
        source=BytesSource(b"image-bytes"),
    )
    pdf = Attachment(
        handle="att_2",
        filename="doc.pdf",
        mime_type="application/pdf",
        size_bytes=8,
        source=BytesSource(b"pdf data"),
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
