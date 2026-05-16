from __future__ import annotations

from typing import Protocol


class PostgresCursor(Protocol):
    """Postgres cursor 的最小类型边界。"""

    def fetchone(self) -> tuple[object, ...] | None:
        """读取一行。"""

    def fetchall(self) -> list[tuple[object, ...]]:
        """读取全部行。"""


class PostgresConnection(Protocol):
    """Postgres connection 的最小执行边界。"""

    def execute(
        self,
        sql: str,
        params: tuple[object, ...] = (),
    ) -> PostgresCursor:
        """执行 SQL 并返回 cursor。"""
