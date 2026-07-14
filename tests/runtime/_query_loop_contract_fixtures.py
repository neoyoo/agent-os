from __future__ import annotations

from dataclasses import dataclass
from collections.abc import AsyncIterator

from agentos import Agent
from agentos.context import ContextRuntime
from agentos.events import AgentEvent, TurnStartedEvent
from agentos.messages import MessageRuntime
from agentos.providers import ProviderRequest, ProviderResponse
from agentos.runtime import EventBus, ProviderRequestBuilder, QueryLoop
from agentos.runtime._execution_lease import ExecutionLease
from agentos.runtime.agent_stream import AgentStream
from agentos.runtime.stream_events import TurnStreamEvent
from agentos.runtime import WaitReason
from agentos.runtime.waiting import WaitingCommit
from agentos.runtime.session import SessionState
from tests._context_protocol_fixtures import default_context_renderer


@dataclass(slots=True)
class Recorder:
    control_flow_runs: int = 0
    provider_calls: int = 0

    def record(self, event: AgentEvent) -> None:
        if isinstance(event, TurnStartedEvent):
            self.control_flow_runs += 1


class RecordingProvider:
    def __init__(self, recorder: Recorder) -> None:
        self._recorder = recorder

    def complete(self, _request: ProviderRequest) -> ProviderResponse:
        self._recorder.provider_calls += 1
        return ProviderResponse(content="answer")


class RecordingWaitingRuntime:
    def __init__(self, order: list[str], *, run_id: str = "run_1") -> None:
        self.order = order
        self.run_id = run_id

    async def commit_waiting(
        self,
        *,
        turn_id: str,
        reason: WaitReason,
    ) -> WaitingCommit:
        self.order.append("state_committed")
        return WaitingCommit(self.run_id, reason)


class FailingWaitingRuntime:
    async def commit_waiting(
        self,
        *,
        turn_id: str,
        reason: WaitReason,
    ) -> WaitingCommit:
        raise RuntimeError("commit failed")


def make_query_loop(
    *,
    recorder: Recorder | None = None,
    waiting_runtime: object | None = None,
) -> QueryLoop:
    recorder = recorder or Recorder()
    context = ContextRuntime()
    messages = MessageRuntime()
    kwargs: dict[str, object] = {
        "context_runtime": context,
        "message_runtime": messages,
        "request_builder": ProviderRequestBuilder(
            context_renderer=default_context_renderer(),
            message_runtime=messages,
            tools=[],
        ),
        "provider": RecordingProvider(recorder),
        "event_bus": EventBus(subscribers=[recorder]),
        "session_state": SessionState("session_1"),
    }
    if waiting_runtime is not None:
        kwargs["waiting_runtime"] = waiting_runtime
    return QueryLoop(**kwargs)


def make_recording_agent() -> tuple[Agent, Recorder]:
    recorder = Recorder()
    return Agent(query_loop=make_query_loop(recorder=recorder)), recorder


def agent_stream_from(
    *events: TurnStreamEvent,
    final_error: BaseException | None = None,
) -> AgentStream:
    async def source() -> AsyncIterator[TurnStreamEvent]:
        for event in events:
            yield event
        if final_error is not None:
            raise final_error

    return ExecutionLease().open_stream(source(), cleanup=lambda: None)
