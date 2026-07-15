import sys

import pytest


def test_redis_adapter_reports_missing_optional_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    from agentos.persistence.redis_session import RedisHotSessionStore

    monkeypatch.setitem(sys.modules, "redis", None)

    with pytest.raises(RuntimeError, match=r"agentos\[redis\]"):
        RedisHotSessionStore(url="redis://localhost:6379/0")
def test_postgres_adapter_reports_missing_optional_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    from agentos.persistence.postgres import PostgresDurableSessionStore

    monkeypatch.setitem(sys.modules, "psycopg", None)

    with pytest.raises(RuntimeError, match=r"agentos\[postgres\]"):
        PostgresDurableSessionStore(dsn="postgresql://localhost/agentos")
