from __future__ import annotations

from types import SimpleNamespace

import pytest

from agentos.context import CompressedSegment
from agentos.memory import CompressedSegmentPackage, HotSessionState, SegmentRecallDocument
from agentos.messages import Message, MessageRef, ToolCall
from agentos.runtime.session import SessionState


class FakeRedisClient:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.expires: list[tuple[str, int]] = []
        self.getdel_calls: list[str] = []
        self.pipeline_executions = 0
        self.pipeline_read_executions = 0

    def pipeline(self) -> "FakeRedisPipeline":
        return FakeRedisPipeline(self)

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.values[key] = value
        if ex is not None:
            self.expires.append((key, ex))

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def getdel(self, key: str) -> str | None:
        self.getdel_calls.append(key)
        return self.values.pop(key, None)

    def hset(self, key: str, field: str, value: str) -> None:
        self.hashes.setdefault(key, {})[field] = value

    def hget(self, key: str, field: str) -> str | None:
        return self.hashes.get(key, {}).get(field)

    def hmget(self, key: str, fields: list[str]) -> list[str | None]:
        values = self.hashes.get(key, {})
        return [values.get(field) for field in fields]

    def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.hashes.get(key, {}))

    def delete(self, key: str) -> None:
        self.values.pop(key, None)
        self.hashes.pop(key, None)

    def expire(self, key: str, ttl: int) -> None:
        self.expires.append((key, ttl))


class FakeRedisPipeline:
    def __init__(self, client: FakeRedisClient) -> None:
        self.client = client
        self.commands: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def set(self, *args: object, **kwargs: object) -> "FakeRedisPipeline":
        self.commands.append(("set", args, kwargs))
        return self

    def hset(self, *args: object, **kwargs: object) -> "FakeRedisPipeline":
        self.commands.append(("hset", args, kwargs))
        return self

    def get(self, *args: object, **kwargs: object) -> "FakeRedisPipeline":
        self.commands.append(("get", args, kwargs))
        return self

    def hgetall(self, *args: object, **kwargs: object) -> "FakeRedisPipeline":
        self.commands.append(("hgetall", args, kwargs))
        return self

    def expire(self, *args: object, **kwargs: object) -> "FakeRedisPipeline":
        self.commands.append(("expire", args, kwargs))
        return self

    def execute(self) -> list[object]:
        self.client.pipeline_executions += 1
        if any(name in {"get", "hgetall"} for name, _, _ in self.commands):
            self.client.pipeline_read_executions += 1
        results: list[object] = []
        for name, args, kwargs in self.commands:
            results.append(getattr(self.client, name)(*args, **kwargs))
        return results


def test_redis_hot_session_store_round_trips_hot_state_and_refs() -> None:
    from agentos.memory.redis_store import RedisHotSessionStore

    client = FakeRedisClient()
    store = RedisHotSessionStore(
        url="redis://unused",
        client=client,
        key_prefix="test",
        ttl_seconds=60,
    )
    message = Message(
        id="msg_1",
        role="assistant",
        content="done",
        tool_calls=[ToolCall(id="call_1", name="read_file", arguments={"path": "pyproject.toml"})],
    )
    state = HotSessionState(
        session_id="session_1",
        active_refs=(MessageRef("msg_1"),),
        recent_messages=(message,),
        temporary_recalled_refs=("msg_1",),
        segment_refs={"seg_1": ("msg_1",)},
        metadata={"profile": "web"},
    )

    store.save_hot_state(state)

    loaded = store.load_hot_state("session_1")
    assert loaded == state
    assert store.get_hot_messages("session_1", ["msg_1"]) == [message]
    assert store.get_hot_messages("session_1", ["missing"]) is None
    assert store.get_segment_refs("session_1", "seg_1") == ("msg_1",)
    assert store.consume_temporary_recalled_refs("session_1") == ("msg_1",)
    assert store.consume_temporary_recalled_refs("session_1") == ()
    assert client.getdel_calls == [
        "test:hot:session_1:temporary_recalled_refs",
        "test:hot:session_1:temporary_recalled_refs",
    ]
    assert client.pipeline_executions >= 1
    assert ("test:hot:session_1:state", 60) in client.expires


def test_redis_hot_session_store_batches_hot_state_reads() -> None:
    from agentos.memory.redis_store import RedisHotSessionStore

    client = FakeRedisClient()
    store = RedisHotSessionStore(url="redis://unused", client=client, ttl_seconds=60)
    state = HotSessionState(
        session_id="session_1",
        active_refs=(MessageRef("msg_1"),),
        segment_refs={"seg_1": ("msg_1",)},
        temporary_recalled_refs=("msg_1",),
    )
    store.save_hot_state(state)
    client.pipeline_executions = 0
    client.pipeline_read_executions = 0

    assert store.load_hot_state("session_1") == state

    assert client.pipeline_read_executions == 1


