import inspect
import asyncio
from io import BytesIO
from urllib.error import HTTPError

import pytest

from agentos.attachments import Attachment, BytesSource, ImagePart, TextPart
from agentos.providers import (
    AssistantMessage,
    HttpxAsyncJSONTransport,
    OpenAICompatibleProviderError,
    OpenAICompatibleProvider,
    ProviderFunctionSpec,
    ProviderInputItem,
    ProviderRequest,
    ProviderToolCall,
    ProviderToolSpec,
    ProviderUsage,
    ToolResultMessage,
    UrlLibJSONTransport,
    UserMessage,
)


_FORBIDDEN_PROVIDER_METADATA = {
    "origin",
    "authority",
    "persistence",
    "visibility",
}


def _logical_message_pairs() -> tuple[
    tuple[UserMessage | AssistantMessage | ToolResultMessage, ProviderInputItem],
    ...,
]:
    tool_call = ProviderToolCall(
        id="call_1",
        name="lookup",
        arguments={"query": "agentos"},
    )
    return (
        (UserMessage("hello"), ProviderInputItem.business_user("hello")),
        (
            AssistantMessage("", tool_calls=(tool_call,)),
            ProviderInputItem.business_assistant("", (tool_call,)),
        ),
        (
            ToolResultMessage("call_1", "done"),
            ProviderInputItem.tool_result("call_1", "done"),
        ),
        (UserMessage("old"), ProviderInputItem.recalled_user("old")),
        (
            ToolResultMessage("call_1", "old result"),
            ProviderInputItem.recalled_tool("call_1", "old result"),
        ),
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


@pytest.mark.parametrize("legacy, logical", _logical_message_pairs())
def test_openai_compatible_provider_input_wire_matches_legacy_messages(
    legacy: UserMessage | AssistantMessage | ToolResultMessage,
    logical: ProviderInputItem,
) -> None:
    provider = OpenAICompatibleProvider(
        api_key="test-key",
        base_url="https://api.example.test",
        model="test-model",
    )

    wire = provider._message(logical)

    assert wire == provider._message(legacy)
    assert _FORBIDDEN_PROVIDER_METADATA.isdisjoint(wire)


@pytest.mark.parametrize("role", ["assistant", "tool"])
@pytest.mark.parametrize(
    "content",
    [
        pytest.param((), id="empty"),
        pytest.param((TextPart("one"), TextPart("two")), id="multiple"),
        pytest.param((ImagePart(object()),), id="non-text"),
    ],
)
def test_openai_compatible_provider_input_rejects_non_single_text_content(
    role: str,
    content: tuple[TextPart | ImagePart, ...],
) -> None:
    provider = OpenAICompatibleProvider(
        api_key="test-key",
        base_url="https://api.example.test",
        model="test-model",
    )
    logical = _provider_input_with_content(role, content)

    with pytest.raises(
        ValueError,
        match=rf"{role} provider input content requires exactly one TextPart",
    ):
        provider._message(logical)


def test_openai_compatible_context_mount_matches_legacy_multimodal_user() -> None:
    provider = OpenAICompatibleProvider(
        api_key="test-key",
        base_url="https://api.example.test",
        model="test-model",
    )
    attachment = Attachment(
        handle="att_1",
        filename="diagram.png",
        mime_type="image/png",
        size_bytes=11,
        source=BytesSource(b"image-bytes"),
    )
    content = (TextPart("inspect"), ImagePart(attachment))

    assert provider._message(ProviderInputItem.context_mount(content)) == (
        provider._message(UserMessage(content))
    )


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
                UserMessage(content="读取项目名"),
                AssistantMessage(
                    content="",
                    tool_calls=(
                        ProviderToolCall(
                            id="call_existing",
                            name="read_file",
                            arguments={"path": "README.md"},
                        ),
                    ),
                ),
                ToolResultMessage(
                    tool_call_id="call_existing",
                    content="readme",
                ),
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


def test_openai_compatible_provider_async_complete_uses_async_transport() -> None:
    class FakeAsyncTransport:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def post_json(
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
            return {"choices": [{"message": {"content": "async done"}}]}

    async def run() -> tuple[str, list[dict[str, object]]]:
        transport = FakeAsyncTransport()
        provider = OpenAICompatibleProvider(
            api_key="test-key",
            base_url="https://api.deepseek.example",
            model="deepseek-chat",
            async_transport=transport,
        )
        response = await provider.async_complete(
            ProviderRequest(system="system", messages=[]),
        )
        return response.content, transport.calls

    content, calls = asyncio.run(run())

    assert content == "async done"
    assert calls[0]["url"] == "https://api.deepseek.example/chat/completions"


def test_openai_compatible_transport_includes_error_body(monkeypatch) -> None:
    def _raise_http_error(*args: object, **kwargs: object) -> object:
        raise HTTPError(
            url="https://api.deepseek.example/chat/completions",
            code=400,
            msg="Bad Request",
            hdrs={},
            fp=BytesIO(b'{"error":{"message":"invalid model"}}'),
        )

    monkeypatch.setattr(
        "agentos.providers.openai_compatible.urlopen",
        _raise_http_error,
    )

    try:
        UrlLibJSONTransport().post_json(
            url="https://api.deepseek.example/chat/completions",
            headers={},
            payload={},
            timeout=1.0,
        )
    except OpenAICompatibleProviderError as error:
        assert "HTTP 400" in str(error)
        assert "invalid model" in str(error)
    else:
        raise AssertionError("Expected OpenAICompatibleProviderError")


def test_openai_compatible_stream_transport_ignores_sse_metadata(monkeypatch) -> None:
    class FakeStreamResponse:
        def __iter__(self):
            return iter(
                [
                    b": keep-alive\n",
                    b"event: message\n",
                    b"id: chunk_1\n",
                    b"retry: 1000\n",
                    b"data:\n",
                    b'data: {"id":"chatcmpl_1","choices":[]}\n',
                    b'{"id":"chatcmpl_2","choices":[]}\n',
                    b"data: [DONE]\n",
                ],
            )

        def __enter__(self):
            return self

        def __exit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(
        "agentos.providers.openai_compatible.urlopen",
        lambda *args, **kwargs: FakeStreamResponse(),
    )

    chunks = list(
        UrlLibJSONTransport().post_json_stream(
            url="https://api.deepseek.example/chat/completions",
            headers={},
            payload={},
            timeout=1.0,
        ),
    )

    assert [chunk["id"] for chunk in chunks] == ["chatcmpl_1", "chatcmpl_2"]


def test_openai_compatible_async_stream_transport_ignores_sse_metadata(
    monkeypatch,
) -> None:
    class FakeStreamResponse:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        def raise_for_status(self) -> None:
            return None

        async def aiter_lines(self):
            for line in [
                ": keep-alive",
                "event: message",
                "id: chunk_1",
                "retry: 1000",
                "data:",
                'data: {"id":"chatcmpl_1","choices":[]}',
                '{"id":"chatcmpl_2","choices":[]}',
                "data: [DONE]",
            ]:
                yield line

    class FakeAsyncClient:
        def __init__(self, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        def stream(self, *args: object, **kwargs: object) -> FakeStreamResponse:
            return FakeStreamResponse()

    class FakeHTTPX:
        AsyncClient = FakeAsyncClient
        HTTPStatusError = RuntimeError
        HTTPError = RuntimeError

    monkeypatch.setattr(HttpxAsyncJSONTransport, "_httpx", lambda self: FakeHTTPX)

    async def collect() -> list[dict[str, object]]:
        return [
            chunk
            async for chunk in HttpxAsyncJSONTransport().post_json_stream(
                url="https://api.deepseek.example/chat/completions",
                headers={},
                payload={},
                timeout=1.0,
            )
        ]

    chunks = asyncio.run(collect())

    assert [chunk["id"] for chunk in chunks] == ["chatcmpl_1", "chatcmpl_2"]


def test_openai_compatible_async_stream_transport_reads_error_body(
    monkeypatch,
) -> None:
    class ResponseNotRead(RuntimeError):
        pass

    class FakeHTTPStatusError(RuntimeError):
        def __init__(self, response: object) -> None:
            super().__init__("401 Unauthorized")
            self.response = response

    class FakeStreamResponse:
        status_code = 401
        _content: bytes | None = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def aread(self) -> bytes:
            self._content = b'{"error":"invalid api key"}'
            return self._content

        @property
        def text(self) -> str:
            if self._content is None:
                raise ResponseNotRead("streaming response has not been read")
            return self._content.decode("utf-8")

        def raise_for_status(self) -> None:
            raise FakeHTTPStatusError(self)

        async def aiter_lines(self):
            yield "data: [DONE]"

    class FakeAsyncClient:
        def __init__(self, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        def stream(self, *args: object, **kwargs: object) -> FakeStreamResponse:
            return FakeStreamResponse()

    class FakeHTTPX:
        AsyncClient = FakeAsyncClient
        HTTPStatusError = FakeHTTPStatusError
        HTTPError = RuntimeError

    monkeypatch.setattr(HttpxAsyncJSONTransport, "_httpx", lambda self: FakeHTTPX)

    async def collect() -> None:
        async for _ in HttpxAsyncJSONTransport().post_json_stream(
            url="https://api.example.test/chat/completions",
            headers={},
            payload={},
            timeout=1.0,
        ):
            pass

    with pytest.raises(OpenAICompatibleProviderError) as error:
        asyncio.run(collect())

    message = str(error.value)
    assert "HTTP 401" in message
    assert "invalid api key" in message


def test_openai_compatible_provider_leaves_http_error_translation_to_transport() -> None:
    source = inspect.getsource(OpenAICompatibleProvider.complete)

    assert "except HTTPError" not in source


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
        ProviderRequest(system="system", messages=[UserMessage(content="hi")]),
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
        ProviderRequest(system="system", messages=[UserMessage(content="hi")]),
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
            messages=[UserMessage(content="hi")],
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
                UserMessage(
                    content=(
                        TextPart("分析图片"),
                        ImagePart(attachment),
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
                UserMessage(content="hi"),
            ],
        )


def test_openai_compatible_provider_rejects_non_object_tool_arguments() -> None:
    transport = FakeTransport(
        {
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "function": {
                                    "name": "read_file",
                                    "arguments": "null",
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
    )

    with pytest.raises(ValueError, match="tool arguments must decode to an object"):
        provider.complete(ProviderRequest(system="system", messages=[]))


@pytest.mark.parametrize(
    "tool_call, message",
    [
        (
            {"id": None, "function": {"name": "read_file", "arguments": "{}"}},
            "tool_call requires id",
        ),
        (
            {"id": "call_1", "function": {"name": None, "arguments": "{}"}},
            "tool_call requires function name",
        ),
    ],
)
def test_openai_compatible_provider_rejects_missing_tool_identity(
    tool_call: dict[str, object],
    message: str,
) -> None:
    transport = FakeTransport(
        {
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "content": None,
                        "tool_calls": [tool_call],
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
    )

    with pytest.raises(ValueError, match=message):
        provider.complete(ProviderRequest(system="system", messages=[]))
