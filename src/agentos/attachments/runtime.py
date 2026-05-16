from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from agentos.attachments.store import AttachmentStore
from agentos.attachments.types import (
    Attachment,
    AttachmentError,
    BytesSource,
)
from agentos.providers.messages import (
    FilePart,
    ImagePart,
    ProviderMessage,
    TextPart,
    UserMessage,
)


DEFAULT_ALLOWED_MIME_TYPES = frozenset(
    {
        "image/gif",
        "image/jpeg",
        "image/png",
        "image/webp",
        "application/pdf",
    },
)
DEFAULT_MAX_SIZE_BYTES = 25 * 1024 * 1024


@dataclass(slots=True)
class AttachmentRuntime:
    """管理 session-scoped 附件和 provider request 一次性展开。"""

    store: AttachmentStore = field(default_factory=AttachmentStore)
    allowed_mime_types: frozenset[str] = DEFAULT_ALLOWED_MIME_TYPES
    max_size_bytes: int = DEFAULT_MAX_SIZE_BYTES
    _next_index: int = 1
    _pending_user_handles: list[str] = field(default_factory=list)
    _pending_user_text_by_handle: dict[str, str] = field(default_factory=dict)
    _pending_recall_handles: list[str] = field(default_factory=list)

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
            self.store.get(handle)
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
                "- status: not loaded in current context",
                f"- preview: {preview}",
                (
                    "- To inspect it again, call "
                    f"recall_context(handle=\"att:{attachment.handle}\")."
                ),
            ],
        )

    def recall_attachment_handle(self, handle: str) -> Attachment:
        """处理 recall_context 的 att: handle，并安排下一次 request 展开。"""

        attachment_handle = self._strip_attachment_namespace(handle)
        attachment = self.store.get(attachment_handle)
        self._pending_recall_handles.append(attachment.handle)
        return attachment

    def project_provider_messages(
        self,
        messages: list[ProviderMessage],
    ) -> list[ProviderMessage]:
        """把待展开附件投影进下一次 provider request，然后消费展开状态。"""

        user_handles, user_text = self._consume_user_handles()
        recall_handles = self._consume_recall_handles()
        projected = list(messages)
        if user_handles:
            projected = self._project_user_handles(projected, user_handles, user_text)
        if recall_handles:
            projected.append(
                UserMessage(
                    content=(
                        TextPart(
                            "Recalled attachment "
                            + ", ".join(recall_handles)
                            + " for inspection.",
                        ),
                        *[
                            self._content_part_for_attachment(self.store.get(handle))
                            for handle in recall_handles
                        ],
                    ),
                ),
            )
        return projected

    def _project_user_handles(
        self,
        messages: list[ProviderMessage],
        handles: list[str],
        user_text: str | None,
    ) -> list[ProviderMessage]:
        """把首轮附件展开到最后一条 user message。"""

        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            if isinstance(message, UserMessage):
                text = (
                    user_text
                    if user_text is not None
                    else message.content if isinstance(message.content, str) else ""
                )
                messages[index] = UserMessage(
                    content=(
                        TextPart(text),
                        *[
                            self._content_part_for_attachment(self.store.get(handle))
                            for handle in handles
                        ],
                    ),
                )
                return messages
        raise AttachmentError("cannot expand attachments without a user message")

    def _consume_user_handles(self) -> tuple[list[str], str | None]:
        handles = list(dict.fromkeys(self._pending_user_handles))
        text = None
        for handle in handles:
            pending_text = self._pending_user_text_by_handle.pop(handle, None)
            if text is None and pending_text is not None:
                text = pending_text
        self._pending_user_handles.clear()
        return handles, text

    def _consume_recall_handles(self) -> list[str]:
        handles = list(dict.fromkeys(self._pending_recall_handles))
        self._pending_recall_handles.clear()
        return handles

    def _next_handle(self) -> str:
        handle = f"att_{self._next_index}"
        self._next_index += 1
        return handle

    def _content_part_for_attachment(self, attachment: Attachment) -> object:
        """按 MIME type 选择 canonical provider content part。"""

        if attachment.mime_type.startswith("image/"):
            return ImagePart(attachment)
        return FilePart(attachment)

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
            raise AttachmentError("attachment recall handle must start with 'att:'")
        stripped = handle.removeprefix("att:")
        if not stripped:
            raise AttachmentError("attachment recall handle is empty")
        return stripped
