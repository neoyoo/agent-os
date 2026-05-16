from __future__ import annotations

from collections.abc import AsyncIterator
from copy import deepcopy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from agentos.providers.messages import (
    ProviderMessage,
    ProviderToolSpec,
    provider_message_from_dict,
    provider_tool_spec_from_dict,
)
if TYPE_CHECKING:
    from agentos.providers.stream import ProviderStreamEvent, ProviderStreamOptions


@dataclass(frozen=True, slots=True)
class ProviderToolCall:
    """provider 返回的标准化工具调用。"""

    id: str
    name: str
    arguments: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """复制参数，避免 frozen dataclass 被外部可变引用污染。"""

        object.__setattr__(self, "arguments", deepcopy(self.arguments))


@dataclass(frozen=True, slots=True)
class ProviderRequest:
    """发送给 provider 的标准化请求。"""

    system: str
    messages: list[ProviderMessage]
    tools: list[ProviderToolSpec] = field(default_factory=list)

    def __post_init__(self) -> None:
        """标准化迁移期传入的 dict message / tool schema。"""

        object.__setattr__(
            self,
            "messages",
            [provider_message_from_dict(message) for message in self.messages],
        )
        object.__setattr__(
            self,
            "tools",
            [provider_tool_spec_from_dict(tool) for tool in self.tools],
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
class ProviderResponse:
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

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        """根据标准请求返回 assistant 响应。"""


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
