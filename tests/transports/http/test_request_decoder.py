from __future__ import annotations

import json

import pytest

from agentos.artifacts.types import ArtifactValidationError
from agentos.distributed.models import RunSubmission
from agentos.runtime.durable_commands import DurableRunCommand
from agentos.transports.http.errors import (
    HttpParseError,
    HttpValidationError,
    RequestTooLargeError,
    UnsupportedMediaTypeError,
)
from agentos.transports.http.request_decoder import (
    decode_artifact_deletion_id,
    decode_artifact_list_request,
    decode_last_event_id,
    decode_run_command,
    decode_run_submission,
)
from agentos.transports.http.request_types import HttpHeaders


def _headers(*items: tuple[str, str]) -> HttpHeaders:
    return HttpHeaders(items)


def _json_headers(*extra: tuple[str, str]) -> HttpHeaders:
    return _headers(
        ("Content-Type", "application/json; charset=utf-8"),
        ("Idempotency-Key", "request_1"),
        *extra,
    )


def test_decode_run_submission_builds_canonical_domain_input() -> None:
    artifact_id = "art_00000000-0000-4000-8000-000000000001"
    body = json.dumps(
        {"content": "完成任务", "artifact_handles": [artifact_id]},
        ensure_ascii=False,
    ).encode()

    submission = decode_run_submission(
        session_id="session_1",
        headers=_json_headers(),
        body=body,
    )

    assert submission == RunSubmission(
        "session_1",
        "request_1",
        "完成任务",
        (artifact_id,),
    )


@pytest.mark.parametrize(
    "body",
    [
        b"\xff",
        b'{"content":"a","content":"b"}',
        b'{"content":"a","number":NaN}',
        b'{"content":"a","number":Infinity}',
        b'{"content":"a","number":1e999}',
    ],
)
def test_decode_run_submission_classifies_invalid_json(body: bytes) -> None:
    with pytest.raises(HttpParseError, match="^invalid JSON request$"):
        decode_run_submission(
            session_id="session_1",
            headers=_json_headers(),
            body=body,
        )


@pytest.mark.parametrize(
    "body",
    [
        b"[]",
        b'{"content":"a","unknown":true}',
        b'{"content":"a","artifact_handles":"not-a-list"}',
        b'{"content":"a","artifact_handles":[1]}',
    ],
)
def test_decode_run_submission_classifies_invalid_wire_shape(body: bytes) -> None:
    with pytest.raises(HttpValidationError, match="^invalid request$"):
        decode_run_submission(
            session_id="session_1",
            headers=_json_headers(),
            body=body,
        )


def test_decode_run_submission_preserves_artifact_validation_category() -> None:
    with pytest.raises(ArtifactValidationError, match="^artifact id is invalid$"):
        decode_run_submission(
            session_id="session_1",
            headers=_json_headers(),
            body=b'{"content":"a","artifact_handles":["not-an-artifact"]}',
        )


def test_decode_json_rejects_excessive_nesting_and_wire_size() -> None:
    nested: object = "value"
    for _ in range(34):
        nested = {"value": nested}
    body = json.dumps({"content": "a", "artifact_handles": [], "nested": nested}).encode()

    with pytest.raises(HttpValidationError, match="^invalid request$"):
        decode_run_submission(
            session_id="session_1",
            headers=_json_headers(),
            body=body,
        )

    with pytest.raises(RequestTooLargeError, match="^request exceeds maximum size$"):
        decode_run_submission(
            session_id="session_1",
            headers=_json_headers(),
            body=b'{"content":"too large"}',
            max_body_bytes=8,
        )


@pytest.mark.parametrize("content_type", [None, "text/plain", "application/json; charset=latin-1"])
def test_decode_json_requires_supported_content_type(content_type: str | None) -> None:
    items = [("Idempotency-Key", "request_1")]
    if content_type is not None:
        items.append(("Content-Type", content_type))

    with pytest.raises(UnsupportedMediaTypeError, match="^unsupported media type$"):
        decode_run_submission(
            session_id="session_1",
            headers=HttpHeaders(items),
            body=b'{"content":"a"}',
        )


def test_sensitive_headers_reject_duplicates_and_content_length_mismatch() -> None:
    with pytest.raises(HttpValidationError, match="^invalid request$"):
        _headers(
            ("content-type", "application/json"),
            ("Content-Type", "application/json"),
            ("idempotency-key", "request_1"),
        )

    wrong_length = _json_headers(("Content-Length", "999"))
    with pytest.raises(HttpValidationError, match="^invalid request$"):
        decode_run_submission(
            session_id="session_1",
            headers=wrong_length,
            body=b'{"content":"a"}',
        )


