# Streaming Thinking OTel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add first-class streaming output, thinking output controls, SSE/callback adapters, and OTel/Langfuse streaming span support to agentos.

**Architecture:** The core runtime exposes typed stream events as the only streaming fact source. `QueryLoop.run_turn_stream()` consumes provider stream events, aggregates final `ProviderResponse` objects, and keeps `run_turn()` compatible by consuming the stream. SSE, JSONL, and callbacks are thin adapters over typed events; OTel instrumentation wraps provider stream iterators and ends spans only after terminal stream events.

**Tech Stack:** Python 3.11 dataclasses and protocols, pytest, existing agentos provider/runtime/observability modules, optional OpenTelemetry OTLP HTTP exporter.

---

## File Structure

- Create `src/agentos/providers/stream.py`: provider-level stream event dataclasses, `ProviderStreamOptions`, `StreamingProvider` protocol, and complete-to-stream fallback helper.
- Modify `src/agentos/providers/base.py`: add `thinking_content` to `ProviderResponse` for final aggregation without mixing it into assistant content.
- Modify `src/agentos/providers/__init__.py`: export stream event types and options.
- Modify `src/agentos/providers/fake.py`: support deterministic stream events for tests.
- Modify `src/agentos/providers/openai_compatible.py`: add `stream()` for OpenAI-compatible SSE chat completions, including content, reasoning/thinking, tool call deltas, usage, and terminal response aggregation.
- Create `src/agentos/runtime/stream_events.py`: turn-level stream event dataclasses and `RunOptions`.
- Modify `src/agentos/runtime/query_loop.py`: add `run_turn_stream()`, keep `run_turn()` compatible, support tool-call loops through streaming.
- Modify `src/agentos/runtime/__init__.py`: export runtime stream events and options.
- Create `src/agentos/runtime/agent.py`: high-level `Agent` facade so users configure dependencies once and pass per-turn options.
- Create `src/agentos/runtime/stream_serializers.py`: SSE, JSONL, and callback adapters over typed events.
- Modify `src/agentos/__init__.py`: export `Agent`, `RunOptions`, and stream event names.
- Modify `src/agentos/observability/config.py`: add stream/thinking capture policy flags.
- Modify `src/agentos/observability/snapshots.py`: include `thinking_content` in response snapshots with policy-aware capture.
- Modify `src/agentos/observability/conventions.py`: add stream, thinking, and Langfuse metadata constants.
- Modify `src/agentos/observability/instrumented.py`: add stream-aware provider instrumentation, root span output from stream terminal events, and delta stats.
- Modify `src/agentos/examples/small_openai_agent.py`: expose `--stream`, `--show-thinking`, and `--output-format stream-json|sse|text`.

---

### Task 1: Provider Stream Types And Complete Fallback

**Files:**
- Create: `src/agentos/providers/stream.py`
- Modify: `src/agentos/providers/base.py`
- Modify: `src/agentos/providers/__init__.py`
- Modify: `src/agentos/providers/fake.py`
- Test: `tests/providers/test_streaming.py`

- [ ] **Step 1: Write failing provider streaming tests**

Create `tests/providers/test_streaming.py`:

```python
from agentos.providers import (
    FakeProvider,
    ProviderContentDelta,
    ProviderResponse,
    ProviderStreamCompleted,
    ProviderStreamOptions,
    complete_response_to_stream_events,
)


def test_complete_response_to_stream_events_emits_delta_and_completed() -> None:
    response = ProviderResponse(content="hello", stop_reason="stop")

    events = list(
        complete_response_to_stream_events(
            request_id="provider_1",
            response=response,
            options=ProviderStreamOptions(),
        ),
    )

    assert [type(event).__name__ for event in events] == [
        "ProviderStreamStarted",
        "ProviderContentDelta",
        "ProviderStreamCompleted",
    ]
    assert events[1] == ProviderContentDelta(
        request_id="provider_1",
        index=1,
        text="hello",
    )
    assert isinstance(events[2], ProviderStreamCompleted)
    assert events[2].response is response


def test_complete_response_to_stream_events_hides_thinking_by_default() -> None:
    response = ProviderResponse(
        content="answer",
        thinking_content="private reasoning",
        stop_reason="stop",
    )

    events = list(
        complete_response_to_stream_events(
            request_id="provider_1",
            response=response,
            options=ProviderStreamOptions(thinking=True, show_thinking=False),
        ),
    )

    assert [type(event).__name__ for event in events] == [
        "ProviderStreamStarted",
        "ProviderContentDelta",
        "ProviderStreamCompleted",
    ]
    assert events[-1].response.thinking_content == "private reasoning"


def test_complete_response_to_stream_events_can_show_thinking() -> None:
    response = ProviderResponse(
        content="answer",
        thinking_content="private reasoning",
        stop_reason="stop",
    )

    events = list(
        complete_response_to_stream_events(
            request_id="provider_1",
            response=response,
            options=ProviderStreamOptions(thinking=True, show_thinking=True),
        ),
    )

    assert [type(event).__name__ for event in events] == [
        "ProviderStreamStarted",
        "ProviderThinkingDelta",
        "ProviderContentDelta",
        "ProviderStreamCompleted",
    ]
    assert events[1].text == "private reasoning"


def test_fake_provider_streams_configured_response() -> None:
    provider = FakeProvider([ProviderResponse(content="ok", stop_reason="stop")])

    events = list(
        provider.stream(
            type(
                "Request",
                (),
                {"system": "system", "messages": [], "tools": []},
            )(),
        ),
    )

    assert [type(event).__name__ for event in events] == [
        "ProviderStreamStarted",
        "ProviderContentDelta",
        "ProviderStreamCompleted",
    ]
    assert events[1].text == "ok"
    assert events[-1].response.content == "ok"
```

- [ ] **Step 2: Run provider streaming tests and verify failure**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/providers/test_streaming.py -q
```

Expected: import failure for `ProviderStreamOptions` or `complete_response_to_stream_events`.

- [ ] **Step 3: Implement provider stream dataclasses and fallback helper**

Create `src/agentos/providers/stream.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Protocol, TypeAlias

from agentos.providers.base import ProviderRequest, ProviderResponse


@dataclass(frozen=True, slots=True)
class ProviderStreamOptions:
    """控制 provider streaming 的单次请求选项。"""

    thinking: bool = False
    show_thinking: bool = False
    max_thinking_chars: int | None = None


@dataclass(frozen=True, slots=True)
class ProviderStreamStarted:
    """一次 provider streaming request 已开始。"""

    request_id: str
    thinking_requested: bool = False
    thinking_supported: bool = False


@dataclass(frozen=True, slots=True)
class ProviderContentDelta:
    """provider 返回的 assistant content 增量。"""

    request_id: str
    index: int
    text: str


@dataclass(frozen=True, slots=True)
class ProviderThinkingDelta:
    """provider 返回的 thinking/reasoning 增量。"""

    request_id: str
    index: int
    text: str


@dataclass(frozen=True, slots=True)
class ProviderToolCallDelta:
    """provider 返回的 tool call 增量。"""

    request_id: str
    index: int
    tool_call_id: str | None = None
    name_delta: str | None = None
    arguments_delta: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderUsageDelta:
    """provider streaming 中返回的 usage 增量或最终 usage。"""

    request_id: str
    index: int
    usage: object


@dataclass(frozen=True, slots=True)
class ProviderStreamCompleted:
    """一次 provider streaming request 已完成。"""

    request_id: str
    response: ProviderResponse
    stop_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderStreamFailed:
    """一次 provider streaming request 失败。"""

    request_id: str
    error: BaseException


@dataclass(frozen=True, slots=True)
class ProviderStreamCancelled:
    """一次 provider streaming request 被调用方取消。"""

    request_id: str
    reason: str | None = None


ProviderStreamEvent: TypeAlias = (
    ProviderStreamStarted
    | ProviderContentDelta
    | ProviderThinkingDelta
    | ProviderToolCallDelta
    | ProviderUsageDelta
    | ProviderStreamCompleted
    | ProviderStreamFailed
    | ProviderStreamCancelled
)


class StreamingProvider(Protocol):
    """支持 streaming 的 provider 协议。"""

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        """返回完整 provider response。"""

    def stream(
        self,
        request: ProviderRequest,
        options: ProviderStreamOptions | None = None,
    ) -> Iterator[ProviderStreamEvent]:
        """返回 provider stream events。"""


