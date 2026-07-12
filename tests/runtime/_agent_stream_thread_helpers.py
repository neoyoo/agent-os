import asyncio
from collections.abc import Callable
from threading import Event, Thread


class _ThreadWorker:
    def __init__(self, target: Callable[[], None]) -> None:
        self._target = target
        self._errors: list[BaseException] = []
        self._finished = Event()
        self._thread = Thread(target=self._run)

    def _run(self) -> None:
        try:
            self._target()
        except BaseException as error:
            self._errors.append(error)
        finally:
            self._finished.set()

    def start(self) -> None:
        self._thread.start()

    async def wait_async(self) -> None:
        assert await asyncio.to_thread(self._finished.wait, 5)
        self.join()
        self.raise_error()

    def wait_sync(self) -> None:
        assert self._finished.wait(timeout=5)
        self.join()
        self.raise_error()

    def join(self) -> None:
        self._thread.join(timeout=5)
        assert not self._thread.is_alive()

    def raise_error(self) -> None:
        if self._errors:
            raise self._errors[0]
