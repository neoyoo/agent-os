import sys

import pytest


def test_redis_adapter_reports_missing_optional_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    from agentos.memory.redis_store import RedisHotSessionStore

    monkeypatch.setitem(sys.modules, "redis", None)

    with pytest.raises(RuntimeError, match=r"agentos\[redis\]"):
        RedisHotSessionStore(url="redis://localhost:6379/0")


def test_qdrant_adapter_reports_missing_optional_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    from agentos.memory.qdrant_index import QdrantRecallIndex

    monkeypatch.setitem(sys.modules, "qdrant_client", None)

    with pytest.raises(RuntimeError, match=r"agentos\[qdrant\]"):
        QdrantRecallIndex(
            url="http://localhost:6333",
            collection_name="agentos-recall",
            embedding_provider=object(),
        )


def test_postgres_adapter_reports_missing_optional_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    from agentos.persistence.postgres import PostgresDurableSessionStore

    monkeypatch.setitem(sys.modules, "psycopg", None)

    with pytest.raises(RuntimeError, match=r"agentos\[postgres\]"):
        PostgresDurableSessionStore(dsn="postgresql://localhost/agentos")
