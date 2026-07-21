from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256

import pytest

from agentos.artifacts.runtime import ArtifactPolicy
from agentos.artifacts.types import (
    ArtifactMediaTypeUnsupportedError,
    ArtifactTooLargeError,
)
from agentos._json_values import FrozenJsonObject
from agentos.distributed.a2a_models import (
    A2APushAttemptResolution,
    A2APushAuthenticationInput,
    A2APushConfigInput,
    A2APushConfigPage,
    A2APushConfigRecord,
    A2APushConfigRecordPage,
    A2APushFailureCategory,
    A2ATaskBinding,
    A2APushDeliveryTarget,
    A2ATaskState,
)
from agentos.distributed.a2a_services import A2APushService, A2ATaskService
from agentos.distributed.errors import (
    A2APushAttemptFencedError,
    A2APushConfigNotFoundError,
    A2ATaskConflictError,
    A2ATaskNotFoundError,
    CommandNotDueError,
)
from agentos.distributed.models import RequestScope
from agentos.distributed.postgres._commands import _validate_continuation
from agentos.distributed.services import ArtifactService
from agentos.runtime.durable_commands import DurableRunCommand
from agentos.runtime.payloads import ProtectedPayloadRef
from agentos.runtime.run_state import RunState, RunStatus
from agentos._waiting import WaitReason
from tests.planning._async import async_test


SCOPE = RequestScope("tenant_1", "principal_1")


@dataclass
class RecordingArtifactPort:
    calls: list[bytes] = field(default_factory=list)

    async def upload(self, **values: object) -> object:
        self.calls.append(values["data"])  # type: ignore[arg-type]
        raise AssertionError("policy must reject before port upload")


@dataclass
class RecordingA2APort:
    bindings: dict[tuple[str, str], A2ATaskBinding] = field(default_factory=dict)

    async def bind(
        self,
        *,
        scope: RequestScope,
        binding: A2ATaskBinding,
    ) -> A2ATaskBinding:
        key = (scope.tenant_id, binding.task_id)
        current = self.bindings.get(key)
        if current is not None and current != binding:
            raise A2ATaskConflictError()
        self.bindings[key] = binding
        return binding

    async def resolve(
        self,
        *,
        scope: RequestScope,
        task_id: str,
    ) -> A2ATaskBinding | None:
        return self.bindings.get((scope.tenant_id, task_id))


@dataclass
class RecordingPushPort:
    records: dict[tuple[str, str, str], A2APushConfigRecord] = field(
        default_factory=dict,
    )
    create_calls: list[tuple[A2ATaskBinding, A2APushConfigRecord, str]] = field(
        default_factory=list,
    )
    list_calls: list[tuple[str, int, str | None]] = field(default_factory=list)

    async def create(
        self,
        *,
        scope: RequestScope,
        binding: A2ATaskBinding,
        record: A2APushConfigRecord,
        operation_id: str,
    ) -> A2APushConfigRecord:
        self.create_calls.append((binding, record, operation_id))
        self.records[(scope.tenant_id, binding.task_id, record.config_id)] = record
        return record

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
        self.list_calls.append((task_id, page_size, page_token))
        return A2APushConfigRecordPage(
            records=tuple(
                record
                for (tenant_id, current_task_id, _), record in self.records.items()
                if tenant_id == scope.tenant_id and current_task_id == task_id
            ),
            next_page_token="next_push_page" if page_token is None else "",
        )

    async def delete(
        self,
        *,
        scope: RequestScope,
        task_id: str,
        config_id: str,
        operation_id: str,
    ) -> None:
        del operation_id
        try:
            del self.records[(scope.tenant_id, task_id, config_id)]
        except KeyError:
            raise A2APushConfigNotFoundError() from None


@dataclass
class RecordingPushUrlPolicy:
    urls: list[str] = field(default_factory=list)

    def validate(self, url: str) -> None:
        self.urls.append(url)


@dataclass
class RecordingV1PushPort:
    calls: list[tuple[A2ATaskBinding, A2APushConfigRecord, str]] = field(
        default_factory=list,
    )

    async def create(
        self,
        *,
        scope: RequestScope,
        binding: A2ATaskBinding,
        record: A2APushConfigRecord,
        operation_id: str,
    ) -> A2APushConfigRecord:
        del scope
        self.calls.append((binding, record, operation_id))
        return record


@dataclass
class RecordingPayloadProtector:
    payloads: list[tuple[FrozenJsonObject, object]] = field(default_factory=list)

    def protect(
        self,
        payload: FrozenJsonObject,
        *,
        context: object,
    ) -> ProtectedPayloadRef:
        self.payloads.append((payload, context))
        return ProtectedPayloadRef("sealed-token", "secret-digest")

    def unprotect(self, reference: object, *, context: object) -> FrozenJsonObject:
        raise AssertionError("set must not unprotect payloads")


