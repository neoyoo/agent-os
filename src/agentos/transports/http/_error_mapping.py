from __future__ import annotations

from agentos.artifacts.types import (
    ArtifactMediaTypeUnsupportedError,
    ArtifactNotFoundError,
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
from agentos.transports.http.errors import (
    AuthenticationRequiredError,
    HttpParseError,
    HttpValidationError,
    PermissionDeniedError,
    RequestTooLargeError,
    UnsupportedMediaTypeError,
)


_ERROR_RESPONSES: dict[type[BaseException], tuple[int, str, str]] = {
    HttpParseError: (400, "invalid_json", "invalid JSON request"),
    HttpValidationError: (400, "invalid_request", "invalid request"),
    AuthenticationRequiredError: (
        401,
        "authentication_required",
        "authentication required",
    ),
    PermissionDeniedError: (403, "permission_denied", "permission denied"),
    RunNotFoundError: (404, "run_not_found", "run not found"),
    ArtifactNotFoundError: (404, "artifact_not_found", "artifact not found"),
    A2ATaskNotFoundError: (404, "a2a_task_not_found", "a2a task not found"),
    RunSubmissionConflictError: (
        409,
        "run_submission_conflict",
        "run submission conflicts with an existing request",
    ),
    ActiveRunConflictError: (
        409,
        "active_run_conflict",
        "session already has an active run",
    ),
    ArtifactConflictError: (
        409,
        "artifact_conflict",
        "artifact operation conflicts with an existing request",
    ),
    CommandConflictError: (
        409,
        "command_conflict",
        "command conflicts with an existing request",
    ),
    CommandStateError: (
        409,
        "command_state",
        "command is invalid for the current run state",
    ),
    CommandNotDueError: (409, "command_not_due", "command is not due"),
    ArtifactInUseError: (
        409,
        "artifact_in_use",
        "artifact is referenced by durable session state",
    ),
    SideEffectInFlightError: (
        409,
        "side_effect_in_flight",
        "side effect is still in flight",
    ),
    A2ATaskConflictError: (
        409,
        "a2a_task_conflict",
        "a2a task conflicts with an existing binding",
    ),
    RequestTooLargeError: (
        413,
        "request_too_large",
        "request exceeds maximum size",
    ),
    ArtifactTooLargeError: (
        413,
        "artifact_too_large",
        "artifact exceeds maximum size",
    ),
    UnsupportedMediaTypeError: (
        415,
        "unsupported_media_type",
        "unsupported media type",
    ),
    ArtifactMediaTypeUnsupportedError: (
        415,
        "unsupported_media_type",
        "unsupported media type",
    ),
    DeliveryUnavailableError: (
        503,
        "delivery_unavailable",
        "delivery backend is unavailable",
    ),
    DistributedBackendUnavailableError: (
        503,
        "distributed_backend_unavailable",
        "distributed backend is unavailable",
    ),
    DistributedStoreClosedError: (
        503,
        "distributed_store_closed",
        "distributed store is closed",
    ),
}


def map_http_error(error: BaseException) -> tuple[int, str, str]:
    """把已冻结的异常类型映射为稳定且脱敏的 HTTP error fields。"""

    mapped = _ERROR_RESPONSES.get(type(error))
    if mapped is not None:
        return mapped
    if isinstance(error, ArtifactValidationError):
        return 400, "invalid_artifact", "invalid artifact request"
    return 500, "internal_error", "internal error"
