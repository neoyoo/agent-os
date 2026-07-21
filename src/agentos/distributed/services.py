from __future__ import annotations

from dataclasses import dataclass, field

from agentos.artifacts.runtime import ArtifactPolicy
from agentos.artifacts.types import (
    ArtifactMediaTypeUnsupportedError,
    ArtifactPage,
    ArtifactRecord,
    ArtifactTooLargeError,
    validate_artifact_filename,
    validate_artifact_id,
    validate_artifact_media_type,
)
from agentos.distributed.models import (
    ArtifactContent,
    RequestScope,
    RunReadModel,
    RunSubmission,
    RunSubmissionReceipt,
)
from agentos.distributed._model_validation import (
    require_identifier,
    require_positive,
)
from agentos.distributed._service_validation import get_run, validated_replay
from agentos.distributed.protocols import (
    DistributedArtifactPort,
    EventReplayPort,
    EventSubscription,
    RunCommandPort,
    RunQueryPort,
    RunSubmissionPort,
)
from agentos.runtime.durable_commands import DurableCommandReceipt, DurableRunCommand


@dataclass(frozen=True, slots=True)
class RunSubmissionService:
    """首次 Run 的 tenant-scoped Application Service。"""

    port: RunSubmissionPort

    async def submit(
        self,
        scope: RequestScope,
        submission: RunSubmission,
    ) -> RunSubmissionReceipt:
        """持久接受首次输入；事务和幂等真值由 Port 拥有。"""

        _require_scope(scope)
        if type(submission) is not RunSubmission:
            raise TypeError("submission must be RunSubmission")
        receipt = await self.port.submit(scope=scope, submission=submission)
        if type(receipt) is not RunSubmissionReceipt or (
            receipt.session_id != submission.session_id
            or receipt.submission_id != submission.submission_id
        ):
            raise RuntimeError("run submission port returned another submission receipt")
        return receipt


@dataclass(frozen=True, slots=True)
class RunCommandService:
    """Durable Command 的 tenant-scoped Application Service。"""

    port: RunCommandPort

    async def submit(
        self,
        scope: RequestScope,
        session_id: str,
        command: DurableRunCommand,
    ) -> DurableCommandReceipt:
        """提交命令但不执行 QueryLoop，也不要求 ingress fence。"""

        _require_scope(scope)
        require_identifier(session_id, "session_id")
        if type(command) is not DurableRunCommand:
            raise TypeError("command must be DurableRunCommand")
        receipt = await self.port.submit_command(
            scope=scope,
            session_id=session_id,
            command=command,
        )
        if type(receipt) is not DurableCommandReceipt or (
            receipt.run_id != command.run_id
            or receipt.command_id != command.command_id
            or receipt.kind != command.kind
        ):
            raise RuntimeError("run command port returned another command receipt")
        return receipt


@dataclass(frozen=True, slots=True)
class RunQueryService:
    """Run read model 的 tenant-scoped Application Service。"""

    port: RunQueryPort

    async def get(
        self,
        scope: RequestScope,
        session_id: str,
        run_id: str,
    ) -> RunReadModel:
        """读取 Run；未知与跨 tenant 使用同一 not-found。"""

        _require_scope(scope)
        require_identifier(session_id, "session_id")
        require_identifier(run_id, "run_id")
        return await get_run(self.port, scope, session_id, run_id)


@dataclass(frozen=True, slots=True)
class RunEventStream:
    """Run live event 的 tenant-scoped replay/tail Application Service。"""

    query_port: RunQueryPort
    replay_port: EventReplayPort

    async def subscribe(
        self,
        scope: RequestScope,
        session_id: str,
        run_id: str,
        cursor: str | None = None,
    ) -> EventSubscription:
        """订阅 typed event；observer 不持有消费型 ACK。"""

        _require_scope(scope)
        require_identifier(session_id, "session_id")
        require_identifier(run_id, "run_id")
        if cursor is not None:
            require_identifier(cursor, "cursor")
        await get_run(self.query_port, scope, session_id, run_id)
        return await self.follow_after(scope, session_id, run_id, cursor)

    async def capture_high_water(
        self,
        scope: RequestScope,
        session_id: str,
        run_id: str,
    ) -> str | None:
        """Capture a Redis stream barrier before a PostgreSQL snapshot read."""

        _require_scope(scope)
        require_identifier(session_id, "session_id")
        require_identifier(run_id, "run_id")
        return await self.replay_port.high_water(
            scope=scope,
            session_id=session_id,
            run_id=run_id,
        )

    async def follow_after(
        self,
        scope: RequestScope,
        session_id: str,
        run_id: str,
        cursor: str | None,
    ) -> EventSubscription:
        """Follow a previously captured barrier without another snapshot read."""

        _require_scope(scope)
        require_identifier(session_id, "session_id")
        require_identifier(run_id, "run_id")
        if cursor is not None:
            require_identifier(cursor, "cursor")
        events = self.replay_port.follow(
            scope=scope,
            session_id=session_id,
            run_id=run_id,
            after=cursor,
        )
        return validated_replay(
            events,
            scope.tenant_id,
            session_id,
            run_id,
            cursor,
        )


