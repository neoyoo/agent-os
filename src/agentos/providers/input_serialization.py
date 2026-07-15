"""ProviderInputItem 的内部 JSON-safe 投影。"""

from agentos._json_values import thaw_json
from agentos.providers.content import (
    FilePart,
    ImagePart,
    ProviderBinaryPayload,
    ProviderContentPart,
    TextPart,
)
from agentos.providers.input import ProviderInputItem


def provider_input_to_dict(item: ProviderInputItem) -> dict[str, object]:
    """把逻辑 Provider 输入转换为不含附件原文的观测形态。"""

    parts = [_content_part_to_dict(part) for part in item.content]
    result: dict[str, object] = {
        "role": item.role,
        "content": parts[0]["text"]
        if len(parts) == 1 and parts[0]["type"] == "text"
        else parts,
    }
    if item.tool_calls:
        result["tool_calls"] = [
            {
                "id": tool_call.id,
                "name": tool_call.name,
                "arguments": thaw_json(tool_call.arguments),
            }
            for tool_call in item.tool_calls
        ]
    if item.tool_call_id is not None:
        result["tool_call_id"] = item.tool_call_id
    return result


def _content_part_to_dict(part: ProviderContentPart) -> dict[str, object]:
    if isinstance(part, TextPart):
        return {"type": "text", "text": part.text}
    if isinstance(part, ImagePart):
        return {
            "type": "image",
            "payload": _payload_metadata(part.payload),
            "detail": part.detail,
        }
    if isinstance(part, FilePart):
        return {
            "type": "file",
            "payload": _payload_metadata(part.payload),
        }
    raise TypeError(f"unsupported provider content part: {type(part).__name__}")


def _payload_metadata(payload: ProviderBinaryPayload) -> dict[str, object]:
    return {
        "handle": payload.handle,
        "filename": payload.filename,
        "media_type": payload.media_type,
    }
