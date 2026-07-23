from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
import os

import pytest

from agentos.channels.service_wiring import ChannelServices
from agentos.distributed.models import (
    LiveContentDelta,
    ReplayItem,
    RequestScope,
    RunEventEnvelope,
    RunReadModel,
    StreamGap,
)
from agentos.distributed.protocols import EventSubscription
from agentos.distributed.redis.replay import RedisEventReplayAdapter
from agentos.distributed.services import (
    ArtifactService,
    RunCommandService,
    RunEventStream,
    RunQueryService,
    RunSubmissionService,
)
from agentos.runtime.run_state import RunStatus
from agentos.transports.run_stream import encode_cursor


SCOPE = RequestScope("tenant_1", "principal_1")
NOW = datetime(2026, 7, 22, 12, tzinfo=UTC)


class TailObservedReplay(RedisEventReplayAdapter):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.tail_waiting = asyncio.Event()

    async def _wait_for_tail(
        self,
        scope: RequestScope,
        session_id: str,
        run_id: str,
        after: str,
    ) -> None:
        self.tail_waiting.set()
        await super()._wait_for_tail(scope, session_id, run_id, after)


@dataclass
class TrackedSubscription:
    inner: EventSubscription
    close_count: int = 0
    closed: asyncio.Event = field(default_factory=asyncio.Event)

    def __aiter__(self) -> TrackedSubscription:
        return self

    async def __anext__(self) -> ReplayItem | StreamGap:
        return await anext(self.inner)

    async def aclose(self) -> None:
        self.close_count += 1
        try:
            await self.inner.aclose()
        finally:
            self.closed.set()


@dataclass
class TrackedReplayPort:
    replay: RedisEventReplayAdapter
    subscriptions: list[TrackedSubscription] = field(default_factory=list)

    def follow(self, **values: object) -> TrackedSubscription:
        subscription = TrackedSubscription(self.replay.follow(**values))  # type: ignore[arg-type]
        self.subscriptions.append(subscription)
        return subscription


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
class Connection:
    incoming: asyncio.Queue[str | BaseException] = field(default_factory=asyncio.Queue)
    sent: list[str] = field(default_factory=list)
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
        del code, reason

    async def wait_sent(self, count: int) -> None:
        async with self.changed:
            await asyncio.wait_for(
                self.changed.wait_for(lambda: len(self.sent) >= count),
                timeout=2,
            )


def services(replay: TrackedReplayPort) -> ChannelServices:
    query = QueryPort()
    return ChannelServices(
        RunSubmissionService(object()),  # type: ignore[arg-type]
        RunCommandService(object()),  # type: ignore[arg-type]
        RunQueryService(query),  # type: ignore[arg-type]
        RunEventStream(query, replay),  # type: ignore[arg-type]
        ArtifactService(object()),  # type: ignore[arg-type]
    )


async def trimmed_stream(
    replay: RedisEventReplayAdapter,
) -> tuple[ReplayItem, ReplayItem, ReplayItem]:
    items = [
        await append_event(replay, sequence)
        for sequence in range(1, 4)
    ]
    return items[0], items[1], items[2]


async def append_event(
    replay: RedisEventReplayAdapter,
    sequence: int,
) -> ReplayItem:
    return await replay.append(scope=SCOPE, event=_event(sequence))


def public_cursor(item: ReplayItem) -> str:
    return encode_cursor(
        tenant_id=SCOPE.tenant_id,
        session_id="session_1",
        run_id="run_1",
        position=item.cursor,
    )


def redis_url() -> str:
    if os.environ.get("AGENTOS_RUN_INTEGRATION") != "1":
        pytest.skip("set AGENTOS_RUN_INTEGRATION=1 to run live integration tests")
    value = os.environ.get("AGENTOS_TEST_REDIS_URL")
    if not value:
        pytest.fail("AGENTOS_TEST_REDIS_URL is required by the live integration suite")
    return value


def _event(sequence: int) -> RunEventEnvelope:
    return RunEventEnvelope(
        SCOPE.tenant_id,
        "session_1",
        "run_1",
        "turn_1",
        1,
        sequence,
        LiveContentDelta(sequence, f"content-{sequence}"),
        NOW,
    )
