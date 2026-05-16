from __future__ import annotations

from dataclasses import dataclass, field

from agentos.attachments.types import Attachment, AttachmentError


@dataclass(slots=True)
class AttachmentStore:
    """session-scoped 附件元数据与 source 存储。"""

    _attachments: dict[str, Attachment] = field(default_factory=dict)

    def put(self, attachment: Attachment) -> None:
        """保存附件引用。"""

        self._attachments[attachment.handle] = attachment

    def get(self, handle: str) -> Attachment:
        """按 handle 读取附件。"""

        try:
            return self._attachments[handle]
        except KeyError as error:
            raise AttachmentError(f"unknown attachment: {handle}") from error

    def list(self) -> list[Attachment]:
        """列出当前 session 内附件。"""

        return list(self._attachments.values())

    def delete(self, handle: str) -> None:
        """删除附件引用。"""

        self.get(handle)
        del self._attachments[handle]
