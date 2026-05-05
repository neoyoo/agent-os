from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol, TypeAlias

from agentos.providers.base import Provider, ProviderRequest, ProviderResponse


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


class StreamingProvider(Provider, Protocol):
    """支持 streaming 的 provider 协议。"""

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
        thinking_text = response.thinking_content
        if stream_options.max_thinking_chars is not None:
            thinking_text = thinking_text[: stream_options.max_thinking_chars]
        yield ProviderThinkingDelta(
            request_id=request_id,
            index=1,
            text=thinking_text,
        )
    if response.content:
        yield ProviderContentDelta(
            request_id=request_id,
            index=1,
            text=response.content,
        )
    yield ProviderStreamCompleted(
        request_id=request_id,
        response=response,
        stop_reason=response.stop_reason,
    )
