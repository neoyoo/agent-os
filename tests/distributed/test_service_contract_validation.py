import asyncio
from datetime import UTC, datetime

import pytest

from agentos.artifacts import ArtifactRecord
from agentos.artifacts.types import ArtifactPage
from agentos.distributed.models import (
    ArtifactContent,
    LiveContentDelta,
    ReplayItem,
    RequestScope,
    RunEventEnvelope,
    RunReadModel,
    RunSubmission,
    RunSubmissionReceipt,
    StreamGap,
)
from agentos.distributed.protocols import EventSubscription
from agentos.distributed.services import (
    ArtifactService,
    RunCommandService,
    RunEventStream,
    RunQueryService,
    RunSubmissionService,
)
from agentos.runtime.durable_commands import DurableCommandReceipt, DurableRunCommand
from agentos.runtime.run_state import RunStatus


NOW = datetime(2026, 7, 20, 12, tzinfo=UTC)
ARTIFACT_ID = "art_00000000-0000-4000-8000-000000000001"
OTHER_ARTIFACT_ID = "art_00000000-0000-4000-8000-000000000002"
SCOPE = RequestScope("tenant_1", "user_1")


class MismatchedRunPort:
    async def submit(
        self,
        *,
        scope: RequestScope,
        submission: RunSubmission,
    ) -> RunSubmissionReceipt:
        return RunSubmissionReceipt("other_session", "run_1", submission.submission_id, 1, False)

    async def submit_command(
        self,
        *,
        scope: RequestScope,
        session_id: str,
        command: DurableRunCommand,
    ) -> DurableCommandReceipt:
        return DurableCommandReceipt("other_run", command.command_id, command.kind, 2, False)

    async def get_run(
        self,
        *,
        scope: RequestScope,
        session_id: str,
        run_id: str,
    ) -> RunReadModel:
        return RunReadModel(
            tenant_id="other_tenant",
            session_id=session_id,
            run_id=run_id,
            status=RunStatus.RUNNING,
            wait_reason=None,
            aggregate_version=1,
            result=None,
        )


class MismatchedArtifactPort:
    async def upload(self, **_: object) -> ArtifactRecord:
        return _artifact("session_1", ARTIFACT_ID)

    async def list(self, **_: object) -> ArtifactPage:
        return ArtifactPage((_artifact("other_session", ARTIFACT_ID),), None)

    async def read(self, **_: object) -> ArtifactContent:
        return ArtifactContent(_artifact("session_1", OTHER_ARTIFACT_ID), b"abc")

    async def delete(self, **_: object) -> None:
        return None


class TrackingSubscription:
    def __init__(self, *items: ReplayItem | StreamGap) -> None:
        self.items = iter(items)
        self.closed = False

    def __aiter__(self) -> EventSubscription:
        return self

    async def __anext__(self) -> ReplayItem | StreamGap:
        try:
            return next(self.items)
        except StopIteration as error:
            raise StopAsyncIteration from error

    async def aclose(self) -> None:
        self.closed = True


class MismatchedReplayPort:
    def __init__(self) -> None:
        self.subscription = TrackingSubscription(
            ReplayItem(
                "1-0",
                RunEventEnvelope(
                    tenant_id="tenant_2",
                    session_id="session_1",
                    run_id="run_1",
                    turn_id="turn_1",
                    execution_attempt=1,
                    event_sequence=0,
                    event=LiveContentDelta(0, "answer"),
                    occurred_at=NOW,
                ),
            ),
        )

    async def get_run(self, **_: object) -> RunReadModel:
        return RunReadModel(
            tenant_id="tenant_1",
            session_id="session_1",
            run_id="run_1",
            status=RunStatus.RUNNING,
            wait_reason=None,
            aggregate_version=1,
            result=None,
        )

    def follow(self, **_: object) -> EventSubscription:
        return self.subscription


