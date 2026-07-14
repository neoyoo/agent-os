import asyncio
from collections.abc import Iterator
from contextlib import suppress
import threading

import pytest

from agentos.runtime._async_bridge import SyncIteratorAsyncBridge


def test_iterator_advances_only_for_explicit_async_demand() -> None:
    async def run() -> None:
        second_next_started = threading.Event()
        first_next_started = threading.Event()
        iterator_closed = threading.Event()
        release_second_next = threading.Event()

        class DemandObservedIterator:
            def __init__(self) -> None:
                self.index = 0

            def __iter__(self) -> Iterator[str]:
                return self

            def __next__(self) -> str:
                self.index += 1
                if self.index == 1:
                    first_next_started.set()
                    return "first"
                second_next_started.set()
                release_second_next.wait()
                raise StopIteration

            def close(self) -> None:
                iterator_closed.set()

        bridge = SyncIteratorAsyncBridge(DemandObservedIterator)
        try:
            bridge.__aiter__()
            assert await asyncio.to_thread(bridge._waiting_for_demand.wait, 2)
            assert not first_next_started.is_set()

            assert await anext(bridge) == "first"
            assert await asyncio.to_thread(bridge._waiting_for_demand.wait, 2)
            assert not second_next_started.is_set()

            second_pull = asyncio.create_task(anext(bridge))
            assert await asyncio.to_thread(second_next_started.wait, 2)
            release_second_next.set()
            with pytest.raises(StopAsyncIteration):
                await second_pull
        finally:
            release_second_next.set()
            await bridge.aclose()

        assert iterator_closed.is_set()

    asyncio.run(run())


def test_aclose_preserves_pending_cancels_under_nested_taskgroup() -> None:
    async def run() -> tuple[int, int, list[int]]:
        result: asyncio.Future[tuple[int, int, list[int]]] = asyncio.Future()

        async def worker() -> None:
            task = asyncio.current_task()
            assert task is not None
            bridge: SyncIteratorAsyncBridge[object] = SyncIteratorAsyncBridge(
                lambda: iter(()),
            )
            seen_cancels_during_close: list[int] = []

            async def fake_aclose() -> None:
                seen_cancels_during_close.append(task.cancelling())

            bridge.aclose = fake_aclose  # type: ignore[method-assign]
            task.cancel()
            task.cancel()
            before = task.cancelling()
            await bridge._aclose_from_cancelled_task()
            after = task.cancelling()
            result.set_result((before, after, seen_cancels_during_close))

        task = asyncio.create_task(worker())
        try:
            await task
        except asyncio.CancelledError:
            pass
        return result.result()

    before, after, seen_cancels_during_close = asyncio.run(run())

    assert before == 2
    assert after == 2
    assert seen_cancels_during_close == [0]


def test_double_cancel_waits_for_blocked_iterator_cleanup() -> None:
    async def run() -> None:
        worker_blocked = threading.Event()
        release_worker = threading.Event()
        iterator_closed = threading.Event()
        cleanup_started = asyncio.Event()

        class BlockingIterator:
            def __init__(self) -> None:
                self.index = 0

            def __iter__(self) -> Iterator[object]:
                return self

            def __next__(self) -> object:
                self.index += 1
                if self.index == 1:
                    return object()
                worker_blocked.set()
                release_worker.wait()
                raise StopIteration

            def close(self) -> None:
                iterator_closed.set()

        bridge: SyncIteratorAsyncBridge[object] = SyncIteratorAsyncBridge(
            BlockingIterator,
        )
        original_aclose = bridge.aclose

        async def observed_aclose() -> None:
            cleanup_started.set()
            await original_aclose()

        bridge.aclose = observed_aclose  # type: ignore[method-assign]

        async def consume() -> None:
            await anext(bridge)
            try:
                await anext(bridge)
            except asyncio.CancelledError:
                await bridge._aclose_from_cancelled_task()
                raise

        consumer = asyncio.create_task(consume())
        try:
            async with asyncio.timeout(2):
                assert await asyncio.to_thread(worker_blocked.wait)
                consumer.cancel()
                await cleanup_started.wait()

                consumer.cancel()
                checkpoint = asyncio.Event()
                asyncio.get_running_loop().call_soon(checkpoint.set)
                await checkpoint.wait()

                assert not consumer.done()
                assert not iterator_closed.is_set()

                release_worker.set()
                with pytest.raises(asyncio.CancelledError):
                    await consumer
                assert iterator_closed.is_set()
        finally:
            release_worker.set()
            if not consumer.done():
                consumer.cancel()
            with suppress(asyncio.CancelledError):
                await consumer

    asyncio.run(run())
