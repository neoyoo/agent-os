from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import aclosing
from dataclasses import dataclass, field

from agentos._waiting import WaitReason, WaitRequest
from agentos.runtime._execution_control import (
    ExecutionControl,
    PendingToolsCheckpointRequest,
    RunningCheckpointRequest,
    WaitingCheckpointRequest,
)
from agentos.runtime.query_loop_support import _FinalContent
from agentos.runtime.durable_commands import AcceptedContinuationInput
from agentos.runtime.errors import (
    CommandStateError,
    RunProtocolError,
    WaitingUnsupportedError,
)
from agentos.runtime.execution import (
    AcceptedStartInput,
    AcceptedTurnExecution,
    ExecutionMode,
    RunExecutionCursor,
)
from agentos.runtime.run import LocalContinuationInput, RunRequest, UserTurnInput
from agentos.runtime.run_commit import RunCommitRuntime, RunTerminalStatus
from agentos.runtime.run_runtime import RunRuntime, RunWriteGuard
from agentos.runtime.run_state import RunAlreadyExistsError, RunNotFoundError, RunStatus
from agentos.runtime.stream_events import FinalResult, StatusUpdate, TurnStreamEvent
from agentos.runtime.turn import TurnState
from agentos.runtime.turn_lifecycle import TurnLifecycle
from agentos.runtime.tool_payloads import ToolPayloadRuntime


ProviderToolEvents = Callable[
    [str, TurnState | None, object, RunExecutionCursor | None],
    AsyncIterator[TurnStreamEvent | _FinalContent | ExecutionControl | WaitRequest],
]


