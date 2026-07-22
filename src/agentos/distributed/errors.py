class DistributedError(RuntimeError):
    """不暴露 Adapter 细节的稳定 Distributed 领域错误。"""

    code = "distributed_error"
    message = "distributed runtime error"

    def __init__(self) -> None:
        super().__init__(self.message)


class RunSubmissionConflictError(DistributedError):
    """同一 submission_id 对应不同 canonical input。"""

    code = "run_submission_conflict"
    message = "run submission conflicts with an existing request"


class RunNotFoundError(DistributedError):
    """当前 tenant/session 中不存在指定 Run。"""

    code = "run_not_found"
    message = "run not found"


class ActiveRunConflictError(DistributedError):
    """Session 已存在另一个非终态 Run。"""

    code = "active_run_conflict"
    message = "session already has an active run"


class ArtifactInUseError(DistributedError):
    """Artifact is pinned by state required for deterministic recovery."""

    code = "artifact_in_use"
    message = "artifact is referenced by durable session state"


class ArtifactConflictError(DistributedError):
    """表示 Artifact 操作标识与既有请求冲突。"""

    code = "artifact_conflict"
    message = "artifact operation conflicts with an existing request"


class CommandConflictError(DistributedError):
    """同一 command_id 对应不同的不可变命令。"""

    code = "command_conflict"
    message = "command conflicts with an existing request"


class CommandStateError(DistributedError):
    """命令不适用于 Run 当前的权威状态。"""

    code = "command_state"
    message = "command is invalid for the current run state"


class CommandNotDueError(DistributedError):
    """定时 Durable Command 尚未到权威数据库时间。"""

    code = "command_not_due"
    message = "command is not due"


class A2ATaskNotFoundError(DistributedError):
    """当前 tenant 中不存在指定 A2A task binding。"""

    code = "a2a_task_not_found"
    message = "a2a task not found"


class A2ATaskConflictError(DistributedError):
    """A2A task identity 已绑定到其他 Run。"""

    code = "a2a_task_conflict"
    message = "a2a task conflicts with an existing binding"


class A2APushConfigNotFoundError(DistributedError):
    """当前 tenant/task 中不存在指定 push config。"""

    code = "a2a_push_config_not_found"
    message = "a2a push config not found"


class A2APushConflictError(DistributedError):
    """Push operation identity 与已有 immutable input 冲突。"""

    code = "a2a_push_conflict"
    message = "a2a push config conflicts with an existing request"


class A2APushAttemptFencedError(DistributedError):
    """Push delivery attempt 已被其他 Worker 或 Delete fencing。"""

    code = "a2a_push_attempt_fenced"
    message = "a2a push delivery attempt is stale"


class A2APushDeliveryDeferredError(DistributedError):
    """Push delivery 尚未到重试时间或必须等待顺序前驱。"""

    code = "a2a_push_delivery_deferred"
    message = "a2a push delivery is deferred"


class ClaimConflictError(DistributedError):
    """Claim 与当前 PostgreSQL 状态冲突。"""

    code = "claim_conflict"
    message = "execution claim conflicts with current state"


class ClaimExpiredError(DistributedError):
    """Claim 已按数据库时间过期。"""

    code = "claim_expired"
    message = "execution claim has expired"


class StaleFenceError(DistributedError):
    """Worker fencing token 已失效。"""

    code = "stale_fence"
    message = "execution fence is stale"


class CheckpointConflictError(DistributedError):
    """Checkpoint 写入与当前 Run 状态冲突。"""

    code = "checkpoint_conflict"
    message = "checkpoint conflicts with current run state"


class DeliveryUnavailableError(DistributedError):
    """Queue 或 delivery backend 当前不可用。"""

    code = "delivery_unavailable"
    message = "delivery backend is unavailable"


class RunEventTooLargeError(DistributedError):
    """A live event cannot be represented within the frozen wire limit."""

    code = "run_event_too_large"
    message = "run event exceeds protocol size limit"


class SideEffectAmbiguousError(DistributedError):
    """外部副作用结果无法安全确定。"""

    code = "side_effect_ambiguous"
    message = "side effect outcome is ambiguous"


class SideEffectInFlightError(DistributedError):
    """副作用仍在执行，当前不能安全取消。"""

    code = "side_effect_in_flight"
    message = "side effect is still in flight"


class DistributedStoreClosedError(DistributedError):
    """Distributed Store 已关闭。"""

    code = "distributed_store_closed"
    message = "distributed store is closed"


class DistributedBackendUnavailableError(DistributedError):
    """权威 Distributed backend 当前不可用。"""

    code = "distributed_backend_unavailable"
    message = "distributed backend is unavailable"


class SchemaMigrationRequiredError(DistributedError):
    """表示数据库尚未达到 SDK 要求的精确 schema 版本。"""

    code = "schema_migration_required"
    message = "database schema migration is required"


class LegacyDistributedSchemaError(DistributedError):
    """表示数据库仍包含不受支持的旧分布式 schema 标记。"""

    code = "legacy_distributed_schema"
    message = "legacy distributed schema must be rebuilt"


class MigrationChecksumMismatchError(DistributedError):
    """表示已记录迁移与 SDK canonical resource 的 checksum 不一致。"""

    code = "migration_checksum_mismatch"
    message = "database migration checksum does not match the SDK"


class MigrationVersionError(DistributedError):
    """表示迁移账本存在缺口、名称偏移或未来版本。"""

    code = "migration_version_invalid"
    message = "database migration history is invalid"


__all__ = [
    "A2APushAttemptFencedError",
    "A2APushConfigNotFoundError",
    "A2APushConflictError",
    "A2APushDeliveryDeferredError",
    "A2ATaskConflictError",
    "A2ATaskNotFoundError",
    "ActiveRunConflictError",
    "ArtifactConflictError",
    "ArtifactInUseError",
    "CheckpointConflictError",
    "ClaimConflictError",
    "ClaimExpiredError",
    "CommandConflictError",
    "CommandNotDueError",
    "CommandStateError",
    "DeliveryUnavailableError",
    "DistributedBackendUnavailableError",
    "DistributedError",
    "DistributedStoreClosedError",
    "LegacyDistributedSchemaError",
    "MigrationChecksumMismatchError",
    "MigrationVersionError",
    "RunSubmissionConflictError",
    "RunNotFoundError",
    "RunEventTooLargeError",
    "SideEffectAmbiguousError",
    "SideEffectInFlightError",
    "SchemaMigrationRequiredError",
    "StaleFenceError",
]
