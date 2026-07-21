from __future__ import annotations

from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
import re

from agentos.artifacts.types import (
    ArtifactTooLargeError,
    validate_artifact_filename,
    validate_artifact_media_type,
)
from agentos.transports.http.errors import (
    RequestTooLargeError,
    UnsupportedMediaTypeError,
)
from agentos.transports.http._headers import (
    bounded_limit,
    invalid_request,
    single_header,
    validate_content_length,
)
from agentos.transports.http.request_types import HttpHeaders


MAX_ARTIFACT_FILE_BYTES = 25 * 1024 * 1024
MAX_MULTIPART_BODY_BYTES = 26 * 1024 * 1024

_BOUNDARY = re.compile(r"[0-9A-Za-z'()+_,./:=?-]{1,70}")
_PART_HEADERS = frozenset({"content-disposition", "content-type"})


@dataclass(frozen=True, slots=True)
class MultipartFile:
    data: bytes
    filename: str | None
    media_type: str


def decode_multipart_file(
    headers: HttpHeaders,
    body: bytes,
    *,
    max_request_bytes: int,
    max_file_bytes: int,
) -> MultipartFile:
    if type(body) is not bytes:
        raise invalid_request()
    request_limit = bounded_limit(
        max_request_bytes,
        hard_limit=MAX_MULTIPART_BODY_BYTES,
    )
    file_limit = bounded_limit(
        max_file_bytes,
        hard_limit=MAX_ARTIFACT_FILE_BYTES,
    )
    if len(body) > request_limit:
        raise RequestTooLargeError()
    validate_content_length(headers, len(body))
    content_type = single_header(headers, "content-type", required=False)
    if content_type is None:
        raise UnsupportedMediaTypeError()
    try:
        encoded_content_type = content_type.encode("ascii")
    except UnicodeEncodeError as error:
        raise UnsupportedMediaTypeError() from error
    message = BytesParser(policy=policy.default).parsebytes(
        b"Content-Type: "
        + encoded_content_type
        + b"\r\nMIME-Version: 1.0\r\n\r\n"
        + body,
    )
    boundary = message.get_boundary()
    content_type_parameters = message.get_params(header="content-type", failobj=[])
    if message.get_content_type() != "multipart/form-data":
        raise UnsupportedMediaTypeError()
    if (
        boundary is None
        or _BOUNDARY.fullmatch(boundary) is None
        or [name.lower() for name, _value in content_type_parameters[1:]]
        != ["boundary"]
        or message.defects
        or not message.is_multipart()
        or (message.preamble is not None and message.preamble.strip())
        or (message.epilogue is not None and message.epilogue.strip())
    ):
        raise invalid_request()
    parts = tuple(message.iter_parts())
    if len(parts) != 1:
        raise invalid_request()
    part = parts[0]
    header_names = tuple(name.lower() for name in part.keys())
    if (
        part.defects
        or part.is_multipart()
        or frozenset(header_names) != _PART_HEADERS
        or len(header_names) != len(_PART_HEADERS)
        or part.get_content_disposition() != "form-data"
        or part.get_param("name", header="content-disposition") != "file"
    ):
        raise invalid_request()
    filename = part.get_filename()
    raw_media_type = part.get("content-type")
    media_type = part.get_content_type()
    data = part.get_payload(decode=True)
    if not isinstance(raw_media_type, str) or type(data) is not bytes:
        raise invalid_request()
    normalized_media_type = raw_media_type.strip().lower()
    validate_artifact_media_type(normalized_media_type)
    if normalized_media_type != media_type:
        raise invalid_request()
    if len(data) > file_limit:
        raise ArtifactTooLargeError()
    validate_artifact_filename(filename)
    return MultipartFile(data=data, filename=filename, media_type=media_type)
