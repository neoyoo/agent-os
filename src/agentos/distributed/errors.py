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


class CommandConflictError(DistributedError):
    """同一 command_id 对应不同的不可变命令。"""

    code = "command_conflict"
    message = "command conflicts with an existing request"


class CommandStateError(DistributedError):
    """命令不适用于 Run 当前的权威状态。"""

    code = "command_state"
    message = "command is invalid for the current run state"


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


__all__ = [
    "ActiveRunConflictError",
    "CheckpointConflictError",
    "ClaimConflictError",
    "ClaimExpiredError",
    "CommandConflictError",
    "CommandStateError",
    "DeliveryUnavailableError",
    "DistributedBackendUnavailableError",
    "DistributedError",
    "DistributedStoreClosedError",
    "RunSubmissionConflictError",
    "RunNotFoundError",
    "SideEffectAmbiguousError",
    "SideEffectInFlightError",
    "StaleFenceError",
]
