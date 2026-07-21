from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from agentos._waiting import WaitReason
from agentos.artifacts.runtime import ArtifactPolicy
from agentos.artifacts.types import ArtifactRecord
from agentos.channels.a2a_endpoint import A2AEndpoint
from agentos.channels.a2a_stream import A2ASseResponse
from agentos.channels.service_wiring import (
    ChannelServices,
    FixedScopeAuthenticator,
    RejectAllChannelAuthenticator,
)
from agentos.distributed.a2a_models import (
    A2APushConfigRecord,
    A2APushConfigRecordPage,
    A2ATaskBinding,
    A2ATaskListItem,
    A2ATaskListPage,
)
from agentos.distributed.a2a_services import (
    A2APushService,
    A2ATaskCatalogService,
    A2ATaskService,
)
from agentos.distributed.models import (
    LiveContentDelta,
    ReplayItem,
    RequestScope,
    RunEventEnvelope,
    RunReadModel,
    RunSubmissionReceipt,
)
from agentos.distributed.services import (
    ArtifactService,
    RunCommandService,
    RunEventStream,
    RunQueryService,
    RunSubmissionService,
)
from agentos.runtime.durable_commands import DurableCommandReceipt
from agentos.runtime.payloads import ProtectedPayloadRef
from agentos.runtime.run import AgentResult
from agentos.runtime.run_state import RunStatus
from agentos.transports.a2a import (
    A2A_SNAPSHOT_RESUME_EXTENSION,
    A2AAgentCapabilities,
    A2AAgentCard,
    A2AAgentExtension,
    A2AAgentInterface,
    A2AAgentSkill,
    A2AOperationRequest,
    decode_operation_response,
    encode_operation_request,
)
from agentos.transports.http.request_types import HttpHeaders
from agentos.transports.http.response_types import HttpResponse


SCOPE = RequestScope("tenant_1", "principal_1")
NOW = datetime(2026, 7, 21, 8, 30, tzinfo=UTC)


@dataclass
class RunPort:
    runs: dict[tuple[str, str, str], RunReadModel] = field(default_factory=dict)
    next_run: int = 1
    submitted_status: RunStatus = RunStatus.QUEUED
    submitted_wait_reason: WaitReason | None = None
    submitted_result: AgentResult | None = None

    async def submit(self, *, scope: RequestScope, submission: object) -> RunSubmissionReceipt:
        run_id = f"run_{self.next_run}"
        self.next_run += 1
        session_id = submission.session_id  # type: ignore[attr-defined]
        submission_id = submission.submission_id  # type: ignore[attr-defined]
        self.runs[(scope.tenant_id, session_id, run_id)] = RunReadModel(
            scope.tenant_id,
            session_id,
            run_id,
            self.submitted_status,
            self.submitted_wait_reason,
            1,
            self.submitted_result,
        )
        return RunSubmissionReceipt(session_id, run_id, submission_id, 1, False)

    async def submit_command(
        self,
        *,
        scope: RequestScope,
        session_id: str,
        command: object,
    ) -> DurableCommandReceipt:
        key = (scope.tenant_id, session_id, command.run_id)  # type: ignore[attr-defined]
        current = self.runs[key]
        status = (
            RunStatus.CANCELLED
            if command.kind == "cancel"  # type: ignore[attr-defined]
            else RunStatus.QUEUED
        )
        self.runs[key] = _run(
            session_id,
            current.run_id,
            status,
            version=current.aggregate_version + 1,
        )
        return DurableCommandReceipt(
            current.run_id,
            command.command_id,  # type: ignore[attr-defined]
            command.kind,  # type: ignore[attr-defined]
            current.aggregate_version + 1,
            False,
        )

    async def get_run(
        self,
        *,
        scope: RequestScope,
        session_id: str,
        run_id: str,
    ) -> RunReadModel | None:
        return self.runs.get((scope.tenant_id, session_id, run_id))


@dataclass
class TaskPort:
    bindings: dict[tuple[str, str], A2ATaskBinding] = field(default_factory=dict)

    async def bind(
        self,
        *,
        scope: RequestScope,
        binding: A2ATaskBinding,
    ) -> A2ATaskBinding:
        self.bindings[(scope.tenant_id, binding.task_id)] = binding
        return binding

    async def resolve(
        self,
        *,
        scope: RequestScope,
        task_id: str,
    ) -> A2ATaskBinding | None:
        return self.bindings.get((scope.tenant_id, task_id))


