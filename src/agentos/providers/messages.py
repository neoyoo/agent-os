from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias, cast

from agentos._frozen_json import FrozenJsonObject, freeze_json, thaw_json
from agentos.providers.input import (
    FilePart,
    ImagePart,
    ProviderContentPart,
    ProviderToolCall,
    TextPart,
)

ProviderMessageContent: TypeAlias = str | tuple[ProviderContentPart, ...]


@dataclass(frozen=True, slots=True)
class UserMessage:
    """用户消息。"""

    content: ProviderMessageContent

    def __post_init__(self) -> None:
        """复制多模态片段，避免外部可变集合污染请求快照。"""

        if isinstance(self.content, str):
            return
        content = tuple(self.content)
        if any(type(part) not in (TextPart, ImagePart, FilePart) for part in content):
            raise TypeError("provider message content contains an unsupported part")
        object.__setattr__(self, "content", content)

    def __getitem__(self, key: str) -> object:
        """迁移期只读 dict-style 访问。"""

        return provider_message_to_dict(self)[key]

    def get(self, key: str, default: object = None) -> object:
        """迁移期只读 dict-style 访问。"""

        return provider_message_to_dict(self).get(key, default)

    def __eq__(self, other: object) -> bool:
        """允许旧 dict 断言与强类型消息比较。"""

        return _provider_message_equals(self, other)


@dataclass(frozen=True, slots=True)
class AssistantMessage:
    """助手消息，可携带 provider tool calls。"""

    content: str = ""
    tool_calls: tuple[ProviderToolCall, ...] = ()
    thinking_content: str | None = None

    def __post_init__(self) -> None:
        """复制工具调用集合，避免外部可变别名进入请求快照。"""

        tool_calls = tuple(self.tool_calls)
        if any(type(call) is not ProviderToolCall for call in tool_calls):
            raise TypeError("assistant tool_calls require ProviderToolCall")
        object.__setattr__(self, "tool_calls", tool_calls)

    def __getitem__(self, key: str) -> object:
        """迁移期只读 dict-style 访问。"""

        return provider_message_to_dict(self)[key]

    def get(self, key: str, default: object = None) -> object:
        """迁移期只读 dict-style 访问。"""

        return provider_message_to_dict(self).get(key, default)

    def __eq__(self, other: object) -> bool:
        """允许旧 dict 断言与强类型消息比较。"""

        return _provider_message_equals(self, other)


@dataclass(frozen=True, slots=True)
class ToolResultMessage:
    """工具执行结果消息。"""

    tool_call_id: str
    content: str

    def __getitem__(self, key: str) -> object:
        """迁移期只读 dict-style 访问。"""

        return provider_message_to_dict(self)[key]

    def get(self, key: str, default: object = None) -> object:
        """迁移期只读 dict-style 访问。"""

        return provider_message_to_dict(self).get(key, default)

    def __eq__(self, other: object) -> bool:
        """允许旧 dict 断言与强类型消息比较。"""

        return _provider_message_equals(self, other)


ProviderMessage: TypeAlias = UserMessage | AssistantMessage | ToolResultMessage


@dataclass(frozen=True, slots=True, init=False)
class ProviderFunctionSpec:
    """OpenAI-style function tool schema 的 function 部分。"""

    name: str
    description: str
    parameters: FrozenJsonObject

    def __init__(
        self,
        name: str,
        description: str,
        parameters: dict[str, object] | FrozenJsonObject | None = None,
    ) -> None:
        frozen = freeze_json({} if parameters is None else parameters)
        if not isinstance(frozen, FrozenJsonObject):
            raise TypeError("provider function parameters must be a JSON object")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "parameters", frozen)


@dataclass(frozen=True, slots=True)
class ProviderToolSpec:
    """Provider 工具 schema，保留 canonical OpenAI-style function 形态。"""

    function: ProviderFunctionSpec
    type: Literal["function"] = "function"

    def __getitem__(self, key: str) -> object:
        """迁移期只读 dict-style 访问。"""

        return provider_tool_spec_to_dict(self)[key]

    def get(self, key: str, default: object = None) -> object:
        """迁移期只读 dict-style 访问。"""

        return provider_tool_spec_to_dict(self).get(key, default)

    def __eq__(self, other: object) -> bool:
        """允许旧 dict 断言与强类型 tool spec 比较。"""

        if isinstance(other, ProviderToolSpec):
            return (
                self.type == other.type
                and self.function == other.function
            )
        if isinstance(other, dict):
            return provider_tool_spec_to_dict(self) == other
        return False


def provider_message_to_dict(message: ProviderMessage) -> dict[str, object]:
    """把强类型 provider message 转成 JSON-safe dict。"""

    if isinstance(message, UserMessage):
        return {"role": "user", "content": _content_to_json_safe(message.content)}
    if isinstance(message, AssistantMessage):
        result: dict[str, object] = {
            "role": "assistant",
            "content": message.content,
        }
        if message.tool_calls:
            result["tool_calls"] = [
                _tool_call_to_dict(tool_call) for tool_call in message.tool_calls
            ]
        if message.thinking_content is not None:
            result["thinking_content"] = message.thinking_content
        return result
    if isinstance(message, ToolResultMessage):
        return {
            "role": "tool",
            "content": message.content,
            "tool_call_id": message.tool_call_id,
        }
    raise TypeError(f"unsupported provider message: {type(message).__name__}")


