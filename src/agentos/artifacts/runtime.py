from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agentos.artifacts.store import ArtifactStore
from agentos.artifacts.types import (
    ArtifactMountReason,
    ArtifactPage,
    ArtifactRecord,
    ArtifactValidationError,
    ContextMount,
    validate_artifact_media_type,
    validate_artifact_session_id,
    validate_artifact_id,
)
from agentos.events.artifacts import (
    ArtifactDeletedEvent,
    ArtifactLoadRequestedEvent,
    ArtifactMountedEvent,
    ArtifactUnmountedEvent,
    ArtifactUploadedEvent,
)
from agentos.events.types import AgentEvent


DEFAULT_ARTIFACT_MEDIA_TYPES = frozenset(
    {
        "application/pdf",
        "image/gif",
        "image/jpeg",
        "image/png",
        "image/webp",
    }
)
DEFAULT_ARTIFACT_MAX_SIZE_BYTES = 25 * 1024 * 1024


class ArtifactEventSink(Protocol):
    """ArtifactRuntime 发布已发生事实的观察边界。"""

    def emit(self, event: AgentEvent) -> AgentEvent:
        """发布一个 typed Artifact event。"""


@dataclass(frozen=True, slots=True)
class ArtifactPolicy:
    """Artifact 上传的媒体类型与单文件大小策略。"""

    allowed_media_types: frozenset[str] = DEFAULT_ARTIFACT_MEDIA_TYPES
    max_size_bytes: int = DEFAULT_ARTIFACT_MAX_SIZE_BYTES

    def __post_init__(self) -> None:
        try:
            allowed_media_types = frozenset(self.allowed_media_types)
        except TypeError:
            raise ArtifactValidationError("artifact media policy is invalid") from None
        for media_type in allowed_media_types:
            validate_artifact_media_type(media_type)
        if type(self.max_size_bytes) is not int or self.max_size_bytes <= 0:
            raise ArtifactValidationError("artifact size policy is invalid")
        object.__setattr__(self, "allowed_media_types", allowed_media_types)


