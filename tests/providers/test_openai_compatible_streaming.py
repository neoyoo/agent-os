from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from agentos.providers import (
    OpenAICompatibleProvider,
    ProviderContentDelta,
    ProviderInputItem,
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
                messages=[ProviderInputItem.business_user("hi")],
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


def test_openai_compatible_streaming_payload_includes_extra_body() -> None:
    transport = FakeStreamingTransport(
        [
            {
                "id": "chatcmpl_1",
                "model": "deepseek-chat",
                "choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}],
            },
        ],
    )
    provider = OpenAICompatibleProvider(
        api_key="test-key",
        base_url="https://api.deepseek.example",
        model="deepseek-chat",
        transport=transport,
        extra_body={
            "vl_high_resolution_images": True,
            "metadata": {"route": "qwen-vl"},
        },
    )

    list(provider.stream(ProviderRequest(system="system", messages=[])))

    payload = transport.calls[0]["payload"]
    assert payload["stream"] is True
    assert payload["vl_high_resolution_images"] is True
    assert payload["metadata"] == {"route": "qwen-vl"}
    assert payload["model"] == "deepseek-chat"


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

    tool_deltas = [
        event for event in events if isinstance(event, ProviderToolCallDelta)
    ]
    assert len(tool_deltas) == 2
    assert isinstance(events[-1], ProviderStreamCompleted)
    assert events[-1].response.tool_calls[0].id == "call_1"
    assert events[-1].response.tool_calls[0].name == "read_file"
    assert events[-1].response.tool_calls[0].arguments == {"path": "pyproject.toml"}


def test_openai_compatible_stream_generates_missing_tool_call_id() -> None:
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
                                    "function": {
                                        "name": "load_skill",
                                        "arguments": '{"skill_name": "drawing"}',
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

    assert isinstance(events[-1], ProviderStreamCompleted)
    tool_deltas = [
        event for event in events if isinstance(event, ProviderToolCallDelta)
    ]
    fallback_id = tool_deltas[0].tool_call_id
    assert fallback_id is not None
    assert fallback_id.startswith("call_fallback_")
    assert fallback_id.endswith("_0")
    assert events[-1].response.tool_calls[0].id == fallback_id
    assert events[-1].response.tool_calls[0].name == "load_skill"
    assert events[-1].response.tool_calls[0].arguments == {
        "skill_name": "drawing",
    }


def test_openai_compatible_stream_generates_unique_missing_tool_call_ids() -> None:
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
                                    "function": {
                                        "name": "update_state",
                                        "arguments": '{"field_name": "a", "value": 1}',
                                    },
                                },
                                {
                                    "index": 1,
                                    "function": {
                                        "name": "update_state",
                                        "arguments": '{"field_name": "b", "value": 2}',
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

    ids = [tool_call.id for tool_call in events[-1].response.tool_calls]
    assert ids[0] != ids[1]
    assert ids[0].startswith("call_fallback_")
    assert ids[0].endswith("_0")
    assert ids[1].startswith("call_fallback_")
    assert ids[1].endswith("_1")


def test_fallback_tool_id_is_independent_of_response_id_chunk_timing() -> None:
    request = ProviderRequest(system="system", messages=[])

    def fallback_id(*, response_id_in_first_chunk: bool) -> str:
        first_chunk: dict[str, object] = {
            "model": "deepseek-chat",
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "function": {
                                    "name": "lookup",
                                    "arguments": '{"query"',
                                },
                            },
                        ],
                    },
                },
            ],
        }
        if response_id_in_first_chunk:
            first_chunk["id"] = "chatcmpl_timing"
        transport = FakeStreamingTransport(
            [
                first_chunk,
                {
                    "id": "chatcmpl_timing",
                    "model": "deepseek-chat",
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "function": {
                                            "arguments": ':"agentos"}',
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
        events = list(provider.stream(request))
        return events[-1].response.tool_calls[0].id

    assert fallback_id(response_id_in_first_chunk=False) == fallback_id(
        response_id_in_first_chunk=True,
    )


def test_missing_response_id_streams_do_not_reuse_fallback_tool_ids() -> None:
    transport = FakeStreamingTransport(
        [
            {
                "model": "deepseek-chat",
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "function": {
                                        "name": "lookup",
                                        "arguments": '{"query":"agentos"}',
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
    request = ProviderRequest(system="system", messages=[])

    first = list(provider.stream(request))[-1].response.tool_calls[0].id
    second = list(provider.stream(request))[-1].response.tool_calls[0].id

    assert first != second


def test_concurrent_streams_allocate_distinct_fallback_tool_ids() -> None:
    barrier = Barrier(2)

    class ConcurrentTransport(FakeStreamingTransport):
        def post_json_stream(
            self,
            url: str,
            headers: dict[str, str],
            payload: dict[str, object],
            timeout: float,
        ) -> Iterator[dict[str, object]]:
            barrier.wait()
            yield from self.chunks

    transport = ConcurrentTransport(
        [
            {
                "model": "deepseek-chat",
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "function": {
                                        "name": "lookup",
                                        "arguments": '{"query":"agentos"}',
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
    request = ProviderRequest(system="system", messages=[])

    def stream_tool_id(_: int) -> str:
        events = list(provider.stream(request))
        return events[-1].response.tool_calls[0].id

    with ThreadPoolExecutor(max_workers=2) as executor:
        ids = tuple(executor.map(stream_tool_id, range(2)))

    assert len(set(ids)) == 2


def test_openai_compatible_stream_preserves_provider_tool_call_id() -> None:
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
                                    "id": "provider_call_1",
                                    "function": {
                                        "name": "load_skill",
                                        "arguments": '{"skill_name": "drawing"}',
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

    assert events[-1].response.tool_calls[0].id == "provider_call_1"


def test_openai_compatible_stream_ignores_empty_tool_call_id_delta() -> None:
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
                                    "id": "provider_call_1",
                                    "function": {
                                        "name": "load_skill",
                                        "arguments": '{"skill_name"',
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
                                    "id": "",
                                    "function": {
                                        "arguments": ': "drawing"}',
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

    tool_deltas = [
        event for event in events if isinstance(event, ProviderToolCallDelta)
    ]
    assert [delta.tool_call_id for delta in tool_deltas] == [
        "provider_call_1",
        "provider_call_1",
    ]
    assert events[-1].response.tool_calls[0].id == "provider_call_1"


def test_openai_compatible_stream_rejects_non_object_tool_arguments() -> None:
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
                                        "arguments": "[]",
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

    with pytest.raises(ValueError, match="tool arguments must decode to an object"):
        list(provider.stream(ProviderRequest(system="system", messages=[])))


def test_openai_compatible_stream_rejects_missing_tool_name() -> None:
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
                                    "function": {"arguments": "{}"},
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

    with pytest.raises(ValueError, match="tool_call requires function name"):
        list(provider.stream(ProviderRequest(system="system", messages=[])))
