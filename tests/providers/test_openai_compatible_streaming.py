import asyncio
from collections.abc import Iterator

import pytest

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


def test_openai_compatible_async_stream_uses_async_transport() -> None:
    class FakeAsyncStreamingTransport:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def post_json_stream(
            self,
            url: str,
            headers: dict[str, str],
            payload: dict[str, object],
            timeout: float,
        ):
            self.calls.append(
                {
                    "url": url,
                    "headers": headers,
                    "payload": payload,
                    "timeout": timeout,
                },
            )
            yield {
                "id": "chatcmpl_async",
                "model": "deepseek-chat",
                "choices": [{"delta": {"content": "async"}, "finish_reason": "stop"}],
            }

    async def collect() -> tuple[list[object], list[dict[str, object]]]:
        transport = FakeAsyncStreamingTransport()
        provider = OpenAICompatibleProvider(
            api_key="test-key",
            base_url="https://api.deepseek.example",
            model="deepseek-chat",
            async_transport=transport,
        )
        events = [
            event
            async for event in provider.async_stream(
                ProviderRequest(system="system", messages=[]),
            )
        ]
        return events, transport.calls

    events, calls = asyncio.run(collect())

    assert calls[0]["payload"]["stream"] is True
    assert [type(event).__name__ for event in events] == [
        "ProviderStreamStarted",
        "ProviderContentDelta",
        "ProviderStreamCompleted",
    ]
    assert events[-1].response.content == "async"


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


def test_openai_compatible_async_streaming_payload_includes_extra_body() -> None:
    class FakeAsyncStreamingTransport:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def post_json_stream(
            self,
            url: str,
            headers: dict[str, str],
            payload: dict[str, object],
            timeout: float,
        ):
            self.calls.append(
                {
                    "url": url,
                    "headers": headers,
                    "payload": payload,
                    "timeout": timeout,
                },
            )
            yield {
                "id": "chatcmpl_async",
                "model": "deepseek-chat",
                "choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}],
            }

    async def collect_payload() -> dict[str, object]:
        transport = FakeAsyncStreamingTransport()
        provider = OpenAICompatibleProvider(
            api_key="test-key",
            base_url="https://api.deepseek.example",
            model="deepseek-chat",
            async_transport=transport,
            extra_body={
                "vl_high_resolution_images": True,
                "metadata": {"route": "qwen-vl"},
            },
        )
        events = [
            event
            async for event in provider.async_stream(
                ProviderRequest(system="system", messages=[]),
            )
        ]
        assert isinstance(events[-1], ProviderStreamCompleted)
        return transport.calls[0]["payload"]

    payload = asyncio.run(collect_payload())

    assert payload["stream"] is True
    assert payload["vl_high_resolution_images"] is True
    assert payload["metadata"] == {"route": "qwen-vl"}
    assert payload["model"] == "deepseek-chat"


def test_openai_compatible_async_stream_cancellation_reaches_transport() -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class BlockingAsyncStreamingTransport:
        async def post_json_stream(
            self,
            url: str,
            headers: dict[str, str],
            payload: dict[str, object],
            timeout: float,
        ):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
            yield {}

    async def run_and_cancel() -> bool:
        provider = OpenAICompatibleProvider(
            api_key="test-key",
            base_url="https://api.deepseek.example",
            model="deepseek-chat",
            async_transport=BlockingAsyncStreamingTransport(),
        )
        task = asyncio.create_task(
            anext(
                provider.async_stream(
                    ProviderRequest(system="system", messages=[]),
                ),
            ),
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return cancelled.is_set()

    assert asyncio.run(run_and_cancel()) is True


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


def test_openai_compatible_stream_generates_missing_tool_call_id(monkeypatch) -> None:
    monkeypatch.setattr(
        "agentos.providers.openai_compatible.time.time_ns",
        lambda: 1_700_000_000_000_000_000,
    )
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
    assert events[-1].response.tool_calls[0].id == (
        "call_ts_1700000000000000000"
    )
    assert events[-1].response.tool_calls[0].name == "load_skill"
    assert events[-1].response.tool_calls[0].arguments == {
        "skill_name": "drawing",
    }


def test_openai_compatible_stream_generates_unique_missing_tool_call_ids(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "agentos.providers.openai_compatible.time.time_ns",
        lambda: 1_700_000_000_000_000_000,
    )
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
    assert ids == [
        "call_ts_1700000000000000000",
        "call_ts_1700000000000000000_2",
    ]


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
