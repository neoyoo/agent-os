import asyncio

from agentos.runtime._async_bridge import SyncIteratorAsyncBridge


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
