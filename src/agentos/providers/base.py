from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from agentos._internal_transcript import InternalTranscriptValue
from agentos.providers.input import ProviderInputItem, ProviderToolCall
from agentos.providers.tool_specs import ProviderToolSpec
if TYPE_CHECKING:
    from agentos.providers.stream import ProviderStreamEvent, ProviderStreamOptions


@dataclass(frozen=True, slots=True)
class ProviderRequest(InternalTranscriptValue):
    """发送给 provider 的标准化请求。"""

    system: str
    messages: tuple[ProviderInputItem, ...]
    tools: tuple[ProviderToolSpec, ...] = ()
    parallel_tool_calls: bool | None = None

    def __post_init__(self) -> None:
        """冻结 typed 输入并拒绝 legacy dict 边界。"""

        messages = tuple(self.messages)
        if any(type(item) is not ProviderInputItem for item in messages):
            raise TypeError("ProviderRequest messages must contain ProviderInputItem")
        tools = tuple(self.tools)
        if any(type(item) is not ProviderToolSpec for item in tools):
            raise TypeError("ProviderRequest tools must contain ProviderToolSpec")
        object.__setattr__(self, "messages", messages)
        object.__setattr__(self, "tools", tools)
        if self.parallel_tool_calls is not None and type(self.parallel_tool_calls) is not bool:
            raise TypeError("parallel_tool_calls must be bool or None")
        model_tasks = tuple(
            item
            for item in messages
            if isinstance(item, ProviderInputItem) and item.kind == "model_task"
        )
        if model_tasks and (
            len(messages) != 1
            or len(model_tasks) != 1
            or tools
            or self.parallel_tool_calls is not None
        ):
            raise ValueError(
                "model_task request requires exactly one model_task, no tools, "
                "and parallel_tool_calls=None",
            )


@dataclass(frozen=True, slots=True)
class ProviderUsage:
    """provider 返回的标准化 token/cost usage。"""

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cached_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    reasoning_output_tokens: int | None = None
    cost_usd: float | None = None


@dataclass(frozen=True, slots=True)
class ProviderResponse(InternalTranscriptValue):
    """provider 返回的标准化响应。"""

    content: str = ""
    tool_calls: tuple[ProviderToolCall, ...] = ()
    stop_reason: str | None = None
    usage: ProviderUsage | None = None
    model: str | None = None
    provider_name: str | None = None
    response_id: str | None = None
    thinking_content: str | None = None

    def __post_init__(self) -> None:
        """把迁移期 list 输入收敛为不可变 tuple。"""

        object.__setattr__(self, "tool_calls", tuple(self.tool_calls))


class Provider(Protocol):
    """provider runtime 的最小协议。"""

    timeout_seconds: float | None

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        """根据标准请求返回 assistant 响应。"""


class ProviderTimeoutError(TimeoutError):
    """Provider 调用超时。"""


class AsyncProvider(Protocol):
    """支持 asyncio 调用的可选 provider 协议。"""

    async def async_complete(self, request: ProviderRequest) -> ProviderResponse:
        """异步返回完整 provider 响应。"""

    async def async_stream(
        self,
        request: ProviderRequest,
        options: ProviderStreamOptions,
    ) -> AsyncIterator[ProviderStreamEvent]:
        """异步 stream provider events。"""