def test_hot_state_serializer_restores_segment_refs_as_tuples() -> None:
    from agentos.memory.serializers import hot_state_from_dict, hot_state_to_dict

    state = HotSessionState(
        session_id="session_1",
        segment_refs={"seg_1": ("msg_1", "msg_2")},
    )

    restored = hot_state_from_dict(hot_state_to_dict(state))

    assert restored.segment_refs["seg_1"] == ("msg_1", "msg_2")
    assert isinstance(restored.segment_refs["seg_1"], tuple)


def test_redis_hot_session_store_requires_atomic_consume() -> None:
    from agentos.memory.redis_store import RedisHotSessionStore

    class NonAtomicClient(FakeRedisClient):
        getdel = None  # type: ignore[assignment]
        eval = None  # type: ignore[assignment]

    store = RedisHotSessionStore(url="redis://unused", client=NonAtomicClient())

    with pytest.raises(RuntimeError, match="atomic consume"):
        store.consume_temporary_recalled_refs("session_1")


class FakePostgresCursor:
    def __init__(self, rows: list[tuple[object, ...]] | None = None) -> None:
        self._rows = rows or []

    def fetchone(self) -> tuple[object, ...] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple[object, ...]]:
        return list(self._rows)


class FakePostgresConnection:
    def __init__(self) -> None:
        self.sessions: dict[str, tuple[str, int]] = {}
        self.messages: dict[tuple[str, str], str] = {}
        self.active_refs: dict[str, str] = {}
        self.packages: dict[tuple[str, str], tuple[str, str]] = {}
        self.ddl_calls: list[str] = []
        self.message_selects: list[str] = []
        self.commits = 0

    def execute(
        self,
        sql: str,
        params: tuple[object, ...] | None = None,
    ) -> FakePostgresCursor:
        params = params or ()
        if "CREATE TABLE" in sql:
            self.ddl_calls.append(sql)
            return FakePostgresCursor()
        if "INSERT INTO agentos_sessions" in sql:
            self.sessions[str(params[0])] = (str(params[1]), int(params[2]))
            return FakePostgresCursor()
        if "SELECT status, next_turn_number FROM agentos_sessions" in sql:
            row = self.sessions.get(str(params[0]))
            return FakePostgresCursor([row] if row else [])
        if "INSERT INTO agentos_messages" in sql:
            self.messages[(str(params[0]), str(params[1]))] = str(params[2])
            return FakePostgresCursor()
        if "message_id = ANY" in sql:
            self.message_selects.append(sql)
            session_id = str(params[0])
            message_ids = [str(message_id) for message_id in params[1]]
            rows = [
                (message_id, self.messages[(session_id, message_id)])
                for message_id in message_ids
                if (session_id, message_id) in self.messages
            ]
            return FakePostgresCursor(rows)
        if "SELECT payload FROM agentos_messages" in sql:
            self.message_selects.append(sql)
            row = self.messages.get((str(params[0]), str(params[1])))
            return FakePostgresCursor([(row,)] if row else [])
        if "INSERT INTO agentos_active_refs" in sql:
            self.active_refs[str(params[0])] = str(params[1])
            return FakePostgresCursor()
        if "SELECT refs FROM agentos_active_refs" in sql:
            row = self.active_refs.get(str(params[0]))
            return FakePostgresCursor([(row,)] if row else [])
        if "INSERT INTO agentos_compressed_segments" in sql:
            self.packages[(str(params[0]), str(params[1]))] = (str(params[2]), str(params[3]))
            return FakePostgresCursor()
        if "SELECT source_refs FROM agentos_compressed_segments" in sql:
            row = self.packages.get((str(params[0]), str(params[1])))
            return FakePostgresCursor([(row[1],)] if row else [])
        if "SELECT package FROM agentos_compressed_segments" in sql:
            rows = [
                (package,)
                for (session_id, _), (package, _) in self.packages.items()
                if session_id == str(params[0])
            ]
            return FakePostgresCursor(rows)
        raise AssertionError(f"unexpected SQL: {sql}")

    def commit(self) -> None:
        self.commits += 1


