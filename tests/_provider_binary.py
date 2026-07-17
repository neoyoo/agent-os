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


def payload_from_attachment(attachment: object) -> ProviderBinaryPayload:
    source = getattr(attachment, "source")
    return binary_payload(
        handle=str(getattr(attachment, "handle")),
        media_type=str(getattr(attachment, "mime_type")),
        data=getattr(source, "data"),
        filename=getattr(attachment, "filename"),
    )
