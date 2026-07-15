"""OpenAI Chat Completions 共享 wire 映射。"""

from __future__ import annotations

import base64
import json

from agentos._json_values import thaw_json
from agentos.providers.base import ProviderRequest
from agentos.providers.content import (
    FilePart,
    ImagePart,
    ProviderBinaryPayload,
    ProviderContentPart,
    TextPart,
)
from agentos.providers.input import ProviderInputItem, ProviderToolCall
from agentos.providers.tool_specs import provider_tool_spec_to_dict


def build_openai_chat_payload(
    *,
    model: str,
    request: ProviderRequest,
    include_parallel_tool_calls: bool,
    invalid_message_error: type[Exception] = ValueError,
) -> dict[str, object]:
    """构造标准 OpenAI Chat Completions payload。"""

    payload: dict[str, object] = {
        "model": model,
        "messages": [
            {"role": "system", "content": request.system},
            *[
                openai_chat_message(
                    message,
                    invalid_message_error=invalid_message_error,
                )
                for message in request.messages
            ],
        ],
    }
    if request.tools:
        payload["tools"] = [
            provider_tool_spec_to_dict(tool) for tool in request.tools
        ]
        if include_parallel_tool_calls and request.parallel_tool_calls is not None:
            payload["parallel_tool_calls"] = request.parallel_tool_calls
    return payload


def openai_chat_message(
    message: ProviderInputItem,
    *,
    invalid_message_error: type[Exception] = ValueError,
) -> dict[str, object]:
    """把逻辑 Provider 输入转换为 Chat Completions message。"""

    if message.role == "user":
        content: object = message.content
        if len(message.content) == 1 and isinstance(message.content[0], TextPart):
            content = message.content[0].text
        return {"role": "user", "content": openai_chat_user_content(content)}
    if len(message.content) != 1 or not isinstance(message.content[0], TextPart):
        raise ValueError(
            f"{message.role} provider input content requires exactly one TextPart",
        )
    if message.role == "assistant":
        result: dict[str, object] = {
            "role": "assistant",
            "content": message.content[0].text,
        }
        if message.tool_calls:
            result["content"] = message.content[0].text or None
            result["tool_calls"] = [
                openai_chat_tool_call(tool_call)
                for tool_call in message.tool_calls
            ]
        return result
    if message.role == "tool" and message.tool_call_id is not None:
        return {
            "role": "tool",
            "tool_call_id": message.tool_call_id,
            "content": message.content[0].text,
        }
    raise invalid_message_error(
        "active messages must not include system role; use ProviderRequest.system",
    )


def openai_chat_tool_call(tool_call: ProviderToolCall) -> dict[str, object]:
    """把逻辑 Tool Call 转换为 Chat Completions function call。"""

    return {
        "id": tool_call.id,
        "type": "function",
        "function": {
            "name": tool_call.name,
            "arguments": json.dumps(
                thaw_json(tool_call.arguments),
                ensure_ascii=False,
            ),
        },
    }


def openai_chat_user_content(content: object) -> object:
    """把 canonical content parts 转为 Chat Completions content。"""

    if isinstance(content, str):
        return content
    if isinstance(content, tuple):
        return [openai_chat_content_part(part) for part in content]
    return content


def openai_chat_content_part(part: ProviderContentPart) -> dict[str, object]:
    """把单个 canonical part 转为 Chat Completions content part。"""

    if isinstance(part, TextPart):
        return {"type": "text", "text": part.text}
    if isinstance(part, ImagePart):
        return {
            "type": "image_url",
            "image_url": {
                "url": image_url(part.payload),
                "detail": part.detail,
            },
        }
    if isinstance(part, FilePart):
        raise ValueError(
            "OpenAI-compatible chat completions does not support file attachments",
        )
    raise ValueError(f"unsupported OpenAI content part: {type(part).__name__}")


def image_url(payload: ProviderBinaryPayload) -> str:
    """把图片载荷转换为 data URL。"""

    if not payload.media_type.startswith("image/"):
        raise ValueError("OpenAI-compatible image parts require image MIME")
    data = base64.b64encode(payload.data).decode("ascii")
    return f"data:{payload.media_type};base64,{data}"
