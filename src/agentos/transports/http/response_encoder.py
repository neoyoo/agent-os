from __future__ import annotations

from datetime import UTC, datetime
import json
import unicodedata

from agentos.artifacts.types import (
    ArtifactPage,
    ArtifactRecord,
)
from agentos.distributed.models import (
    ArtifactContent,
    RunReadModel,
    RunSubmissionReceipt,
)
from agentos.runtime.durable_commands import DurableCommandReceipt
from agentos.transports.http._error_mapping import map_http_error
from agentos.transports.http.response_types import HttpResponse


def encode_submission_receipt_response(receipt: RunSubmissionReceipt) -> HttpResponse:
    """编码首次 Run 持久回执。"""

    if type(receipt) is not RunSubmissionReceipt:
        raise TypeError("receipt must be RunSubmissionReceipt")
    return _json_response(
        202,
        {
            "session_id": receipt.session_id,
            "run_id": receipt.run_id,
            "submission_id": receipt.submission_id,
            "aggregate_version": receipt.aggregate_version,
            "duplicate": receipt.duplicate,
        },
    )


def encode_command_receipt_response(receipt: DurableCommandReceipt) -> HttpResponse:
    """编码 Durable Command 持久回执。"""

    if type(receipt) is not DurableCommandReceipt:
        raise TypeError("receipt must be DurableCommandReceipt")
    return _json_response(
        202,
        {
            "run_id": receipt.run_id,
            "command_id": receipt.command_id,
            "kind": receipt.kind,
            "aggregate_version": receipt.aggregate_version,
            "duplicate": receipt.duplicate,
        },
    )


def encode_run_read_response(model: RunReadModel) -> HttpResponse:
    """编码不含 tenant 和内部 wait detail 的 Run read model。"""

    if type(model) is not RunReadModel:
        raise TypeError("model must be RunReadModel")
    reason = model.wait_reason
    return _json_response(
        200,
        {
            "session_id": model.session_id,
            "run_id": model.run_id,
            "status": model.status.value,
            "aggregate_version": model.aggregate_version,
            "wait_reason": None
            if reason is None
            else {
                "kind": reason.kind,
                "handle": reason.handle,
                "not_before": _datetime(reason.not_before),
            },
            "result": None if model.result is None else {"content": model.result.content},
        },
    )


def encode_artifact_response(record: ArtifactRecord) -> HttpResponse:
    """编码 Artifact upload metadata。"""

    return _json_response(201, _artifact_payload(record))


def encode_artifact_page_response(page: ArtifactPage) -> HttpResponse:
    """编码 Artifact metadata page。"""

    if type(page) is not ArtifactPage:
        raise TypeError("page must be ArtifactPage")
    return _json_response(
        200,
        {
            "items": [_artifact_payload(item) for item in page.items],
            "next_cursor": page.next_cursor,
        },
    )


def encode_artifact_content_response(content: ArtifactContent) -> HttpResponse:
    """编码受控媒体类型与 bytes，不暴露 storage identity。"""

    if type(content) is not ArtifactContent:
        raise TypeError("content must be ArtifactContent")
    return HttpResponse(
        status_code=200,
        headers=(
            ("Content-Type", content.record.media_type),
            ("Content-Length", str(len(content.data))),
            ("X-Content-Type-Options", "nosniff"),
            ("Cache-Control", "private, no-store"),
        ),
        body=content.data,
    )


def encode_artifact_delete_response() -> HttpResponse:
    """编码成功的幂等 Artifact 删除。"""

    return HttpResponse(status_code=204, headers=(), body=b"")


def encode_error_response(error: BaseException, *, request_id: str) -> HttpResponse:
    """按固定类型映射编码脱敏 error body。"""

    _validate_request_id(request_id)
    status, code, message = map_http_error(error)
    return _json_response(
        status,
        {"code": code, "message": message, "request_id": request_id},
    )


def _artifact_payload(record: ArtifactRecord) -> dict[str, object]:
    if type(record) is not ArtifactRecord:
        raise TypeError("record must be ArtifactRecord")
    return {
        "id": record.id,
        "session_id": record.session_id,
        "filename": record.filename,
        "media_type": record.media_type,
        "size_bytes": record.size_bytes,
        "created_at": _datetime(record.created_at),
    }


def _json_response(status_code: int, payload: dict[str, object]) -> HttpResponse:
    body = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")
    return HttpResponse(
        status_code=status_code,
        headers=(
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(body))),
        ),
        body=body,
    )


def _datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace(
        "+00:00",
        "Z",
    )


def _validate_request_id(value: str) -> None:
    if (
        type(value) is not str
        or not value
        or len(value) > 255
        or not value.isascii()
        or value.strip() != value
        or any(
            char.isspace() or unicodedata.category(char).startswith("C")
            for char in value
        )
    ):
        raise ValueError("request_id is invalid")


__all__ = [
    "encode_artifact_content_response",
    "encode_artifact_delete_response",
    "encode_artifact_page_response",
    "encode_artifact_response",
    "encode_command_receipt_response",
    "encode_error_response",
    "encode_run_read_response",
    "encode_submission_receipt_response",
]
