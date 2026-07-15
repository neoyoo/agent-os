from agentos.attachments import Attachment, BytesSource
from agentos.providers.content import ProviderBinaryPayload


def binary_payload(
    *,
    handle: str = "art_1",
    media_type: str = "image/png",
    data: bytes = b"image-bytes",
    filename: str | None = "diagram.png",
) -> ProviderBinaryPayload:
    return ProviderBinaryPayload(
        handle=handle,
        media_type=media_type,
        data=data,
        filename=filename,
    )


def payload_from_attachment(attachment: Attachment) -> ProviderBinaryPayload:
    assert isinstance(attachment.source, BytesSource)
    return binary_payload(
        handle=attachment.handle,
        media_type=attachment.mime_type,
        data=attachment.source.data,
        filename=attachment.filename,
    )
