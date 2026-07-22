from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agentos._waiting import WaitReason
from agentos.artifacts.types import (
    ArtifactMediaTypeUnsupportedError,
    ArtifactNotFoundError,
    ArtifactPage,
    ArtifactRecord,
    ArtifactTooLargeError,
    ArtifactValidationError,
)
from agentos.distributed.errors import (
    A2ATaskConflictError,
    A2ATaskNotFoundError,
    ActiveRunConflictError,
    ArtifactConflictError,
    ArtifactInUseError,
    CommandConflictError,
    CommandNotDueError,
    CommandStateError,
    DeliveryUnavailableError,
    DistributedBackendUnavailableError,
    DistributedStoreClosedError,
    RunNotFoundError,
    RunSubmissionConflictError,
    SideEffectInFlightError,
)
from agentos.distributed.models import (
    ArtifactContent,
    RunReadModel,
    RunSubmissionReceipt,
)
from agentos.runtime.durable_commands import DurableCommandReceipt
from agentos.runtime.run import AgentResult
from agentos.runtime.run_state import RunStatus
from agentos.transports.http.errors import (
    AuthenticationRequiredError,
    HttpParseError,
    HttpValidationError,
    PermissionDeniedError,
    RequestTooLargeError,
    UnsupportedMediaTypeError,
)
from agentos.transports.http.response_encoder import (
    encode_artifact_content_response,
    encode_artifact_delete_response,
    encode_artifact_page_response,
    encode_artifact_response,
    encode_command_receipt_response,
    encode_error_response,
    encode_run_read_response,
    encode_submission_receipt_response,
)


NOW = datetime(2026, 7, 21, 12, 30, tzinfo=UTC)
ARTIFACT_ID = "art_00000000-0000-4000-8000-000000000001"


def _artifact(*, filename: str | None = "drawing.png") -> ArtifactRecord:
    return ArtifactRecord(
        id=ARTIFACT_ID,
        session_id="session_1",
        filename=filename,
        media_type="image/png",
        size_bytes=3,
        created_at=NOW,
    )


def _assert_json_headers(response: object) -> None:
    headers = dict(response.headers)  # type: ignore[attr-defined]
    assert headers == {
        "Content-Type": "application/json; charset=utf-8",
        "Content-Length": str(len(response.body)),  # type: ignore[attr-defined]
    }


def test_encode_run_receipts_use_fixed_wire_fields() -> None:
    submission = encode_submission_receipt_response(
        RunSubmissionReceipt("session_1", "run_1", "request_1", 1, False),
    )
    command = encode_command_receipt_response(
        DurableCommandReceipt("run_1", "command_1", "cancel", 2, True),
    )

    assert submission.status_code == 202
    assert submission.body == (
        b'{"aggregate_version":1,"duplicate":false,"run_id":"run_1",'
        b'"session_id":"session_1","submission_id":"request_1"}'
    )
    assert command.status_code == 202
    assert command.body == (
        b'{"aggregate_version":2,"command_id":"command_1","duplicate":true,'
        b'"kind":"cancel","run_id":"run_1"}'
    )
    _assert_json_headers(submission)
    _assert_json_headers(command)


def test_encode_run_read_model_omits_tenant_and_wait_detail() -> None:
    response = encode_run_read_response(
        RunReadModel(
            tenant_id="tenant_1",
            session_id="session_1",
            run_id="run_1",
            status=RunStatus.WAITING,
            wait_reason=WaitReason("human_input", "approval_1", "private detail"),
            aggregate_version=4,
            result=None,
        ),
    )

    assert response.status_code == 200
    assert response.body == (
        b'{"aggregate_version":4,"result":null,"run_id":"run_1",'
        b'"session_id":"session_1","status":"waiting","wait_reason":'
        b'{"handle":"approval_1","kind":"human_input","not_before":null}}'
    )
    assert b"tenant_1" not in response.body
    assert b"private detail" not in response.body


def test_encode_completed_run_is_the_only_result_shape() -> None:
    response = encode_run_read_response(
        RunReadModel(
            tenant_id="tenant_1",
            session_id="session_1",
            run_id="run_1",
            status=RunStatus.COMPLETED,
            wait_reason=None,
            aggregate_version=5,
            result=AgentResult("完成"),
        ),
    )

    assert response.body == (
        '{"aggregate_version":5,"result":{"content":"完成"},"run_id":"run_1",'
        '"session_id":"session_1","status":"completed","wait_reason":null}'
    ).encode()


