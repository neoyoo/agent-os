import asyncio
from threading import Event as ThreadEvent

import pytest

from agentos.channels import InMemorySseEventBuffer, RedisSseEventBuffer, SseReplayWindow
from agentos.persistence import BackendUnavailableError


async def _event_loop_checkpoint() -> None:
    checkpoint = asyncio.Event()
    asyncio.get_running_loop().call_soon(checkpoint.set)
    await checkpoint.wait()


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


def test_in_memory_sse_buffer_describes_replay_window_gaps() -> None:
    async def run() -> None:
        buffer = InMemorySseEventBuffer(max_events_per_stream=1)

        assert await buffer.replay_window("session_1:turn_1") == SseReplayWindow(
            exists=False,
            terminal=False,
            first_sequence=None,
            last_sequence=None,
        )

        await buffer.append("session_1:turn_1", 1, "one")
        await buffer.append("session_1:turn_1", 2, "two")
        await buffer.mark_terminal("session_1:turn_1")

        window = await buffer.replay_window("session_1:turn_1")

        assert window == SseReplayWindow(
            exists=True,
            terminal=True,
            first_sequence=2,
            last_sequence=2,
        )
        assert window.has_gap_after(0)
        assert not window.has_gap_after(1)

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


def test_redis_sse_buffer_drop_waits_for_cancelled_append_worker() -> None:
    xadd_started = ThreadEvent()
    release_xadd = ThreadEvent()
    delete_selected = ThreadEvent()
    order: list[str] = []

    class BlockingRedis(FakeRedis):
        def xadd(
            self,
            name: str,
            fields: dict[str, str],
            maxlen: int | None = None,
            approximate: bool = True,
        ) -> str:
            xadd_started.set()
            release_xadd.wait()
            result = super().xadd(
                name,
                fields,
                maxlen=maxlen,
                approximate=approximate,
            )
            order.append("xadd_finished")
            return result

        @property
        def delete(self):
            delete_selected.set()

            def execute(name: str) -> int:
                order.append("delete")
                return super(BlockingRedis, self).delete(name)

            return execute

    async def run() -> None:
        client = BlockingRedis()
        buffer = RedisSseEventBuffer("redis://unused", client=client)
        append_task = asyncio.create_task(
            buffer.append("session_1:turn_1", 1, "one"),
        )
        loop = asyncio.get_running_loop()
        assert await loop.run_in_executor(None, xadd_started.wait, 5)
        append_task.cancel()
        drop_task = asyncio.create_task(buffer.drop("session_1:turn_1"))
        try:
            checkpoint = asyncio.Event()
            loop.call_soon(checkpoint.set)
            await checkpoint.wait()
            assert not append_task.done()
            assert not drop_task.done()
            assert not delete_selected.is_set()
        finally:
            release_xadd.set()

        with pytest.raises(asyncio.CancelledError):
            await append_task
        await drop_task
        assert order == ["xadd_finished", "delete"]
        assert "agentos:channels:sse:session_1:turn_1" not in client.streams

    try:
        asyncio.run(run())
    finally:
        release_xadd.set()


def test_redis_sse_buffer_drop_rejects_new_same_key_append_while_draining() -> None:
    first_xadd_started = ThreadEvent()
    release_first_xadd = ThreadEvent()
    late_xadd_started = ThreadEvent()

    class BlockingRedis(FakeRedis):
        def xadd(
            self,
            name: str,
            fields: dict[str, str],
            maxlen: int | None = None,
            approximate: bool = True,
        ) -> str:
            if fields.get("sequence") == "1":
                first_xadd_started.set()
                release_first_xadd.wait()
            else:
                late_xadd_started.set()
            return super().xadd(
                name,
                fields,
                maxlen=maxlen,
                approximate=approximate,
            )

    async def run() -> None:
        client = BlockingRedis()
        buffer = RedisSseEventBuffer("redis://unused", client=client)
        stream_key = "session_1:turn_1"
        redis_key = "agentos:channels:sse:session_1:turn_1"
        append_task = asyncio.create_task(buffer.append(stream_key, 1, "one"))
        loop = asyncio.get_running_loop()
        assert await loop.run_in_executor(None, first_xadd_started.wait, 5)
        append_task.cancel()

        drop_task = asyncio.create_task(buffer.drop(stream_key))
        await _event_loop_checkpoint()
        late_append_task = asyncio.create_task(buffer.append(stream_key, 2, "two"))
        await _event_loop_checkpoint()
        release_first_xadd.set()

        with pytest.raises(asyncio.CancelledError):
            await append_task
        await drop_task
        with pytest.raises(BackendUnavailableError):
            await late_append_task
        assert not late_xadd_started.is_set()
        assert redis_key not in client.streams

    try:
        asyncio.run(run())
    finally:
        release_first_xadd.set()


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


def test_redis_sse_buffer_describes_replay_window_gaps() -> None:
    async def run() -> None:
        buffer = RedisSseEventBuffer(
            "redis://unused",
            client=FakeRedis(),
            max_stream_length=1,
        )

        assert await buffer.replay_window("session_1:turn_1") == SseReplayWindow(
            exists=False,
            terminal=False,
            first_sequence=None,
            last_sequence=None,
        )

        await buffer.append("session_1:turn_1", 1, "one")
        await buffer.append("session_1:turn_1", 2, "two")
        await buffer.mark_terminal("session_1:turn_1")

        window = await buffer.replay_window("session_1:turn_1")

        assert window == SseReplayWindow(
            exists=True,
            terminal=True,
            first_sequence=2,
            last_sequence=2,
        )
        assert window.has_gap_after(0)
        assert not window.has_gap_after(1)

    asyncio.run(run())


def test_redis_sse_buffer_reports_missing_optional_dependency(monkeypatch) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "redis", None)

    with pytest.raises(RuntimeError, match=r"agentos\[redis\]"):
        RedisSseEventBuffer("redis://localhost:6379/0")
