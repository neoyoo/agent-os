from __future__ import annotations

from typing import Any

from agentos.artifacts.types import ArtifactValidationError
from agentos.distributed.models import RunSubmission
from agentos.runtime.durable_commands import DurableRunCommand
from agentos.transports.http._headers import (
    bounded_limit,
    idempotency_key,
    invalid_request,
    parse_decimal,
    single_header,
    validate_content_length,
    validate_identifier,
)
from agentos.transports.http._json import (
    MAX_JSON_BODY_BYTES,
    MAX_JSON_NESTING,
    canonical_json_bytes,
    decode_json_object,
)
from agentos.transports.http._multipart import (
    MAX_ARTIFACT_FILE_BYTES,
    MAX_MULTIPART_BODY_BYTES,
    decode_multipart_file,
)
from agentos.transports.http.request_types import (
    ArtifactListRequest,
    ArtifactUploadRequest,
    HttpHeaders,
)


MAX_COMMAND_PAYLOAD_BYTES = 64 * 1024
MAX_ARTIFACT_PAGE_SIZE = 100


def decode_run_submission(
    *,
    session_id: str,
    headers: HttpHeaders,
    body: bytes,
    max_body_bytes: int = MAX_JSON_BODY_BYTES,
) -> RunSubmission:
    """严格解码首次 Run 提交并构造 canonical `RunSubmission`。"""

    session_id = validate_identifier(session_id)
    payload = decode_json_object(headers, body, max_body_bytes=max_body_bytes)
    _require_fields(payload, required={"content"}, optional={"artifact_handles"})
    content = payload["content"]
    handles = payload.get("artifact_handles", [])
    if type(content) is not str or type(handles) is not list:
        raise invalid_request()
    if any(type(handle) is not str for handle in handles):
        raise invalid_request()
    try:
        return RunSubmission(
            session_id=session_id,
            submission_id=idempotency_key(headers),
            content=content,
            artifact_handles=tuple(handles),
        )
    except ArtifactValidationError:
        raise
    except (TypeError, ValueError) as error:
        raise invalid_request() from error


def decode_run_command(
    *,
    run_id: str,
    headers: HttpHeaders,
    body: bytes,
    max_body_bytes: int = MAX_JSON_BODY_BYTES,
    max_payload_bytes: int = MAX_COMMAND_PAYLOAD_BYTES,
) -> DurableRunCommand:
    """严格解码 Durable Command，身份只来自 path/header。"""

    run_id = validate_identifier(run_id)
    payload = decode_json_object(headers, body, max_body_bytes=max_body_bytes)
    _require_fields(payload, required={"kind", "payload"}, optional=set())
    kind = payload["kind"]
    command_payload = payload["payload"]
    if type(kind) is not str or type(command_payload) is not dict:
        raise invalid_request()
    payload_limit = bounded_limit(
        max_payload_bytes,
        hard_limit=MAX_COMMAND_PAYLOAD_BYTES,
    )
    if len(canonical_json_bytes(command_payload)) > payload_limit:
        raise invalid_request()
    try:
        return DurableRunCommand(
            run_id=run_id,
            command_id=idempotency_key(headers),
            kind=kind,  # type: ignore[arg-type]
            payload=command_payload,
        )
    except (TypeError, ValueError) as error:
        raise invalid_request() from error


def decode_last_event_id(headers: HttpHeaders) -> str | None:
    """读取可选 `Last-Event-ID`，重复项一律拒绝。"""

    return single_header(headers, "last-event-id", required=False)


def decode_artifact_upload(
    *,
    session_id: str,
    headers: HttpHeaders,
    body: bytes,
    max_request_bytes: int = MAX_MULTIPART_BODY_BYTES,
    max_file_bytes: int = MAX_ARTIFACT_FILE_BYTES,
) -> ArtifactUploadRequest:
    """使用 MIME 结构解析有界的单文件 multipart 请求。"""

    session_id = validate_identifier(session_id)
    file = decode_multipart_file(
        headers,
        body,
        max_request_bytes=max_request_bytes,
        max_file_bytes=max_file_bytes,
    )
    return ArtifactUploadRequest(
        session_id=session_id,
        upload_id=idempotency_key(headers),
        data=file.data,
        filename=file.filename,
        media_type=file.media_type,
    )


def decode_artifact_list_request(
    *,
    cursor: str | None,
    limit: str | None,
) -> ArtifactListRequest:
    """严格解码 Artifact list query。"""

    if cursor is not None:
        validate_identifier(cursor)
    parsed_limit = 20 if limit is None else parse_decimal(limit)
    if not 1 <= parsed_limit <= MAX_ARTIFACT_PAGE_SIZE:
        raise invalid_request()
    return ArtifactListRequest(cursor=cursor, limit=parsed_limit)


def decode_artifact_deletion_id(headers: HttpHeaders) -> str:
    """读取 Artifact delete 的幂等 identity。"""

    return idempotency_key(headers)


def _require_fields(
    payload: dict[str, Any],
    *,
    required: set[str],
    optional: set[str],
) -> None:
    keys = set(payload)
    if not required <= keys or keys - required - optional:
        raise invalid_request()


__all__ = [
    "MAX_ARTIFACT_FILE_BYTES",
    "MAX_ARTIFACT_PAGE_SIZE",
    "MAX_COMMAND_PAYLOAD_BYTES",
    "MAX_JSON_BODY_BYTES",
    "MAX_JSON_NESTING",
    "MAX_MULTIPART_BODY_BYTES",
    "decode_artifact_list_request",
    "decode_artifact_deletion_id",
    "decode_artifact_upload",
    "decode_last_event_id",
    "decode_run_command",
    "decode_run_submission",
    "validate_content_length",
]
