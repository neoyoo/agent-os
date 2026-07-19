from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True, slots=True)
class RequestScope:
    """由已鉴权入口注入的 tenant 与 principal identity。"""

    tenant_id: str
    principal_id: str

    def __post_init__(self) -> None:
        _require_identifier(self.tenant_id, "tenant_id")
        _require_identifier(self.principal_id, "principal_id")


@dataclass(frozen=True, slots=True)
class RunSubmission:
    """首次 Run 的幂等持久提交。"""

    session_id: str
    submission_id: str
    content: str
    artifact_handles: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_identifier(self.session_id, "session_id")
        _require_identifier(self.submission_id, "submission_id")
        if type(self.content) is not str:
            raise TypeError("content must be str")
        if type(self.artifact_handles) is str:
            raise TypeError("artifact_handles must contain str values")
        handles = tuple(self.artifact_handles)
        if any(type(handle) is not str for handle in handles):
            raise TypeError("artifact_handles must contain str values")
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
        _require_identifier(self.session_id, "session_id")
        _require_identifier(self.run_id, "run_id")
        _require_identifier(self.submission_id, "submission_id")
        _require_non_negative_version(self.aggregate_version)
        if type(self.duplicate) is not bool:
            raise TypeError("duplicate must be bool")


@dataclass(frozen=True, slots=True)
class ExecutionClaim:
    """PostgreSQL 激活并以数据库时间约束的 Worker execution claim。"""

    tenant_id: str
    session_id: str
    run_id: str
    owner_id: str
    claim_id: str
    fencing_token: int
    expires_at: datetime

    def __post_init__(self) -> None:
        _require_identifier(self.tenant_id, "tenant_id")
        _require_identifier(self.session_id, "session_id")
        _require_identifier(self.run_id, "run_id")
        _require_identifier(self.owner_id, "owner_id")
        _require_identifier(self.claim_id, "claim_id")
        if type(self.fencing_token) is not int or self.fencing_token <= 0:
            raise ValueError("fencing_token must be a positive integer")
        if not isinstance(self.expires_at, datetime):
            raise TypeError("expires_at must be datetime")
        if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
            raise ValueError("expires_at must be timezone-aware")
        object.__setattr__(self, "expires_at", self.expires_at.astimezone(UTC))


def _require_identifier(value: object, field_name: str) -> None:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{field_name} must not be empty")


def _require_non_negative_version(value: object) -> None:
    if type(value) is not int or value < 0:
        raise ValueError("aggregate_version must be a non-negative integer")


__all__ = [
    "ExecutionClaim",
    "RequestScope",
    "RunSubmission",
    "RunSubmissionReceipt",
]
