from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import aclosing
from dataclasses import dataclass

from agentos._waiting import WaitRequest
from agentos.runtime.query_loop_support import _FinalContent
from agentos.runtime.run import LocalContinuationInput, RunRequest, UserTurnInput
from agentos.runtime.run_runtime import RunRuntime
from agentos.runtime.run_state import RunStatus
from agentos.runtime.stream_events import StatusUpdate, TurnStreamEvent
from agentos.runtime.turn import TurnState
from agentos.runtime.turn_lifecycle import TurnLifecycle


ProviderToolEvents = Callable[
    [TurnState | None, object],
    AsyncIterator[TurnStreamEvent | _FinalContent | WaitRequest],
]


@dataclass(slots=True)
class RunDriver:
    """Coordinate Run and Turn state around the provider/tool event source."""

    runs: RunRuntime
    turns: TurnLifecycle

    def prepare(self) -> str:
        run = self.runs.create_run()
        self.runs.queue(run.run_id)
        return run.run_id

    def cancel_open(self, run_id: str) -> None:
        state = self.runs.get_run(run_id)
        if state.status in {
            RunStatus.CREATED,
            RunStatus.QUEUED,
            RunStatus.RUNNING,
        }:
            self.runs.cancel(run_id)

    async def events(
        self,
        request: RunRequest,
        run_id: str,
        provider_tool_events: ProviderToolEvents,
    ) -> AsyncIterator[TurnStreamEvent]:
        turn: TurnState | None = None
        pending: tuple[TurnStreamEvent, ...] = ()
        continuation = isinstance(request.input, LocalContinuationInput)
        waiting_requested = False
        try:
            self.runs.start(run_id)
            if isinstance(request.input, UserTurnInput):
                turn, pending = self.turns.prepare_user_turn(request.input)
            else:
                turn, pending = self.turns.prepare_continuation_turn()

            final_content = ""
            async with aclosing(
                provider_tool_events(turn, request.options)
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
                    elif isinstance(event, WaitRequest):
                        waiting_requested = True
                        waiting = await self.turns.commit_waiting(
                            run_id=run_id,
                            turn=turn,
                            reason=event.reason,
                        )
                        if self.runs.get_run(run_id).status is RunStatus.RUNNING:
                            self.runs.wait(run_id, reason=event.reason)
                        yield waiting
                        return
                    else:
                        yield event
            self.runs.complete(run_id)
            yield self.turns.complete(turn, final_content)
        except asyncio.CancelledError:
            self.turns.cancel(turn)
            self.cancel_open(run_id)
            raise
        except Exception as error:
            if self.runs.get_run(run_id).status is RunStatus.RUNNING:
                self.runs.fail(run_id)
            for prepared_event in pending:
                yield prepared_event
            yield self.turns.fail(turn, error, mark_turn=not waiting_requested)
            raise
        finally:
            self.turns.cleanup(is_continuation=continuation)
