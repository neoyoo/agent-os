class MemoryStoreError(RuntimeError):
    """Memory Store 稳定错误基类。"""


class SQLiteMemoryStoreClosedError(MemoryStoreError):
    """SQLiteMemoryStore 已关闭。"""


class SQLiteMemoryStoreCorruptedError(MemoryStoreError):
    """SQLite Memory schema 或记录无法通过严格校验。"""


__all__ = [
    "MemoryStoreError",
    "SQLiteMemoryStoreClosedError",
    "SQLiteMemoryStoreCorruptedError",
]
