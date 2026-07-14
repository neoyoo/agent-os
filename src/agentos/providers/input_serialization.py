"""ProviderInputItem 的内部 JSON-safe 投影。"""

from agentos.providers.json_values import thaw_json
from agentos.providers.input import (
    FilePart,
    ImagePart,
    ProviderContentPart,
    ProviderInputItem,
    TextPart,
)


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
    return {
        "handle": str(getattr(attachment, "handle", "")),
        "filename": getattr(attachment, "filename", None),
        "mime_type": str(getattr(attachment, "mime_type", "")),
        "size_bytes": int(getattr(attachment, "size_bytes", 0)),
    }
