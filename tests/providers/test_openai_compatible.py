import pytest

from agentos.providers import (
    ImagePart,
    OpenAICompatibleProvider,
    ProviderFunctionSpec,
    ProviderInputItem,
    ProviderRequest,
    ProviderToolCall,
    ProviderToolSpec,
    ProviderUsage,
    TextPart,
)
from agentos.providers.openai_chat_wire import openai_chat_message
from tests._provider_binary import binary_payload

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


@pytest.mark.parametrize("logical", _logical_messages())
def test_openai_compatible_provider_input_wire_excludes_internal_metadata(
    logical: ProviderInputItem,
) -> None:
    wire = openai_chat_message(logical)

    assert _FORBIDDEN_PROVIDER_METADATA.isdisjoint(wire)


@pytest.mark.parametrize("role", ["assistant", "tool"])
@pytest.mark.parametrize(
    "content",
    [
        pytest.param((), id="empty"),
        pytest.param((TextPart("one"), TextPart("two")), id="multiple"),
        pytest.param((ImagePart(binary_payload()),), id="non-text"),
    ],
)
def test_openai_compatible_provider_input_rejects_non_single_text_content(
    role: str,
    content: tuple[TextPart | ImagePart, ...],
) -> None:
    logical = _provider_input_with_content(role, content)

    with pytest.raises(
        ValueError,
        match=rf"{role} provider input content requires exactly one TextPart",
    ):
        openai_chat_message(logical)


def test_openai_compatible_context_mount_maps_to_multimodal_user() -> None:
    payload = binary_payload(
        handle="att_1",
        filename="diagram.png",
        media_type="image/png",
        data=b"image-bytes",
    )
    content = (TextPart("inspect"), ImagePart(payload))

    assert openai_chat_message(ProviderInputItem.context_mount(content)) == {
        "role": "user",
        "content": [
            {"type": "text", "text": "inspect"},
            {
                "type": "image_url",
                "image_url": {
                    "url": "data:image/png;base64,aW1hZ2UtYnl0ZXM=",
                    "detail": "auto",
                },
            },
        ],
    }


class FakeTransport:
    """记录 HTTP 请求并返回预设 JSON。"""

    def __init__(self, response: dict[str, object]) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def post_json(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, object],
        timeout: float,
    ) -> dict[str, object]:
        self.calls.append(
            {
                "url": url,
                "headers": headers,
                "payload": payload,
                "timeout": timeout,
            },
        )
        return self.response


def test_openai_compatible_parallel_flag_requires_capability_opt_in() -> None:
    tool = ProviderToolSpec(
        function=ProviderFunctionSpec("lookup", "lookup", {"type": "object"}),
    )
    request = ProviderRequest(
        system="system",
        messages=(),
        tools=(tool,),
        parallel_tool_calls=True,
    )
    default_provider = OpenAICompatibleProvider(
        api_key="key",
        base_url="https://example.test",
        model="model",
    )
    enabled_provider = OpenAICompatibleProvider(
        api_key="key",
        base_url="https://example.test",
        model="model",
        supports_parallel_tool_calls_parameter=True,
    )

    assert "parallel_tool_calls" not in default_provider._payload(request)
    assert enabled_provider._payload(request)["parallel_tool_calls"] is True


def test_openai_compatible_provider_posts_chat_completion_request() -> None:
    transport = FakeTransport(
        {
            "id": "chatcmpl_1",
            "model": "deepseek-chat",
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "prompt_tokens_details": {"cached_tokens": 2},
                "completion_tokens_details": {"reasoning_tokens": 1},
            },
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "content": "需要读取文件。",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "function": {
                                    "name": "read_file",
                                    "arguments": '{"path": "pyproject.toml"}',
                                },
                            },
                        ],
                    },
                },
            ],
        },
    )
    provider = OpenAICompatibleProvider(
        api_key="test-key",
        base_url="https://api.deepseek.example",
        model="deepseek-chat",
        transport=transport,
        timeout_seconds=12.0,
    )

    response = provider.complete(
        ProviderRequest(
            system="system prompt",
            messages=[
                ProviderInputItem.business_user("读取项目名"),
                ProviderInputItem.business_assistant(
                    "",
                    (
                        ProviderToolCall(
                            id="call_existing",
                            name="read_file",
                            arguments={"path": "README.md"},
                        ),
                    ),
                ),
                ProviderInputItem.tool_result("call_existing", "readme"),
            ],
            tools=[
                ProviderToolSpec(
                    function=ProviderFunctionSpec(
                        name="read_file",
                        description="读取文件。",
                        parameters={"type": "object"},
                    ),
                ),
            ],
        ),
    )

    assert transport.calls == [
        {
            "url": "https://api.deepseek.example/chat/completions",
            "headers": {
                "Authorization": "Bearer test-key",
                "Content-Type": "application/json",
            },
            "payload": {
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": "system prompt"},
                    {"role": "user", "content": "读取项目名"},
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_existing",
                                "type": "function",
                                "function": {
                                    "name": "read_file",
                                    "arguments": '{"path": "README.md"}',
                                },
                            },
                        ],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": "call_existing",
                        "content": "readme",
                    },
                ],
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "description": "读取文件。",
                            "parameters": {"type": "object"},
                        },
                    },
                ],
            },
            "timeout": 12.0,
        },
    ]
    assert response.content == "需要读取文件。"
    assert response.stop_reason == "tool_calls"
    assert response.model == "deepseek-chat"
    assert response.provider_name == "openai-compatible"
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


