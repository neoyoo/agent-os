from __future__ import annotations

from dataclasses import fields
from typing import Literal, cast, overload

from agentos.artifacts import ArtifactRuntime
from agentos.runtime.agent_stream import AgentStream
from agentos.runtime.durable_commands import (
    AcceptedContinuationInput,
    DurableCommandReceipt,
    DurableRunCommand,
)
from agentos.runtime.durable_runtime import DurableCommandRuntime
from agentos.runtime.errors import DurableCommandUnsupportedError, RunProtocolError
from agentos.runtime.query_loop import QueryLoop
from agentos.runtime.run import (
    AgentResult,
    AgentWaiting,
    LocalContinuationInput,
    RunInput,
    RunOptions,
    RunOutcome,
    RunRequest,
    UserTurnInput,
)
from agentos.runtime.stream_events import (
    TurnStreamCompleted,
    TurnStreamFailed,
    TurnStreamWaiting,
)


class Agent:
    """向用户提供唯一异步执行入口的 agent facade。"""

    def __init__(
        self,
        query_loop: QueryLoop | None = None,
        query_loop_kwargs: dict[str, object] | None = None,
        durable_command_runtime: DurableCommandRuntime | None = None,
    ) -> None:
        self._durable_command_runtime = durable_command_runtime
        if query_loop is None and query_loop_kwargs is None:
            raise ValueError("query_loop or query_loop_kwargs is required")
        if query_loop is not None and query_loop_kwargs is not None:
            raise ValueError("query_loop and query_loop_kwargs are mutually exclusive")
        if query_loop is not None:
            self.query_loop = query_loop
            return
        kwargs = dict(query_loop_kwargs or {})
        allowed_keys = {item.name for item in fields(QueryLoop) if item.init}
        unknown_keys = sorted(set(kwargs) - allowed_keys)
        if unknown_keys:
            raise ValueError(
                "unknown query_loop_kwargs: " + ", ".join(unknown_keys),
            )
        try:
            self.query_loop = QueryLoop(**kwargs)
        except TypeError as error:
            raise ValueError(f"invalid query_loop_kwargs: {error}") from error

    @property
    def artifacts(self) -> ArtifactRuntime:
        """返回当前 Agent 配置的 Session ArtifactRuntime。"""

        runtime = self.query_loop.artifact_runtime
        if runtime is None:
            raise RuntimeError("artifact runtime is not configured")
        return cast(ArtifactRuntime, runtime)

    def interrupt(self) -> bool:
        """请求取消当前执行；空闲时返回 False。"""

        return self.query_loop.interrupt()

    def _wait_until_idle(self) -> None:
        self.query_loop._wait_until_idle()

    @overload
    async def run(
        self,
        input: RunInput,
        *,
        stream: Literal[False] = False,
        options: RunOptions | None = None,
    ) -> RunOutcome | DurableCommandReceipt: ...

    @overload
    async def run(
        self,
        input: RunInput,
        *,
        stream: Literal[True],
        options: RunOptions | None = None,
    ) -> AgentStream | DurableCommandReceipt: ...

    @overload
    async def run(
        self,
        input: RunInput,
        *,
        stream: bool,
        options: RunOptions | None = None,
    ) -> RunOutcome | AgentStream | DurableCommandReceipt: ...

    async def run(
        self,
        input: RunInput,
        *,
        stream: bool = False,
        options: RunOptions | None = None,
    ) -> RunOutcome | AgentStream | DurableCommandReceipt:
        """执行用户/continuation 输入，Durable receipt 不进入 QueryLoop。"""

        reservation: object | None = None
        normalized: UserTurnInput | LocalContinuationInput | AcceptedContinuationInput
        try:
            if type(input) is DurableRunCommand:
                reservation = self.query_loop._execution_lease.reserve()
                accepted = self._accept_durable_command(input)
                if type(accepted) is DurableCommandReceipt:
                    return accepted
                normalized = accepted
            else:
                normalized = self._normalize_input(input)
            request = RunRequest(normalized, options or RunOptions())
            events = await self.query_loop.execute(request)
        finally:
            if reservation is not None:
                self.query_loop._execution_lease.cancel_reservation(reservation)
        if stream:
            return events
        return await self._collect_outcome(events)

    @staticmethod
    def _normalize_input(input: RunInput) -> UserTurnInput | LocalContinuationInput:
        if isinstance(input, str):
            return UserTurnInput(input)
        if type(input) in {UserTurnInput, LocalContinuationInput}:
            return input
        raise TypeError("agent input must be str, UserTurnInput, or LocalContinuationInput")

    def _accept_durable_command(
        self,
        command: DurableRunCommand,
    ) -> AcceptedContinuationInput | DurableCommandReceipt:
        runtime = self._durable_command_runtime
        if runtime is None:
            raise DurableCommandUnsupportedError(
                "durable command runtime is not configured",
            )
        accepted = runtime.accept(command)
        if not isinstance(accepted, DurableCommandReceipt) or not accepted.duplicate:
            return accepted
        pending = runtime.pending(command.run_id)
        if pending is not None and pending.command_id == command.command_id:
            return pending
        return accepted

    @staticmethod
    async def _collect_outcome(events: AgentStream) -> RunOutcome:
        terminal: RunOutcome | None = None
        failure: BaseException | None = None
        try:
            async for event in events:
                if isinstance(event, TurnStreamCompleted):
                    if terminal is not None:
                        raise RunProtocolError("run stream emitted multiple outcomes")
                    terminal = AgentResult(event.content)
                elif isinstance(event, TurnStreamWaiting):
                    if terminal is not None:
                        raise RunProtocolError("run stream emitted multiple outcomes")
                    terminal = AgentWaiting(event.run_id, event.reason)
                elif isinstance(event, TurnStreamFailed):
                    if failure is not None:
                        raise RunProtocolError("run stream emitted multiple failures")
                    failure = event.error
        finally:
            await events.aclose()
        if failure is not None:
            raise failure
        if terminal is None:
            raise RunProtocolError("run stream ended without an outcome")
        return terminal
