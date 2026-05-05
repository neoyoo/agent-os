from __future__ import annotations


class PostgresDurableSessionStore:
    """Postgres-backed DurableSessionStore adapter boundary."""

    def __init__(self, dsn: str, connection: object | None = None) -> None:
        """创建 Postgres durable store；未安装 postgres extra 时给出清晰错误。"""

        if connection is not None:
            self._connection = connection
            self._dsn = dsn
            return
        try:
            import psycopg
        except ImportError as error:
            raise RuntimeError(
                "PostgresDurableSessionStore requires the optional dependency "
                "`agentos[postgres]`.",
            ) from error
        self._connection = psycopg.connect(dsn)
        self._dsn = dsn

    def __getattr__(self, name: str) -> object:
        """第一版只固定构造边界，具体 Postgres schema 后续实现。"""

        raise NotImplementedError(
            f"PostgresDurableSessionStore.{name} is not implemented in this phase",
        )
