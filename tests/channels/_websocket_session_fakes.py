from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime

from agentos.channels.auth import ChannelAuthContext
from agentos.channels.service_wiring import ChannelServices
from agentos.channels.websocket_buffer import WebSocketBufferLimits
from agentos.channels.websocket_session import WebSocketSession
from agentos.distributed.models import (
    ReplayItem,
    RequestScope,
    RunEventEnvelope,
    RunReadModel,
    RunSubmission,
    RunSubmissionReceipt,
    StreamGap,
)
from agentos.distributed.services import (
    ArtifactService,
    RunCommandService,
    RunEventStream,
    RunQueryService,
    RunSubmissionService,
)
from agentos.runtime.durable_commands import DurableCommandReceipt, DurableRunCommand
from agentos.runtime.run_state import RunStatus
from agentos.transports.http.request_types import HttpHeaders


SCOPE = RequestScope("tenant_1", "principal_1")
NOW = datetime(2026, 7, 21, tzinfo=UTC)


@dataclass
class Connection:
    incoming: asyncio.Queue[str | BaseException] = field(default_factory=asyncio.Queue)
    sent: list[str] = field(default_factory=list)
    closed: list[int] = field(default_factory=list)
    changed: asyncio.Condition = field(default_factory=asyncio.Condition)

    async def receive_text(self, *, max_bytes: int) -> str:
        value = await self.incoming.get()
        if isinstance(value, BaseException):
            raise value
        assert len(value.encode("utf-8")) <= max_bytes
        return value

    async def send_text(self, text: str) -> None:
        async with self.changed:
            self.sent.append(text)
            self.changed.notify_all()

    async def close(self, code: int, reason: str = "") -> None:
        del reason
        self.closed.append(code)

    async def wait_sent(self, count: int) -> None:
        async with self.changed:
            await self.changed.wait_for(lambda: len(self.sent) >= count)

    async def wait_closed(self) -> None:
        async with self.changed:
            await self.changed.wait_for(lambda: bool(self.closed))


@dataclass
class Subscription:
    items: list[ReplayItem | StreamGap]
    close_count: int = 0
    waiting: asyncio.Event = field(default_factory=asyncio.Event)
    close_finished: asyncio.Event = field(default_factory=asyncio.Event)
    close_error: BaseException | None = None

    def __aiter__(self) -> Subscription:
        return self

    async def __anext__(self) -> ReplayItem | StreamGap:
        if self.items:
            return self.items.pop(0)
        self.waiting.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def aclose(self) -> None:
        self.close_count += 1
        self.close_finished.set()
        if self.close_error is not None:
            raise self.close_error


@dataclass
class RunPort:
    submissions: list[RunSubmission] = field(default_factory=list)
    commands: list[tuple[str, DurableRunCommand]] = field(default_factory=list)
    submit_error: BaseException | None = None

    async def submit(
        self,
        *,
        scope: RequestScope,
        submission: RunSubmission,
    ) -> RunSubmissionReceipt:
        assert scope == SCOPE
        if self.submit_error is not None:
            raise self.submit_error
        self.submissions.append(submission)
        return RunSubmissionReceipt(
            submission.session_id,
            "run_1",
            submission.submission_id,
            1,
            False,
        )

    async def submit_command(
        self,
        *,
        scope: RequestScope,
        session_id: str,
        command: DurableRunCommand,
    ) -> DurableCommandReceipt:
        assert scope == SCOPE
        self.commands.append((session_id, command))
        return DurableCommandReceipt(
            command.run_id,
            command.command_id,
            command.kind,
            2,
            False,
        )


class QueryPort:
    async def get_run(self, **values: object) -> RunReadModel:
        scope = values["scope"]
        assert type(scope) is RequestScope
        return RunReadModel(
            scope.tenant_id,
            str(values["session_id"]),
            str(values["run_id"]),
            RunStatus.RUNNING,
            None,
            1,
            None,
        )


@dataclass
class ReplayPort:
    subscription: Subscription

    def follow(self, **values: object) -> Subscription:
        del values
        return self.subscription


@dataclass
class MultiReplayPort:
    subscriptions: dict[str, Subscription]

    def follow(self, **values: object) -> Subscription:
        return self.subscriptions[str(values["run_id"])]


@dataclass
class Authenticator:
    calls: list[ChannelAuthContext] = field(default_factory=list)
    scope: RequestScope = SCOPE

    async def authenticate(
        self,
        headers: HttpHeaders,
        *,
        context: ChannelAuthContext,
    ) -> RequestScope:
        del headers
        self.calls.append(context)
        return self.scope


def services(run: RunPort, subscription: Subscription) -> ChannelServices:
    query = QueryPort()
    return ChannelServices(
        RunSubmissionService(run),  # type: ignore[arg-type]
        RunCommandService(run),  # type: ignore[arg-type]
        RunQueryService(query),  # type: ignore[arg-type]
        RunEventStream(query, ReplayPort(subscription)),  # type: ignore[arg-type]
        ArtifactService(object()),  # type: ignore[arg-type]
    )


def multi_services(
    run: RunPort,
    subscriptions: dict[str, Subscription],
) -> ChannelServices:
    query = QueryPort()
    return ChannelServices(
        RunSubmissionService(run),  # type: ignore[arg-type]
        RunCommandService(run),  # type: ignore[arg-type]
        RunQueryService(query),  # type: ignore[arg-type]
        RunEventStream(query, MultiReplayPort(subscriptions)),  # type: ignore[arg-type]
        ArtifactService(object()),  # type: ignore[arg-type]
    )


def replay_item(event: object, sequence: int, cursor: str) -> ReplayItem:
    return ReplayItem(
        cursor,
        RunEventEnvelope(
            "tenant_1",
            "session_1",
            "run_1",
            "turn_1",
            1,
            sequence,
            event,  # type: ignore[arg-type]
            NOW,
        ),
    )


def session(
    connection: Connection,
    run: RunPort,
    subscription: Subscription,
    authenticator: Authenticator | None = None,
) -> WebSocketSession:
    return WebSocketSession(
        services=services(run, subscription),
        authenticator=authenticator or Authenticator(),
        handshake_scope=SCOPE,
        headers=HttpHeaders(()),
        connection=connection,  # type: ignore[arg-type]
        buffer_limits=WebSocketBufferLimits(),
    )