@dataclass(slots=True)
class RunDriver:
    """Coordinate Run and Turn state around the provider/tool event source."""

    runs: RunRuntime
    turns: TurnLifecycle
    commits: RunCommitRuntime
    recovery_cursor: RunExecutionCursor | None = None
    tool_payloads: ToolPayloadRuntime | None = None
    _execution_guards: dict[str, RunWriteGuard] = field(default_factory=dict, init=False)
    _execution_modes: dict[str, ExecutionMode] = field(default_factory=dict, init=False)
    _local_run_ids: set[str] = field(default_factory=set, init=False)
    _turn_ids: dict[str, str] = field(default_factory=dict, init=False)
    _uncertain_commits: set[str] = field(default_factory=set, init=False)

    async def prepare(
        self,
        input: UserTurnInput | LocalContinuationInput | AcceptedTurnExecution,
    ) -> str:
        if type(input) is AcceptedTurnExecution:
            return await self._prepare_accepted(input)

        run_id = self.runs.new_run_id()
        self._local_run_ids.add(run_id)
        self._execution_modes[run_id] = "start"
        try:
            run = await self.runs.create_run(run_id=run_id)
            self._execution_guards[run_id] = RunWriteGuard(
                expected_version=run.aggregate_version,
            )
            queued = await self.runs.queue(
                run_id,
                guard=self._execution_guards[run_id],
            )
            self._execution_guards[run_id] = RunWriteGuard(
                expected_version=queued.aggregate_version,
            )
            return run_id
        except RunAlreadyExistsError:
            self._forget(run_id)
            raise
        except BaseException as error:
            await self._cancel_preserving(run_id, error)
            self._forget(run_id)
            raise

    async def cancel_open(self, run_id: str) -> RunStatus | None:
        try:
            return await self._close_open(run_id)
        finally:
            self._forget(run_id)

    async def _close_open(self, run_id: str) -> RunStatus | None:
        local_run = run_id in self._local_run_ids
        try:
            state = await self.runs.get_run(run_id)
        except RunNotFoundError:
            if local_run:
                return None
            raise
        if run_id in self._uncertain_commits:
            return state.status
        if state.status in {
            RunStatus.CREATED,
            RunStatus.QUEUED,
            RunStatus.RUNNING,
        }:
            guard = self._guard_for_state(run_id, state.aggregate_version)
            if guard.claim_id is None:
                await self.commits.commit_terminal(
                    run_id=run_id,
                    guard=guard,
                    status="cancelled",
                    turn_id=self._turn_ids.get(run_id),
                )
                return RunStatus.CANCELLED
        return state.status

    async def cleanup_open(self, run_id: str) -> None:
        await self.cancel_open(run_id)

    async def events(
        self,
        request: RunRequest,
        run_id: str,
        provider_tool_events: ProviderToolEvents,
    ) -> AsyncIterator[TurnStreamEvent]:
        turn: TurnState | None = None
        pending: tuple[TurnStreamEvent, ...] = ()
        final_events: tuple[TurnStreamEvent, ...] = ()
        turn_input = _turn_input(request.input)
        continuation = not isinstance(
            turn_input,
            (UserTurnInput, AcceptedStartInput),
        )
        try:
            execution_guard = self._require_guard(run_id)
            mode = self._execution_modes.get(run_id)
            if mode == "start":
                running = await self.runs.start(run_id, guard=execution_guard)
                execution_guard = _next_guard(execution_guard, running.aggregate_version)
            elif mode == "recover":
                running = await self.runs.get_run(run_id)
                if (
                    running.status is not RunStatus.RUNNING
                    or running.aggregate_version != execution_guard.expected_version
                ):
                    raise CommandStateError("accepted recovery is stale")
            else:
                raise RunProtocolError("run execution mode is missing")
            self._execution_guards[run_id] = execution_guard
            if type(turn_input) is AcceptedStartInput:
                turn, pending = self.turns.prepare_user_turn(
                    turn_input.input,
                    turn_id=turn_input.turn_id,
                    user_message_id=turn_input.user_message_id,
                )
            elif isinstance(turn_input, UserTurnInput):
                turn, pending = self.turns.prepare_user_turn(turn_input)
            else:
                turn, pending = self.turns.prepare_continuation_turn(turn_input)
            if turn is not None:
                self._turn_ids[run_id] = turn.id

            recovery_cursor = self._take_recovery_cursor(mode)
            if recovery_cursor is not None:
                if turn is None or recovery_cursor.turn_id != turn.id:
                    raise RunProtocolError(
                        "running execution cursor does not match the prepared turn",
                    )
            elif turn is not None:
                recovery_cursor = RunExecutionCursor(
                    turn_id=turn.id,
                    stage="before_provider",
                    provider_call_index=0,
                )
                execution_guard = await self._commit_running(
                    run_id=run_id,
                    turn_id=turn.id,
                    cursor=recovery_cursor,
                    guard=execution_guard,
                )
                self._execution_guards[run_id] = execution_guard

            final_content = ""
            async with aclosing(
                provider_tool_events(
                    run_id,
                    turn,
                    request.options,
                    recovery_cursor,
                )
            ) as provider_events:
                async for event in provider_events:
                    if pending and isinstance(event, StatusUpdate) and event.stage == "context":
                        pending += (event,)
                        continue
                    if pending:
                        for prepared_event in pending:
                            yield prepared_event
                        pending = ()
                    if isinstance(event, _FinalContent):
                        final_content = event.content
                    elif isinstance(event, FinalResult):
                        final_events += (event,)
                    elif isinstance(event, PendingToolsCheckpointRequest):
                        if self.commits.checkpointing:
                            if turn is None:
                                raise RunProtocolError(
                                    "pending tool checkpoint requires turn state",
                                )
                            try:
                                payloads = self._require_tool_payloads()
                                cursor = payloads.pending_cursor(
                                    run_id=run_id,
                                    turn_id=turn.id,
                                    provider_call_index=event.provider_call_index,
                                    assistant_message_id=event.assistant_message_id,
                                    calls=event.calls,
                                )
                                execution_guard = await self._commit_running(
                                    run_id=run_id,
                                    turn_id=turn.id,
                                    cursor=cursor,
                                    guard=execution_guard,
                                )
                            except BaseException:
                                self._uncertain_commits.add(run_id)
                                raise
                            self._execution_guards[run_id] = execution_guard
                    elif isinstance(event, RunningCheckpointRequest):
                        if turn is None or event.cursor.turn_id != turn.id:
                            raise RunProtocolError(
                                "running checkpoint does not match the prepared turn",
                            )
                        execution_guard = await self._commit_running(
                            run_id=run_id,
                            turn_id=turn.id,
                            cursor=event.cursor,
                            guard=execution_guard,
                        )
                        self._execution_guards[run_id] = execution_guard
                    elif isinstance(event, WaitingCheckpointRequest):
                        if turn is None:
                            raise WaitingUnsupportedError(
                                "waiting requires session turn state",
                            )
                        execution_guard = await self._commit_waiting(
                            run_id=run_id,
                            turn_id=turn.id,
                            reason=event.reason,
                            guard=execution_guard,
                            active_refs=event.active_refs,
                        )
                        self._execution_guards[run_id] = execution_guard
                        try:
                            self.turns.apply_waiting_projection(
                                event.remove_active_refs,
                            )
                        except BaseException:
                            self._uncertain_commits.add(run_id)
                            raise
                        yield self.turns.mark_waiting(
                            run_id=run_id,
                            turn=turn,
                            reason=event.reason,
                        )
                        return
                    elif isinstance(event, WaitRequest):
                        if turn is None:
                            raise WaitingUnsupportedError(
                                "waiting requires session turn state",
                            )
                        execution_guard = await self._commit_waiting(
                            run_id=run_id,
                            turn_id=turn.id,
                            reason=event.reason,
                            guard=execution_guard,
                        )
                        self._execution_guards[run_id] = execution_guard
                        yield self.turns.mark_waiting(
                            run_id=run_id,
                            turn=turn,
                            reason=event.reason,
                        )
                        return
                    else:
                        yield event
            execution_guard = await self._commit_terminal(
                run_id=run_id,
                guard=execution_guard,
                status="completed",
                turn_id=None if turn is None else turn.id,
            )
            self._execution_guards[run_id] = execution_guard
            for event in final_events:
                yield event
            yield self.turns.complete(turn, final_content)
        except asyncio.CancelledError as error:
            final_status = await self._cancel_preserving(run_id, error)
            _align_cancelled_turn(turn, final_status)
            raise
        except Exception as error:
            if run_id in self._uncertain_commits:
                raise
            state = await self.runs.get_run(run_id)
            if state.status is RunStatus.RUNNING:
                await self._commit_terminal(
                    run_id=run_id,
                    guard=self._require_guard(run_id),
                    status="failed",
                    turn_id=None if turn is None else turn.id,
                )
            for prepared_event in pending:
                yield prepared_event
            yield self.turns.fail(turn, error)
            raise
        finally:
            self.turns.cleanup(is_continuation=continuation)

    async def _prepare_accepted(self, execution: AcceptedTurnExecution) -> str:
        run_id = execution.input.run_id
        run = await self.runs.get_run(run_id)
        expected_status = (
            RunStatus.QUEUED if execution.mode == "start" else RunStatus.RUNNING
        )
        if (
            run.status is not expected_status
            or run.aggregate_version != execution.guard.expected_version
        ):
            raise CommandStateError("accepted turn execution is stale")
        self._execution_guards[run_id] = execution.guard
        self._execution_modes[run_id] = execution.mode
        return run_id

    def _require_guard(self, run_id: str) -> RunWriteGuard:
        guard = self._execution_guards.get(run_id)
        if guard is None:
            raise RunProtocolError("run execution guard is missing")
        return guard

    def _take_recovery_cursor(
        self,
        mode: ExecutionMode,
    ) -> RunExecutionCursor | None:
        if mode != "recover":
            if self.recovery_cursor is not None:
                raise RunProtocolError(
                    "running execution cursor requires recover mode",
                )
            return None
        cursor, self.recovery_cursor = self.recovery_cursor, None
        return cursor

    def _require_tool_payloads(self) -> ToolPayloadRuntime:
        payloads = self.tool_payloads
        if payloads is None:
            raise RunProtocolError(
                "durable tool checkpoint requires a payload runtime",
            )
        return payloads

    async def _commit_running(
        self,
        *,
        run_id: str,
        turn_id: str,
        cursor: RunExecutionCursor,
        guard: RunWriteGuard,
    ) -> RunWriteGuard:
        try:
            return await self.commits.commit_running(
                run_id=run_id,
                turn_id=turn_id,
                cursor=cursor,
                guard=guard,
            )
        except BaseException:
            self._uncertain_commits.add(run_id)
            raise

    async def _commit_waiting(
        self,
        *,
        run_id: str,
        turn_id: str,
        reason: WaitReason,
        guard: RunWriteGuard,
        active_refs: tuple[str, ...] | None = None,
    ) -> RunWriteGuard:
        try:
            return await self.commits.commit_waiting(
                run_id=run_id,
                turn_id=turn_id,
                reason=reason,
                guard=guard,
                active_refs=active_refs,
            )
        except BaseException:
            self._uncertain_commits.add(run_id)
            raise

    async def _commit_terminal(
        self,
        *,
        run_id: str,
        turn_id: str | None,
        status: RunTerminalStatus,
        guard: RunWriteGuard,
    ) -> RunWriteGuard:
        try:
            return await self.commits.commit_terminal(
                run_id=run_id,
                turn_id=turn_id,
                status=status,
                guard=guard,
            )
        except BaseException:
            self._uncertain_commits.add(run_id)
            raise

    def _guard_for_state(self, run_id: str, expected_version: int) -> RunWriteGuard:
        guard = self._execution_guards.get(run_id)
        if guard is None:
            if run_id not in self._local_run_ids:
                raise RunProtocolError("run execution guard is missing")
            return RunWriteGuard(expected_version=expected_version)
        return _next_guard(guard, expected_version)

    async def _cancel_preserving(
        self,
        run_id: str,
        original_error: BaseException,
    ) -> RunStatus | None:
        try:
            return await self._close_open(run_id)
        except BaseException:
            original_error.add_note(
                "AgentOS could not close the open run during error cleanup.",
            )
            return None

    def _forget(self, run_id: str) -> None:
        self._execution_guards.pop(run_id, None)
        self._execution_modes.pop(run_id, None)
        self._local_run_ids.discard(run_id)
        self._turn_ids.pop(run_id, None)
        self._uncertain_commits.discard(run_id)


def _turn_input(
    input: UserTurnInput | LocalContinuationInput | AcceptedTurnExecution,
) -> (
    UserTurnInput
    | LocalContinuationInput
    | AcceptedStartInput
    | AcceptedContinuationInput
):
    if type(input) is not AcceptedTurnExecution:
        return input
    return input.input


def _next_guard(guard: RunWriteGuard, expected_version: int) -> RunWriteGuard:
    return RunWriteGuard(
        expected_version=expected_version,
        claim_id=guard.claim_id,
        fencing_token=guard.fencing_token,
    )


def _align_cancelled_turn(turn: TurnState | None, status: RunStatus | None) -> None:
    if turn is None or turn.status != "running":
        return
    if status is RunStatus.WAITING:
        turn.mark_waiting()
    elif status is RunStatus.COMPLETED:
        turn.complete()
    elif status is RunStatus.CANCELLED:
        turn.cancel()
