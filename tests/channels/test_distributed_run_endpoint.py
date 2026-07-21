from __future__ import annotations

from dataclasses import dataclass, field
import json

from agentos.channels.auth import ChannelAuthContext
from agentos.channels.run_endpoint import RunEndpoint
from agentos.channels.service_wiring import (
    ChannelServices,
    RejectAllChannelAuthenticator,
)
from agentos.distributed.models import (
    RequestScope,
    RunReadModel,
    RunSubmission,
    RunSubmissionReceipt,
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
from tests.planning._async import async_test


SCOPE = RequestScope("tenant_1", "principal_1")


@dataclass
class RecordingAuthenticator:
    calls: list[ChannelAuthContext] = field(default_factory=list)

    async def authenticate(
        self,
        headers: HttpHeaders,
        *,
        context: ChannelAuthContext,
    ) -> RequestScope:
        del headers
        self.calls.append(context)
        return SCOPE


@dataclass
class RunPort:
    submissions: list[tuple[RequestScope, RunSubmission]] = field(default_factory=list)
    commands: list[tuple[RequestScope, str, DurableRunCommand]] = field(
        default_factory=list,
    )

    async def submit(
        self,
        *,
        scope: RequestScope,
        submission: RunSubmission,
    ) -> RunSubmissionReceipt:
        self.submissions.append((scope, submission))
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
        self.commands.append((scope, session_id, command))
        return DurableCommandReceipt(command.run_id, command.command_id, command.kind, 2, False)


class QueryPort:
    async def get_run(
        self,
        *,
        scope: RequestScope,
        session_id: str,
        run_id: str,
    ) -> RunReadModel:
        return RunReadModel(
            scope.tenant_id,
            session_id,
            run_id,
            RunStatus.RUNNING,
            None,
            2,
            None,
        )


def _services(run_port: RunPort) -> ChannelServices:
    query_port = QueryPort()
    return ChannelServices(
        RunSubmissionService(run_port),  # type: ignore[arg-type]
        RunCommandService(run_port),  # type: ignore[arg-type]
        RunQueryService(query_port),  # type: ignore[arg-type]
        RunEventStream(query_port, object()),  # type: ignore[arg-type]
        ArtifactService(object()),  # type: ignore[arg-type]
    )


@async_test
async def test_run_endpoint_routes_only_through_authenticated_services() -> None:
    port = RunPort()
    authenticator = RecordingAuthenticator()
    endpoint = RunEndpoint(_services(port), authenticator)  # type: ignore[arg-type]
    headers = HttpHeaders(
        (
            ("Content-Type", "application/json"),
            ("Idempotency-Key", "request_1"),
            ("X-Tenant-ID", "attacker"),
        ),
    )

    submitted = await endpoint.submit(
        session_id="session_1",
        headers=headers,
        body=b'{"content":"hello"}',
        request_id="trace_1",
    )
    commanded = await endpoint.command(
        session_id="session_1",
        run_id="run_1",
        headers=headers,
        body=b'{"kind":"cancel","payload":{}}',
        request_id="trace_2",
    )
    queried = await endpoint.get(
        session_id="session_1",
        run_id="run_1",
        headers=HttpHeaders(()),
        request_id="trace_3",
    )

    assert submitted.status_code == 202
    assert commanded.status_code == 202
    assert queried.status_code == 200
    assert port.submissions[0][0] == SCOPE
    assert port.commands[0][0] == SCOPE
    assert [call.operation for call in authenticator.calls] == [
        "submit_run",
        "submit_command",
        "query_run",
    ]
    assert json.loads(queried.body)["status"] == "running"


@async_test
async def test_run_endpoint_maps_fail_closed_authentication() -> None:
    endpoint = RunEndpoint(_services(RunPort()), RejectAllChannelAuthenticator())

    response = await endpoint.get(
        session_id="session_1",
        run_id="run_1",
        headers=HttpHeaders(()),
        request_id="trace_1",
    )

    assert response.status_code == 401
    assert json.loads(response.body) == {
        "code": "authentication_required",
        "message": "authentication required",
        "request_id": "trace_1",
    }
