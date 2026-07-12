"""Phase 2 消息类型迁移桥。"""

from typing import TypeAlias, cast

from agentos._frozen_json import thaw_json
from agentos.messages.types import Message, StoredMessage
from agentos.providers.base import ProviderToolCall
from agentos.providers.messages import (
    AssistantMessage,
    ProviderMessage,
    ToolResultMessage,
    UserMessage,
)

# Phase 2 Provider projection bridge; remove in Task 7.
_LegacyProviderMessage: TypeAlias = ProviderMessage


def materialize_provider_messages(
    messages: list[StoredMessage],
) -> list[_LegacyProviderMessage]:
    """临时把业务消息投影为旧 Provider 消息。"""

    return [_to_provider_message(message) for message in messages]


def _to_provider_message(message: StoredMessage) -> _LegacyProviderMessage:
    """临时执行 StoredMessage 到旧 Provider 消息的机械映射。"""

    if message.role == "user":
        return UserMessage(content=message.content)
    if message.role == "assistant":
        return AssistantMessage(
            content=message.content,
            tool_calls=tuple(
                ProviderToolCall(
                    id=tool_call.id,
                    name=tool_call.name,
                    arguments=cast(
                        dict[str, object],
                        thaw_json(tool_call.arguments),
                    ),
                )
                for tool_call in message.tool_calls
            ),
        )
    if message.role == "tool":
        if message.tool_call_id is None:
            raise ValueError("tool message requires tool_call_id")
        return ToolResultMessage(
            tool_call_id=message.tool_call_id,
            content=message.content,
        )
    raise ValueError(f"unsupported message role: {message.role}")

# Phase 2 migration bridge; remove in Task 13.
__all__ = ["Message", "StoredMessage"]
