from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json

from agentos._waiting import WaitReason
from agentos.artifacts.types import ArtifactRecord, validate_artifact_id
from agentos.distributed._model_validation import (
    require_identifier,
    require_non_negative,
)
from agentos.runtime.run import AgentResult
from agentos.runtime.run_state import RunStatus


@dataclass(frozen=True, slots=True)
class RequestScope:
    """由已鉴权入口注入的 tenant 与 principal identity。"""

    tenant_id: str
    principal_id: str

    def __post_init__(self) -> None:
        require_identifier(self.tenant_id, "tenant_id")
        require_identifier(self.principal_id, "principal_id")


@dataclass(frozen=True, slots=True)
class RunSubmission:
    """首次 Run 的幂等持久提交。"""

    session_id: str
    submission_id: str
    content: str
    artifact_handles: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_identifier(self.session_id, "session_id")
        require_identifier(self.submission_id, "submission_id")
        if type(self.content) is not str:
            raise TypeError("content must be str")
        if type(self.artifact_handles) is str:
            raise TypeError("artifact_handles must contain str values")
        handles = tuple(self.artifact_handles)
        if any(type(handle) is not str for handle in handles):
            raise TypeError("artifact_handles must contain str values")
        for handle in handles:
            validate_artifact_id(handle)
        object.__setattr__(self, "artifact_handles", handles)


@dataclass(frozen=True, slots=True)
class RunSubmissionReceipt:
    """RunSubmission 已原子应用或去重的持久回执。"""

    session_id: str
    run_id: str
    submission_id: str
    aggregate_version: int
    duplicate: bool

    def __post_init__(self) -> None:
        require_identifier(self.session_id, "session_id")
        require_identifier(self.run_id, "run_id")
        require_identifier(self.submission_id, "submission_id")
        require_non_negative(self.aggregate_version, "aggregate_version")
        if type(self.duplicate) is not bool:
            raise TypeError("duplicate must be bool")


@dataclass(frozen=True, slots=True)
class RunReadModel:
    """PostgreSQL-owned tenant-scoped Run query result."""

    tenant_id: str
    session_id: str
    run_id: str
    status: RunStatus
    wait_reason: WaitReason | None
    aggregate_version: int
    result: AgentResult | None

    def __post_init__(self) -> None:
        require_identifier(self.tenant_id, "tenant_id")
        require_identifier(self.session_id, "session_id")
        require_identifier(self.run_id, "run_id")
        if type(self.status) is not RunStatus:
            raise TypeError("status must be RunStatus")
        if self.wait_reason is not None and type(self.wait_reason) is not WaitReason:
            raise TypeError("wait_reason must be WaitReason or None")
        if self.status is RunStatus.WAITING and self.wait_reason is None:
            raise ValueError("waiting run read model requires a wait reason")
        if self.status is not RunStatus.WAITING and self.wait_reason is not None:
            raise ValueError("wait reason is only valid for a waiting run read model")
        require_non_negative(self.aggregate_version, "aggregate_version")
        if self.result is not None and type(self.result) is not AgentResult:
            raise TypeError("result must be AgentResult or None")
        if self.result is not None and type(self.result.content) is not str:
            raise TypeError("result content must be str")
        if self.status is RunStatus.COMPLETED and self.result is None:
            raise ValueError("completed run read model requires a result")
        if self.status is not RunStatus.COMPLETED and self.result is not None:
            raise ValueError("non-completed run read model cannot contain a result")


def canonical_submission_digest(
    scope: RequestScope,
    submission: RunSubmission,
) -> str:
    """Return the v1 canonical SHA-256 digest used for submission idempotency."""

    if type(scope) is not RequestScope:
        raise TypeError("scope must be RequestScope")
    if type(submission) is not RunSubmission:
        raise TypeError("submission must be RunSubmission")
    payload = {
        "version": 1,
        "tenant_id": scope.tenant_id,
        "session_id": submission.session_id,
        "content": submission.content,
        "artifact_handles": list(submission.artifact_handles),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class ArtifactContent:
    """Artifact read 返回的 metadata 与不可变原始内容。"""

    record: ArtifactRecord
    data: bytes

    def __post_init__(self) -> None:
        if type(self.record) is not ArtifactRecord:
            raise TypeError("record must be ArtifactRecord")
        if type(self.data) is not bytes:
            raise TypeError("data must be bytes")
        if len(self.data) != self.record.size_bytes:
            raise ValueError("artifact data size does not match metadata")