class ArtifactRuntime:
    """管理单个 Session 的 Artifact 操作与当前 Turn Mount。"""

    def __init__(
        self,
        *,
        session_id: str,
        store: ArtifactStore,
        policy: ArtifactPolicy | None = None,
        event_bus: ArtifactEventSink | None = None,
    ) -> None:
        validate_artifact_session_id(session_id)
        self._session_id = session_id
        self._store = store
        self._policy = policy if policy is not None else ArtifactPolicy()
        self._event_bus = event_bus
        self._mounts: list[ContextMount] = []

    @property
    def session_id(self) -> str:
        """返回构造时冻结的 Session Scope。"""

        return self._session_id

    @property
    def policy(self) -> ArtifactPolicy:
        """返回构造时冻结的 Artifact Policy。"""

        return self._policy

    def upload(
        self,
        *,
        data: bytes,
        filename: str | None,
        media_type: str,
    ) -> ArtifactRecord:
        """校验 Policy 后把 bytes 写入 Session ArtifactStore。"""

        if type(data) is not bytes:
            raise ArtifactValidationError("artifact data must be bytes")
        validate_artifact_media_type(media_type)
        if media_type not in self._policy.allowed_media_types:
            raise ArtifactValidationError("unsupported artifact media type")
        if len(data) > self._policy.max_size_bytes:
            raise ArtifactValidationError("artifact exceeds maximum size")
        record = self._store.put(
            session_id=self._session_id,
            data=data,
            filename=filename,
            media_type=media_type,
        )
        self._emit(
            ArtifactUploadedEvent(
                session_id=self._session_id,
                handle=record.id,
                filename=record.filename,
                media_type=record.media_type,
                size_bytes=record.size_bytes,
            )
        )
        return record

    def list(
        self,
        cursor: str | None = None,
        limit: int = 20,
    ) -> ArtifactPage:
        """列出当前 Session 的 Artifact 元数据。"""

        return self._store.list(self._session_id, cursor, limit)

    def read(self, artifact_id: str) -> bytes:
        """读取当前 Session 的 Artifact 内容。"""

        return self._store.read(self._session_id, artifact_id)

    def load_attachment(self, handle: str) -> str:
        """挂载 Tool Result Artifact，并返回固定有界确认。"""

        validate_artifact_id(handle)
        self._emit(
            ArtifactLoadRequestedEvent(
                session_id=self._session_id,
                handle=handle,
            )
        )
        mount, record, created = self._mount(handle, "tool_result")
        if created:
            self._emit_mounted(mount, record)
        return (
            f"附件已挂载：{handle}。"
            "附件内容将在下一次模型请求中作为当前轮次的工具结果数据提供。"
        )

    def mount_user_upload(self, handle: str) -> ContextMount:
        """把已存储 Artifact 挂载为当前 Turn 的用户上传。"""

        mount, record, created = self._mount(handle, "user_upload")
        if created:
            self._emit_mounted(mount, record)
        return mount

    def active_mounts(self) -> tuple[ContextMount, ...]:
        """按建立顺序返回当前 Turn 的不可变 Mount 快照。"""

        return tuple(self._mounts)

    def resolve_mount(self, mount: ContextMount) -> tuple[ArtifactRecord, bytes]:
        """从 Store 重新读取一个仍然有效的 Mount。"""

        if type(mount) is not ContextMount or mount not in self._mounts:
            raise ArtifactValidationError("artifact mount is not active")
        record = self._store.get(self._session_id, mount.artifact_id)
        data = self._store.read(self._session_id, mount.artifact_id)
        return record, data

    def clear_mounts(self) -> tuple[ContextMount, ...]:
        """清除当前 Turn Mount，但不删除 Artifact。"""

        cleared = tuple(self._mounts)
        self._mounts.clear()
        for mount in cleared:
            self._emit_unmounted(mount)
        return cleared

    def delete(self, artifact_id: str) -> None:
        """删除当前 Session Artifact，并移除对应 Mount。"""

        record = self._store.get(self._session_id, artifact_id)
        removed_mounts = tuple(
            mount for mount in self._mounts if mount.artifact_id == artifact_id
        )
        self._store.delete(self._session_id, artifact_id)
        self._mounts = [mount for mount in self._mounts if mount not in removed_mounts]
        for mount in removed_mounts:
            self._emit_unmounted(mount)
        self._emit_deleted(record)

    def delete_session(self) -> None:
        """删除当前 Session 全部 Artifact 和 Mount。"""

        records = self._all_records()
        mounts = tuple(self._mounts)
        self._store.delete_session(self._session_id)
        self._mounts.clear()
        for mount in mounts:
            self._emit_unmounted(mount)
        for record in records:
            self._emit_deleted(record)

    def _mount(
        self,
        artifact_id: str,
        reason: ArtifactMountReason,
    ) -> tuple[ContextMount, ArtifactRecord, bool]:
        record = self._store.get(self._session_id, artifact_id)
        for index, mount in enumerate(self._mounts):
            if mount.artifact_id == artifact_id:
                if mount.reason == "user_upload" and reason == "tool_result":
                    upgraded = ContextMount(artifact_id=artifact_id, reason=reason)
                    self._mounts[index] = upgraded
                    self._emit_unmounted(mount)
                    return upgraded, record, True
                return mount, record, False
        mount = ContextMount(artifact_id=artifact_id, reason=reason)
        self._mounts.append(mount)
        return mount, record, True

    def _all_records(self) -> tuple[ArtifactRecord, ...]:
        records: list[ArtifactRecord] = []
        cursor: str | None = None
        while True:
            page = self._store.list(self._session_id, cursor, 100)
            records.extend(page.items)
            if page.next_cursor is None:
                return tuple(records)
            cursor = page.next_cursor

    def _emit_mounted(
        self,
        mount: ContextMount,
        record: ArtifactRecord,
    ) -> None:
        self._emit(
            ArtifactMountedEvent(
                session_id=self._session_id,
                handle=record.id,
                filename=record.filename,
                media_type=record.media_type,
                reason=mount.reason,
            )
        )

    def _emit_unmounted(self, mount: ContextMount) -> None:
        self._emit(
            ArtifactUnmountedEvent(
                session_id=self._session_id,
                handle=mount.artifact_id,
                reason=mount.reason,
            )
        )

    def _emit_deleted(self, record: ArtifactRecord) -> None:
        self._emit(
            ArtifactDeletedEvent(
                session_id=self._session_id,
                handle=record.id,
                filename=record.filename,
                media_type=record.media_type,
                size_bytes=record.size_bytes,
            )
        )

    def _emit(self, event: AgentEvent) -> None:
        if self._event_bus is not None:
            self._event_bus.emit(event)
