"""OpenAI Responses API 请求与内容映射。"""

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
from agentos.providers.tool_specs import ProviderToolSpec


_FILE_EXTENSION_BY_MEDIA_TYPE = {
    "application/pdf": ".pdf",
}


def build_openai_responses_payload(
    *,
    model: str,
    request: ProviderRequest,
) -> dict[str, object]:
    """构造 OpenAI Responses API 请求参数。"""

    input_items: list[dict[str, object]] = []
    for message in request.messages:
        input_items.extend(openai_responses_items(message))
    payload: dict[str, object] = {
        "model": model,
        "instructions": request.system,
        "input": input_items,
    }
    if request.tools:
        payload["tools"] = [
            openai_responses_tool(tool) for tool in request.tools
        ]
        if request.parallel_tool_calls is not None:
            payload["parallel_tool_calls"] = request.parallel_tool_calls
    return payload


def openai_responses_items(
    message: ProviderInputItem,
) -> tuple[dict[str, object], ...]:
    """把一个逻辑输入映射为一个或多个有序 Responses Item。"""

    if message.role == "user":
        return (
            {
                "type": "message",
                "role": "user",
                "content": _user_content(message.content),
            },
        )
    if len(message.content) != 1 or not isinstance(message.content[0], TextPart):
        raise ValueError(
            f"{message.role} provider input content requires exactly one TextPart",
        )
    if message.role == "assistant":
        items: list[dict[str, object]] = []
        text = message.content[0].text
        if text or not message.tool_calls:
            items.append(
                {
                    "type": "message",
                    "role": "assistant",
                    "content": text,
                },
            )
        items.extend(_function_call(tool_call) for tool_call in message.tool_calls)
        return tuple(items)
    if message.role == "tool" and message.tool_call_id is not None:
        return (
            {
                "type": "function_call_output",
                "call_id": message.tool_call_id,
                "output": message.content[0].text,
            },
        )
    raise ValueError(
        "active messages must not include system role; use ProviderRequest.system",
    )


def openai_responses_tool(tool: ProviderToolSpec) -> dict[str, object]:
    """把 canonical function tool 映射为 Responses 扁平 schema。"""

    return {
        "type": "function",
        "name": tool.function.name,
        "description": tool.function.description,
        "parameters": thaw_json(tool.function.parameters),
    }


def _function_call(tool_call: ProviderToolCall) -> dict[str, object]:
    return {
        "type": "function_call",
        "call_id": tool_call.id,
        "name": tool_call.name,
        "arguments": json.dumps(
            thaw_json(tool_call.arguments),
            ensure_ascii=False,
        ),
    }


def _user_content(content: tuple[ProviderContentPart, ...]) -> object:
    if len(content) == 1 and isinstance(content[0], TextPart):
        return content[0].text
    return [_content_part(part) for part in content]


def _content_part(part: ProviderContentPart) -> dict[str, object]:
    if isinstance(part, TextPart):
        return {"type": "input_text", "text": part.text}
    if isinstance(part, ImagePart):
        if not part.payload.media_type.startswith("image/"):
            raise ValueError("OpenAI Responses image parts require image MIME")
        return {
            "type": "input_image",
            "image_url": _data_url(part.payload),
            "detail": part.detail,
        }
    if isinstance(part, FilePart):
        return {
            "type": "input_file",
            "filename": _provider_filename(part.payload),
            "file_data": _data_url(part.payload),
        }
    raise ValueError(f"unsupported OpenAI content part: {type(part).__name__}")


def _data_url(payload: ProviderBinaryPayload) -> str:
    data = base64.b64encode(payload.data).decode("ascii")
    return f"data:{payload.media_type};base64,{data}"


def _provider_filename(payload: ProviderBinaryPayload) -> str:
    if payload.filename:
        return payload.filename
    extension = _FILE_EXTENSION_BY_MEDIA_TYPE.get(payload.media_type)
    if extension is None:
        raise ValueError("OpenAI Responses file parts require filename")
    return f"{payload.handle}{extension}"
