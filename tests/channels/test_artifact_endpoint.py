from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import json

from agentos.artifacts.types import ArtifactPage, ArtifactRecord
from agentos.channels.artifact_endpoint import ArtifactEndpoint
from agentos.channels.service_wiring import ChannelServices, FixedScopeAuthenticator
from agentos.distributed.models import ArtifactContent, RequestScope
from agentos.distributed.services import (
    ArtifactService,
    RunCommandService,
    RunEventStream,
    RunQueryService,
    RunSubmissionService,
)
from agentos.transports.http.request_types import HttpHeaders
from tests.planning._async import async_test


SCOPE = RequestScope("tenant_1", "principal_1")
ARTIFACT_ID = "art_00000000-0000-4000-8000-000000000001"
CREATED_AT = datetime(2026, 7, 21, tzinfo=UTC)


@dataclass
class ArtifactPort:
    record: ArtifactRecord | None = None
    deleted: list[tuple[RequestScope, str, str, str]] = field(default_factory=list)

    async def upload(self, **values: object) -> ArtifactRecord:
        self.record = ArtifactRecord(
            ARTIFACT_ID,
            str(values["session_id"]),
            values["filename"],  # type: ignore[arg-type]
            str(values["media_type"]),
            len(values["data"]),  # type: ignore[arg-type]
            CREATED_AT,
        )
        return self.record

    async def list(self, **values: object) -> ArtifactPage:
        del values
        return ArtifactPage(() if self.record is None else (self.record,), None)

    async def read(self, **values: object) -> ArtifactContent:
        del values
        assert self.record is not None
        return ArtifactContent(self.record, b"png-data")

    async def delete(self, **values: object) -> None:
        self.deleted.append(
            (
                values["scope"],  # type: ignore[arg-type]
                str(values["session_id"]),
                str(values["artifact_id"]),
                str(values["deletion_id"]),
            ),
        )


def _services(port: ArtifactPort) -> ChannelServices:
    run_port = object()
    query_port = object()
    return ChannelServices(
        RunSubmissionService(run_port),  # type: ignore[arg-type]
        RunCommandService(run_port),  # type: ignore[arg-type]
        RunQueryService(query_port),  # type: ignore[arg-type]
        RunEventStream(query_port, object()),  # type: ignore[arg-type]
        ArtifactService(port),  # type: ignore[arg-type]
    )


def _multipart() -> tuple[HttpHeaders, bytes]:
    boundary = "agentos-boundary"
    body = b"".join(
        (
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="file"; filename="drawing.png"\r\n',
            b"Content-Type: image/png\r\n\r\n",
            b"png-data",
            f"\r\n--{boundary}--\r\n".encode(),
        ),
    )
    headers = HttpHeaders(
        (
            ("Content-Type", f"multipart/form-data; boundary={boundary}"),
            ("Content-Length", str(len(body))),
            ("Idempotency-Key", "upload_1"),
        ),
    )
    return headers, body


@async_test
async def test_artifact_endpoint_covers_upload_list_read_and_delete() -> None:
    port = ArtifactPort()
    endpoint = ArtifactEndpoint(
        _services(port),
        FixedScopeAuthenticator(SCOPE),
    )
    upload_headers, body = _multipart()

    uploaded = await endpoint.upload(
        session_id="session_1",
        headers=upload_headers,
        body=body,
        request_id="trace_1",
    )
    listed = await endpoint.list(
        session_id="session_1",
        headers=HttpHeaders(()),
        cursor=None,
        limit="20",
        request_id="trace_2",
    )
    read = await endpoint.read(
        session_id="session_1",
        artifact_id=ARTIFACT_ID,
        headers=HttpHeaders(()),
        request_id="trace_3",
    )
    deleted = await endpoint.delete(
        session_id="session_1",
        artifact_id=ARTIFACT_ID,
        headers=HttpHeaders((("Idempotency-Key", "delete_1"),)),
        request_id="trace_4",
    )

    assert uploaded.status_code == 201
    assert json.loads(listed.body)["items"][0]["id"] == ARTIFACT_ID
    assert read.body == b"png-data"
    assert ("X-Content-Type-Options", "nosniff") in read.headers
    assert deleted.status_code == 204
    assert port.deleted == [(SCOPE, "session_1", ARTIFACT_ID, "delete_1")]
