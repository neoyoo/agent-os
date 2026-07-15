import base64

from agentos.providers.content import (
    FilePart,
    ImagePart,
    ProviderBinaryPayload,
    ProviderContentPart,
    TextPart,
)


def openai_chat_user_content(content: object) -> object:
    """把 canonical content parts 转为 OpenAI Chat-compatible content。"""

    if isinstance(content, str):
        return content
    if isinstance(content, tuple):
        return [openai_chat_content_part(part) for part in content]
    return content


def openai_chat_content_part(part: ProviderContentPart) -> dict[str, object]:
    """把单个 canonical part 转为 OpenAI Chat-compatible part。"""

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
    """把图片载荷转为 OpenAI image_url 字符串。"""

    if not payload.media_type.startswith("image/"):
        raise ValueError("OpenAI-compatible image parts require image MIME")
    data = base64.b64encode(payload.data).decode("ascii")
    return f"data:{payload.media_type};base64,{data}"