def complete_response_to_stream_events(
    *,
    request_id: str,
    response: ProviderResponse,
    options: ProviderStreamOptions | None = None,
) -> Iterator[ProviderStreamEvent]:
    """把完整 ProviderResponse 适配成 provider stream events。"""

    stream_options = options or ProviderStreamOptions()
    yield ProviderStreamStarted(
        request_id=request_id,
        thinking_requested=stream_options.thinking,
        thinking_supported=response.thinking_content is not None,
    )
    if (
        stream_options.thinking
        and stream_options.show_thinking
        and response.thinking_content
    ):
        text = response.thinking_content
        if stream_options.max_thinking_chars is not None:
            text = text[: stream_options.max_thinking_chars]
        yield ProviderThinkingDelta(request_id=request_id, index=1, text=text)
    if response.content:
        yield ProviderContentDelta(request_id=request_id, index=1, text=response.content)
    yield ProviderStreamCompleted(
        request_id=request_id,
        response=response,
        stop_reason=response.stop_reason,
    )
```

Modify `src/agentos/providers/base.py`:

```python
@dataclass(frozen=True, slots=True)
class ProviderResponse:
    """provider 返回的标准化响应。"""

    content: str = ""
    tool_calls: list[ProviderToolCall] = field(default_factory=list)
    stop_reason: str | None = None
    usage: ProviderUsage | None = None
    model: str | None = None
    provider_name: str | None = None
    response_id: str | None = None
    thinking_content: str | None = None
```

Modify `src/agentos/providers/__init__.py` to export all stream names:

```python
from agentos.providers.stream import (
    ProviderContentDelta,
    ProviderStreamCancelled,
    ProviderStreamCompleted,
    ProviderStreamEvent,
    ProviderStreamFailed,
    ProviderStreamOptions,
    ProviderStreamStarted,
    ProviderThinkingDelta,
    ProviderToolCallDelta,
    ProviderUsageDelta,
    StreamingProvider,
    complete_response_to_stream_events,
)
```

Append the same names to `__all__`.

Modify `src/agentos/providers/fake.py`:

```python
from typing import Iterator

from agentos.providers.stream import (
    ProviderStreamEvent,
    ProviderStreamOptions,
    complete_response_to_stream_events,
)


    def stream(
        self,
        request: ProviderRequest,
        options: ProviderStreamOptions | None = None,
    ) -> Iterator[ProviderStreamEvent]:
        """按完整响应适配成 stream events。"""

        response = self.complete(request)
        yield from complete_response_to_stream_events(
            request_id=f"provider_{len(self.requests)}",
            response=response,
            options=options,
        )
```

- [ ] **Step 4: Run provider streaming tests and verify pass**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/providers/test_streaming.py tests/providers/test_adapters.py tests/providers/test_openai_compatible.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit provider stream boundary**

```bash
git add src/agentos/providers/base.py src/agentos/providers/stream.py src/agentos/providers/__init__.py src/agentos/providers/fake.py tests/providers/test_streaming.py
git commit -m "feat: add provider streaming events"
```

---

### Task 2: QueryLoop Streaming Without Tools

**Files:**
- Create: `src/agentos/runtime/stream_events.py`
- Modify: `src/agentos/runtime/query_loop.py`
- Modify: `src/agentos/runtime/__init__.py`
- Test: `tests/runtime/test_streaming_query_loop.py`

- [ ] **Step 1: Write failing query loop streaming tests**

Create `tests/runtime/test_streaming_query_loop.py`:

```python
from agentos.context import ContextRenderer, ContextRuntime
from agentos.messages import MessageRuntime
from agentos.providers import FakeProvider, ProviderResponse
from agentos.runtime import (
    AssistantCompleted,
    AssistantContentDelta,
    ProviderRequestBuilder,
    QueryLoop,
    RunOptions,
    TurnStreamCompleted,
    TurnStreamStarted,
)


def build_loop(provider: FakeProvider, messages: MessageRuntime | None = None) -> QueryLoop:
    message_runtime = messages or MessageRuntime()
    context = ContextRuntime()
    return QueryLoop(
        context_runtime=context,
        message_runtime=message_runtime,
        request_builder=ProviderRequestBuilder(
            context_renderer=ContextRenderer(),
            message_runtime=message_runtime,
            tools=[],
        ),
        provider=provider,
    )


def test_query_loop_streams_content_and_completes_turn() -> None:
    messages = MessageRuntime()
    loop = build_loop(
        FakeProvider([ProviderResponse(content="hello", stop_reason="stop")]),
        messages,
    )

    events = list(loop.run_turn_stream("hi"))

    assert [type(event).__name__ for event in events] == [
        "TurnStreamStarted",
        "ProviderStreamStarted",
        "AssistantContentDelta",
        "ProviderStreamCompleted",
        "AssistantCompleted",
        "TurnStreamCompleted",
    ]
    assert isinstance(events[0], TurnStreamStarted)
    assert events[2] == AssistantContentDelta(index=1, text="hello")
    assert isinstance(events[-1], TurnStreamCompleted)
    assert messages.materialize_provider_messages() == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]


def test_run_turn_consumes_stream_and_returns_final_content() -> None:
    loop = build_loop(FakeProvider([ProviderResponse(content="hello")]))

    assert loop.run_turn("hi") == "hello"


def test_query_loop_hides_thinking_by_default() -> None:
    loop = build_loop(
        FakeProvider(
            [
                ProviderResponse(
                    content="answer",
                    thinking_content="private reasoning",
                    stop_reason="stop",
                ),
            ],
        ),
    )

    events = list(loop.run_turn_stream("hi", RunOptions(thinking=True)))

    assert "AssistantThinkingDelta" not in [type(event).__name__ for event in events]


def test_query_loop_can_emit_thinking_when_requested() -> None:
    loop = build_loop(
        FakeProvider(
            [
                ProviderResponse(
                    content="answer",
                    thinking_content="private reasoning",
                    stop_reason="stop",
                ),
            ],
        ),
    )

    events = list(
        loop.run_turn_stream(
            "hi",
            RunOptions(thinking=True, show_thinking=True),
        ),
    )

    assert [type(event).__name__ for event in events] == [
        "TurnStreamStarted",
        "ProviderStreamStarted",
        "AssistantThinkingDelta",
        "AssistantContentDelta",
        "ProviderStreamCompleted",
        "AssistantCompleted",
        "TurnStreamCompleted",
    ]
    assert events[2].text == "private reasoning"
```

- [ ] **Step 2: Run query loop streaming tests and verify failure**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/runtime/test_streaming_query_loop.py -q
```

Expected: import failure for `RunOptions` or missing `QueryLoop.run_turn_stream`.

- [ ] **Step 3: Implement turn stream events**

Create `src/agentos/runtime/stream_events.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from agentos.providers import ProviderResponse


@dataclass(frozen=True, slots=True)
class RunOptions:
    """单次 agent run 的交互选项。"""

    thinking: bool = False
    show_thinking: bool = False


@dataclass(frozen=True, slots=True)
class TurnStreamStarted:
    """agent turn stream 已开始。"""

    user_message: str


@dataclass(frozen=True, slots=True)
class AssistantContentDelta:
    """assistant content 增量。"""

    index: int
    text: str


@dataclass(frozen=True, slots=True)
class AssistantThinkingDelta:
    """assistant thinking 增量。"""

    index: int
    text: str


@dataclass(frozen=True, slots=True)
class AssistantCompleted:
    """assistant 最终响应已完成。"""

    response: ProviderResponse


@dataclass(frozen=True, slots=True)
class TurnStreamCompleted:
    """agent turn stream 已完成。"""

    content: str


@dataclass(frozen=True, slots=True)
class TurnStreamFailed:
    """agent turn stream 失败。"""

    error: BaseException


@dataclass(frozen=True, slots=True)
class TurnStreamCancelled:
    """agent turn stream 被取消。"""

    reason: str | None = None


TurnStreamEvent: TypeAlias = (
    TurnStreamStarted
    | AssistantContentDelta
    | AssistantThinkingDelta
    | AssistantCompleted
    | TurnStreamCompleted
    | TurnStreamFailed
    | TurnStreamCancelled
    | object
)
```

The `object` union member allows provider stream events to be yielded directly in the first implementation. Later tasks can tighten this with explicit wrapped event types if needed by type checking.

- [ ] **Step 4: Implement `QueryLoop.run_turn_stream()` for no-tool streaming**

Modify `src/agentos/runtime/query_loop.py` imports:

```python
from typing import Iterator, Protocol

from agentos.providers import (
    ProviderContentDelta,
    ProviderStreamCompleted,
    ProviderStreamOptions,
    ProviderThinkingDelta,
    StreamingProvider,
    complete_response_to_stream_events,
)
from agentos.runtime.stream_events import (
    AssistantCompleted,
    AssistantContentDelta,
    AssistantThinkingDelta,
    RunOptions,
    TurnStreamCompleted,
    TurnStreamEvent,
    TurnStreamFailed,
    TurnStreamStarted,
)
```