@dataclass(frozen=True, slots=True)
class ArtifactService:
    """Shared Artifact 的 tenant-scoped Application Service。"""

    port: DistributedArtifactPort
    policy: ArtifactPolicy = field(default_factory=ArtifactPolicy)

    async def upload(
        self,
        scope: RequestScope,
        session_id: str,
        upload_id: str,
        data: bytes,
        filename: str | None,
        media_type: str,
    ) -> ArtifactRecord:
        """提交一次幂等上传，不暴露 blob key 或本地路径。"""

        _require_scope(scope)
        require_identifier(session_id, "session_id")
        require_identifier(upload_id, "upload_id")
        if type(data) is not bytes:
            raise TypeError("data must be bytes")
        validate_artifact_filename(filename)
        validate_artifact_media_type(media_type)
        if media_type not in self.policy.allowed_media_types:
            raise ArtifactMediaTypeUnsupportedError()
        if len(data) > self.policy.max_size_bytes:
            raise ArtifactTooLargeError()
        record = await self.port.upload(
            scope=scope,
            session_id=session_id,
            upload_id=upload_id,
            data=data,
            filename=filename,
            media_type=media_type,
        )
        if type(record) is not ArtifactRecord or (
            record.session_id != session_id
            or record.filename != filename
            or record.media_type != media_type
            or record.size_bytes != len(data)
        ):
            raise RuntimeError("artifact port returned an invalid uploaded artifact")
        return record

    async def list(
        self,
        scope: RequestScope,
        session_id: str,
        cursor: str | None = None,
        limit: int = 20,
    ) -> ArtifactPage:
        """按 tenant/session-scoped cursor 返回 Artifact metadata。"""

        _require_scope(scope)
        require_identifier(session_id, "session_id")
        if cursor is not None:
            require_identifier(cursor, "cursor")
        require_positive(limit, "limit")
        page = await self.port.list(
            scope=scope,
            session_id=session_id,
            cursor=cursor,
            limit=limit,
        )
        if type(page) is not ArtifactPage or any(
            item.session_id != session_id for item in page.items
        ):
            raise RuntimeError("artifact port returned an invalid artifact page")
        return page

    async def read(
        self,
        scope: RequestScope,
        session_id: str,
        artifact_id: str,
    ) -> ArtifactContent:
        """读取受 scope 保护的 metadata 与 bytes。"""

        _require_scope(scope)
        require_identifier(session_id, "session_id")
        validate_artifact_id(artifact_id)
        content = await self.port.read(
            scope=scope,
            session_id=session_id,
            artifact_id=artifact_id,
        )
        if type(content) is not ArtifactContent or (
            content.record.session_id != session_id
            or content.record.id != artifact_id
        ):
            raise RuntimeError("artifact port returned invalid artifact content")
        return content

    async def delete(
        self,
        scope: RequestScope,
        session_id: str,
        artifact_id: str,
        deletion_id: str,
    ) -> None:
        """提交幂等删除，metadata/blob 一致性由 Port 保证。"""

        _require_scope(scope)
        require_identifier(session_id, "session_id")
        validate_artifact_id(artifact_id)
        require_identifier(deletion_id, "deletion_id")
        await self.port.delete(
            scope=scope,
            session_id=session_id,
            artifact_id=artifact_id,
            deletion_id=deletion_id,
        )


def _require_scope(scope: object) -> None:
    if type(scope) is not RequestScope:
        raise TypeError("scope must be RequestScope")


__all__ = [
    "ArtifactService",
    "RunCommandService",
    "RunEventStream",
    "RunQueryService",
    "RunSubmissionService",
]
