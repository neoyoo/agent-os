from __future__ import annotations

from concurrent.futures import Future
from threading import Condition, Lock, get_ident
from types import TracebackType
from typing import Literal, overload

from agentos.runtime._sync_host import SyncHost
from agentos.runtime.agent import Agent
from agentos.runtime.agent_stream import AgentStream
from agentos.runtime.errors import SyncAgentClosedError
from agentos.runtime.run import RunInput, RunOptions, RunOutcome
from agentos.sync.stream import SyncAgentStream, create_stream_driver


_OPEN = "open"
_CLOSING = "closing"
_CLOSED = "closed"


class SyncAgent:
    """在专用 owner loop 上同步驱动现有 Agent。"""

    def __init__(self, agent: Agent) -> None:
        self.agent = agent
        self._host = SyncHost()
        self._lock = Lock()
        self._stream_condition = Condition(self._lock)
        self._state = _OPEN
        self._closing_thread_id: int | None = None
        self._opening_streams = 0
        self._streams: set[SyncAgentStream] = set()

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._state == _CLOSED

    def __enter__(self) -> SyncAgent:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    @overload
    def run(
        self,
        input: RunInput,
        *,
        stream: Literal[False] = False,
        options: RunOptions | None = None,
    ) -> RunOutcome: ...

    @overload
    def run(
        self,
        input: RunInput,
        *,
        stream: Literal[True],
        options: RunOptions | None = None,
    ) -> SyncAgentStream: ...

    @overload
    def run(
        self,
        input: RunInput,
        *,
        stream: bool,
        options: RunOptions | None = None,
    ) -> RunOutcome | SyncAgentStream: ...

    def run(
        self,
        input: RunInput,
        *,
        stream: bool = False,
        options: RunOptions | None = None,
    ) -> RunOutcome | SyncAgentStream:
        """同步执行一次 run，或返回同步事件流。"""

        if not stream:
            with self._lock:
                if self._state != _OPEN:
                    raise SyncAgentClosedError("SyncAgent is closed")
            return self._submit(
                self.agent.run(input, stream=False, options=options),
            ).result()  # type: ignore[return-value]

        self._reserve_stream_open()
        agent_stream: AgentStream | None = None
        driver = None
        registered = False
        try:
            outcome = self._submit(
                self.agent.run(input, stream=True, options=options),
            ).result()
            if not isinstance(outcome, AgentStream):
                raise RuntimeError("streaming Agent.run did not return AgentStream")
            agent_stream = outcome
            driver = self._submit(create_stream_driver(agent_stream)).result()
            sync_stream = SyncAgentStream._create(self, driver)
            with self._lock:
                if self._state == _OPEN:
                    self._streams.add(sync_stream)
                    registered = True
            if registered:
                return sync_stream
            self._submit(driver.close()).result()
            raise SyncAgentClosedError("SyncAgent is closed")
        except BaseException:
            if not registered and agent_stream is not None and not agent_stream.closed:
                if driver is not None:
                    self._submit(driver.close()).result()
                else:
                    self._submit(agent_stream.aclose()).result()
            raise
        finally:
            self._release_stream_open()

    def _reserve_stream_open(self) -> None:
        with self._lock:
            if self._state != _OPEN:
                raise SyncAgentClosedError("SyncAgent is closed")
            self._opening_streams += 1

    def _release_stream_open(self) -> None:
        with self._stream_condition:
            self._opening_streams -= 1
            self._stream_condition.notify_all()

    def close(self) -> None:
        self._host._raise_if_owner_thread_reentry()
        closing_thread_id = get_ident()
        with self._stream_condition:
            if self._state == _CLOSED:
                return
            if self._state == _CLOSING:
                if self._closing_thread_id == closing_thread_id:
                    return
                while self._state != _CLOSED:
                    self._stream_condition.wait()
                return
            self._state = _CLOSING
            self._closing_thread_id = closing_thread_id
            while self._opening_streams:
                self._stream_condition.wait()
            streams = tuple(self._streams)

        first_error: BaseException | None = None
        try:
            for stream in streams:
                try:
                    stream.close()
                except BaseException as error:
                    if first_error is None:
                        first_error = error
            try:
                self._host.close()
            except BaseException as error:
                if first_error is None:
                    first_error = error
        finally:
            with self._stream_condition:
                self._state = _CLOSED
                self._closing_thread_id = None
                self._stream_condition.notify_all()

        if first_error is not None:
            raise first_error

    def _wait_until_idle(self) -> None:
        self.agent._wait_until_idle()

    def _submit(self, awaitable: object) -> Future[object]:
        return self._host.submit(awaitable)  # type: ignore[arg-type]

    def _detach_stream(self, stream: SyncAgentStream) -> None:
        with self._lock:
            self._streams.discard(stream)


@overload
def run(
    agent: Agent,
    input: RunInput,
    *,
    stream: Literal[False] = False,
    options: RunOptions | None = None,
) -> RunOutcome: ...


@overload
def run(
    agent: Agent,
    input: RunInput,
    *,
    stream: Literal[True],
    options: RunOptions | None = None,
) -> SyncAgentStream: ...


@overload
def run(
    agent: Agent,
    input: RunInput,
    *,
    stream: bool,
    options: RunOptions | None = None,
) -> RunOutcome | SyncAgentStream: ...


def run(
    agent: Agent,
    input: RunInput,
    *,
    stream: bool = False,
    options: RunOptions | None = None,
) -> RunOutcome | SyncAgentStream:
    """用一次性 SyncAgent 同步驱动一个 Agent。"""

    owner = SyncAgent(agent)
    if not stream:
        try:
            return owner.run(input, options=options)
        finally:
            owner.close()
    sync_stream = owner.run(input, stream=True, options=options)
    sync_stream._close_owner_when_done = True
    return sync_stream