Add:

```python
    def run_turn_stream(
        self,
        user_message: str,
        options: RunOptions | None = None,
    ) -> Iterator[TurnStreamEvent]:
        """运行一轮 user -> provider -> assistant，并产出 typed stream events。"""

        run_options = options or RunOptions()
        turn = self._start_turn(user_message)
        user = self.message_runtime.append_user(user_message)
        self._emit(
            UserMessageAppendedEvent(
                message_id=user.id,
                **self._event_context(turn),
            ),
        )
        yield TurnStreamStarted(user_message=user_message)
        try:
            content = yield from self._run_provider_loop_stream(turn, run_options)
        except Exception as error:
            if turn is not None:
                turn.fail(str(error))
            self._emit(
                TurnFailedEvent(
                    error=str(error),
                    **self._event_context(turn),
                ),
            )
            yield TurnStreamFailed(error=error)
            raise
        if turn is not None:
            turn.complete()
        self._emit(TurnCompletedEvent(**self._event_context(turn)))
        yield TurnStreamCompleted(content=content)

    def _run_provider_loop_stream(
        self,
        turn: TurnState | None,
        options: RunOptions,
    ) -> Iterator[TurnStreamEvent]:
        """执行 provider streaming loop，直到得到最终 assistant response。"""

        request = self.build_request()
        self._emit(ProviderRequestBuiltEvent(**self._event_context(turn)))
        content_parts: list[str] = []
        response: ProviderResponse | None = None
        provider_options = ProviderStreamOptions(
            thinking=options.thinking,
            show_thinking=options.show_thinking,
        )
        for event in self._provider_stream_events(request, provider_options):
            yield event
            if isinstance(event, ProviderContentDelta):
                content_parts.append(event.text)
                yield AssistantContentDelta(index=event.index, text=event.text)
            elif isinstance(event, ProviderThinkingDelta):
                if options.show_thinking:
                    yield AssistantThinkingDelta(index=event.index, text=event.text)
            elif isinstance(event, ProviderStreamCompleted):
                response = event.response
        if response is None:
            raise RuntimeError("provider stream ended without completion event")
        self._emit(ProviderResponseReceivedEvent(**self._event_context(turn)))
        self._ensure_provider_response_usable(response)
        assistant = self.message_runtime.append_assistant(
            response.content,
            tool_calls=[
                ToolCall(
                    id=tool_call.id,
                    name=tool_call.name,
                    arguments=dict(tool_call.arguments),
                )
                for tool_call in response.tool_calls
            ],
        )
        self._emit(
            AssistantMessageAppendedEvent(
                message_id=assistant.id,
                **self._event_context(turn),
            ),
        )
        yield AssistantCompleted(response=response)
        if response.tool_calls:
            raise RuntimeError("streaming tool calls are implemented in Task 4")
        return response.content

    def _provider_stream_events(
        self,
        request: ProviderRequest,
        options: ProviderStreamOptions,
    ) -> Iterator[object]:
        """返回 provider stream events，必要时使用 complete fallback。"""

        if hasattr(self.provider, "stream"):
            yield from self.provider.stream(request, options)  # type: ignore[attr-defined]
            return
        response = self.provider.complete(request)
        yield from complete_response_to_stream_events(
            request_id="provider_1",
            response=response,
            options=options,
        )
```

Modify `run_turn()`:

```python
    def run_turn(self, user_message: str) -> str:
        """运行一轮 user -> provider -> assistant。"""

        final_content = ""
        for event in self.run_turn_stream(user_message):
            if isinstance(event, TurnStreamCompleted):
                final_content = event.content
        return final_content
```

- [ ] **Step 5: Export runtime stream events**

Modify `src/agentos/runtime/__init__.py`:

```python
from agentos.runtime.stream_events import (
    AssistantCompleted,
    AssistantContentDelta,
    AssistantThinkingDelta,
    RunOptions,
    TurnStreamCancelled,
    TurnStreamCompleted,
    TurnStreamEvent,
    TurnStreamFailed,
    TurnStreamStarted,
)
```

Append these names to `__all__`.

- [ ] **Step 6: Run query loop streaming tests and selected runtime tests**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/runtime/test_streaming_query_loop.py tests/runtime/test_query_loop.py tests/runtime/test_tool_loop.py -q
```

Expected: selected tests pass except streaming tool-call tests not yet present.

- [ ] **Step 7: Commit basic query loop streaming**

```bash
git add src/agentos/runtime/stream_events.py src/agentos/runtime/query_loop.py src/agentos/runtime/__init__.py tests/runtime/test_streaming_query_loop.py
git commit -m "feat: stream basic query loop output"
```

---

### Task 3: OpenAI-Compatible Streaming Parser

**Files:**
- Modify: `src/agentos/providers/openai_compatible.py`
- Test: `tests/providers/test_openai_compatible_streaming.py`

- [ ] **Step 1: Write failing OpenAI-compatible streaming parser tests**

Create `tests/providers/test_openai_compatible_streaming.py`:

```python
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
    def __init__(self, chunks: list[dict[str, object]]) -> None:
        self.chunks = chunks
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
            ProviderRequest(system="system", messages=[{"role": "user", "content": "hi"}]),
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
    assert events[-1].response.tool_calls[0].id == "call_1"
    assert events[-1].response.tool_calls[0].name == "read_file"
    assert events[-1].response.tool_calls[0].arguments == {"path": "pyproject.toml"}
```

- [ ] **Step 2: Run OpenAI-compatible streaming tests and verify failure**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/providers/test_openai_compatible_streaming.py -q
```

Expected: failure because `OpenAICompatibleTransport` has no `post_json_stream` and provider has no `stream()`.

- [ ] **Step 3: Extend transport protocol and urllib streaming transport**

Modify `src/agentos/providers/openai_compatible.py` imports:

```python
from typing import Iterator, Protocol
```

Extend `OpenAICompatibleTransport`:

```python
    def post_json_stream(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, object],
        timeout: float,
    ) -> Iterator[dict[str, object]]:
        """发送 JSON streaming POST 并逐个返回 SSE JSON object。"""
```

Add to `UrlLibJSONTransport`:

```python
    def post_json_stream(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, object],
        timeout: float,
    ) -> Iterator[dict[str, object]]:
        """发送 JSON streaming POST 请求并解析 OpenAI-compatible SSE。"""

        stream_payload = dict(payload)
        stream_payload["stream"] = True
        request = Request(
            url=url,
            data=json.dumps(stream_payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout) as response:  # noqa: S310
                for raw_line in response:
                    line = raw_line.decode("utf-8").strip()
                    if not line:
                        continue
                    if line.startswith("data:"):
                        line = line.removeprefix("data:").strip()
                    if line == "[DONE]":
                        break
                    parsed = json.loads(line)
                    if not isinstance(parsed, dict):
                        raise ValueError("stream chunk must be a JSON object")
                    yield parsed
        except HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            raise OpenAICompatibleProviderError(
                f"OpenAI-compatible request failed with HTTP {error.code}: {body}",
            ) from error
        except URLError as error:
            raise OpenAICompatibleProviderError(
                f"OpenAI-compatible request failed: {error.reason}",
            ) from error
```

- [ ] **Step 4: Implement provider stream aggregation**

Add imports:

```python
from agentos.providers.stream import (
    ProviderContentDelta,
    ProviderStreamCompleted,
    ProviderStreamEvent,
    ProviderStreamOptions,
    ProviderStreamStarted,
    ProviderThinkingDelta,
    ProviderToolCallDelta,
)
```

Add method to `OpenAICompatibleProvider`:

```python
    def stream(
        self,
        request: ProviderRequest,
        options: ProviderStreamOptions | None = None,
    ) -> Iterator[ProviderStreamEvent]:
        """调用 OpenAI-compatible streaming chat completions。"""

        stream_options = options or ProviderStreamOptions()
        transport = self.transport or UrlLibJSONTransport()
        payload = self._payload(request)
        payload["stream"] = True
        content_parts: list[str] = []
        thinking_parts: list[str] = []
        tool_builders: dict[int, dict[str, str]] = {}
        response_id = "stream"
        response_model: str | None = self.model
        stop_reason: str | None = None
        usage = None
        started = False
        content_index = 0
        thinking_index = 0
        tool_index = 0
        for chunk in transport.post_json_stream(
            url=self._chat_completions_url(),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            payload=payload,
            timeout=self.timeout,
        ):
            response_id = str(chunk.get("id") or response_id)
            response_model = (
                self.model if chunk.get("model") is None else str(chunk.get("model"))
            )
            if not started:
                started = True
                yield ProviderStreamStarted(
                    request_id=response_id,
                    thinking_requested=stream_options.thinking,
                    thinking_supported=True,
                )
            raw_usage = chunk.get("usage")
            if raw_usage is not None:
                usage = self._usage(raw_usage)
            choices = chunk.get("choices")
            if not isinstance(choices, list) or not choices:
                continue
            choice = choices[0]
            if not isinstance(choice, dict):
                continue
            raw_finish_reason = choice.get("finish_reason")
            if raw_finish_reason is not None:
                stop_reason = str(raw_finish_reason)
            delta = choice.get("delta")
            if not isinstance(delta, dict):
                continue
            reasoning = delta.get("reasoning_content")
            if isinstance(reasoning, str) and reasoning:
                thinking_parts.append(reasoning)
                if stream_options.thinking and stream_options.show_thinking:
                    thinking_index += 1
                    text = reasoning
                    if stream_options.max_thinking_chars is not None:
                        text = text[: stream_options.max_thinking_chars]
                    yield ProviderThinkingDelta(
                        request_id=response_id,
                        index=thinking_index,
                        text=text,
                    )
            content = delta.get("content")
            if isinstance(content, str) and content:
                content_parts.append(content)
                content_index += 1
                yield ProviderContentDelta(
                    request_id=response_id,
                    index=content_index,
                    text=content,
                )
            for raw_tool_call in delta.get("tool_calls") or []:
                if not isinstance(raw_tool_call, dict):
                    continue
                index = int(raw_tool_call.get("index", 0))
                builder = tool_builders.setdefault(
                    index,
                    {"id": "", "name": "", "arguments": ""},
                )
                tool_call_id = raw_tool_call.get("id")
                if isinstance(tool_call_id, str):
                    builder["id"] = tool_call_id
                function = raw_tool_call.get("function")
                name_delta = None
                arguments_delta = None
                if isinstance(function, dict):
                    raw_name = function.get("name")
                    if isinstance(raw_name, str):
                        builder["name"] += raw_name
                        name_delta = raw_name
                    raw_arguments = function.get("arguments")
                    if isinstance(raw_arguments, str):
                        builder["arguments"] += raw_arguments
                        arguments_delta = raw_arguments
                tool_index += 1
                yield ProviderToolCallDelta(
                    request_id=response_id,
                    index=tool_index,
                    tool_call_id=builder["id"] or None,
                    name_delta=name_delta,
                    arguments_delta=arguments_delta,
                )
        if not started:
            yield ProviderStreamStarted(
                request_id=response_id,
                thinking_requested=stream_options.thinking,
                thinking_supported=False,
            )
        response = ProviderResponse(
            content="".join(content_parts),
            tool_calls=self._built_tool_calls(tool_builders),
            stop_reason=stop_reason,
            usage=usage,
            model=response_model,
            provider_name="openai-compatible",
            response_id=response_id,
            thinking_content="".join(thinking_parts) or None,
        )
        yield ProviderStreamCompleted(
            request_id=response_id,
            response=response,
            stop_reason=stop_reason,
        )
```

Refactor `complete()` payload creation into:

```python
    def _payload(self, request: ProviderRequest) -> dict[str, object]:
        """构造 OpenAI-compatible chat completions payload。"""

        payload: dict[str, object] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": request.system},
                *[self._message(message) for message in request.messages],
            ],
        }
        if request.tools:
            payload["tools"] = request.tools
        if self.thinking is not None:
            payload["thinking"] = dict(self.thinking)
        return payload
```

Use `_payload(request)` in `complete()`.

Add:

```python
    def _built_tool_calls(
        self,
        tool_builders: dict[int, dict[str, str]],
    ) -> list[ProviderToolCall]:
        """把 streaming tool call builder 转为 ProviderToolCall。"""

        tool_calls: list[ProviderToolCall] = []
        for index in sorted(tool_builders):
            item = tool_builders[index]
            arguments = item["arguments"] or "{}"
            tool_calls.append(
                ProviderToolCall(
                    id=item["id"],
                    name=item["name"],
                    arguments=json.loads(arguments),
                ),
            )
        return tool_calls
```

- [ ] **Step 5: Run streaming parser tests**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/providers/test_openai_compatible_streaming.py tests/providers/test_openai_compatible.py -q
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit OpenAI-compatible streaming parser**

```bash
git add src/agentos/providers/openai_compatible.py tests/providers/test_openai_compatible_streaming.py
git commit -m "feat: stream openai compatible responses"
```

---

### Task 4: Streaming Tool Call Loop

**Files:**
- Modify: `src/agentos/runtime/stream_events.py`
- Modify: `src/agentos/runtime/query_loop.py`
- Test: `tests/runtime/test_streaming_tool_loop.py`

- [ ] **Step 1: Write failing streaming tool loop tests**

Create `tests/runtime/test_streaming_tool_loop.py`:

```python
from pathlib import Path

from agentos.capabilities import ToolCallRouter, ToolRegistry, read_file_tool
from agentos.context import ContextRenderer, ContextRuntime
from agentos.messages import MessageRuntime
from agentos.providers import FakeProvider, ProviderResponse, ProviderToolCall
from agentos.runtime import (
    ProviderRequestBuilder,
    QueryLoop,
    SessionState,
    ToolStreamCompleted,
    ToolStreamStarted,
    TurnStreamCompleted,
)


def test_streaming_query_loop_executes_tool_and_continues(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('name = "agent-os"', encoding="utf-8")
    context = ContextRuntime()
    messages = MessageRuntime()
    registry = ToolRegistry()
    registry.register(read_file_tool(root=tmp_path))
    router = ToolCallRouter(tool_registry=registry, context_runtime=context)
    provider = FakeProvider(
        [
            ProviderResponse(
                tool_calls=[
                    ProviderToolCall(
                        id="call_read",
                        name="read_file",
                        arguments={"path": "pyproject.toml"},
                    ),
                ],
                stop_reason="tool_calls",
            ),
            ProviderResponse(content="项目名是 agent-os。", stop_reason="stop"),
        ],
    )
    loop = QueryLoop(
        context_runtime=context,
        message_runtime=messages,
        request_builder=ProviderRequestBuilder(
            context_renderer=ContextRenderer(),
            message_runtime=messages,
            tools=router.tool_specs(),
        ),
        provider=provider,
        tool_call_router=router,
        session_state=SessionState(id="session_stream"),
    )

    events = list(loop.run_turn_stream("读取项目名"))

    assert ToolStreamStarted(
        tool_name="read_file",
        tool_call_id="call_read",
    ) in events
    assert any(isinstance(event, ToolStreamCompleted) for event in events)
    assert events[-1] == TurnStreamCompleted(content="项目名是 agent-os。")
    assert provider.requests[1].messages[-1]["role"] == "tool"
    assert 'name = "agent-os"' in str(provider.requests[1].messages[-1]["content"])
```

- [ ] **Step 2: Run streaming tool loop test and verify failure**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/runtime/test_streaming_tool_loop.py -q
```

Expected: failure because `ToolStreamStarted` is not defined or streaming tool calls still raise the explicit unsupported-tool-streaming runtime error from Task 2.

- [ ] **Step 3: Add tool stream events**

Modify `src/agentos/runtime/stream_events.py`:

```python
@dataclass(frozen=True, slots=True)
class ToolStreamStarted:
    """tool execution 已开始。"""

    tool_name: str
    tool_call_id: str


@dataclass(frozen=True, slots=True)
class ToolStreamCompleted:
    """tool execution 已完成。"""

    tool_name: str
    tool_call_id: str
    content: str


@dataclass(frozen=True, slots=True)
class ToolStreamFailed:
    """tool execution 失败。"""

    tool_name: str
    tool_call_id: str
    error: BaseException
