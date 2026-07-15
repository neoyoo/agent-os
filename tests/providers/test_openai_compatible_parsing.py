import pytest

from agentos.providers import (
    OpenAICompatibleProvider,
    ProviderRequest,
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
