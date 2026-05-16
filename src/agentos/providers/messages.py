from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, TypeAlias

if TYPE_CHECKING:
    from agentos.providers.base import ProviderToolCall


@dataclass(frozen=True, slots=True)
class UserMessage:
    """用户消息。"""

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


@dataclass(frozen=True, slots=True)
class AssistantMessage:
    """助手消息，可携带 provider tool calls。"""

    content: str = ""
    tool_calls: tuple[ProviderToolCall, ...] = ()
    thinking_content: str | None = None

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


@dataclass(frozen=True, slots=True)
class ProviderFunctionSpec:
    """OpenAI-style function tool schema 的 function 部分。"""

    name: str
    description: str
    parameters: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """复制可变 schema，避免调用方后续修改污染 provider request。"""

        object.__setattr__(self, "parameters", deepcopy(self.parameters))


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
        return {"role": "user", "content": message.content}
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
            "parameters": deepcopy(spec.function.parameters),
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
            parameters=deepcopy(parameters),
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
        result["arguments"] = deepcopy(tool_call.arguments)
    return result


def _tool_call_from_dict(value: object) -> ProviderToolCall:
    if not isinstance(value, dict):
        raise ValueError("provider tool call must be an object")
    from agentos.providers.base import ProviderToolCall

    raw_arguments = value.get("arguments", {})
    if not isinstance(raw_arguments, dict):
        raise ValueError("provider tool call arguments must be an object")
    return ProviderToolCall(
        id=str(value["id"]),
        name=str(value["name"]),
        arguments=deepcopy(raw_arguments),
    )