```

Append these to `TurnStreamEvent` and `runtime/__init__.py`.

- [ ] **Step 4: Implement streaming tool loop**

Replace the unsupported-tool-streaming branch in `_run_provider_loop_stream()` with a loop matching existing non-streaming behavior:

```python
        iterations = 0
        while True:
            request = self.build_request()
            self._emit(ProviderRequestBuiltEvent(**self._event_context(turn)))
            response = yield from self._consume_provider_stream(request, options)
            self._emit(ProviderResponseReceivedEvent(**self._event_context(turn)))
            self._ensure_provider_response_usable(response)
            assistant = self.message_runtime.append_assistant(
                response.content,
                tool_calls=[
                    ToolCall(
                        id=tool_call.id,
                        name=tool_call.name,
                        arguments=dict(tool_call.arguments),
                    )
                    for tool_call in response.tool_calls
                ],
            )
            self._emit(
                AssistantMessageAppendedEvent(
                    message_id=assistant.id,
                    **self._event_context(turn),
                ),
            )
            yield AssistantCompleted(response=response)
            if not response.tool_calls:
                return response.content
            if self.tool_call_router is None:
                raise RuntimeError("tool call router is required for tool calls")
            iterations += 1
            if iterations > self.max_tool_iterations:
                raise RuntimeError("provider tool-call loop exceeded max iterations")
            if turn is not None:
                turn.increment_tool_iteration()
            appended_message_ids = [assistant.id]
            for tool_call in response.tool_calls:
                yield ToolStreamStarted(
                    tool_name=tool_call.name,
                    tool_call_id=tool_call.id,
                )
                self._emit(
                    ToolCallRequestedEvent(
                        tool_name=tool_call.name,
                        tool_call_id=tool_call.id,
                        **self._event_context(turn),
                    ),
                )
                self._emit(
                    ToolExecutionStartedEvent(
                        tool_name=tool_call.name,
                        tool_call_id=tool_call.id,
                        **self._event_context(turn),
                    ),
                )
                try:
                    result = self.tool_call_router.execute_tool_call(tool_call)
                except Exception as error:
                    self.message_runtime.active_window.remove_refs(
                        appended_message_ids,
                        self.message_runtime.store,
                    )
                    yield ToolStreamFailed(
                        tool_name=tool_call.name,
                        tool_call_id=tool_call.id,
                        error=error,
                    )
                    raise
                self._emit(
                    ToolExecutionCompletedEvent(
                        tool_name=tool_call.name,
                        tool_call_id=tool_call.id,
                        **self._event_context(turn),
                    ),
                )
                tool_result = self.message_runtime.append_tool_result(
                    tool_call_id=result.tool_call_id,
                    content=result.content,
                )
                appended_message_ids.append(tool_result.id)
                self._emit(
                    ToolResultAppendedEvent(
                        tool_name=tool_call.name,
                        tool_call_id=tool_call.id,
                        message_id=tool_result.id,
                        **self._event_context(turn),
                    ),
                )
                yield ToolStreamCompleted(
                    tool_name=tool_call.name,
                    tool_call_id=tool_call.id,
                    content=result.content,
                )
```

Extract provider stream consumption into:

```python
    def _consume_provider_stream(
        self,
        request: ProviderRequest,
        options: RunOptions,
    ) -> Iterator[TurnStreamEvent]:
        """消费 provider stream 并返回最终 ProviderResponse。"""

        provider_options = ProviderStreamOptions(
            thinking=options.thinking,
            show_thinking=options.show_thinking,
        )
        response: ProviderResponse | None = None
        for event in self._provider_stream_events(request, provider_options):
            yield event
            if isinstance(event, ProviderContentDelta):
                yield AssistantContentDelta(index=event.index, text=event.text)
            elif isinstance(event, ProviderThinkingDelta) and options.show_thinking:
                yield AssistantThinkingDelta(index=event.index, text=event.text)
            elif isinstance(event, ProviderStreamCompleted):
                response = event.response
        if response is None:
            raise RuntimeError("provider stream ended without completion event")
        return response
```

- [ ] **Step 5: Run streaming tool tests**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/runtime/test_streaming_tool_loop.py tests/runtime/test_streaming_query_loop.py tests/runtime/test_tool_loop.py -q
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit streaming tool loop**

```bash
git add src/agentos/runtime/stream_events.py src/agentos/runtime/query_loop.py src/agentos/runtime/__init__.py tests/runtime/test_streaming_tool_loop.py
git commit -m "feat: stream tool call loop events"
```

---

### Task 5: Agent Facade, SSE, JSONL, And Callbacks

**Files:**
- Create: `src/agentos/runtime/agent.py`
- Create: `src/agentos/runtime/stream_serializers.py`
- Modify: `src/agentos/runtime/__init__.py`
- Modify: `src/agentos/__init__.py`
- Test: `tests/runtime/test_agent_stream_api.py`
- Test: `tests/runtime/test_stream_serializers.py`

- [ ] **Step 1: Write failing Agent facade and serializer tests**

Create `tests/runtime/test_agent_stream_api.py`:

```python
from agentos import Agent
from agentos.context import ContextRenderer, ContextRuntime
from agentos.messages import MessageRuntime
from agentos.providers import FakeProvider, ProviderResponse
from agentos.runtime import ProviderRequestBuilder


def build_agent(provider: FakeProvider) -> Agent:
    context = ContextRuntime()
    messages = MessageRuntime()
    return Agent(
        query_loop_kwargs={
            "context_runtime": context,
            "message_runtime": messages,
            "request_builder": ProviderRequestBuilder(
                context_renderer=ContextRenderer(),
                message_runtime=messages,
                tools=[],
            ),
            "provider": provider,
        },
    )


def test_agent_run_returns_result_without_extra_objects() -> None:
    agent = build_agent(FakeProvider([ProviderResponse(content="ok")]))

    result = agent.run("hello")

    assert result.content == "ok"


def test_agent_stream_accepts_per_turn_thinking_options() -> None:
    agent = build_agent(
        FakeProvider(
            [
                ProviderResponse(
                    content="answer",
                    thinking_content="think",
                ),
            ],
        ),
    )

    events = list(agent.stream("hello", thinking=True, show_thinking=True))

    assert "AssistantThinkingDelta" in [type(event).__name__ for event in events]


def test_agent_stream_sse_returns_sse_strings() -> None:
    agent = build_agent(FakeProvider([ProviderResponse(content="ok")]))

    chunks = list(agent.stream_sse("hello"))

    assert any(chunk.startswith("event: content_delta") for chunk in chunks)
    assert chunks[-1].startswith("event: done")
```

Create `tests/runtime/test_stream_serializers.py`:

```python
import json

from agentos.runtime import (
    AssistantContentDelta,
    AssistantThinkingDelta,
    ToolStreamStarted,
    TurnStreamCompleted,
    event_to_json,
    event_to_sse,
)


def test_event_to_sse_serializes_content_delta() -> None:
    chunk = event_to_sse(AssistantContentDelta(index=1, text="hello"))

    assert chunk.startswith("event: content_delta\n")
    assert chunk.endswith("\n\n")
    assert json.loads(chunk.split("data: ", 1)[1]) == {
        "index": 1,
        "text": "hello",
    }


def test_event_to_sse_can_hide_thinking() -> None:
    assert event_to_sse(
        AssistantThinkingDelta(index=1, text="secret"),
        show_thinking=False,
    ) is None


def test_event_to_sse_serializes_tool_and_done() -> None:
    assert event_to_sse(
        ToolStreamStarted(tool_name="read_file", tool_call_id="call_1"),
    ).startswith("event: tool_started")
    assert event_to_sse(TurnStreamCompleted(content="ok")).startswith("event: done")


def test_event_to_json_serializes_event_type() -> None:
    payload = json.loads(event_to_json(AssistantContentDelta(index=1, text="hello")))

    assert payload == {
        "type": "content_delta",
        "index": 1,
        "text": "hello",
    }
```

- [ ] **Step 2: Run Agent API tests and verify failure**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/runtime/test_agent_stream_api.py tests/runtime/test_stream_serializers.py -q
```

Expected: import failure for `Agent` or serializers.

- [ ] **Step 3: Implement Agent facade**