def test_http_headers_preserve_duplicates_and_reject_header_injection() -> None:
    headers = _headers(("X-Trace", "one"), ("x-trace", "two"))

    assert headers.get_all("x-trace") == ("one", "two")

    with pytest.raises(HttpValidationError, match="^invalid request$"):
        _headers(("X-Test", "safe\r\nInjected: true"))


def test_http_headers_repr_does_not_expose_authorization() -> None:
    headers = _headers(("Authorization", "Bearer top-secret"))

    assert "top-secret" not in repr(headers)


@pytest.mark.parametrize(
    "name",
    [
        "Authorization",
        "A2A-Version",
        "A2A-Extensions",
        "Idempotency-Key",
        "Last-Event-ID",
        "Content-Type",
        "Content-Length",
        "X-Request-ID",
    ],
)
def test_sensitive_header_duplicates_are_case_insensitive(name: str) -> None:
    with pytest.raises(HttpValidationError, match="^invalid request$"):
        _headers((name, "1"), (name.swapcase(), "1"))


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("Authorization", "a" * 8193),
        ("A2A-Extensions", "a" * 4097),
        ("Last-Event-ID", "a" * 1025),
        ("A2A-Version", "a" * 33),
        ("Content-Length", "1" * 21),
        ("Idempotency-Key", "界" * 86),
        ("Content-Type", "界" * 86),
        ("X-Request-ID", "a" * 256),
        ("X-Other", "界" * 86),
    ],
)
def test_header_values_enforce_frozen_byte_limits(name: str, value: str) -> None:
    with pytest.raises(HttpValidationError, match="^invalid request$"):
        _headers((name, value))


@pytest.mark.parametrize("value", ["", "+1", "01", "1.0", "1e2", "１"])
def test_content_length_requires_canonical_ascii_decimal(value: str) -> None:
    with pytest.raises(HttpValidationError, match="^invalid request$"):
        _headers(("Content-Length", value))


def test_decode_run_command_uses_path_and_header_identities_only() -> None:
    command = decode_run_command(
        run_id="run_1",
        headers=_headers(
            ("Content-Type", "application/json"),
            ("Idempotency-Key", "command_1"),
        ),
        body=b'{"kind":"cancel","payload":{}}',
    )

    assert command == DurableRunCommand("run_1", "command_1", "cancel", {})

    with pytest.raises(HttpValidationError, match="^invalid request$"):
        decode_run_command(
            run_id="run_1",
            headers=_headers(
                ("Content-Type", "application/json"),
                ("Idempotency-Key", "command_1"),
            ),
            body=b'{"kind":"cancel","payload":{},"run_id":"other"}',
        )


@pytest.mark.parametrize("run_id", ["run id", "run\x00id", "r" * 256])
def test_decode_run_command_rejects_invalid_path_identifier(run_id: str) -> None:
    with pytest.raises(HttpValidationError, match="^invalid request$"):
        decode_run_command(
            run_id=run_id,
            headers=_headers(
                ("Content-Type", "application/json"),
                ("Idempotency-Key", "command_1"),
            ),
            body=b'{"kind":"cancel","payload":{}}',
        )


def test_decode_run_command_limits_canonical_payload_bytes() -> None:
    body = json.dumps({"kind": "resume", "payload": {"answer": "12345"}}).encode()

    with pytest.raises(HttpValidationError, match="^invalid request$"):
        decode_run_command(
            run_id="run_1",
            headers=_headers(
                ("Content-Type", "application/json"),
                ("Idempotency-Key", "command_1"),
            ),
            body=body,
            max_payload_bytes=4,
        )


def test_decode_last_event_id_reads_optional_scoped_cursor() -> None:
    assert decode_last_event_id(_headers(("Last-Event-ID", "cursor_1"))) == "cursor_1"


def test_decode_artifact_list_request_is_strict_and_bounded() -> None:
    assert decode_artifact_list_request(cursor=None, limit=None).limit == 20
    assert decode_artifact_list_request(cursor="cursor_1", limit="100").limit == 100

    for value in ("0", "101", "1.5", "true"):
        with pytest.raises(HttpValidationError, match="^invalid request$"):
            decode_artifact_list_request(cursor=None, limit=value)

    with pytest.raises(HttpValidationError, match="^invalid request$"):
        decode_artifact_list_request(cursor="cursor\x00value", limit=None)


def test_decode_artifact_deletion_id_requires_idempotency_key() -> None:
    assert decode_artifact_deletion_id(
        _headers(("Idempotency-Key", "delete_1")),
    ) == "delete_1"

    with pytest.raises(HttpValidationError, match="^invalid request$"):
        decode_artifact_deletion_id(_headers())
