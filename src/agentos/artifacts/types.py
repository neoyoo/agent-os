from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, TypeAlias
from uuid import UUID, uuid4


_ARTIFACT_ID_PATTERN = re.compile(
    r"art_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)
_MEDIA_TYPE_PATTERN = re.compile(
    r"[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+"
)

ArtifactMountReason: TypeAlias = Literal["user_upload", "tool_result"]
ArtifactToolState: TypeAlias = Literal["available", "mounted"]


class ArtifactError(ValueError):
    """Artifact 领域错误基类。"""


class ArtifactNotFoundError(ArtifactError):
    """Artifact 在指定 Session 中不存在。"""

    def __init__(self) -> None:
        super().__init__("artifact not found")


class ArtifactValidationError(ArtifactError):
    """Artifact 输入或领域值不合法。"""


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    """ArtifactStore 保存的内容元数据，不包含原始 bytes。"""

    id: str
    session_id: str
    filename: str | None
    media_type: str
    size_bytes: int
    created_at: datetime

    def __post_init__(self) -> None:
        validate_artifact_id(self.id)
        validate_artifact_session_id(self.session_id)
        validate_artifact_filename(self.filename)
        validate_artifact_media_type(self.media_type)
        if type(self.size_bytes) is not int or self.size_bytes < 0:
            raise ArtifactValidationError("artifact size is invalid")
        if (
            not isinstance(self.created_at, datetime)
            or self.created_at.tzinfo is None
            or self.created_at.utcoffset() != timedelta(0)
        ):
            raise ArtifactValidationError("artifact created_at must be UTC")


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    """StoredMessage 保存的附件轻量引用。"""

    artifact_id: str
    filename: str | None
    media_type: str

    def __post_init__(self) -> None:
        validate_artifact_id(self.artifact_id)
        validate_artifact_filename(self.filename)
        validate_artifact_media_type(self.media_type)


@dataclass(frozen=True, slots=True)
class ArtifactPage:
    """ArtifactStore 按稳定游标返回的一页记录。"""

    items: tuple[ArtifactRecord, ...]
    next_cursor: str | None

    def __post_init__(self) -> None:
        items = tuple(self.items)
        if any(type(item) is not ArtifactRecord for item in items):
            raise ArtifactValidationError("artifact page items are invalid")
        if self.next_cursor is not None and (
            type(self.next_cursor) is not str or not self.next_cursor
        ):
            raise ArtifactValidationError("artifact cursor is invalid")
        object.__setattr__(self, "items", items)


@dataclass(frozen=True, slots=True)
class ArtifactToolItem:
    """`list_attachments` 可向模型返回的安全 Artifact 元数据。"""

    handle: str
    filename: str | None
    media_type: str
    state: ArtifactToolState

    def __post_init__(self) -> None:
        validate_artifact_id(self.handle)
        validate_artifact_filename(self.filename)
        validate_artifact_media_type(self.media_type)
        if self.state not in ("available", "mounted"):
            raise ArtifactValidationError("artifact tool state is invalid")


@dataclass(frozen=True, slots=True)
class ArtifactToolPage:
    """`list_attachments` 的有界、模型安全分页结果。"""

    items: tuple[ArtifactToolItem, ...]
    next_cursor: str | None

    def __post_init__(self) -> None:
        items = tuple(self.items)
        if any(type(item) is not ArtifactToolItem for item in items):
            raise ArtifactValidationError("artifact tool page items are invalid")
        if self.next_cursor is not None and (
            type(self.next_cursor) is not str or not self.next_cursor
        ):
            raise ArtifactValidationError("artifact cursor is invalid")
        object.__setattr__(self, "items", items)


@dataclass(frozen=True, slots=True)
class ContextMount:
    """当前 Turn 内有效的 Artifact Provider 投影引用。"""

    artifact_id: str
    reason: ArtifactMountReason
    scope: Literal["current_turn"] = "current_turn"

    def __post_init__(self) -> None:
        validate_artifact_id(self.artifact_id)
        if self.reason not in ("user_upload", "tool_result"):
            raise ArtifactValidationError("artifact mount reason is invalid")
        if self.scope != "current_turn":
            raise ArtifactValidationError("artifact mount scope is invalid")


def new_artifact_id() -> str:
    """生成使用 UUID4 的稳定 Artifact Handle。"""

    return f"art_{uuid4()}"


def validate_artifact_id(artifact_id: str) -> None:
    """校验 canonical `art_` + UUID4 Handle。"""

    if (
        type(artifact_id) is not str
        or _ARTIFACT_ID_PATTERN.fullmatch(artifact_id) is None
    ):
        raise ArtifactValidationError("artifact id is invalid")
    try:
        parsed = UUID(artifact_id[4:])
    except ValueError:
        raise ArtifactValidationError("artifact id is invalid") from None
    if parsed.version != 4 or str(parsed) != artifact_id[4:]:
        raise ArtifactValidationError("artifact id is invalid")


def validate_artifact_filename(filename: str | None) -> None:
    """拒绝路径、控制字符和超长 Artifact 文件名。"""

    if filename is None:
        return
    if (
        type(filename) is not str
        or not filename
        or len(filename) > 255
        or "/" in filename
        or "\\" in filename
        or any(
            unicodedata.category(character).startswith("C")
            for character in filename
        )
    ):
        raise ArtifactValidationError("artifact filename is invalid")


def validate_artifact_media_type(media_type: str) -> None:
    """校验受控 ASCII MIME 语法，具体 allowlist 由 Runtime Policy 决定。"""

    if type(media_type) is not str or _MEDIA_TYPE_PATTERN.fullmatch(media_type) is None:
        raise ArtifactValidationError("artifact media type is invalid")


def validate_artifact_session_id(session_id: str) -> None:
    """校验 Artifact 操作使用的 Session Scope。"""

    if (
        type(session_id) is not str
        or not session_id
        or any(
            unicodedata.category(character).startswith("C")
            for character in session_id
        )
    ):
        raise ArtifactValidationError("artifact session id is invalid")
