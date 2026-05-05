from collections.abc import Iterator

from agentos.providers import (
    OpenAICompatibleProvider,
    ProviderContentDelta,
    ProviderRequest,
    ProviderStreamCompleted,
    ProviderStreamOptions,
    ProviderThinkingDelta,
    ProviderToolCallDelta,
    ProviderUsage,
)


class FakeStreamingTransport:
    """记录 streaming HTTP 请求并返回预设 chunk。"""

    def __init__(self, chunks: list[dict[str, object]]) -> None:
        self.chunks = chunks
        self.calls: list[dict[str, object]] = []

    def post_json_stream(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, object],
        timeout: float,
    ) -> Iterator[dict[str, object]]:
        self.calls.append(
            {
                "url": url,
                "headers": headers,
                "payload": payload,
                "timeout": timeout,
            },
        )
        yield from self.chunks


def test_openai_compatible_streams_content_and_completion() -> None:
    transport = FakeStreamingTransport(
        [
            {
                "id": "chatcmpl_1",
                "model": "deepseek-chat",
                "choices": [{"delta": {"content": "hel"}, "finish_reason": None}],
            },
            {
                "id": "chatcmpl_1",
                "model": "deepseek-chat",
                "choices": [{"delta": {"content": "lo"}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": 3,
                    "completion_tokens": 2,
                    "total_tokens": 5,
                },
            },
        ],
    )
    provider = OpenAICompatibleProvider(
        api_key="test-key",
        base_url="https://api.deepseek.example",
        model="deepseek-chat",
        transport=transport,
    )

    events = list(
        provider.stream(
            ProviderRequest(
                system="system",
                messages=[{"role": "user", "content": "hi"}],
            ),
        ),
    )

    assert transport.calls[0]["payload"]["stream"] is True
    assert [type(event).__name__ for event in events] == [
        "ProviderStreamStarted",
        "ProviderContentDelta",
        "ProviderContentDelta",
        "ProviderStreamCompleted",
    ]
    assert events[1] == ProviderContentDelta(
        request_id="chatcmpl_1",
        index=1,
        text="hel",
    )
    assert events[2].text == "lo"
    assert isinstance(events[-1], ProviderStreamCompleted)
    assert events[-1].response.content == "hello"
    assert events[-1].response.usage == ProviderUsage(
        input_tokens=3,
        output_tokens=2,
        total_tokens=5,
    )


def test_openai_compatible_streams_reasoning_when_visible() -> None:
    transport = FakeStreamingTransport(
        [
            {
                "id": "chatcmpl_1",
                "model": "deepseek-chat",
                "choices": [
                    {
                        "delta": {
                            "reasoning_content": "think",
                            "content": "answer",
                        },
                        "finish_reason": "stop",
                    },
                ],
            },
        ],
    )
    provider = OpenAICompatibleProvider(
        api_key="test-key",
        base_url="https://api.deepseek.example",
        model="deepseek-chat",
        transport=transport,
    )

    events = list(
        provider.stream(
            ProviderRequest(system="system", messages=[]),
            ProviderStreamOptions(thinking=True, show_thinking=True),
        ),
    )

    assert [type(event).__name__ for event in events] == [
        "ProviderStreamStarted",
        "ProviderThinkingDelta",
        "ProviderContentDelta",
        "ProviderStreamCompleted",
    ]
    assert isinstance(events[1], ProviderThinkingDelta)
    assert events[1].text == "think"
    assert isinstance(events[-1], ProviderStreamCompleted)
    assert events[-1].response.thinking_content == "think"


def test_openai_compatible_streams_tool_call_deltas() -> None:
    transport = FakeStreamingTransport(
        [
            {
                "id": "chatcmpl_1",
                "model": "deepseek-chat",
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": '{"path"',
                                    },
                                },
                            ],
                        },
                        "finish_reason": None,
                    },
                ],
            },
            {
                "id": "chatcmpl_1",
                "model": "deepseek-chat",
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "function": {
                                        "arguments": ': "pyproject.toml"}',
                                    },
                                },
                            ],
                        },
                        "finish_reason": "tool_calls",
                    },
                ],
            },
        ],
    )
    provider = OpenAICompatibleProvider(
        api_key="test-key",
        base_url="https://api.deepseek.example",
        model="deepseek-chat",
        transport=transport,
    )

    events = list(provider.stream(ProviderRequest(system="system", messages=[])))

    tool_deltas = [event for event in events if isinstance(event, ProviderToolCallDelta)]
    assert len(tool_deltas) == 2
    assert isinstance(events[-1], ProviderStreamCompleted)
    assert events[-1].response.tool_calls[0].id == "call_1"
    assert events[-1].response.tool_calls[0].name == "read_file"
    assert events[-1].response.tool_calls[0].arguments == {"path": "pyproject.toml"}