def _content_to_json_safe(content: ProviderMessageContent) -> object:
    """把 provider content 转成 JSON-safe 且附件 source 已脱敏的形态。"""

    if isinstance(content, str):
        return content
    return [_content_part_to_json_safe(part) for part in content]


def _content_part_to_json_safe(part: ProviderContentPart) -> dict[str, object]:
    """把单个 content part 转为可观测安全摘要。"""

    if isinstance(part, TextPart):
        return {"type": "text", "text": part.text}
    if isinstance(part, ImagePart):
        return {
            "type": "image",
            "attachment": _attachment_metadata(part.attachment),
            "detail": part.detail,
        }
    if isinstance(part, FilePart):
        return {
            "type": "file",
            "attachment": _attachment_metadata(part.attachment),
        }
    raise TypeError(f"unsupported provider content part: {type(part).__name__}")


def _attachment_metadata(attachment: object) -> dict[str, object]:
    """提取 LLM/observability 可见的附件元数据，不暴露 source。"""

    return {
        "handle": str(getattr(attachment, "handle", "")),
        "filename": getattr(attachment, "filename", None),
        "mime_type": str(getattr(attachment, "mime_type", "")),
        "size_bytes": int(getattr(attachment, "size_bytes", 0)),
    }


def provider_message_from_dict(value: object) -> ProviderMessage:
    """把迁移期 dict message 标准化为强类型 provider message。"""

    if isinstance(value, (UserMessage, AssistantMessage, ToolResultMessage)):
        return value
    if not isinstance(value, dict):
        raise ValueError("provider message must be an object")
    role = value.get("role")
    content = str(value.get("content") or "")
    if role == "user":
        return UserMessage(content=content)
    if role == "assistant":
        return AssistantMessage(
            content=content,
            tool_calls=tuple(
                _tool_call_from_dict(tool_call)
                for tool_call in value.get("tool_calls", []) or []
            ),
            thinking_content=(
                None
                if value.get("thinking_content") is None
                else str(value.get("thinking_content"))
            ),
        )
    if role == "tool":
        tool_call_id = value.get("tool_call_id")
        if not isinstance(tool_call_id, str) or not tool_call_id:
            raise ValueError("tool provider message requires tool_call_id")
        return ToolResultMessage(tool_call_id=tool_call_id, content=content)
    raise ValueError(f"unsupported provider message role: {role!r}")


def provider_tool_spec_to_dict(spec: ProviderToolSpec) -> dict[str, object]:
    """把强类型 provider tool schema 转成 canonical dict。"""

    if not isinstance(spec, ProviderToolSpec):
        spec = provider_tool_spec_from_dict(spec)
    return {
        "type": spec.type,
        "function": {
            "name": spec.function.name,
            "description": spec.function.description,
            "parameters": thaw_json(spec.function.parameters),
        },
    }


def provider_tool_spec_from_dict(value: object) -> ProviderToolSpec:
    """把 canonical function-tool dict 标准化为 ProviderToolSpec。"""

    if isinstance(value, ProviderToolSpec):
        return value
    if not isinstance(value, dict):
        raise ValueError("provider tool spec must be an object")
    if value.get("type") != "function":
        raise ValueError("provider tool spec type must be 'function'")
    function = value.get("function")
    if not isinstance(function, dict):
        raise ValueError("provider tool spec requires function object")
    name = function.get("name")
    description = function.get("description")
    parameters = function.get("parameters", {})
    if not isinstance(name, str) or not name:
        raise ValueError("provider function spec requires name")
    if not isinstance(description, str):
        raise ValueError("provider function spec requires description")
    if not isinstance(parameters, dict):
        raise ValueError("provider function parameters must be an object")
    return ProviderToolSpec(
        function=ProviderFunctionSpec(
            name=name,
            description=description,
            parameters=parameters,
        ),
    )


def _provider_message_equals(message: ProviderMessage, other: object) -> bool:
    if isinstance(other, (UserMessage, AssistantMessage, ToolResultMessage)):
        return provider_message_to_dict(message) == provider_message_to_dict(other)
    if isinstance(other, dict):
        return provider_message_to_dict(message) == other
    return False


def _tool_call_to_dict(tool_call: ProviderToolCall) -> dict[str, object]:
    result: dict[str, object] = {
        "id": tool_call.id,
        "name": tool_call.name,
    }
    if tool_call.arguments:
        result["arguments"] = thaw_json(tool_call.arguments)
    return result


def _tool_call_from_dict(value: object) -> ProviderToolCall:
    if not isinstance(value, dict):
        raise ValueError("provider tool call must be an object")
    raw_id = value.get("id")
    raw_name = value.get("name")
    if not isinstance(raw_id, str) or not raw_id:
        raise ValueError("provider tool call requires id")
    if not isinstance(raw_name, str) or not raw_name:
        raise ValueError("provider tool call requires name")
    raw_arguments = value.get("arguments", {})
    if not isinstance(raw_arguments, dict):
        raise ValueError("provider tool call arguments must be an object")
    return ProviderToolCall(
        id=raw_id,
        name=raw_name,
        arguments=cast(dict[str, object], raw_arguments),
    )