@async_test
async def test_artifact_service_enforces_policy_before_port_io() -> None:
    port = RecordingArtifactPort()
    service = ArtifactService(
        port,  # type: ignore[arg-type]
        policy=ArtifactPolicy(
            allowed_media_types=frozenset({"image/png"}),
            max_size_bytes=3,
        ),
    )

    with pytest.raises(ArtifactMediaTypeUnsupportedError):
        await service.upload(
            SCOPE,
            "session_1",
            "upload_1",
            b"abc",
            "drawing.jpg",
            "image/jpeg",
        )
    with pytest.raises(ArtifactTooLargeError):
        await service.upload(
            SCOPE,
            "session_1",
            "upload_2",
            b"abcd",
            "drawing.png",
            "image/png",
        )

    assert port.calls == []


@async_test
async def test_a2a_task_service_binds_and_resolves_tenant_scope() -> None:
    port = RecordingA2APort()
    service = A2ATaskService(port)

    binding = await service.bind(
        SCOPE,
        task_id="run_1",
        session_id="session_1",
        run_id="run_1",
    )
    duplicate = await service.bind(
        SCOPE,
        task_id="run_1",
        session_id="session_1",
        run_id="run_1",
    )

    assert duplicate == binding
    assert await service.resolve(SCOPE, "run_1") == binding
    with pytest.raises(A2ATaskNotFoundError, match="^a2a task not found$"):
        await service.resolve(RequestScope("tenant_2", "principal_1"), "run_1")


@async_test
async def test_a2a_push_service_protects_secrets_before_port_and_redacts_view() -> None:
    port = RecordingPushPort()
    policy = RecordingPushUrlPolicy()
    protector = RecordingPayloadProtector()
    service = A2APushService(port, policy, protector)  # type: ignore[arg-type]
    binding = A2ATaskBinding("tenant_1", "run_1", "session_1", "run_1")
    config = A2APushConfigInput(
        config_id="push_1",
        url="https://push.example.test/a2a",
        token="wire-token",
        authentication=A2APushAuthenticationInput(
            "Bearer",
            credentials="wire-credential",
        ),
    )

    view = await service.create(
        SCOPE,
        binding=binding,
        config_input=config,
        operation_id="request_1",
    )

    assert policy.urls == ["https://push.example.test/a2a"]
    assert len(protector.payloads) == 1
    protected_payload, context = protector.payloads[0]
    assert dict(protected_payload) == {
        "credentials": "wire-credential",
        "token": "wire-token",
    }
    assert context.tenant_id == "tenant_1"  # type: ignore[attr-defined]
    assert context.session_id == "session_1"  # type: ignore[attr-defined]
    assert port.create_calls[0][1].secret_ref == ProtectedPayloadRef(
        "sealed-token",
        "secret-digest",
    )
    assert view.task_id == "run_1"
    assert view.config_id == "push_1"
    assert view.authentication_scheme == "Bearer"
    assert "wire-token" not in repr(view)
    assert "wire-credential" not in repr(view)
    assert "wire-token" not in repr(config)
    assert "wire-credential" not in repr(config)


@async_test
async def test_a2a_push_service_get_list_delete_are_tenant_scoped() -> None:
    port = RecordingPushPort()
    service = A2APushService(
        port,
        RecordingPushUrlPolicy(),
        RecordingPayloadProtector(),  # type: ignore[arg-type]
    )
    binding = A2ATaskBinding("tenant_1", "run_1", "session_1", "run_1")
    created = await service.create(
        SCOPE,
        binding=binding,
        config_input=A2APushConfigInput(
            config_id="push_1",
            url="https://push.example.test/a2a",
        ),
        operation_id="request_1",
    )

    assert await service.get(SCOPE, task_id="run_1", config_id="push_1") == created
    assert await service.list(SCOPE, task_id="run_1", page_size=1) == A2APushConfigPage(
        configs=(created,),
        next_page_token="next_push_page",
    )
    assert port.list_calls == [("run_1", 1, None)]
    with pytest.raises(A2APushConfigNotFoundError):
        await service.get(
            RequestScope("tenant_2", "principal_1"),
            task_id="run_1",
            config_id="push_1",
        )
    await service.delete(
        SCOPE,
        task_id="run_1",
        config_id="push_1",
        operation_id="a2a_op_2",
    )
    with pytest.raises(A2APushConfigNotFoundError):
        await service.get(SCOPE, task_id="run_1", config_id="push_1")


@async_test
async def test_a2a_push_service_validates_page_request_before_port_io() -> None:
    port = RecordingPushPort()
    service = A2APushService(
        port,
        RecordingPushUrlPolicy(),
        RecordingPayloadProtector(),  # type: ignore[arg-type]
    )

    with pytest.raises(ValueError, match="page_size"):
        await service.list(SCOPE, task_id="run_1", page_size=101)
    with pytest.raises(ValueError, match="page_token"):
        await service.list(SCOPE, task_id="run_1", page_token="")

    assert port.list_calls == []


def test_a2a_task_binding_v1_uses_run_id_as_task_id() -> None:
    with pytest.raises(ValueError, match="task_id must equal run_id"):
        A2ATaskBinding("tenant_1", "task_1", "session_1", "run_1")


