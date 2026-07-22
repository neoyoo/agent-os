from __future__ import annotations


class TeamError(RuntimeError):
    """Team Domain 的稳定基础错误。"""


class TeamNotFoundError(TeamError):
    """Team 不存在、已删除或不属于当前 tenant。"""

    def __init__(self) -> None:
        super().__init__("team was not found")


class TeamMembershipError(TeamError, PermissionError):
    """调用方不是满足当前操作要求的 active member。"""

    def __init__(self) -> None:
        super().__init__("team membership does not allow this operation")


class TeamConflictError(TeamError):
    """Team operation identity 或当前状态发生冲突。"""

    def __init__(self) -> None:
        super().__init__("team operation conflicts with existing state")


class TeamCursorError(TeamError):
    """Team message cursor 不属于当前可见历史。"""

    def __init__(self) -> None:
        super().__init__("team message cursor is invalid")


class TeamBoundaryError(TeamError, PermissionError):
    """成员请求扩大了 Team 的 workspace 或 capability 边界。"""

    def __init__(self) -> None:
        super().__init__("team member boundary cannot be broadened")


class TeamToolAuthorizationError(TeamError, PermissionError):
    """Team Tool 未通过显式授权策略。"""

    def __init__(self) -> None:
        super().__init__("team tool requires explicit authorization")


class TeamToolResultTooLargeError(TeamError):
    """Team Tool 的模型可见结果超过固定资源预算。"""

    def __init__(self) -> None:
        super().__init__("team tool result exceeds the size limit")


class StaleTeamDeliveryClaimError(TeamError):
    """Team delivery claim 已过期或 fencing token 已失效。"""

    def __init__(self) -> None:
        super().__init__("team delivery claim is stale")


class TeamActiveRunConflictError(TeamConflictError):
    """Active Run 阻止 Team 或 member binding 删除。"""

    def __init__(self) -> None:
        super().__init__()


__all__ = [
    "StaleTeamDeliveryClaimError",
    "TeamActiveRunConflictError",
    "TeamBoundaryError",
    "TeamConflictError",
    "TeamCursorError",
    "TeamError",
    "TeamMembershipError",
    "TeamNotFoundError",
    "TeamToolAuthorizationError",
    "TeamToolResultTooLargeError",
]