@dataclass
class CatalogPort:
    task_port: TaskPort
    run_port: RunPort

    async def list(self, *, scope: RequestScope, query: object) -> A2ATaskListPage:
        items = tuple(
            sorted(
                (
                    A2ATaskListItem(
                        binding=binding,
                        run=self.run_port.runs[
                            (scope.tenant_id, binding.session_id, binding.run_id)
                        ],
                        status_updated_at=NOW,
                    )
                    for (tenant_id, _), binding in self.task_port.bindings.items()
                    if tenant_id == scope.tenant_id
                ),
                key=lambda item: (item.status_updated_at, item.binding.task_id),
                reverse=True,
            )
        )
        return A2ATaskListPage(
            items=items,
            next_page_token="",
            page_size=query.page_size,  # type: ignore[attr-defined]
            total_size=len(items),
        )


@dataclass
class PushPort:
    records: dict[tuple[str, str, str], A2APushConfigRecord] = field(
        default_factory=dict,
    )

    async def create(self, **values: object) -> A2APushConfigRecord:
        scope = values["scope"]
        record = values["record"]
        self.records[(scope.tenant_id, record.task_id, record.config_id)] = record  # type: ignore[attr-defined]
        return record  # type: ignore[return-value]

    async def get(
        self,
        *,
        scope: RequestScope,
        task_id: str,
        config_id: str,
    ) -> A2APushConfigRecord | None:
        return self.records.get((scope.tenant_id, task_id, config_id))

    async def list(
        self,
        *,
        scope: RequestScope,
        task_id: str,
        page_size: int,
        page_token: str | None,
    ) -> A2APushConfigRecordPage:
        del page_size, page_token
        records = tuple(
            value
            for (tenant_id, current_task_id, _), value in self.records.items()
            if tenant_id == scope.tenant_id and current_task_id == task_id
        )
        return A2APushConfigRecordPage(records=records, next_page_token="")

    async def delete(
        self,
        *,
        scope: RequestScope,
        task_id: str,
        config_id: str,
        operation_id: str,
    ) -> None:
        del operation_id
        del self.records[(scope.tenant_id, task_id, config_id)]


class UrlPolicy:
    def validate(self, url: str) -> None:
        assert url.startswith("https://")


class PayloadProtector:
    def protect(self, payload: object, *, context: object) -> ProtectedPayloadRef:
        del payload, context
        return ProtectedPayloadRef("sealed", "digest")

    def unprotect(self, reference: object, *, context: object) -> object:
        raise AssertionError((reference, context))


class Subscription:
    def __init__(
        self,
        item: ReplayItem | None = None,
        on_next: Callable[[], None] | None = None,
    ) -> None:
        self.close_calls = 0
        self.item = item
        self.on_next = on_next

    def __aiter__(self) -> Subscription:
        return self

    async def __anext__(self) -> object:
        item = self.item
        if item is None:
            raise StopAsyncIteration
        self.item = None
        if self.on_next is not None:
            self.on_next()
        return item

    async def aclose(self) -> None:
        self.close_calls += 1


@dataclass
class ReplayPort:
    subscriptions: list[Subscription] = field(default_factory=list)
    on_follow: Callable[[], None] | None = None

    async def high_water(self, **values: object) -> str:
        del values
        return "1-0"

    def follow(self, **values: object) -> Subscription:
        scope = values["scope"]
        item = (
            None
            if self.on_follow is None
            else ReplayItem(
                "2-0",
                RunEventEnvelope(
                    tenant_id=scope.tenant_id,  # type: ignore[attr-defined]
                    session_id=values["session_id"],  # type: ignore[arg-type]
                    run_id=values["run_id"],  # type: ignore[arg-type]
                    turn_id="turn_1",
                    execution_attempt=1,
                    event_sequence=1,
                    event=LiveContentDelta(0, "done"),
                    occurred_at=NOW,
                ),
            )
        )
        subscription = Subscription(item, self.on_follow)
        self.subscriptions.append(subscription)
        return subscription


@dataclass
class ArtifactPort:
    uploads: list[dict[str, object]] = field(default_factory=list)

    async def upload(self, **values: object) -> ArtifactRecord:
        self.uploads.append(values)
        index = len(self.uploads)
        return ArtifactRecord(
            id=f"art_00000000-0000-4000-8000-{index:012d}",
            session_id=values["session_id"],  # type: ignore[arg-type]
            filename=values["filename"],  # type: ignore[arg-type]
            media_type=values["media_type"],  # type: ignore[arg-type]
            size_bytes=len(values["data"]),  # type: ignore[arg-type]
            created_at=NOW,
        )


@dataclass(frozen=True)
class CardProvider:
    public: A2AAgentCard
    extended: A2AAgentCard | None

    async def get_public_card(self, *, scope: RequestScope) -> A2AAgentCard:
        assert scope == SCOPE
        return self.public

    async def get_extended_card(self, *, scope: RequestScope) -> A2AAgentCard | None:
        assert scope == SCOPE
        return self.extended