def build_package() -> CompressedSegmentPackage:
    segment = CompressedSegment(id="seg_1", topic="project metadata", summary="agent-os")
    return CompressedSegmentPackage(
        segment=segment,
        source_refs=("msg_1", "msg_2"),
        recall_document=SegmentRecallDocument(
            session_id="session_1",
            segment_id="seg_1",
            topic=segment.topic,
            summary=segment.summary,
            keywords=("pyproject.toml",),
        ),
    )


def test_postgres_durable_session_store_round_trips_protocol_state() -> None:
    from agentos.persistence.postgres import PostgresDurableSessionStore

    connection = FakePostgresConnection()
    store = PostgresDurableSessionStore(dsn="postgresql://unused", connection=connection)
    session = SessionState(id="session_1")
    session.start()
    session.new_turn("hello")
    messages = [
        Message(id="msg_1", role="user", content="hello"),
        Message(id="msg_2", role="assistant", content="world"),
    ]

    store.save_session(session)
    for message in messages:
        store.append_message("session_1", message)
    store.save_active_refs("session_1", (MessageRef("msg_2"),))
    store.save_compressed_segment("session_1", build_package())

    assert store.load_session("session_1").next_turn_number() == session.next_turn_number()
    assert store.get_messages("session_1", ["msg_2", "msg_1"]) == [messages[1], messages[0]]
    assert len(connection.message_selects) == 1
    assert "message_id = ANY" in connection.message_selects[0]
    assert store.load_active_refs("session_1") == (MessageRef("msg_2"),)
    assert store.get_segment_refs("session_1", "seg_1") == ("msg_1", "msg_2")
    assert store.list_compressed_segments("session_1") == (build_package().segment,)
    assert connection.commits > 0


def test_postgres_durable_session_store_does_not_create_tables_at_runtime() -> None:
    from agentos.persistence.postgres import PostgresDurableSessionStore

    connection = FakePostgresConnection()

    PostgresDurableSessionStore(dsn="postgresql://unused", connection=connection)

    assert connection.ddl_calls == []


def test_postgres_durable_session_store_reports_missing_message_from_batch() -> None:
    from agentos.persistence.postgres import PostgresDurableSessionStore

    connection = FakePostgresConnection()
    store = PostgresDurableSessionStore(dsn="postgresql://unused", connection=connection)
    store.append_message("session_1", Message(id="msg_1", role="user", content="hello"))

    with pytest.raises(KeyError) as error:
        store.get_messages("session_1", ["msg_1", "missing"])

    assert error.value.args == ("missing",)
    assert len(connection.message_selects) == 1


class FakeEmbeddingProvider:
    def __init__(self) -> None:
        self.texts: list[str] = []

    def embed_text(self, text: str) -> list[float]:
        self.texts.append(text)
        return [float(len(text)), 1.0]


class FakeQdrantClient:
    def __init__(self) -> None:
        self.points: list[dict[str, object]] = []
        self.deleted_filters: list[dict[str, object]] = []

    def upsert(self, collection_name: str, points: list[dict[str, object]]) -> None:
        self.points.extend(points)

    def search(
        self,
        collection_name: str,
        query_vector: list[float],
        query_filter: dict[str, object],
        limit: int,
    ) -> list[SimpleNamespace]:
        session_id = query_filter["must"][0]["match"]["value"]  # type: ignore[index]
        results = [
            SimpleNamespace(payload=point["payload"], score=0.7)
            for point in self.points
            if point["payload"]["session_id"] == session_id  # type: ignore[index]
        ]
        return results[:limit]

    def delete(self, collection_name: str, points_selector: dict[str, object]) -> None:
        self.deleted_filters.append(points_selector)
        session_id = points_selector["filter"]["must"][0]["match"]["value"]  # type: ignore[index]
        self.points = [
            point
            for point in self.points
            if point["payload"]["session_id"] != session_id  # type: ignore[index]
        ]


def test_qdrant_recall_index_indexes_searches_and_deletes_by_session() -> None:
    from agentos.memory.qdrant_index import QdrantRecallIndex

    client = FakeQdrantClient()
    embeddings = FakeEmbeddingProvider()
    index = QdrantRecallIndex(
        url="http://unused",
        collection_name="agentos-recall",
        embedding_provider=embeddings,
        client=client,
    )
    document = build_package().recall_document

    index.index_segment(document)
    candidates = index.search_segments("session_1", "pyproject", limit=1)
    index.delete_session("session_1")

    assert candidates[0].segment_id == "seg_1"
    assert candidates[0].score == 0.7
    assert document.to_text() in embeddings.texts
    assert "pyproject" in embeddings.texts
    assert client.points == []
