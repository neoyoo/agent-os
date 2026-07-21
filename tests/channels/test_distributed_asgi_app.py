from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from agentos.channels.asgi_app import DistributedAsgiApp
from agentos.channels.asgi_router import AsgiRouter
from agentos.channels.service_wiring import ChannelServices, FixedScopeAuthenticator
from agentos.distributed.models import RequestScope, RunSubmission, RunSubmissionReceipt
from agentos.distributed.services import (
    ArtifactService,
    RunCommandService,
    RunEventStream,
    RunQueryService,
    RunSubmissionService,
)
from agentos.transports.http.request_decoder import MAX_JSON_BODY_BYTES
from tests.planning._async import async_test


SCOPE = RequestScope("tenant_1", "principal_1")


@dataclass
class SubmissionPort:
    submission: RunSubmission | None = None

    async def submit(
        self,
        *,
        scope: RequestScope,
        submission: RunSubmission,
    ) -> RunSubmissionReceipt:
        assert scope == SCOPE
        self.submission = submission
        return RunSubmissionReceipt(
            submission.session_id,
            "run_1",
            submission.submission_id,
            1,
            False,
        )


def _services(port: SubmissionPort) -> ChannelServices:
    return ChannelServices(
        RunSubmissionService(port),  # type: ignore[arg-type]
        RunCommandService(object()),  # type: ignore[arg-type]
        RunQueryService(object()),  # type: ignore[arg-type]
        RunEventStream(object(), object()),  # type: ignore[arg-type]
        ArtifactService(object()),  # type: ignore[arg-type]
    )


async def _invoke(
    app: DistributedAsgiApp,
    *,
    method: str,
    path: str,
    body: bytes = b"",
    headers: tuple[tuple[bytes, bytes], ...] = (),
) -> list[dict[str, Any]]:
    received = False
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        nonlocal received
        if received:
            raise AssertionError("non-streaming request read more than one message")
        received = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await app(
        {
            "type": "http",
            "method": method,
            "path": path,
            "query_string": b"",
            "headers": list(headers),
        },
        receive,
        send,
    )
    return sent


def test_asgi_router_matches_only_frozen_http_routes() -> None:
    router = AsgiRouter()

    match = router.match("POST", "/v1/sessions/session_1/runs/run_1/commands")
    assert match is not None
    assert match.operation == "submit_command"
    assert match.parameters == {"session_id": "session_1", "run_id": "run_1"}
    a2a = router.match("POST", "/a2a")
    assert a2a is not None
    assert a2a.operation == "a2a"
    assert a2a.parameters == {}
    assert router.match("GET", "/a2a") is None
    assert router.match("PATCH", "/v1/sessions/session_1/runs/run_1") is None
    assert router.match("GET", "/v1/sessions//runs/run_1") is None


@async_test
async def test_distributed_asgi_app_dispatches_submit_and_bounds_body() -> None:
    port = SubmissionPort()
    app = DistributedAsgiApp(
        _services(port),
        authenticator=FixedScopeAuthenticator(SCOPE),
        max_request_bytes=64,
    )
    headers = (
        (b"content-type", b"application/json"),
        (b"idempotency-key", b"submit_1"),
        (b"x-request-id", b"trace_1"),
    )

    sent = await _invoke(
        app,
        method="POST",
        path="/v1/sessions/session_1/runs",
        body=b'{"content":"hello"}',
        headers=headers,
    )

    assert sent[0]["status"] == 202
    assert json.loads(sent[1]["body"])["run_id"] == "run_1"
    assert port.submission == RunSubmission("session_1", "submit_1", "hello")

    too_large = await _invoke(
        app,
        method="POST",
        path="/v1/sessions/session_1/runs",
        body=b"x" * 65,
        headers=headers,
    )
    assert too_large[0]["status"] == 413
    assert json.loads(too_large[1]["body"])["request_id"] == "trace_1"


@async_test
async def test_distributed_asgi_app_returns_stable_not_found() -> None:
    app = DistributedAsgiApp(
        _services(SubmissionPort()),
        authenticator=FixedScopeAuthenticator(SCOPE),
    )

    sent = await _invoke(app, method="GET", path="/unknown")

    assert sent[0]["status"] == 404
    assert json.loads(sent[1]["body"])["code"] == "route_not_found"


@async_test
async def test_a2a_route_applies_the_json_rpc_body_limit() -> None:
    app = DistributedAsgiApp(
        _services(SubmissionPort()),
        authenticator=FixedScopeAuthenticator(SCOPE),
    )

    sent = await _invoke(
        app,
        method="POST",
        path="/a2a",
        body=b"x" * (1024 * 1024 + 1),
        headers=((b"content-type", b"application/json"),),
    )

    assert sent[0]["status"] == 413
    assert json.loads(sent[1]["body"])["code"] == "request_too_large"


@async_test
async def test_json_route_rejects_oversize_body_before_buffering_tail() -> None:
    app = DistributedAsgiApp(
        _services(SubmissionPort()),
        authenticator=FixedScopeAuthenticator(SCOPE),
    )
    reads = 0
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        nonlocal reads
        reads += 1
        if reads == 1:
            return {
                "type": "http.request",
                "body": b"x" * (MAX_JSON_BODY_BYTES + 1),
                "more_body": True,
            }
        raise AssertionError("oversize JSON tail must not be buffered")

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await app(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/sessions/session_1/runs",
            "query_string": b"",
            "headers": [
                (b"content-type", b"application/json"),
                (b"idempotency-key", b"submit_large"),
            ],
        },
        receive,
        send,
    )

    assert reads == 1
    assert sent[0]["status"] == 413
    assert json.loads(sent[1]["body"])["code"] == "request_too_large"