def test_openai_compatible_provider_can_disable_thinking() -> None:
    transport = FakeTransport({"choices": [{"message": {"content": "done"}}]})
    provider = OpenAICompatibleProvider(
        api_key="test-key",
        base_url="https://api.deepseek.example",
        model="deepseek-chat",
        transport=transport,
        thinking={"type": "disabled"},
    )

    provider.complete(
        ProviderRequest(
            system="system",
            messages=[ProviderInputItem.business_user("hi")],
        ),
    )

    assert transport.calls[0]["payload"]["thinking"] == {"type": "disabled"}


def test_openai_compatible_provider_merges_extra_body_into_payload() -> None:
    transport = FakeTransport({"choices": [{"message": {"content": "done"}}]})
    provider = OpenAICompatibleProvider(
        api_key="test-key",
        base_url="https://api.deepseek.example",
        model="deepseek-chat",
        transport=transport,
        extra_body={
            "vl_high_resolution_images": True,
            "temperature": 0,
        },
    )

    provider.complete(
        ProviderRequest(
            system="system",
            messages=[ProviderInputItem.business_user("hi")],
        ),
    )

    payload = transport.calls[0]["payload"]
    assert payload["vl_high_resolution_images"] is True
    assert payload["temperature"] == 0
    assert payload["model"] == "deepseek-chat"
    assert payload["messages"][0] == {"role": "system", "content": "system"}


def test_openai_compatible_provider_deep_copies_extra_body() -> None:
    transport = FakeTransport({"choices": [{"message": {"content": "done"}}]})
    extra_body = {"metadata": {"route": "qwen-vl"}}
    provider = OpenAICompatibleProvider(
        api_key="test-key",
        base_url="https://api.deepseek.example",
        model="deepseek-chat",
        transport=transport,
        extra_body=extra_body,
    )

    provider.complete(ProviderRequest(system="system", messages=[]))

    payload = transport.calls[0]["payload"]
    assert payload["metadata"] == {"route": "qwen-vl"}
    assert payload["metadata"] is not extra_body["metadata"]


def test_openai_compatible_provider_core_payload_overrides_extra_body() -> None:
    transport = FakeTransport({"choices": [{"message": {"content": "done"}}]})
    provider = OpenAICompatibleProvider(
        api_key="test-key",
        base_url="https://api.deepseek.example",
        model="deepseek-chat",
        transport=transport,
        thinking={"type": "disabled"},
        extra_body={
            "model": "wrong-model",
            "messages": [],
            "tools": [],
            "thinking": {"type": "enabled"},
        },
    )

    provider.complete(
        ProviderRequest(
            system="system",
            messages=[ProviderInputItem.business_user("hi")],
            tools=[
                ProviderToolSpec(
                    function=ProviderFunctionSpec(
                        name="read_file",
                        description="read file",
                        parameters={"type": "object"},
                    ),
                ),
            ],
        ),
    )

    payload = transport.calls[0]["payload"]
    assert payload["model"] == "deepseek-chat"
    assert payload["messages"] == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "hi"},
    ]
    assert payload["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "read file",
                "parameters": {"type": "object"},
            },
        },
    ]
    assert payload["thinking"] == {"type": "disabled"}


def test_openai_compatible_provider_maps_image_content_parts() -> None:
    transport = FakeTransport({"choices": [{"message": {"content": "ok"}}]})
    provider = OpenAICompatibleProvider(
        api_key="test-key",
        base_url="https://api.deepseek.example",
        model="deepseek-chat",
        transport=transport,
    )
    payload = binary_payload(
        handle="att_1",
        filename="diagram.png",
        media_type="image/png",
        data=b"image-bytes",
    )

    provider.complete(
        ProviderRequest(
            system="system",
            messages=[
                ProviderInputItem.context_mount(
                    (
                        TextPart("分析图片"),
                        ImagePart(payload),
                    ),
                ),
            ],
        ),
    )

    assert transport.calls[0]["payload"]["messages"][1] == {
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


def test_openai_compatible_provider_rejects_system_messages_in_active_window() -> None:
    with pytest.raises(TypeError, match="ProviderInputItem"):
        ProviderRequest(
            system="system",
            messages=[
                {"role": "system", "content": "extra system"},
                ProviderInputItem.business_user("hi"),
            ],
        )