def test_a2a_push_input_uses_v1_singular_authentication_and_optional_id() -> None:
    authentication = A2APushAuthenticationInput(
        "Bearer",
        credentials="wire-credential",
    )
    config = A2APushConfigInput(
        config_id=None,
        url="https://push.example.test/a2a",
        authentication=authentication,
    )

    assert authentication.scheme == "Bearer"
    assert config.config_id is None
    assert "wire-credential" not in repr(authentication)


def test_a2a_push_delivery_target_is_typed_frozen_and_secret_safe() -> None:
    target = A2APushDeliveryTarget(
        scope=SCOPE,
        outbox_id="outbox_1",
        delivery_id="delivery_1",
        task_id="run_1",
        context_id="session_1",
        config_id="push_1",
        url="https://push.example.test/a2a",
        authentication_scheme="Bearer",
        secret_ref=ProtectedPayloadRef("sealed-token", "secret-digest"),
        event_id="event_1",
        protocol_version="1.0",
        status_sequence=4,
        task_state=A2ATaskState.COMPLETED,
        delivered_at=datetime(2026, 7, 21, 12, tzinfo=UTC),
        suppressed_at=None,
        abandoned_at=None,
    )

    assert target.task_state is A2ATaskState.COMPLETED
    assert target.delivered_at == datetime(2026, 7, 21, 12, tzinfo=UTC)
    assert "sealed-token" not in repr(target)
    assert "secret-digest" not in repr(target)

    with pytest.raises(TypeError, match="task_state"):
        A2APushDeliveryTarget(
            scope=SCOPE,
            outbox_id="outbox_1",
            delivery_id="delivery_1",
            task_id="run_1",
            context_id="session_1",
            config_id="push_1",
            url="https://push.example.test/a2a",
            authentication_scheme=None,
            secret_ref=None,
            event_id="event_1",
            protocol_version="1.0",
            status_sequence=4,
            task_state="TASK_STATE_COMPLETED",  # type: ignore[arg-type]
            delivered_at=None,
            suppressed_at=None,
            abandoned_at=None,
        )


def test_a2a_push_attempt_contract_uses_typed_outcomes() -> None:
    assert A2APushAttemptResolution.RETRY_PENDING.value == "retry_pending"
    assert A2APushAttemptResolution.ACK_SAFE.value == "ack_safe"
    assert A2APushFailureCategory.SECRET_UNAVAILABLE.value == "secret_unavailable"
    assert A2APushFailureCategory.HTTP_REJECTED.value == "http_rejected"
    assert A2APushAttemptFencedError.code == "a2a_push_attempt_fenced"


@async_test
async def test_a2a_push_create_derives_missing_id_before_port_io() -> None:
    port = RecordingV1PushPort()
    service = A2APushService(
        port,  # type: ignore[arg-type]
        RecordingPushUrlPolicy(),
        RecordingPayloadProtector(),  # type: ignore[arg-type]
    )
    operation_id = "a2a_op_123"

    view = await service.create(
        SCOPE,
        binding=A2ATaskBinding("tenant_1", "run_1", "session_1", "run_1"),
        config_input=A2APushConfigInput(
            config_id=None,
            url="https://push.example.test/a2a",
            authentication=A2APushAuthenticationInput("Bearer"),
        ),
        operation_id=operation_id,
    )

    expected_id = "a2a_push_" + sha256(operation_id.encode("utf-8")).hexdigest()
    assert view.config_id == expected_id
    assert view.authentication_scheme == "Bearer"
    assert port.calls[0][1].authentication_scheme == "Bearer"


def test_distributed_command_not_due_is_distinct_from_state_error() -> None:
    command = DurableRunCommand("run_1", "command_1", "wakeup")
    state = RunState(
        "run_1",
        "session_1",
        status=RunStatus.WAITING,
        wait_reason=WaitReason(
            "timer",
            "timer_1",
            not_before=datetime(2026, 7, 22, tzinfo=UTC),
        ),
        aggregate_version=1,
    )

    with pytest.raises(CommandNotDueError, match="^command is not due$"):
        _validate_continuation(
            command,
            state,
            datetime(2026, 7, 21, tzinfo=UTC),
        )


@pytest.mark.parametrize(
    ("error_type", "code", "message"),
    (
        (CommandNotDueError, "command_not_due", "command is not due"),
        (A2ATaskNotFoundError, "a2a_task_not_found", "a2a task not found"),
        (
            A2ATaskConflictError,
            "a2a_task_conflict",
            "a2a task conflicts with an existing binding",
        ),
    ),
)
def test_wave4_distributed_errors_are_fixed_and_redacted(
    error_type: type[Exception],
    code: str,
    message: str,
) -> None:
    error = error_type()

    assert error.code == code  # type: ignore[attr-defined]
    assert str(error) == message
    with pytest.raises(TypeError):
        error_type("postgres://user:secret@example.invalid/db")