@dataclass
class Fixture:
    endpoint: A2AEndpoint
    run_port: RunPort
    task_port: TaskPort
    push_port: PushPort
    replay_port: ReplayPort
    artifact_port: ArtifactPort


def _fixture(
    *,
    reject_auth: bool = False,
    public_card: A2AAgentCard | None = None,
    has_extended_card: bool = True,
    submitted_status: RunStatus = RunStatus.QUEUED,
    submitted_wait_reason: WaitReason | None = None,
    submitted_result: AgentResult | None = None,
    interface_tenant: str | None = None,
) -> Fixture:
    run_port = RunPort(
        submitted_status=submitted_status,
        submitted_wait_reason=submitted_wait_reason,
        submitted_result=submitted_result,
    )
    task_port = TaskPort()
    push_port = PushPort()
    replay_port = ReplayPort()
    artifact_port = ArtifactPort()
    selected_public_card = public_card or _card("public")
    selected_extended_card = _card("extended") if has_extended_card else None
    services = ChannelServices(
        run_submissions=RunSubmissionService(run_port),  # type: ignore[arg-type]
        run_commands=RunCommandService(run_port),  # type: ignore[arg-type]
        run_queries=RunQueryService(run_port),  # type: ignore[arg-type]
        run_events=RunEventStream(run_port, replay_port),  # type: ignore[arg-type]
        artifacts=ArtifactService(  # type: ignore[arg-type]
            artifact_port,
            ArtifactPolicy(
                allowed_media_types=frozenset(
                    {"application/octet-stream", "image/png"},
                ),
            ),
        ),
        a2a_tasks=A2ATaskService(task_port),  # type: ignore[arg-type]
        a2a_catalog=A2ATaskCatalogService(CatalogPort(task_port, run_port)),
        a2a_push=A2APushService(  # type: ignore[arg-type]
            push_port,
            UrlPolicy(),
            PayloadProtector(),
        ),
        a2a_cards=CardProvider(selected_public_card, selected_extended_card),
    )
    authenticator = (
        RejectAllChannelAuthenticator()
        if reject_auth
        else FixedScopeAuthenticator(SCOPE)
    )
    return Fixture(
        A2AEndpoint(
            services,
            authenticator,
            heartbeat_interval=0.01,
            interface_tenant=interface_tenant,
        ),
        run_port,
        task_port,
        push_port,
        replay_port,
        artifact_port,
    )


def _card(
    name: str,
    *,
    extensions: tuple[A2AAgentExtension, ...] | None = None,
    tenant: str | None = None,
) -> A2AAgentCard:
    return A2AAgentCard(
        name=name,
        description="test card",
        supported_interfaces=(
            A2AAgentInterface(
                "https://agent.example/a2a",
                "JSONRPC",
                "1.0",
                tenant,
            ),
        ),
        version="1.0.0",
        capabilities=A2AAgentCapabilities(
            streaming=True,
            push_notifications=True,
            extended_agent_card=True,
            extensions=(
                (A2AAgentExtension(A2A_SNAPSHOT_RESUME_EXTENSION),)
                if extensions is None
                else extensions
            ),
        ),
        default_input_modes=("text/plain", "application/json", "image/png"),
        default_output_modes=("text/plain",),
        skills=(A2AAgentSkill("skill_1", "test", "test skill", ("test",)),),
    )


def _run(
    session_id: str,
    run_id: str,
    status: RunStatus,
    *,
    version: int = 1,
) -> RunReadModel:
    return RunReadModel(
        tenant_id=SCOPE.tenant_id,
        session_id=session_id,
        run_id=run_id,
        status=status,
        wait_reason=None,
        aggregate_version=version,
        result=None,
    )


def _headers(
    *,
    version: str | None = "1.0",
    extensions: str | None = None,
    last_event_id: str | None = None,
) -> HttpHeaders:
    values = [("Content-Type", "application/json")]
    if version is not None:
        values.append(("A2A-Version", version))
    if extensions is not None:
        values.append(("A2A-Extensions", extensions))
    if last_event_id is not None:
        values.append(("Last-Event-ID", last_event_id))
    return HttpHeaders(tuple(values))


async def _request(
    endpoint: A2AEndpoint,
    params: object,
    *,
    request_id: str = "rpc_1",
) -> HttpResponse | A2ASseResponse:
    body = encode_operation_request(
        A2AOperationRequest(request_id=request_id, params=params),  # type: ignore[arg-type]
    )
    return await endpoint.handle(
        headers=_headers(),
        body=body,
        request_id="opaque_request",
    )


def _decoded(response: HttpResponse) -> object:
    assert response.status_code == 200
    assert dict(response.headers)["Content-Type"] == "application/json"
    return decode_operation_response(response.body)
