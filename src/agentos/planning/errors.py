class PlanError(RuntimeError):
    """Planner 基础错误。"""


class PlanNotFoundError(PlanError):
    """Plan 不存在。"""


class PlanStepNotFoundError(PlanError):
    """Plan Step 不存在。"""


class PlanClaimLostError(PlanError):
    """Scheduler 在保存前失去精确 Plan Claim。"""


class PlanConflictError(PlanError):
    """Plan 在读取后发生变化，调用方必须重新加载。"""


class PlannerToolAuthorizationError(PlanError, PermissionError):
    """Planner Tool 授权策略拒绝模型可调用操作。"""