Create `src/agentos/runtime/agent.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterator

from agentos.runtime.query_loop import QueryLoop
from agentos.runtime.stream_events import RunOptions, TurnStreamCompleted, TurnStreamEvent
from agentos.runtime.stream_serializers import event_to_sse


@dataclass(frozen=True, slots=True)
class AgentResult:
    """Agent 完整响应结果。"""

    content: str


@dataclass(slots=True)
class Agent:
    """用户侧 agent facade，隐藏 QueryLoop 装配细节。"""

    query_loop: QueryLoop

    def __init__(
        self,
        query_loop: QueryLoop | None = None,
        query_loop_kwargs: dict[str, object] | None = None,
    ) -> None:
        """从 QueryLoop 或 QueryLoop kwargs 创建 Agent。"""

        if query_loop is None and query_loop_kwargs is None:
            raise ValueError("query_loop or query_loop_kwargs is required")
        self.query_loop = query_loop or QueryLoop(**query_loop_kwargs)  # type: ignore[arg-type]

    def run(
        self,
        user_message: str,
        *,
        thinking: bool = False,
        show_thinking: bool = False,
    ) -> AgentResult:
        """运行完整 turn，并返回最终内容。"""

        final_content = ""
        for event in self.stream(
            user_message,
            thinking=thinking,
            show_thinking=show_thinking,
        ):
            if isinstance(event, TurnStreamCompleted):
                final_content = event.content
        return AgentResult(content=final_content)

    def stream(
        self,
        user_message: str,
        *,
        thinking: bool = False,
        show_thinking: bool = False,
    ) -> Iterator[TurnStreamEvent]:
        """运行 turn，并返回 typed stream events。"""

        yield from self.query_loop.run_turn_stream(
            user_message,
            RunOptions(thinking=thinking, show_thinking=show_thinking),
        )

    def stream_sse(
        self,
        user_message: str,
        *,
        thinking: bool = False,
        show_thinking: bool = False,
    ) -> Iterator[str]:
        """运行 turn，并返回 SSE 字符串。"""

        for event in self.stream(
            user_message,
            thinking=thinking,
            show_thinking=show_thinking,
        ):
            chunk = event_to_sse(event, show_thinking=show_thinking)
            if chunk is not None:
                yield chunk

    def run_with_callbacks(
        self,
        user_message: str,
        *,
        thinking: bool = False,
        show_thinking: bool = False,
        on_event: Callable[[TurnStreamEvent], None] | None = None,
    ) -> AgentResult:
        """运行 turn，并把每个 typed event 分发给 callback。"""

        final_content = ""
        for event in self.stream(
            user_message,
            thinking=thinking,
            show_thinking=show_thinking,
        ):
            if on_event is not None:
                on_event(event)
            if isinstance(event, TurnStreamCompleted):
                final_content = event.content
        return AgentResult(content=final_content)
```

- [ ] **Step 4: Implement serializers**

Create `src/agentos/runtime/stream_serializers.py`:

```python
from __future__ import annotations

from dataclasses import asdict, is_dataclass
import json

from agentos.runtime.stream_events import (
    AssistantContentDelta,
    AssistantThinkingDelta,
    ToolStreamCompleted,
    ToolStreamFailed,
    ToolStreamStarted,
    TurnStreamCompleted,
)


def event_type(event: object) -> str:
    """返回 channel adapter 使用的稳定 event type。"""

    if isinstance(event, AssistantContentDelta):
        return "content_delta"
    if isinstance(event, AssistantThinkingDelta):
        return "thinking_delta"
    if isinstance(event, ToolStreamStarted):
        return "tool_started"
    if isinstance(event, ToolStreamCompleted):
        return "tool_completed"
    if isinstance(event, ToolStreamFailed):
        return "tool_failed"
    if isinstance(event, TurnStreamCompleted):
        return "done"
    return type(event).__name__


def event_payload(event: object) -> dict[str, object]:
    """把 typed event 转成 JSON-safe payload。"""

    if is_dataclass(event):
        return {
            key: value
            for key, value in asdict(event).items()
            if isinstance(value, (str, int, float, bool, type(None), list, dict))
        }
    return {}


def event_to_json(event: object, *, show_thinking: bool = True) -> str | None:
    """把 typed event 转成 JSONL 字符串。"""

    if isinstance(event, AssistantThinkingDelta) and not show_thinking:
        return None
    payload = {"type": event_type(event), **event_payload(event)}
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def event_to_sse(event: object, *, show_thinking: bool = True) -> str | None:
    """把 typed event 转成 SSE chunk。"""

    payload = event_to_json(event, show_thinking=show_thinking)
    if payload is None:
        return None
    return f"event: {event_type(event)}\ndata: {payload}\n\n"
```

- [ ] **Step 5: Export Agent and serializers**

Modify `src/agentos/runtime/__init__.py`:

```python
from agentos.runtime.agent import Agent, AgentResult
from agentos.runtime.stream_serializers import event_payload, event_to_json, event_to_sse, event_type
```

Append names to `__all__`.

Modify `src/agentos/__init__.py`:

```python
from agentos.runtime import Agent, AgentResult, RunOptions
```

Append names to `__all__`.

- [ ] **Step 6: Run Agent API and serializer tests**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/runtime/test_agent_stream_api.py tests/runtime/test_stream_serializers.py tests/architecture/test_public_api.py -q
```

Expected: all selected tests pass.

- [ ] **Step 7: Commit Agent API and stream serializers**

```bash
git add src/agentos/runtime/agent.py src/agentos/runtime/stream_serializers.py src/agentos/runtime/__init__.py src/agentos/__init__.py tests/runtime/test_agent_stream_api.py tests/runtime/test_stream_serializers.py
git commit -m "feat: add agent stream api"
```

---

### Task 6: Thinking Capture Policy And Snapshots

**Files:**
- Modify: `src/agentos/observability/config.py`
- Modify: `src/agentos/observability/snapshots.py`
- Test: `tests/observability/test_capture_policy.py`
- Test: `tests/observability/test_snapshots.py`

- [ ] **Step 1: Write failing capture policy tests**

Append to `tests/observability/test_capture_policy.py`:

```python
from agentos.observability import CapturePolicy


def test_capture_policy_defaults_do_not_capture_thinking_or_delta_text() -> None:
    policy = CapturePolicy.metadata_only()

    assert policy.capture_thinking is False
    assert policy.capture_stream_deltas is False
    assert policy.capture_stream_delta_text is False


def test_full_local_capture_includes_thinking_and_delta_text() -> None:
    policy = CapturePolicy.full_for_local_development()

    assert policy.capture_thinking is True
    assert policy.capture_stream_deltas is True
    assert policy.capture_stream_delta_text is True
```

Append to `tests/observability/test_snapshots.py`:

```python
from agentos.observability import CapturePolicy
from agentos.observability.snapshots import build_provider_response_snapshot
from agentos.providers import ProviderResponse


def test_response_snapshot_hides_thinking_by_default() -> None:
    snapshot = build_provider_response_snapshot(
        ProviderResponse(content="answer", thinking_content="secret"),
        CapturePolicy.metadata_only(),
    )

    assert snapshot.thinking_content is None
    assert snapshot.thinking_length == 6


def test_response_snapshot_captures_thinking_in_full_mode() -> None:
    snapshot = build_provider_response_snapshot(
        ProviderResponse(content="answer", thinking_content="secret"),
        CapturePolicy.full_for_local_development(),
    )

    assert snapshot.thinking_content == "secret"
    assert snapshot.thinking_length == 6
```

- [ ] **Step 2: Run capture policy tests and verify failure**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/observability/test_capture_policy.py tests/observability/test_snapshots.py -q
```

Expected: failure because policy fields and snapshot fields do not exist.

- [ ] **Step 3: Extend CapturePolicy**

Modify `src/agentos/observability/config.py`:

```python
    capture_thinking: bool = False
    capture_stream_deltas: bool = False
    capture_stream_delta_text: bool = False
    max_stream_delta_events: int = 200
```

Modify classmethods:

```python
    @classmethod
    def redacted(
        cls,
        *,
        max_string_length: int = 4000,
        redactor: Redactor = default_redactor,
    ) -> "CapturePolicy":
        """捕获经过 redaction 的 provider/tool payload。"""

        return cls(
            mode="redacted",
            capture_system=True,
            capture_messages=True,
            capture_tool_schemas=True,
            capture_provider_output=True,
            capture_tool_arguments=True,
            capture_tool_result=True,
            capture_thinking=False,
            capture_stream_deltas=True,
            capture_stream_delta_text=False,
            max_string_length=max_string_length,
            redactor=redactor,
        )

    @classmethod
    def full_for_local_development(
        cls,
        *,
        max_string_length: int = 4000,
    ) -> "CapturePolicy":
        """本地开发用完整捕获模式。"""

        return cls(
            mode="full",
            capture_system=True,
            capture_messages=True,
            capture_tool_schemas=True,
            capture_provider_output=True,
            capture_tool_arguments=True,
            capture_tool_result=True,
            capture_thinking=True,
            capture_stream_deltas=True,
            capture_stream_delta_text=True,
            max_string_length=max_string_length,
        )
```

- [ ] **Step 4: Extend response snapshots**

Modify `ProviderResponseSnapshot` in `src/agentos/observability/snapshots.py`:

```python
    thinking_content: str | None
    thinking_length: int
    thinking_sha256: str
```

Modify `build_provider_response_snapshot()`:

```python
    thinking_content = response.thinking_content or ""
```

Add fields:

```python
        thinking_content=(
            _captured_string(thinking_content, policy)
            if policy.capture_thinking and thinking_content
            else None
        ),
        thinking_length=len(thinking_content),
        thinking_sha256=stable_sha256(thinking_content),
```