def test_encode_artifact_operations_never_expose_storage_identity() -> None:
    record = _artifact()
    uploaded = encode_artifact_response(record)
    page = encode_artifact_page_response(ArtifactPage((record,), "cursor_2"))
    content = encode_artifact_content_response(ArtifactContent(record, b"png"))
    deleted = encode_artifact_delete_response()

    assert uploaded.status_code == 201
    assert uploaded.body == (
        b'{"created_at":"2026-07-21T12:30:00.000000Z","filename":"drawing.png",'
        b'"id":"art_00000000-0000-4000-8000-000000000001",'
        b'"media_type":"image/png","session_id":"session_1","size_bytes":3}'
    )
    assert page.status_code == 200
    assert page.body == b'{"items":[' + uploaded.body + b'],"next_cursor":"cursor_2"}'
    assert content.status_code == 200
    assert content.body == b"png"
    assert dict(content.headers) == {
        "Content-Type": "image/png",
        "Content-Length": "3",
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-store",
    }
    assert deleted.status_code == 204
    assert deleted.body == b""
    assert b"blob" not in uploaded.body and b"path" not in uploaded.body


@pytest.mark.parametrize(
    ("error", "status", "code", "message"),
    [
        (HttpParseError(), 400, "invalid_json", "invalid JSON request"),
        (HttpValidationError(), 400, "invalid_request", "invalid request"),
        (ArtifactValidationError("unsafe detail"), 400, "invalid_artifact", "invalid artifact request"),
        (AuthenticationRequiredError(), 401, "authentication_required", "authentication required"),
        (PermissionDeniedError(), 403, "permission_denied", "permission denied"),
        (RunNotFoundError(), 404, "run_not_found", "run not found"),
        (ArtifactNotFoundError(), 404, "artifact_not_found", "artifact not found"),
        (A2ATaskNotFoundError(), 404, "a2a_task_not_found", "a2a task not found"),
        (
            RunSubmissionConflictError(),
            409,
            "run_submission_conflict",
            "run submission conflicts with an existing request",
        ),
        (ActiveRunConflictError(), 409, "active_run_conflict", "session already has an active run"),
        (
            ArtifactConflictError(),
            409,
            "artifact_conflict",
            "artifact operation conflicts with an existing request",
        ),
        (
            CommandConflictError(),
            409,
            "command_conflict",
            "command conflicts with an existing request",
        ),
        (
            CommandStateError(),
            409,
            "command_state",
            "command is invalid for the current run state",
        ),
        (CommandNotDueError(), 409, "command_not_due", "command is not due"),
        (
            ArtifactInUseError(),
            409,
            "artifact_in_use",
            "artifact is referenced by durable session state",
        ),
        (
            SideEffectInFlightError(),
            409,
            "side_effect_in_flight",
            "side effect is still in flight",
        ),
        (
            A2ATaskConflictError(),
            409,
            "a2a_task_conflict",
            "a2a task conflicts with an existing binding",
        ),
        (RequestTooLargeError(), 413, "request_too_large", "request exceeds maximum size"),
        (ArtifactTooLargeError(), 413, "artifact_too_large", "artifact exceeds maximum size"),
        (UnsupportedMediaTypeError(), 415, "unsupported_media_type", "unsupported media type"),
        (
            ArtifactMediaTypeUnsupportedError(),
            415,
            "unsupported_media_type",
            "unsupported media type",
        ),
        (DeliveryUnavailableError(), 503, "delivery_unavailable", "delivery backend is unavailable"),
        (
            DistributedBackendUnavailableError(),
            503,
            "distributed_backend_unavailable",
            "distributed backend is unavailable",
        ),
        (DistributedStoreClosedError(), 503, "distributed_store_closed", "distributed store is closed"),
    ],
)
def test_encode_error_response_uses_fixed_safe_mapping(
    error: Exception,
    status: int,
    code: str,
    message: str,
) -> None:
    response = encode_error_response(error, request_id="request_opaque")

    assert response.status_code == status
    assert response.body == (
        f'{{"code":"{code}","message":"{message}",'
        '"request_id":"request_opaque"}'
    ).encode()
    _assert_json_headers(response)


def test_unknown_error_is_always_redacted() -> None:
    response = encode_error_response(
        RuntimeError("postgres://user:secret@host/db SELECT token prompt"),
        request_id="request_opaque",
    )

    assert response.status_code == 500
    assert response.body == (
        b'{"code":"internal_error","message":"internal error",'
        b'"request_id":"request_opaque"}'
    )
    assert b"secret" not in response.body


@pytest.mark.parametrize("request_id", ["request id", "请求", "r" * 256])
def test_error_response_requires_opaque_ascii_request_id(request_id: str) -> None:
    with pytest.raises(ValueError, match="^request_id is invalid$"):
        encode_error_response(HttpValidationError(), request_id=request_id)
