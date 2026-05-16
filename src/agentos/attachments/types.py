from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeAlias


AttachmentLifecycle = Literal["ephemeral"]


class AttachmentError(ValueError):
    """附件生命周期操作失败。"""


@dataclass(frozen=True, slots=True)
class LocalFileSource:
    """SDK 可读取的本地文件。"""

    path: Path


@dataclass(frozen=True, slots=True)
class BytesSource:
    """调用方提供的 bytes。"""

    data: bytes


@dataclass(frozen=True, slots=True)
class UrlSource:
    """远程 URL source。"""

    url: str


@dataclass(frozen=True, slots=True)
class InlineBase64Source:
    """provider-ready base64 source。"""

    data: str
    mime_type: str


@dataclass(frozen=True, slots=True)
class ProviderFileSource:
    """provider 文件 API 上传后的引用。"""

    provider_name: str
    file_id: str
    state: Literal["uploading", "processing", "ready", "failed"] = "ready"


AttachmentSource: TypeAlias = (
    LocalFileSource
    | BytesSource
    | UrlSource
    | InlineBase64Source
    | ProviderFileSource
)


@dataclass(frozen=True, slots=True)
class Attachment:
    """session 内的附件引用。"""

    handle: str
    filename: str | None
    mime_type: str
    size_bytes: int
    source: AttachmentSource
    lifecycle: AttachmentLifecycle = "ephemeral"
    preview: str | None = None
    summary: str | None = None