- [ ] **Step 5: Run capture policy tests**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/observability/test_capture_policy.py tests/observability/test_snapshots.py -q
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit thinking capture policy**

```bash
git add src/agentos/observability/config.py src/agentos/observability/snapshots.py tests/observability/test_capture_policy.py tests/observability/test_snapshots.py
git commit -m "feat: control thinking stream capture"
```

---

### Task 7: Streaming OTel Instrumentation

**Files:**
- Modify: `src/agentos/observability/conventions.py`
- Modify: `src/agentos/observability/instrumented.py`
- Modify: `src/agentos/observability/tracer.py`
- Test: `tests/observability/test_streaming_provider_span.py`

- [ ] **Step 1: Write failing streaming provider span tests**

Create `tests/observability/test_streaming_provider_span.py`:

```python
from agentos.observability import CapturePolicy, InMemoryTracer
from agentos.observability.instrumented import InstrumentedProvider
from agentos.providers import (
    ProviderContentDelta,
    ProviderRequest,
    ProviderResponse,
    ProviderStreamCompleted,
    ProviderStreamOptions,
    ProviderStreamStarted,
)


class StreamingProviderStub:
    def complete(self, request: ProviderRequest) -> ProviderResponse:
        return ProviderResponse(content="unused")

    def stream(self, request: ProviderRequest, options: ProviderStreamOptions | None = None):
        yield ProviderStreamStarted(request_id="stream_1")
        yield ProviderContentDelta(request_id="stream_1", index=1, text="hel")
        yield ProviderContentDelta(request_id="stream_1", index=2, text="lo")
        yield ProviderStreamCompleted(
            request_id="stream_1",
            response=ProviderResponse(
                content="hello",
                stop_reason="stop",
                model="model-test",
                provider_name="provider-test",
            ),
            stop_reason="stop",
        )


def test_instrumented_provider_stream_finishes_span_after_completed_event() -> None:
    tracer = InMemoryTracer()
    provider = InstrumentedProvider(
        StreamingProviderStub(),
        tracer=tracer,
        capture_policy=CapturePolicy.metadata_only(),
    )

    events = list(
        provider.stream(
            ProviderRequest(system="system", messages=[], tools=[]),
            ProviderStreamOptions(),
        ),
    )

    assert [type(event).__name__ for event in events] == [
        "ProviderStreamStarted",
        "ProviderContentDelta",
        "ProviderContentDelta",
        "ProviderStreamCompleted",
    ]
    record = tracer.records[0]
    assert record.name == "provider.stream"
    assert record.attributes["gen_ai.request.stream"] is True
    assert record.attributes["agentos.stream.content.delta_count"] == 2
    assert record.attributes["agentos.stream.content.char_count"] == 5
    assert record.attributes["gen_ai.response.finish_reasons"] == ["stop"]


def test_instrumented_provider_stream_does_not_capture_delta_text_by_default() -> None:
    tracer = InMemoryTracer()
    provider = InstrumentedProvider(
        StreamingProviderStub(),
        tracer=tracer,
        capture_policy=CapturePolicy.metadata_only(),
    )

    list(provider.stream(ProviderRequest(system="system", messages=[], tools=[])))

    assert all(
        "text" not in event.attributes
        for event in tracer.records[0].events
        if event.name == "agentos.stream.content_delta"
    )
```

- [ ] **Step 2: Run streaming observability tests and verify failure**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/observability/test_streaming_provider_span.py -q
```

Expected: failure because `InstrumentedProvider.stream()` is missing.

- [ ] **Step 3: Add stream constants**

Modify `src/agentos/observability/conventions.py`:

```python
GEN_AI_REQUEST_STREAM = "gen_ai.request.stream"
GEN_AI_RESPONSE_TIME_TO_FIRST_CHUNK = "gen_ai.response.time_to_first_chunk"
AGENTOS_STREAM_CONTENT_DELTA = "agentos.stream.content_delta"
AGENTOS_STREAM_THINKING_DELTA = "agentos.stream.thinking_delta"
AGENTOS_STREAM_TOOL_CALL_DELTA = "agentos.stream.tool_call_delta"
```

- [ ] **Step 4: Implement `InstrumentedProvider.stream()`**

Modify `src/agentos/observability/instrumented.py` imports:

```python
from time import monotonic

from agentos.providers import (
    ProviderContentDelta,
    ProviderStreamCompleted,
    ProviderStreamEvent,
    ProviderStreamOptions,
    ProviderThinkingDelta,
    ProviderToolCallDelta,
    complete_response_to_stream_events,
)
```

Add to `InstrumentedProvider`:

```python
    def stream(
        self,
        request: ProviderRequest,
        options: ProviderStreamOptions | None = None,
    ) -> object:
        """调用 provider stream，并记录覆盖完整 streaming 生命周期的 generation span。"""

        request_snapshot = build_provider_request_snapshot(
            request,
            self._capture_policy,
        )
        started_at = monotonic()
        first_chunk_at: float | None = None
        content_delta_count = 0
        content_char_count = 0
        thinking_delta_count = 0
        thinking_char_count = 0
        tool_delta_count = 0
        with self._tracer.start_span(
            "provider.stream",
            attributes={
                LANGFUSE_OBSERVATION_TYPE: "generation",
                GEN_AI_OPERATION_NAME: "chat",
                "gen_ai.request.stream": True,
                "agentos.provider_request.system.length": request_snapshot.system_length,
                "agentos.provider_request.messages.count": request_snapshot.message_count,
                "agentos.provider_request.tools.count": request_snapshot.tool_count,
                "agentos.provider_request.system.sha256": request_snapshot.system_sha256,
                "agentos.provider_request.messages.sha256": request_snapshot.messages_sha256,
                "agentos.provider_request.tools.sha256": request_snapshot.tools_sha256,
            },
        ) as span:
            apply_common_observability_attributes(
                span,
                tracer=self._tracer,
                capture_policy=self._capture_policy,
            )
            span.set_attribute(
                LANGFUSE_OBSERVATION_INPUT,
                json_attribute(
                    self._provider_input_payload(request_snapshot),
                    policy=self._capture_policy,
                ),
            )
            try:
                for event in self._inner_stream(request, options):
                    if first_chunk_at is None and not isinstance(event, ProviderStreamCompleted):
                        first_chunk_at = monotonic()
                    if isinstance(event, ProviderContentDelta):
                        content_delta_count += 1
                        content_char_count += len(event.text)
                        self._record_stream_event(
                            span,
                            "agentos.stream.content_delta",
                            sequence=event.index,
                            char_count=len(event.text),
                            text=event.text,
                        )
                    elif isinstance(event, ProviderThinkingDelta):
                        thinking_delta_count += 1
                        thinking_char_count += len(event.text)
                        self._record_stream_event(
                            span,
                            "agentos.stream.thinking_delta",
                            sequence=event.index,
                            char_count=len(event.text),
                            text=event.text,
                        )
                    elif isinstance(event, ProviderToolCallDelta):
                        tool_delta_count += 1
                        self._record_stream_event(
                            span,
                            "agentos.stream.tool_call_delta",
                            sequence=event.index,
                            char_count=len(event.arguments_delta or ""),
                            text=event.arguments_delta or "",
                        )
                    elif isinstance(event, ProviderStreamCompleted):
                        response_snapshot = build_provider_response_snapshot(
                            event.response,
                            self._capture_policy,
                        )
                        self._set_stream_response_attributes(
                            span,
                            response_snapshot,
                            event.stop_reason,
                            first_chunk_at,
                            started_at,
                            content_delta_count,
                            content_char_count,
                            thinking_delta_count,
                            thinking_char_count,
                            tool_delta_count,
                        )
                    yield event
            except Exception as error:
                span.record_exception(error)
                span.set_status("error", str(error))
                span.set_attribute("agentos.stream.partial", True)
                span.set_attribute("agentos.stream.content.char_count", content_char_count)
                raise
