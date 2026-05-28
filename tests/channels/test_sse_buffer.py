import asyncio

import pytest

from agentos.channels import InMemorySseEventBuffer, RedisSseEventBuffer


class FakeRedis:
    def __init__(self) -> None:
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self.expires: list[tuple[str, int]] = []
        self.deleted: list[str] = []

    def xadd(
        self,
        name: str,
        fields: dict[str, str],
        maxlen: int | None = None,
        approximate: bool = True,
    ) -> str:
        stream = self.streams.setdefault(name, [])
        message_id = f"{len(stream) + 1}-0"
        stream.append((message_id, fields))
        if maxlen is not None:
            del stream[: max(0, len(stream) - maxlen)]
        return message_id

    def xrange(
        self,
        name: str,
        min: str = "-",
        max: str = "+",
        count: int | None = None,
    ) -> list[tuple[str, dict[str, str]]]:
        messages = list(self.streams.get(name, []))
        if count is not None:
            return messages[:count]
        return messages

    def xread(
        self,
        streams: dict[str, str],
        count: int = 100,
        block: int | None = None,
    ) -> list[tuple[str, list[tuple[str, dict[str, str]]]]]:
        result = []
        for name, last_id in streams.items():
            messages = [
                message
                for message in self.streams.get(name, [])
                if self._id_gt(message[0], last_id)
            ]
            if messages:
                result.append((name, messages[:count]))
        return result

    def expire(self, name: str, seconds: int) -> bool:
        self.expires.append((name, seconds))
        return True

    def delete(self, name: str) -> int:
        self.deleted.append(name)
        self.streams.pop(name, None)
        return 1

    def _id_gt(self, left: str, right: str) -> bool:
        left_ms, left_seq = left.split("-", 1)
        right_ms, right_seq = right.split("-", 1)
        return (int(left_ms), int(left_seq)) > (int(right_ms), int(right_seq))


def test_in_memory_sse_buffer_replays_events_after_sequence() -> None:
    async def run() -> None:
        buffer = InMemorySseEventBuffer()
        await buffer.append("session_1:turn_1", 1, "one")
        await buffer.append("session_1:turn_1", 2, "two")

        assert await buffer.replay_since("session_1:turn_1", 1) == [(2, "two")]

    asyncio.run(run())


def test_in_memory_sse_buffer_follow_yields_new_events_and_stops_on_terminal() -> None:
    async def run() -> None:
        buffer = InMemorySseEventBuffer()
        received: list[tuple[int, str]] = []

        async def follow() -> None:
            async for event in buffer.follow("session_1:turn_1", 0):
                received.append(event)

        task = asyncio.create_task(follow())
        await buffer.append("session_1:turn_1", 1, "one")
        await asyncio.sleep(0)
        await buffer.mark_terminal("session_1:turn_1")
        await task

        assert received == [(1, "one")]

    asyncio.run(run())


def test_in_memory_sse_buffer_bounds_old_events() -> None:
    async def run() -> None:
        buffer = InMemorySseEventBuffer(max_events_per_stream=1)
        await buffer.append("session_1:turn_1", 1, "one")
        await buffer.append("session_1:turn_1", 2, "two")

        assert await buffer.replay_since("session_1:turn_1", 0) == [(2, "two")]

    asyncio.run(run())


def test_redis_sse_buffer_replays_events_after_sequence() -> None:
    async def run() -> None:
        client = FakeRedis()
        buffer = RedisSseEventBuffer("redis://unused", client=client)
        await buffer.append("session_1:turn_1", 1, "one")
        await buffer.append("session_1:turn_1", 2, "two")

        assert await buffer.replay_since("session_1:turn_1", 1) == [(2, "two")]
        assert "agentos:channels:sse:session_1:turn_1" in client.streams

    asyncio.run(run())


def test_redis_sse_buffer_follow_yields_new_events_and_stops_on_terminal() -> None:
    async def run() -> None:
        buffer = RedisSseEventBuffer(
            "redis://unused",
            client=FakeRedis(),
            xread_block_ms=1,
        )
        received: list[tuple[int, str]] = []

        async def follow() -> None:
            async for event in buffer.follow("session_1:turn_1", 0):
                received.append(event)

        task = asyncio.create_task(follow())
        await buffer.append("session_1:turn_1", 1, "one")
        await asyncio.sleep(0.01)
        await buffer.mark_terminal("session_1:turn_1")
        await task

        assert received == [(1, "one")]

    asyncio.run(run())


def test_redis_sse_buffer_drop_deletes_stream() -> None:
    async def run() -> None:
        client = FakeRedis()
        buffer = RedisSseEventBuffer("redis://unused", client=client)
        await buffer.append("session_1:turn_1", 1, "one")
        await buffer.drop("session_1:turn_1")

        assert client.deleted == ["agentos:channels:sse:session_1:turn_1"]

    asyncio.run(run())


def test_redis_sse_buffer_terminal_marker_does_not_evict_last_event() -> None:
    async def run() -> None:
        buffer = RedisSseEventBuffer(
            "redis://unused",
            client=FakeRedis(),
            max_stream_length=1,
        )
        await buffer.append("session_1:turn_1", 1, "one")
        await buffer.mark_terminal("session_1:turn_1")

        assert await buffer.replay_since("session_1:turn_1", 0) == [(1, "one")]

    asyncio.run(run())


def test_redis_sse_buffer_reports_missing_optional_dependency(monkeypatch) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "redis", None)

    with pytest.raises(RuntimeError, match=r"agentos\[redis\]"):
        RedisSseEventBuffer("redis://localhost:6379/0")
