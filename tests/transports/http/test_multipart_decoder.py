from __future__ import annotations

import pytest

from agentos.artifacts.types import ArtifactTooLargeError, ArtifactValidationError
from agentos.transports.http.errors import (
    HttpValidationError,
    RequestTooLargeError,
    UnsupportedMediaTypeError,
)
from agentos.transports.http.request_decoder import decode_artifact_upload
from agentos.transports.http.request_types import HttpHeaders


def _headers(*items: tuple[str, str]) -> HttpHeaders:
    return HttpHeaders(items)


def _multipart_body(
    *,
    boundary: str = "agentos-boundary",
    filename: str | None = "drawing.png",
    media_type: str = "image/png",
    data: bytes = b"png-data",
    extra_headers: bytes = b"",
) -> bytes:
    filename_parameter = b"" if filename is None else f'; filename="{filename}"'.encode()
    return b"".join(
        (
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="file"',
            filename_parameter,
            b"\r\n",
            f"Content-Type: {media_type}\r\n".encode(),
            extra_headers,
            b"\r\n",
            data,
            f"\r\n--{boundary}--\r\n".encode(),
        ),
    )


def _upload(body: bytes, **limits: int):
    return decode_artifact_upload(
        session_id="session_1",
        headers=_headers(
            ("Content-Type", "multipart/form-data; boundary=agentos-boundary"),
            ("Idempotency-Key", "upload_1"),
            ("Content-Length", str(len(body))),
        ),
        body=body,
        **limits,
    )


@pytest.mark.parametrize("filename", ["drawing.png", None])
def test_decode_artifact_upload_uses_structured_multipart_parser(
    filename: str | None,
) -> None:
    body = _multipart_body(filename=filename)

    upload = _upload(body)

    assert upload.session_id == "session_1"
    assert upload.upload_id == "upload_1"
    assert upload.filename == filename
    assert upload.media_type == "image/png"
    assert upload.data == b"png-data"


def test_multipart_payload_can_contain_boundary_like_bytes() -> None:
    data = b"prefix--agentos-boundary-not-a-delimiter-suffix"

    assert _upload(_multipart_body(data=data)).data == data


@pytest.mark.parametrize("content_type", [None, "application/json", "multipart/mixed"])
def test_decode_artifact_upload_requires_multipart_content_type(
    content_type: str | None,
) -> None:
    items = [("Idempotency-Key", "upload_1")]
    if content_type is not None:
        items.append(("Content-Type", content_type))

    with pytest.raises(UnsupportedMediaTypeError, match="^unsupported media type$"):
        decode_artifact_upload(
            session_id="session_1",
            headers=HttpHeaders(items),
            body=_multipart_body(),
        )


@pytest.mark.parametrize(
    "body",
    [
        b"not multipart",
        b"preamble\r\n" + _multipart_body(),
        _multipart_body() + b"epilogue",
        _multipart_body(extra_headers=b"X-Extra: forbidden\r\n"),
        _multipart_body() + _multipart_body(),
    ],
)
def test_decode_artifact_upload_rejects_malformed_mime(body: bytes) -> None:
    with pytest.raises(HttpValidationError, match="^invalid request$"):
        _upload(body)


@pytest.mark.parametrize(
    "body",
    [
        _multipart_body(filename="../secret.txt"),
        _multipart_body(media_type="not-a-media-type"),
    ],
)
def test_decode_artifact_upload_preserves_artifact_validation(body: bytes) -> None:
    with pytest.raises(ArtifactValidationError):
        _upload(body)


def test_decode_artifact_upload_enforces_request_and_file_limits() -> None:
    body = _multipart_body(data=b"1234")

    with pytest.raises(RequestTooLargeError, match="^request exceeds maximum size$"):
        _upload(body, max_request_bytes=8)

    with pytest.raises(ArtifactTooLargeError, match="^artifact exceeds maximum size$"):
        _upload(body, max_file_bytes=3)


def test_decode_artifact_upload_rejects_invalid_session_identifier() -> None:
    body = _multipart_body()

    with pytest.raises(HttpValidationError, match="^invalid request$"):
        decode_artifact_upload(
            session_id="session id",
            headers=_headers(
                ("Content-Type", "multipart/form-data; boundary=agentos-boundary"),
                ("Idempotency-Key", "upload_1"),
            ),
            body=body,
        )
