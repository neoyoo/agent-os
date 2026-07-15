from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

from agentos.attachments.store import AttachmentStore
from agentos.attachments.types import (
    Attachment,
    AttachmentError,
    BytesSource,
)
from agentos.providers.content import (
    ImagePart,
    ProviderBinaryPayload,
    TextPart,
)
from agentos.providers.input import ProviderInputItem


DEFAULT_ALLOWED_MIME_TYPES = frozenset(
    {
        "image/gif",
        "image/jpeg",
        "image/png",
        "image/webp",
    },
)
DEFAULT_MAX_SIZE_BYTES = 25 * 1024 * 1024


@dataclass(slots=True)
class AttachmentRuntime:
    """管理 session-scoped 图片附件和 turn-scoped provider 投影。"""

    store: AttachmentStore = field(default_factory=AttachmentStore)
    allowed_mime_types: frozenset[str] = DEFAULT_ALLOWED_MIME_TYPES
    max_size_bytes: int = DEFAULT_MAX_SIZE_BYTES
    _next_index: int = 1
    _pending_user_handles: list[str] = field(default_factory=list)
    _pending_user_text_by_handle: dict[str, str] = field(default_factory=dict)
    _turn_loaded_attachment_handles: list[str] = field(default_factory=list)

    def upload(self, path: str | Path, mime_type: str) -> Attachment:
        """登记本地文件附件。"""

        file_path = Path(path)
        data = file_path.read_bytes()
        self._validate_upload(mime_type=mime_type, size_bytes=len(data))
        attachment = Attachment(
            handle=self._next_handle(),
            filename=file_path.name,
            mime_type=mime_type,
            size_bytes=len(data),
            source=BytesSource(data=data),
            preview=f"user uploaded file {file_path.name}",
        )
        self.store.put(attachment)
        return attachment

    def upload_bytes(
        self,
        data: bytes,
        *,
        filename: str | None,
        mime_type: str,
    ) -> Attachment:
        """登记 bytes 附件。"""

        self._validate_upload(mime_type=mime_type, size_bytes=len(data))
        attachment = Attachment(
            handle=self._next_handle(),
            filename=filename,
            mime_type=mime_type,
            size_bytes=len(data),
            source=BytesSource(data=data),
            preview=(
                f"user uploaded file {filename}"
                if filename
                else f"user uploaded {mime_type} attachment"
            ),
        )
        self.store.put(attachment)
        return attachment

    def prepare_user_message(
        self,
        content: str,
        attachments: list[Attachment],
    ) -> str:
        """给 user message 追加安全占位符并安排首轮一次性展开。"""

        if not attachments:
            return content
        handles = [attachment.handle for attachment in attachments]
        for handle in handles:
            self._ensure_image_attachment(self.store.get(handle))
        self._pending_user_handles.extend(handles)
        for handle in handles:
            self._pending_user_text_by_handle[handle] = content
        placeholders = "\n\n".join(self.placeholder_text(handle) for handle in handles)
        return f"{content}\n\n{placeholders}"

    def placeholder_text(self, handle: str) -> str:
        """渲染 LLM 可见的附件占位符，不暴露 bytes/source/provider id。"""

        attachment = self.store.get(handle)
        filename = attachment.filename or "(unnamed)"
        preview = attachment.preview or f"user uploaded {attachment.mime_type} attachment"
        return "\n".join(
            [
                f"Attachment {attachment.handle}",
                f"- filename: {filename}",
                f"- mime_type: {attachment.mime_type}",
                f"- size_bytes: {attachment.size_bytes}",
                "- status: not loaded in current turn",
                f"- preview: {preview}",
                f"- handle: att:{attachment.handle}",
            ],
        )

    def load_attachment_handle(self, handle: str) -> Attachment:
        """处理 load_attachment 的 att: handle，并在当前 turn 内持续展开。"""

        attachment_handle = self._strip_attachment_namespace(handle)
        attachment = self.store.get(attachment_handle)
        self._ensure_image_attachment(attachment)
        if attachment.handle not in self._turn_loaded_attachment_handles:
            self._turn_loaded_attachment_handles.append(attachment.handle)
        return attachment

    def clear_turn_loaded_attachments(self) -> None:
        """清理当前 turn 加载的附件状态。"""

        self._turn_loaded_attachment_handles.clear()

    def _project_provider_inputs_compat(
        self,
        items: tuple[ProviderInputItem, ...],
    ) -> tuple[ProviderInputItem, ...]:
        """在 Phase 3A 前保留现有图片附件的 ProviderInput 投影。"""

        user_handles, user_text = self._consume_user_handles()
        loaded_handles = self._loaded_handles_excluding(user_handles)
        projected = list(items)
        if user_handles:
            projected = self._project_user_input_handles(
                projected,
                user_handles,
                user_text,
            )
        if loaded_handles:
            projected.append(
                ProviderInputItem.context_mount(
                    self._loaded_attachment_content(loaded_handles),
                ),
            )
        return tuple(projected)

    def _loaded_handles_excluding(self, excluded: list[str]) -> list[str]:
        excluded_handles = set(excluded)
        return [
            handle for handle in dict.fromkeys(self._turn_loaded_attachment_handles)
            if handle not in excluded_handles
        ]

    def _loaded_attachment_content(
        self, handles: list[str]
    ) -> tuple[TextPart | ImagePart, ...]:
        return (
            TextPart(f"Loaded attachment {', '.join(handles)} for inspection."),
            *[
                self._content_part_for_attachment(self.store.get(handle))
                for handle in handles
            ],
        )

    def _project_user_input_handles(
        self,
        items: list[ProviderInputItem],
        handles: list[str],
        user_text: str | None,
    ) -> list[ProviderInputItem]:
        """把首次上传图片展开到对应的业务 user item。"""

        for index in range(len(items) - 1, -1, -1):
            item = items[index]
            if item.role != "user" or item.kind != "business_message":
                continue
            text = user_text if user_text is not None else ""
            items[index] = replace(
                item,
                content=(
                    TextPart(text),
                    *[
                        self._content_part_for_attachment(self.store.get(handle))
                        for handle in handles
                    ],
                ),
            )
            return items
        raise AttachmentError("cannot expand attachments without a user message")

    def _consume_user_handles(self) -> tuple[list[str], str | None]:
        handles = list(dict.fromkeys(self._pending_user_handles))
        text = None
        for handle in handles:
            pending_text = self._pending_user_text_by_handle.pop(handle, None)
            if text is None and pending_text is not None:
                text = pending_text
            if handle not in self._turn_loaded_attachment_handles:
                self._turn_loaded_attachment_handles.append(handle)
        self._pending_user_handles.clear()
        return handles, text

    def _next_handle(self) -> str:
        handle = f"att_{self._next_index}"
        self._next_index += 1
        return handle

    def _content_part_for_attachment(self, attachment: Attachment) -> ImagePart:
        """按 MIME type 选择 canonical provider content part。"""

        self._ensure_image_attachment(attachment)
        if not isinstance(attachment.source, BytesSource):
            raise AttachmentError("attachment provider projection requires bytes")
        return ImagePart(
            ProviderBinaryPayload(
                handle=attachment.handle,
                media_type=attachment.mime_type,
                data=attachment.source.data,
                filename=attachment.filename,
            ),
        )

    def _ensure_image_attachment(self, attachment: Attachment) -> None:
        if attachment.mime_type not in self.allowed_mime_types:
            raise AttachmentError(
                f"unsupported attachment MIME type: {attachment.mime_type}",
            )
        if not attachment.mime_type.startswith("image/"):
            raise AttachmentError(
                f"only image attachments can be projected: {attachment.mime_type}",
            )

    def _validate_upload(self, *, mime_type: str, size_bytes: int) -> None:
        """执行 v1 最小 MIME 和大小策略。"""

        if mime_type not in self.allowed_mime_types:
            raise AttachmentError(f"unsupported attachment MIME type: {mime_type}")
        if size_bytes > self.max_size_bytes:
            raise AttachmentError(
                f"attachment exceeds max attachment size: {size_bytes} > "
                f"{self.max_size_bytes}",
            )

    def _strip_attachment_namespace(self, handle: str) -> str:
        if not handle.startswith("att:"):
            raise AttachmentError("attachment handle must start with 'att:'")
        stripped = handle.removeprefix("att:")
        if not stripped:
            raise AttachmentError("attachment handle is empty")
        return stripped
