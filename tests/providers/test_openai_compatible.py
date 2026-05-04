from agentos.providers import (
    OpenAICompatibleProviderError,
    OpenAICompatibleProvider,
    ProviderRequest,
    ProviderToolCall,
)
from io import BytesIO
from urllib.error import HTTPError


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
        timeout=12.0,
    )

    response = provider.complete(
        ProviderRequest(
            system="system prompt",
            messages=[
                {"role": "user", "content": "读取项目名"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_existing",
                            "name": "read_file",
                            "arguments": {"path": "README.md"},
                        },
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call_existing",
                    "content": "readme",
                },
            ],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "description": "读取文件。",
                        "parameters": {"type": "object"},
                    },
                },
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
    assert response.tool_calls == [
        ProviderToolCall(
            id="call_1",
            name="read_file",
            arguments={"path": "pyproject.toml"},
        ),
    ]


def test_openai_compatible_transport_includes_error_body() -> None:
    provider = OpenAICompatibleProvider(
        api_key="test-key",
        base_url="https://api.deepseek.example",
        model="deepseek-chat",
    )

    def _raise_http_error(*args: object, **kwargs: object) -> object:
        raise HTTPError(
            url="https://api.deepseek.example/chat/completions",
            code=400,
            msg="Bad Request",
            hdrs={},
            fp=BytesIO(b'{"error":{"message":"invalid model"}}'),
        )

    provider.transport = type(
        "FailingTransport",
        (),
        {"post_json": _raise_http_error},
    )()

    try:
        provider.complete(
            ProviderRequest(system="system", messages=[{"role": "user", "content": "hi"}]),
        )
    except OpenAICompatibleProviderError as error:
        assert "HTTP 400" in str(error)
        assert "invalid model" in str(error)
    else:
        raise AssertionError("Expected OpenAICompatibleProviderError")


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
        ProviderRequest(system="system", messages=[{"role": "user", "content": "hi"}]),
    )

    assert transport.calls[0]["payload"]["thinking"] == {"type": "disabled"}


def test_openai_compatible_provider_rejects_system_messages_in_active_window() -> None:
    transport = FakeTransport({"choices": [{"message": {"content": "done"}}]})
    provider = OpenAICompatibleProvider(
        api_key="test-key",
        base_url="https://api.deepseek.example",
        model="deepseek-chat",
        transport=transport,
    )

    try:
        provider.complete(
            ProviderRequest(
                system="system",
                messages=[
                    {"role": "system", "content": "extra system"},
                    {"role": "user", "content": "hi"},
                ],
            ),
        )
    except OpenAICompatibleProviderError as error:
        assert "ProviderRequest.system" in str(error)
    else:
        raise AssertionError("Expected OpenAICompatibleProviderError")
