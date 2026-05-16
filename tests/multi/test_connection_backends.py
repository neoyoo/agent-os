from __future__ import annotations

import pytest

from agentos.multi.postgres_tasks import PostgresTaskStore
from agentos.multi.redis_queue import RedisAgentMessageQueue
from agentos.persistence.postgres import BackendUnavailableError, PostgresDurableSessionStore


class FailingConnection:
    def execute(self, sql: str, params: tuple[object, ...] = ()) -> object:
        raise OSError("connection reset")


def test_postgres_task_store_wraps_connection_errors() -> None:
    store = PostgresTaskStore("postgres://example", connection=FailingConnection())

    with pytest.raises(BackendUnavailableError, match="Postgres backend unavailable"):
        store.get("task_1")


def test_postgres_session_store_wraps_connection_errors() -> None:
    store = PostgresDurableSessionStore("postgres://example", connection=FailingConnection())

    with pytest.raises(BackendUnavailableError, match="Postgres backend unavailable"):
        store.load_session("session_1")


def test_redis_queue_uses_connection_pool_when_constructed_from_url() -> None:
    queue = RedisAgentMessageQueue(
        "redis://localhost/0",
        client=object(),
    )

    assert queue.backend_url == "redis://localhost/0"
