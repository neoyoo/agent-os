import base64

from agentos.attachments.types import (
    BytesSource,
    InlineBase64Source,
    LocalFileSource,
    ProviderFileSource,
    UrlSource,
)
from agentos.providers.messages import (
    FilePart,
    ImagePart,
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
                "url": image_url(part.attachment),
                "detail": part.detail,
            },
        }
    if isinstance(part, FilePart):
        raise ValueError(
            "OpenAI-compatible chat completions does not support file attachments",
        )
    raise ValueError(f"unsupported OpenAI content part: {type(part).__name__}")


def image_url(attachment: object) -> str:
    """把图片附件 source 转为 OpenAI image_url 字符串。"""

    mime_type = str(getattr(attachment, "mime_type", ""))
    if not mime_type.startswith("image/"):
        raise ValueError("OpenAI-compatible image parts require image MIME")
    source = getattr(attachment, "source", None)
    if isinstance(source, UrlSource):
        return source.url
    if isinstance(source, InlineBase64Source):
        return f"data:{source.mime_type};base64,{source.data}"
    if isinstance(source, BytesSource):
        data = base64.b64encode(source.data).decode("ascii")
        return f"data:{mime_type};base64,{data}"
    if isinstance(source, LocalFileSource):
        data = base64.b64encode(source.path.read_bytes()).decode("ascii")
        return f"data:{mime_type};base64,{data}"
    if isinstance(source, ProviderFileSource):
        raise ValueError(
            "OpenAI-compatible chat completions does not support provider file attachments",
        )
    raise ValueError("unsupported OpenAI-compatible image attachment source")
