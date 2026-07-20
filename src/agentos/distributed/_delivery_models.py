from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from agentos._json_values import FrozenJsonObject, freeze_json_mapping
from agentos.distributed._core_models import RequestScope
from agentos.distributed._model_validation import (
    normalize_optional_utc,
    normalize_utc,
    require_identifier,
    require_non_negative,
    require_positive,
)
from agentos.runtime.execution import AcceptedTurnExecution, RestoreAcceptedTurn
from agentos.runtime.run_state import RunState, RunStatus


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
        require_identifier(self.tenant_id, "tenant_id")
        require_identifier(self.session_id, "session_id")
        require_identifier(self.run_id, "run_id")
        require_identifier(self.owner_id, "owner_id")
        require_identifier(self.claim_id, "claim_id")
        require_positive(self.fencing_token, "fencing_token")
        object.__setattr__(
            self,
            "expires_at",
            normalize_utc(self.expires_at, "expires_at"),
        )


@dataclass(frozen=True, slots=True)
class QueueDelivery:
    """Redis 投递标识与稳定 Outbox ID，不携带业务 scope。"""

    delivery_id: str
    outbox_id: str
    delivery_count: int

    def __post_init__(self) -> None:
        require_identifier(self.delivery_id, "delivery_id")
        require_identifier(self.outbox_id, "outbox_id")
        require_positive(self.delivery_count, "delivery_count")


@dataclass(frozen=True, slots=True)
class RunDeliveryTarget:
    """PostgreSQL 根据 Outbox ID 解析出的权威执行目标。"""

    scope: RequestScope
    outbox_id: str
    session_id: str
    run: RunState

    def __post_init__(self) -> None:
        if type(self.scope) is not RequestScope:
            raise TypeError("scope must be RequestScope")
        require_identifier(self.outbox_id, "outbox_id")
        require_identifier(self.session_id, "session_id")
        if type(self.run) is not RunState:
            raise TypeError("run must be RunState")
        if self.run.session_id != self.session_id:
            raise ValueError("run delivery target session does not match run")


@dataclass(frozen=True, slots=True)
class ClaimedExecution:
    """同一 Run 的 PostgreSQL Claim 与 canonical accepted execution。"""

    target: RunDeliveryTarget
    claim: ExecutionClaim
    execution: AcceptedTurnExecution

    def __post_init__(self) -> None:
        if type(self.target) is not RunDeliveryTarget:
            raise TypeError("target must be RunDeliveryTarget")
        if type(self.claim) is not ExecutionClaim:
            raise TypeError("claim must be ExecutionClaim")
        if type(self.execution) is not AcceptedTurnExecution:
            raise TypeError("execution must be AcceptedTurnExecution")
        if (
            self.claim.tenant_id != self.target.scope.tenant_id
            or self.claim.session_id != self.target.session_id
            or self.claim.run_id != self.target.run.run_id
        ):
            raise ValueError("claim does not match delivery target")
        if self.target.run.run_id != self.execution.input.run_id:
            raise ValueError("delivery target does not match accepted execution")
        if (
            self.execution.guard.claim_id != self.claim.claim_id
            or self.execution.guard.fencing_token != self.claim.fencing_token
        ):
            raise ValueError("execution guard does not match claim")
        if self.execution.guard.expected_version != self.target.run.aggregate_version:
            raise ValueError("execution guard version does not match delivery target")
        if (
            self.target.run.status not in {RunStatus.QUEUED, RunStatus.RUNNING}
            or (
                type(self.execution.preparation) is RestoreAcceptedTurn
                and self.target.run.status is not RunStatus.RUNNING
            )
        ):
            raise ValueError(
                "execution preparation does not match delivery target status",
            )


@dataclass(frozen=True, slots=True)
class SessionLease:
    """Redis Session Lease；它不授予 PostgreSQL 写权限。"""

    scope: RequestScope
    session_id: str
    owner_id: str
    lease_id: str
    expires_at: datetime

    def __post_init__(self) -> None:
        if type(self.scope) is not RequestScope:
            raise TypeError("scope must be RequestScope")
        require_identifier(self.session_id, "session_id")
        require_identifier(self.owner_id, "owner_id")
        require_identifier(self.lease_id, "lease_id")
        object.__setattr__(
            self,
            "expires_at",
            normalize_utc(self.expires_at, "expires_at"),
        )


@dataclass(frozen=True, slots=True, init=False)
class OutboxRecord:
    """PostgreSQL 保存的不可变 Outbox 事实与发布 metadata。"""

    scope: RequestScope
    outbox_id: str
    topic: str
    payload: FrozenJsonObject
    created_at: datetime
    publish_attempts: int
    last_publish_attempt_at: datetime | None
    published_at: datetime | None

    def __init__(
        self,
        *,
        scope: RequestScope,
        outbox_id: str,
        topic: str,
        payload: Mapping[str, object] | FrozenJsonObject,
        created_at: datetime,
        publish_attempts: int = 0,
        last_publish_attempt_at: datetime | None = None,
        published_at: datetime | None = None,
    ) -> None:
        if type(scope) is not RequestScope:
            raise TypeError("scope must be RequestScope")
        require_identifier(outbox_id, "outbox_id")
        require_identifier(topic, "topic")
        require_non_negative(publish_attempts, "publish_attempts")
        created = normalize_utc(created_at, "created_at")
        last_attempt = normalize_optional_utc(
            last_publish_attempt_at,
            "last_publish_attempt_at",
        )
        published = normalize_optional_utc(published_at, "published_at")
        if any(
            timestamp is not None and timestamp < created
            for timestamp in (last_attempt, published)
        ):
            raise ValueError("outbox attempt times must not precede created_at")
        if publish_attempts == 0 and (last_attempt is not None or published is not None):
            raise ValueError("zero publish attempts cannot contain attempt timestamps")
        if publish_attempts > 0 and last_attempt is None:
            raise ValueError("publish attempts require last_publish_attempt_at")
        if published is not None and last_attempt is not None and published < last_attempt:
            raise ValueError("published_at must not precede last_publish_attempt_at")
        object.__setattr__(self, "scope", scope)
        object.__setattr__(self, "outbox_id", outbox_id)
        object.__setattr__(self, "topic", topic)
        object.__setattr__(self, "payload", freeze_json_mapping(payload))
        object.__setattr__(self, "created_at", created)
        object.__setattr__(self, "publish_attempts", publish_attempts)
        object.__setattr__(self, "last_publish_attempt_at", last_attempt)
        object.__setattr__(self, "published_at", published)


@dataclass(frozen=True, slots=True)
class OutboxClaim:
    """Outbox Relay 在 PostgreSQL 中取得的短期发布权。"""

    record: OutboxRecord
    owner_id: str
    claim_id: str
    expires_at: datetime

    def __post_init__(self) -> None:
        if type(self.record) is not OutboxRecord:
            raise TypeError("record must be OutboxRecord")
        require_identifier(self.owner_id, "owner_id")
        require_identifier(self.claim_id, "claim_id")
        expires_at = normalize_utc(self.expires_at, "expires_at")
        if expires_at < self.record.created_at:
            raise ValueError("outbox claim expires_at precedes record creation")
        object.__setattr__(self, "expires_at", expires_at)
