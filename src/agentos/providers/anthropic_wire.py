"""Anthropic Messages API 请求与 strict-role 映射。"""

from __future__ import annotations

import base64
from copy import deepcopy

from agentos._json_values import thaw_json
from agentos.providers.content import (
    FilePart,
    ImagePart,
    ProviderBinaryPayload,
    ProviderContentPart,
    TextPart,
)
from agentos.providers.input import ProviderInputItem, ProviderToolCall
from agentos.providers.tool_specs import ProviderToolSpec


def build_anthropic_messages(
    messages: tuple[ProviderInputItem, ...],
) -> list[dict[str, object]]:
    """转换逻辑输入，并合并 Anthropic 不允许的相邻 user 角色。"""

    return merge_adjacent_user_messages(
        [anthropic_message(message) for message in messages],
    )


def anthropic_message(message: ProviderInputItem) -> dict[str, object]:
    """把逻辑 Provider 输入转换为 Anthropic message。"""

    if message.role == "user":
        content: object = message.content
        if len(message.content) == 1 and isinstance(message.content[0], TextPart):
            content = message.content[0].text
        return {"role": "user", "content": _user_content(content)}
    if len(message.content) != 1 or not isinstance(message.content[0], TextPart):
        raise ValueError(
            f"{message.role} provider input content requires exactly one TextPart",
        )
    if message.role == "assistant":
        content: list[dict[str, object]] = []
        if message.content[0].text:
            content.append({"type": "text", "text": message.content[0].text})
        content.extend(_tool_use(tool_call) for tool_call in message.tool_calls)
        return {
            "role": "assistant",
            "content": content if content else message.content[0].text,
        }
    if message.role == "tool" and message.tool_call_id is not None:
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": message.tool_call_id,
                    "content": message.content[0].text,
                },
            ],
        }
    raise ValueError(
        "active messages must not include system role; use ProviderRequest.system",
    )


def merge_adjacent_user_messages(
    messages: list[dict[str, object]],
) -> list[dict[str, object]]:
    """合并所有相邻 user 消息，同时保留 block 顺序和输入不变性。"""

    merged: list[dict[str, object]] = []
    for message in messages:
        current = deepcopy(message)
        if current.get("role") != "user" or not merged:
            merged.append(current)
            continue
        previous = merged[-1]
        if previous.get("role") != "user":
            merged.append(current)
            continue
        previous["content"] = [
            *_content_blocks(previous.get("content")),
            *_content_blocks(current.get("content")),
        ]
    return merged


def anthropic_tools(
    tools: tuple[ProviderToolSpec, ...],
) -> list[dict[str, object]]:
    """把 canonical function tools 转换为 Anthropic input_schema。"""

    return [
        {
            "name": tool.function.name,
            "description": tool.function.description,
            "input_schema": thaw_json(tool.function.parameters),
        }
        for tool in tools
    ]


def _tool_use(tool_call: ProviderToolCall) -> dict[str, object]:
    return {
        "type": "tool_use",
        "id": tool_call.id,
        "name": tool_call.name,
        "input": thaw_json(tool_call.arguments),
    }


def _user_content(content: object) -> object:
    if isinstance(content, str):
        return content
    if isinstance(content, tuple):
        return [_content_part(part) for part in content]
    return content


def _content_part(part: ProviderContentPart) -> dict[str, object]:
    if isinstance(part, TextPart):
        return {"type": "text", "text": part.text}
    if isinstance(part, ImagePart):
        return {
            "type": "image",
            "source": _source_block(part.payload, require_image=True),
        }
    if isinstance(part, FilePart):
        if part.payload.media_type != "application/pdf":
            raise ValueError("Anthropic file attachments only support PDF in v1")
        return {
            "type": "document",
            "source": _source_block(part.payload),
        }
    raise ValueError(f"unsupported Anthropic content part: {type(part).__name__}")


def _source_block(
    payload: ProviderBinaryPayload,
    *,
    require_image: bool = False,
) -> dict[str, object]:
    if require_image and not payload.media_type.startswith("image/"):
        raise ValueError("Anthropic image parts require image MIME")
    return {
        "type": "base64",
        "media_type": payload.media_type,
        "data": base64.b64encode(payload.data).decode("ascii"),
    }


def _content_blocks(content: object) -> list[dict[str, object]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        if not all(isinstance(block, dict) for block in content):
            raise ValueError("Anthropic user content blocks must be objects")
        return deepcopy(content)
    raise ValueError("Anthropic user content must be text or content blocks")
