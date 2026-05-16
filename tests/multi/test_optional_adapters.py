import sys

import pytest


def test_postgres_task_store_reports_missing_optional_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agentos.multi.postgres_tasks import PostgresTaskStore

    monkeypatch.setitem(sys.modules, "psycopg", None)

    with pytest.raises(RuntimeError, match=r"agentos\[postgres\]"):
        PostgresTaskStore(dsn="postgresql://localhost/agentos")


def test_redis_agent_message_queue_reports_missing_optional_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agentos.multi.redis_queue import RedisAgentMessageQueue

    monkeypatch.setitem(sys.modules, "redis", None)

    with pytest.raises(RuntimeError, match=r"agentos\[redis\]"):
        RedisAgentMessageQueue(url="redis://localhost:6379/0")