```

Add helpers:

```python
    def _inner_stream(
        self,
        request: ProviderRequest,
        options: ProviderStreamOptions | None,
    ) -> object:
        """返回 inner provider stream，必要时使用 complete fallback。"""

        if hasattr(self._inner, "stream"):
            yield from self._inner.stream(request, options)  # type: ignore[attr-defined]
            return
        response = self._inner.complete(request)
        yield from complete_response_to_stream_events(
            request_id="provider_1",
            response=response,
            options=options,
        )

    def _record_stream_event(
        self,
        span: object,
        name: str,
        *,
        sequence: int,
        char_count: int,
        text: str,
    ) -> None:
        """按 capture policy 记录低容量 stream span event。"""

        if not self._capture_policy.capture_stream_deltas:
            return
        attributes: dict[str, object] = {
            "sequence": sequence,
            "char_count": char_count,
        }
        if self._capture_policy.capture_stream_delta_text:
            attributes["text"] = text[: self._capture_policy.max_string_length]
        span.add_event(name, attributes)

    def _set_stream_response_attributes(
        self,
        span: object,
        snapshot: ProviderResponseSnapshot,
        stop_reason: str | None,
        first_chunk_at: float | None,
        started_at: float,
        content_delta_count: int,
        content_char_count: int,
        thinking_delta_count: int,
        thinking_char_count: int,
        tool_delta_count: int,
    ) -> None:
        """stream terminal event 后写 response attributes。"""

        provider_name = snapshot.provider_name or "unknown"
        model = snapshot.model or "unknown"
        span.set_attributes(
            {
                GEN_AI_PROVIDER_NAME: provider_name,
                GEN_AI_REQUEST_MODEL: model,
                GEN_AI_RESPONSE_MODEL: model,
                LANGFUSE_OBSERVATION_MODEL_NAME: model,
                GEN_AI_RESPONSE_FINISH_REASONS: [] if stop_reason is None else [stop_reason],
                "agentos.stream.content.delta_count": content_delta_count,
                "agentos.stream.content.char_count": content_char_count,
                "agentos.stream.thinking.delta_count": thinking_delta_count,
                "agentos.stream.thinking.char_count": thinking_char_count,
                "agentos.stream.tool_call.delta_count": tool_delta_count,
            },
        )
        if first_chunk_at is not None:
            span.set_attribute(
                "gen_ai.response.time_to_first_chunk",
                first_chunk_at - started_at,
            )
        if snapshot.response_id is not None:
            span.set_attribute(GEN_AI_RESPONSE_ID, snapshot.response_id)
        if snapshot.usage is not None:
            self._set_usage_attributes(span, snapshot.usage)
        span.set_attribute(
            LANGFUSE_OBSERVATION_OUTPUT,
            json_attribute(
                self._provider_output_payload(snapshot),
                policy=self._capture_policy,
            ),
        )
```

- [ ] **Step 5: Run streaming observability tests**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/observability/test_streaming_provider_span.py tests/observability/test_instrumented_provider.py tests/observability/test_query_loop_instrumentation.py -q
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit streaming observability instrumentation**

```bash
git add src/agentos/observability/conventions.py src/agentos/observability/instrumented.py src/agentos/observability/tracer.py tests/observability/test_streaming_provider_span.py
git commit -m "feat: trace provider stream spans"
```

---

### Task 8: Small Agent Streaming CLI

**Files:**
- Modify: `src/agentos/examples/small_openai_agent.py`
- Test: `tests/examples/test_small_openai_agent.py`

- [ ] **Step 1: Write failing small agent CLI streaming tests**

Append to `tests/examples/test_small_openai_agent.py`:

```python
def test_main_accepts_stream_flag(monkeypatch, capsys) -> None:
    provider = FakeProvider([ProviderResponse(content="ok")])
    monkeypatch.setattr(
        "agentos.examples.small_openai_agent.provider_from_env",
        lambda: provider,
    )

    exit_code = main(["--stream", "hello"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "ok" in output


def test_main_accepts_stream_json_output(monkeypatch, capsys) -> None:
    provider = FakeProvider([ProviderResponse(content="ok")])
    monkeypatch.setattr(
        "agentos.examples.small_openai_agent.provider_from_env",
        lambda: provider,
    )

    exit_code = main(["--stream", "--output-format", "stream-json", "hello"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert '"type":"content_delta"' in output
    assert '"type":"done"' in output


def test_main_accepts_sse_output(monkeypatch, capsys) -> None:
    provider = FakeProvider([ProviderResponse(content="ok")])
    monkeypatch.setattr(
        "agentos.examples.small_openai_agent.provider_from_env",
        lambda: provider,
    )

    exit_code = main(["--stream", "--output-format", "sse", "hello"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "event: content_delta" in output
    assert "event: done" in output
```

- [ ] **Step 2: Run small agent tests and verify failure**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/examples/test_small_openai_agent.py -q
```

Expected: failure because CLI flags are not implemented.

- [ ] **Step 3: Implement CLI flags and stream output**

Modify parser in `src/agentos/examples/small_openai_agent.py`:

```python
    parser.add_argument(
        "--stream",
        action="store_true",
        help="Stream typed output events.",
    )
    parser.add_argument(
        "--show-thinking",
        action="store_true",
        help="Show provider thinking/reasoning deltas when available.",
    )
    parser.add_argument(
        "--output-format",
        choices=["text", "stream-json", "sse"],
        default="text",
        help="Streaming output format.",
    )
```

Use serializers:

```python
from agentos.runtime import RunOptions, event_to_json, event_to_sse
```

In `main()` after loop creation:

```python
    if args.stream:
        options = RunOptions(
            thinking=args.show_thinking,
            show_thinking=args.show_thinking,
        )
        for event in loop.run_turn_stream(args.prompt, options):
            if args.output_format == "text":
                if type(event).__name__ == "AssistantContentDelta":
                    print(event.text, end="", flush=True)
                elif type(event).__name__ == "TurnStreamCompleted":
                    print()
            elif args.output_format == "stream-json":
                payload = event_to_json(event, show_thinking=args.show_thinking)
                if payload is not None:
                    print(payload)
            elif args.output_format == "sse":
                chunk = event_to_sse(event, show_thinking=args.show_thinking)
                if chunk is not None:
                    print(chunk, end="")
        return 0
```

Keep the existing non-streaming path unchanged.

- [ ] **Step 4: Run small agent tests**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/examples/test_small_openai_agent.py tests/scripts/test_langfuse_otel_smoke_test.py tests/scripts/test_langfuse_smoke_test.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit small agent streaming CLI**

```bash
git add src/agentos/examples/small_openai_agent.py tests/examples/test_small_openai_agent.py
git commit -m "feat: stream small openai agent output"
```

---

### Task 9: Full Verification And Drift Checks

**Files:**
- Verify only

- [ ] **Step 1: Run full dev test suite**

Run:

```bash
uv run --python 3.11 --extra dev pytest -q
```

Expected: all tests pass.

- [ ] **Step 2: Run optional OTel tests**

Run:

```bash
uv run --python 3.11 --extra observability --extra dev pytest tests/observability/test_otel_propagation.py tests/observability/test_otel_config.py tests/observability/test_streaming_provider_span.py -q
```

Expected: all selected tests pass.

- [ ] **Step 3: Compile source, tests, and scripts**

Run:

```bash
uv run --python 3.11 --extra dev python -m compileall -q src tests scripts
```

Expected: command exits 0.

- [ ] **Step 4: Check whitespace**

Run:

```bash
git diff --check
```

Expected: no output and exit 0.

- [ ] **Step 5: Check observability boundary drift**

Run:

```bash
rg -n "from opentelemetry|import opentelemetry|langfuse" src/agentos/runtime src/agentos/providers src/agentos/capabilities src/agentos/context
```

Expected: no output. Exit code 1 from `rg` is acceptable when no matches exist.

- [ ] **Step 6: Check default prompt metadata drift**

Run:

```bash
rg -n "session_id|turn_id|message_id|trace_id|span_id|tool_call_id|schema_id|projection_id|compression_id|source|relevance|thinking|stream" tests/context/goldens src/agentos/context/renderer.py
```

Expected: no output. Exit code 1 from `rg` is acceptable when no matches exist.

- [ ] **Step 7: Commit verification-only updates if files changed**

If no files changed, do not create a commit. If documentation or generated fixtures changed as part of verification, run:

```bash
git add <changed-files>
git commit -m "test: verify streaming thinking otel"
```

Expected: final working tree is clean.

---

## Plan Self-Review

- Spec coverage: streaming API, thinking separation, SSE/callback adapters, provider stream completion, turn completion, OTel span lifecycle, Langfuse input/output mapping, capture policy, failure/cancel behavior, and prompt metadata drift all map to tasks above.
- Scope: this plan intentionally keeps HTTP server/FastAPI, async API, subagent streaming, provider replay of reasoning details, and token-level cost estimation outside this implementation. The spec marks those as out of scope.
- Type consistency: provider-level events use `Provider*`; turn-level events use `Assistant*`, `ToolStream*`, and `TurnStream*`; per-turn options use `RunOptions`; provider stream options use `ProviderStreamOptions`.
- Boundary consistency: runtime and provider modules never import OpenTelemetry or Langfuse. OTel adaptation remains in `observability/`.
