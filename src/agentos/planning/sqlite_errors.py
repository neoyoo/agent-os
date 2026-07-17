from agentos.planning.errors import PlanError


class SQLitePlanStoreClosedError(PlanError):
    """SQLitePlanStore 已关闭。"""


class SQLitePlanStoreCorruptedError(PlanError):
    """SQLite Plan schema 或记录无法通过严格校验。"""


class SQLitePlanStoreUnsafeError(PlanError):
    """Plan 包含 Durable Store 禁止持久化的数据。"""


__all__ = [
    "SQLitePlanStoreClosedError",
    "SQLitePlanStoreCorruptedError",
    "SQLitePlanStoreUnsafeError",
]