class GapReplayPort(MismatchedReplayPort):
    def __init__(self) -> None:
        self.subscription = TrackingSubscription(
            StreamGap(
                tenant_id="tenant_1",
                session_id="session_1",
                run_id="run_1",
                requested_cursor="other-cursor",
                oldest_available_cursor="2-0",
                reason="trimmed",
            ),
        )


def _artifact(session_id: str, artifact_id: str) -> ArtifactRecord:
    return ArtifactRecord(
        id=artifact_id,
        session_id=session_id,
        filename="drawing.png",
        media_type="image/png",
        size_bytes=3,
        created_at=NOW,
    )


def test_run_services_reject_mismatched_port_receipts() -> None:
    async def scenario() -> None:
        port = MismatchedRunPort()
        submission = RunSubmission("session_1", "submission_1", "inspect")
        command = DurableRunCommand("run_1", "command_1", "cancel")

        with pytest.raises(RuntimeError, match="submission receipt"):
            await RunSubmissionService(port).submit(SCOPE, submission)
        with pytest.raises(RuntimeError, match="command receipt"):
            await RunCommandService(port).submit(SCOPE, "session_1", command)
        with pytest.raises(RuntimeError, match="run read model"):
            await RunQueryService(port).get(SCOPE, "session_1", "run_1")

    asyncio.run(scenario())


def test_artifact_service_rejects_cross_session_or_wrong_id_results() -> None:
    async def scenario() -> None:
        service = ArtifactService(MismatchedArtifactPort())

        with pytest.raises(RuntimeError, match="uploaded artifact"):
            await service.upload(SCOPE, "session_1", "upload_1", b"abc", None, "image/png")
        with pytest.raises(RuntimeError, match="artifact page"):
            await service.list(SCOPE, "session_1")
        with pytest.raises(RuntimeError, match="artifact content"):
            await service.read(SCOPE, "session_1", ARTIFACT_ID)

    asyncio.run(scenario())


def test_event_stream_rejects_replay_items_for_another_tenant() -> None:
    async def scenario() -> None:
        port = MismatchedReplayPort()
        events = await RunEventStream(port, port).subscribe(
            SCOPE,
            "session_1",
            "run_1",
        )

        with pytest.raises(RuntimeError, match="replay item"):
            async for _ in events:
                pass
        assert port.subscription.closed is True

    asyncio.run(scenario())


def test_event_stream_binds_gap_to_requested_cursor() -> None:
    async def scenario() -> None:
        port = GapReplayPort()
        events = await RunEventStream(port, port).subscribe(
            SCOPE,
            "session_1",
            "run_1",
            "1-0",
        )

        with pytest.raises(RuntimeError, match="stream gap"):
            async for _ in events:
                pass
        assert port.subscription.closed is True

    asyncio.run(scenario())


def test_event_stream_closes_underlying_tail_when_consumer_closes() -> None:
    async def scenario() -> None:
        port = MismatchedReplayPort()
        port.subscription = TrackingSubscription(
            ReplayItem(
                "1-0",
                RunEventEnvelope(
                    tenant_id="tenant_1",
                    session_id="session_1",
                    run_id="run_1",
                    turn_id="turn_1",
                    execution_attempt=1,
                    event_sequence=0,
                    event=LiveContentDelta(0, "answer"),
                    occurred_at=NOW,
                ),
            ),
        )
        events = await RunEventStream(port, port).subscribe(
            SCOPE,
            "session_1",
            "run_1",
        )

        assert (await anext(events)).cursor == "1-0"
        await events.aclose()

        assert port.subscription.closed is True

    asyncio.run(scenario())


def test_event_stream_closes_underlying_tail_before_first_event() -> None:
    async def scenario() -> None:
        port = MismatchedReplayPort()
        events = await RunEventStream(port, port).subscribe(
            SCOPE,
            "session_1",
            "run_1",
        )

        await events.aclose()

        assert port.subscription.closed is True

    asyncio.run(scenario())
