import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from agentos.artifacts import ArtifactRecord
from agentos.artifacts.types import ArtifactPage
from agentos.distributed.models import (
    ArtifactContent,
    ReplayBatch,
    RequestScope,
    RunReadModel,
    RunSubmission,
    RunSubmissionReceipt,
)
from agentos.distributed.errors import RunNotFoundError
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


@dataclass
class RecordingPort:
    calls: list[tuple[str, tuple[object, ...]]] = field(default_factory=list)

    async def submit(self, *, scope: RequestScope, submission: RunSubmission) -> RunSubmissionReceipt:
        self.calls.append(("submit", (scope, submission)))
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
        self.calls.append(("submit_command", (scope, session_id, command)))
        return DurableCommandReceipt(command.run_id, command.command_id, command.kind, 2, False)

    async def get_run(
        self,
        *,
        scope: RequestScope,
        session_id: str,
        run_id: str,
    ) -> RunReadModel | None:
        self.calls.append(("get_run", (scope, session_id, run_id)))
        if run_id == "missing":
            return None
        return RunReadModel(
            tenant_id=scope.tenant_id,
            session_id=session_id,
            run_id=run_id,
            status=RunStatus.RUNNING,
            wait_reason=None,
            aggregate_version=1,
            result=None,
        )

    def follow(
        self,
        *,
        scope: RequestScope,
        session_id: str,
        run_id: str,
        after: str | None,
    ) -> AsyncIterator[object]:
        self.calls.append(("follow", (scope, session_id, run_id, after)))

        async def events() -> AsyncIterator[object]:
            if False:
                yield ReplayBatch((), None)

        return events()

    async def upload(
        self,
        *,
        scope: RequestScope,
        session_id: str,
        upload_id: str,
        data: bytes,
        filename: str | None,
        media_type: str,
    ) -> ArtifactRecord:
        self.calls.append(
            ("upload", (scope, session_id, upload_id, data, filename, media_type)),
        )
        return _artifact(session_id)

    async def list(
        self,
        *,
        scope: RequestScope,
        session_id: str,
        cursor: str | None,
        limit: int,
    ) -> ArtifactPage:
        self.calls.append(("list", (scope, session_id, cursor, limit)))
        return ArtifactPage((_artifact(session_id),), None)

    async def read(
        self,
        *,
        scope: RequestScope,
        session_id: str,
        artifact_id: str,
    ) -> ArtifactContent:
        self.calls.append(("read", (scope, session_id, artifact_id)))
        return ArtifactContent(_artifact(session_id), b"abc")

    async def delete(
        self,
        *,
        scope: RequestScope,
        session_id: str,
        artifact_id: str,
        deletion_id: str,
    ) -> None:
        self.calls.append(("delete", (scope, session_id, artifact_id, deletion_id)))


def _artifact(session_id: str) -> ArtifactRecord:
    return ArtifactRecord(
        id=ARTIFACT_ID,
        session_id=session_id,
        filename="drawing.png",
        media_type="image/png",
        size_bytes=3,
        created_at=NOW,
    )


def test_run_services_forward_scope_without_running_the_loop() -> None:
    async def scenario() -> None:
        port = RecordingPort()
        scope = RequestScope("tenant_1", "user_1")
        submission = RunSubmission("session_1", "submission_1", "inspect")
        command = DurableRunCommand("run_1", "command_1", "cancel")

        receipt = await RunSubmissionService(port).submit(scope, submission)
        command_receipt = await RunCommandService(port).submit(
            scope,
            "session_1",
            command,
        )
        run = await RunQueryService(port).get(scope, "session_1", "run_1")

        assert receipt.run_id == "run_1"
        assert command_receipt.command_id == "command_1"
        assert run.run_id == "run_1"
        assert port.calls == [
            ("submit", (scope, submission)),
            ("submit_command", (scope, "session_1", command)),
            ("get_run", (scope, "session_1", "run_1")),
        ]

    asyncio.run(scenario())


def test_run_query_maps_unknown_and_cross_tenant_to_same_not_found() -> None:
    async def scenario() -> None:
        port = RecordingPort()
        service = RunQueryService(port)

        for tenant in ("tenant_1", "tenant_2"):
            with pytest.raises(RunNotFoundError, match="^run not found$"):
                await service.get(
                    RequestScope(tenant, "user_1"),
                    "session_1",
                    "missing",
                )

    asyncio.run(scenario())


def test_event_stream_checks_postgres_truth_before_following_replay() -> None:
    async def scenario() -> None:
        port = RecordingPort()
        scope = RequestScope("tenant_1", "user_1")

        events = await RunEventStream(port, port).subscribe(
            scope,
            "session_1",
            "run_1",
            "1-0",
        )

        assert hasattr(events, "__aiter__")
        assert port.calls == [
            ("get_run", (scope, "session_1", "run_1")),
            ("follow", (scope, "session_1", "run_1", "1-0")),
        ]

    asyncio.run(scenario())


def test_event_stream_rejects_missing_run_before_opening_replay() -> None:
    async def scenario() -> None:
        port = RecordingPort()
        scope = RequestScope("tenant_1", "user_1")

        with pytest.raises(RunNotFoundError, match="^run not found$"):
            await RunEventStream(port, port).subscribe(
                scope,
                "session_1",
                "missing",
            )

        assert port.calls == [("get_run", (scope, "session_1", "missing"))]

    asyncio.run(scenario())


def test_artifact_service_forwards_tenant_scoped_operations() -> None:
    async def scenario() -> None:
        port = RecordingPort()
        scope = RequestScope("tenant_1", "user_1")
        service = ArtifactService(port)

        uploaded = await service.upload(
            scope,
            "session_1",
            "upload_1",
            b"abc",
            "drawing.png",
            "image/png",
        )
        page = await service.list(scope, "session_1", None, 20)
        content = await service.read(scope, "session_1", ARTIFACT_ID)
        await service.delete(scope, "session_1", ARTIFACT_ID, "delete_1")

        assert uploaded.id == ARTIFACT_ID
        assert page.items == (uploaded,)
        assert content.data == b"abc"
        assert [name for name, _ in port.calls] == [
            "upload",
            "list",
            "read",
            "delete",
        ]

    asyncio.run(scenario())


def test_same_ids_in_different_tenants_remain_distinct_calls() -> None:
    async def scenario() -> None:
        port = RecordingPort()
        service = RunQueryService(port)

        await service.get(
            RequestScope("tenant_1", "user_1"),
            "session_1",
            "run_1",
        )
        await service.get(
            RequestScope("tenant_2", "user_1"),
            "session_1",
            "run_1",
        )

        assert port.calls[0][1][0] != port.calls[1][1][0]

    asyncio.run(scenario())
