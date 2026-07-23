"""Durable Profile 与 SQLite 参考 Adapter。"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentos.durable.profile import DurableRuntimeProfile
    from agentos.durable.sqlite_store import SQLiteDurableStore


def __getattr__(name: str) -> object:
    """惰性导出 Durable Adapter，保持序列化等领域模块独立。"""

    if name == "DurableRuntimeProfile":
        from agentos.durable.profile import DurableRuntimeProfile

        return DurableRuntimeProfile
    if name == "SQLiteDurableStore":
        from agentos.durable.sqlite_store import SQLiteDurableStore

        return SQLiteDurableStore
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["DurableRuntimeProfile", "SQLiteDurableStore"]
