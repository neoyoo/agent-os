import asyncio

from agentos.channels import InMemorySseEventBuffer


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
