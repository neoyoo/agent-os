import asyncio

import pytest

from agentos.providers import (
    OpenAICompatibleProvider,
    ProviderRequest,
    ProviderStreamCompleted,
    ProviderToolCallDelta,
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


def test_openai_compatible_async_stream_uses_explicit_sync_transport() -> None:
    class FakeSyncStreamingTransport:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def post_json_stream(
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
                "id": "chatcmpl_sync",
                "model": "deepseek-chat",
                "choices": [{"delta": {"content": "sync"}, "finish_reason": "stop"}],
            }

    async def collect() -> tuple[list[object], list[dict[str, object]]]:
        transport = FakeSyncStreamingTransport()
        provider = OpenAICompatibleProvider(
            api_key="test-key",
            base_url="https://api.deepseek.example",
            model="deepseek-chat",
            transport=transport,
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
    assert events[-1].response.content == "sync"


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


def test_openai_compatible_cancelled_stream_does_not_complete() -> None:
    blocked = asyncio.Event()

    class BlockingAsyncStreamingTransport:
        async def post_json_stream(
            self,
            url: str,
            headers: dict[str, str],
            payload: dict[str, object],
            timeout: float,
        ):
            yield {
                "id": "chatcmpl_cancel",
                "model": "model",
                "choices": [{"delta": {"content": "partial"}}],
            }
            blocked.set()
            await asyncio.Event().wait()

    async def run_and_cancel() -> list[object]:
        provider = OpenAICompatibleProvider(
            api_key="key",
            base_url="https://example.test",
            model="model",
            async_transport=BlockingAsyncStreamingTransport(),
        )
        events: list[object] = []

        async def consume() -> None:
            async for event in provider.async_stream(
                ProviderRequest(system="system", messages=()),
            ):
                events.append(event)

        task = asyncio.create_task(consume())
        await blocked.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return events

    events = asyncio.run(run_and_cancel())

    assert not any(isinstance(event, ProviderStreamCompleted) for event in events)


def test_openai_compatible_sync_and_async_stream_events_are_equal() -> None:
    chunks = (
        {
            "model": "model",
            "choices": [{"delta": {"content": "hello "}}],
        },
        {
            "model": "model",
            "choices": [
                {
                    "delta": {
                        "content": "world",
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
                    "finish_reason": None,
                },
            ],
        },
        {
            "model": "model",
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "late_provider_id",
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
    )

    class SyncTransport:
        def post_json_stream(self, **kwargs: object):
            yield from chunks

    class AsyncTransport:
        async def post_json_stream(self, **kwargs: object):
            for chunk in chunks:
                yield chunk

    request = ProviderRequest(system="system", messages=())
    sync_provider = OpenAICompatibleProvider(
        api_key="key",
        base_url="https://example.test",
        model="model",
        transport=SyncTransport(),  # type: ignore[arg-type]
    )
    async_provider = OpenAICompatibleProvider(
        api_key="key",
        base_url="https://example.test",
        model="model",
        async_transport=AsyncTransport(),  # type: ignore[arg-type]
    )

    sync_events = list(sync_provider.stream(request))

    async def collect() -> list[object]:
        return [event async for event in async_provider.async_stream(request)]

    async_events = asyncio.run(collect())

    assert async_events == sync_events
    tool_deltas = [
        event for event in sync_events if isinstance(event, ProviderToolCallDelta)
    ]
    fallback_id = tool_deltas[0].tool_call_id
    assert fallback_id is not None
    assert [event.tool_call_id for event in tool_deltas] == [
        fallback_id,
        fallback_id,
    ]
    assert sync_events[-1].response.tool_calls[0].id == fallback_id


def test_openai_compatible_async_stream_generates_missing_tool_call_delta_id() -> None:
    class FakeAsyncStreamingTransport:
        async def post_json_stream(
            self,
            url: str,
            headers: dict[str, str],
            payload: dict[str, object],
            timeout: float,
        ):
            yield {
                "id": "chatcmpl_async",
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
            }

    async def collect() -> list[object]:
        provider = OpenAICompatibleProvider(
            api_key="test-key",
            base_url="https://api.deepseek.example",
            model="deepseek-chat",
            async_transport=FakeAsyncStreamingTransport(),
        )
        return [
            event
            async for event in provider.async_stream(
                ProviderRequest(system="system", messages=[]),
            )
        ]

    events = asyncio.run(collect())

    tool_deltas = [
        event for event in events if isinstance(event, ProviderToolCallDelta)
    ]
    fallback_id = tool_deltas[0].tool_call_id
    assert fallback_id is not None
    assert fallback_id.startswith("call_fallback_")
    assert events[-1].response.tool_calls[0].id == fallback_id
