from __future__ import annotations

from dataclasses import replace
import json

import pytest

from agentos._waiting import WaitReason
from agentos.channels.a2a_endpoint import A2AEndpoint
from agentos.channels.a2a_stream import A2ASseResponse
from agentos.distributed.models import RunReadModel
from agentos.runtime.run import AgentResult
from agentos.runtime.run_state import RunStatus
from agentos.transports.a2a import (
    A2A_SNAPSHOT_RESUME_EXTENSION,
    A2AAgentExtension,
    A2AAgentInterface,
    A2AGetExtendedAgentCardParams,
    A2AMessage,
    A2AOperationRequest,
    A2APart,
    A2ARole,
    A2ASendMessageConfiguration,
    A2ASendMessageParams,
    A2ASendStreamingMessageParams,
    A2ASubscribeToTaskParams,
    a2a_artifact_upload_id,
    decode_stream_operation_response,
    encode_operation_request,
)
from agentos.transports.http.request_types import HttpHeaders
from agentos.transports.http.response_types import HttpResponse
from tests.channels._a2a_endpoint_support import (
    SCOPE,
    _card,
    _decoded,
    _fixture,
    _headers,
    _request,
    _run,
)
from tests.planning._async import async_test


@async_test
async def test_a2a_stream_close_is_idempotent_and_closes_subscription_once() -> None:
    fixture = _fixture()
    response = await _request(
        fixture.endpoint,
        A2ASendStreamingMessageParams(
            message=A2AMessage(
                message_id="message_stream",
                role=A2ARole.ROLE_USER,
                parts=(A2APart(text="stream"),),
            ),
        ),
    )
    assert type(response) is A2ASseResponse
    await response.aclose()
    await response.aclose()

    assert fixture.replay_port.subscriptions[-1].close_calls == 1


@async_test
async def test_a2a_streaming_honors_zero_history_length() -> None:
    fixture = _fixture(
        submitted_status=RunStatus.COMPLETED,
        submitted_result=AgentResult("done"),
    )
    response = await _request(
        fixture.endpoint,
        A2ASendStreamingMessageParams(
            message=A2AMessage(
                message_id="message_stream_history",
                role=A2ARole.ROLE_USER,
                parts=(A2APart(text="stream"),),
            ),
            configuration=A2ASendMessageConfiguration(history_length=0),
        ),
    )

    assert type(response) is A2ASseResponse
    frame = await response.next_frame()
    initial = decode_stream_operation_response(
        frame.removeprefix(b"data: ").strip(),
    )
    assert initial.result.task.history is None  # type: ignore[union-attr]


@pytest.mark.parametrize("kind", ["human_input", "resource_availability"])
@async_test
async def test_a2a_streaming_waiting_snapshot_closes_without_following(
    kind: str,
) -> None:
    fixture = _fixture(
        submitted_status=RunStatus.WAITING,
        submitted_wait_reason=WaitReason(kind, "wait_1"),  # type: ignore[arg-type]
    )
    response = await _request(
        fixture.endpoint,
        A2ASendStreamingMessageParams(
            message=A2AMessage(
                message_id=f"message_waiting_{kind}",
                role=A2ARole.ROLE_USER,
                parts=(A2APart(text="stream"),),
            ),
        ),
    )

    assert type(response) is A2ASseResponse
    frame = await response.next_frame()
    expected = (
        b"TASK_STATE_INPUT_REQUIRED"
        if kind == "human_input"
        else b"TASK_STATE_WORKING"
    )
    assert expected in frame
    assert fixture.replay_port.subscriptions == []
    with pytest.raises(StopAsyncIteration):
        await response.next_frame()


@async_test
async def test_a2a_send_uploads_inline_raw_through_artifact_service() -> None:
    fixture = _fixture()
    response = await _request(
        fixture.endpoint,
        A2ASendMessageParams(
            message=A2AMessage(
                message_id="message_raw",
                role=A2ARole.ROLE_USER,
                context_id="session_raw",
                parts=(
                    A2APart(text="inspect"),
                    A2APart(
                        raw=b"png",
                        filename="drawing.png",
                        media_type="image/png",
                    ),
                ),
            ),
            configuration=A2ASendMessageConfiguration(return_immediately=True),
        ),
    )

    assert isinstance(response, HttpResponse)
    assert _decoded(response).error is None  # type: ignore[attr-defined]
    upload = fixture.artifact_port.uploads[0]
    assert upload["upload_id"] == a2a_artifact_upload_id(
        message_id="message_raw",
        part_index=1,
    )
    assert upload["session_id"] == "session_raw"
    assert upload["data"] == b"png"


@async_test
async def test_a2a_send_waits_for_barrier_followed_stable_state_by_default() -> None:
    fixture = _fixture()

    def complete_run() -> None:
        fixture.run_port.runs[(SCOPE.tenant_id, "session_wait", "run_1")] = _run(
            "session_wait",
            "run_1",
            RunStatus.CANCELLED,
            version=2,
        )

    fixture.replay_port.on_follow = complete_run
    response = await _request(
        fixture.endpoint,
        A2ASendMessageParams(
            A2AMessage(
                message_id="message_wait",
                role=A2ARole.ROLE_USER,
                context_id="session_wait",
                parts=(A2APart(text="wait"),),
            ),
        ),
    )

    assert isinstance(response, HttpResponse)
    assert _decoded(response).result.task.status.state.name == (  # type: ignore[attr-defined]
        "TASK_STATE_CANCELED"
    )
    assert fixture.replay_port.subscriptions[-1].close_calls == 1


@async_test
async def test_a2a_rejects_url_part_and_invalid_interface_routing_hint() -> None:
    card = _card("public", tenant="route-eu")
    card = replace(
        card,
        supported_interfaces=(
            card.supported_interfaces[0],
            A2AAgentInterface(
                "https://agent.example/a2a-us",
                "JSONRPC",
                "1.0",
                "route-us",
            ),
        ),
    )
    fixture = _fixture(
        public_card=card,
        interface_tenant="route-eu",
    )
    url_response = await _request(
        fixture.endpoint,
        A2ASendMessageParams(
            message=A2AMessage(
                message_id="message_url",
                role=A2ARole.ROLE_USER,
                parts=(A2APart(url="https://files.example/drawing.png"),),
            ),
            configuration=A2ASendMessageConfiguration(return_immediately=True),
        ),
    )
    matching_route = await _request(
        fixture.endpoint,
        A2AGetExtendedAgentCardParams(tenant="route-eu"),
    )
    invalid_route = await _request(
        fixture.endpoint,
        A2AGetExtendedAgentCardParams(tenant="route-us"),
    )

    assert isinstance(url_response, HttpResponse)
    assert _decoded(url_response).error.code == -32602  # type: ignore[attr-defined]
    assert isinstance(matching_route, HttpResponse)
    assert _decoded(matching_route).result.name == "extended"  # type: ignore[attr-defined]
    assert SCOPE.tenant_id != "route-eu"
    assert isinstance(invalid_route, HttpResponse)
    assert _decoded(invalid_route).error.code == -32602  # type: ignore[attr-defined]


@async_test
async def test_a2a_negotiates_version_and_extensions_before_routing_hint() -> None:
    params = A2AGetExtendedAgentCardParams(tenant="route-us")
    body = encode_operation_request(A2AOperationRequest("rpc_order", params))
    version_fixture = _fixture(public_card=_card("public", tenant="route-eu"))

    version_response = await version_fixture.endpoint.handle(
        headers=_headers(version="0.9"),
        body=body,
        request_id="opaque_request",
    )

    required_extension = "https://agent.example/extensions/required/v1"
    extension_fixture = _fixture(
        public_card=_card(
            "public",
            tenant="route-eu",
            extensions=(
                A2AAgentExtension(A2A_SNAPSHOT_RESUME_EXTENSION),
                A2AAgentExtension(required_extension, required=True),
            ),
        ),
    )
    extension_response = await extension_fixture.endpoint.handle(
        headers=_headers(),
        body=body,
        request_id="opaque_request",
    )

    assert isinstance(version_response, HttpResponse)
    assert _decoded(version_response).error.code == -32009  # type: ignore[attr-defined]
    assert isinstance(extension_response, HttpResponse)
    assert _decoded(extension_response).error.code == -32008  # type: ignore[attr-defined]


@async_test
async def test_a2a_last_event_id_checks_extension_before_operation() -> None:
    fixture = _fixture()
    body = encode_operation_request(
        A2AOperationRequest("rpc_resume", A2AGetExtendedAgentCardParams()),
    )

    missing_extension = await fixture.endpoint.handle(
        headers=_headers(last_event_id="opaque_cursor"),
        body=body,
        request_id="opaque_request",
    )
    wrong_operation = await fixture.endpoint.handle(
        headers=_headers(
            extensions=A2A_SNAPSHOT_RESUME_EXTENSION,
            last_event_id="opaque_cursor",
        ),
        body=body,
        request_id="opaque_request",
    )
    invalid_hint_body = encode_operation_request(
        A2AOperationRequest(
            "rpc_resume_hint",
            A2AGetExtendedAgentCardParams(tenant="route-us"),
        ),
    )
    invalid_hint = await fixture.endpoint.handle(
        headers=_headers(last_event_id="opaque_cursor"),
        body=invalid_hint_body,
        request_id="opaque_request",
    )

    assert isinstance(missing_extension, HttpResponse)
    assert _decoded(missing_extension).error.code == -32008  # type: ignore[attr-defined]
    assert isinstance(wrong_operation, HttpResponse)
    assert _decoded(wrong_operation).error.code == -32602  # type: ignore[attr-defined]
    assert isinstance(invalid_hint, HttpResponse)
    assert _decoded(invalid_hint).error.code == -32008  # type: ignore[attr-defined]


@async_test
async def test_a2a_rejects_mismatched_content_length() -> None:
    fixture = _fixture()
    body = encode_operation_request(
        A2AOperationRequest("rpc_length", A2AGetExtendedAgentCardParams()),
    )
    headers = HttpHeaders(
        _headers().items + (("Content-Length", str(len(body) + 1)),),
    )

    response = await fixture.endpoint.handle(
        headers=headers,
        body=body,
        request_id="opaque_request",
    )

    assert isinstance(response, HttpResponse)
    assert response.status_code == 400
    assert json.loads(response.body)["code"] == "invalid_request"


@async_test
async def test_a2a_snapshot_resume_requires_negotiated_extension() -> None:
    fixture = _fixture()
    await _request(
        fixture.endpoint,
        A2ASendMessageParams(
            A2AMessage(
                message_id="message_1",
                role=A2ARole.ROLE_USER,
                context_id="session_1",
                parts=(A2APart(text="hello"),),
            ),
            configuration=A2ASendMessageConfiguration(return_immediately=True),
        ),
    )
    body = encode_operation_request(
        A2AOperationRequest("rpc_resume", A2ASubscribeToTaskParams("run_1")),
    )

    response = await fixture.endpoint.handle(
        headers=_headers(last_event_id="opaque_cursor"),
        body=body,
        request_id="opaque_request",
    )

    assert isinstance(response, HttpResponse)
    assert _decoded(response).error.code == -32008  # type: ignore[attr-defined]
    assert fixture.replay_port.subscriptions == []


@pytest.mark.parametrize("status", [RunStatus.WAITING, RunStatus.CANCELLED])
@async_test
async def test_a2a_subscribe_stable_preflight_does_not_open_stream(
    status: RunStatus,
) -> None:
    fixture = _fixture()
    await _request(
        fixture.endpoint,
        A2ASendMessageParams(
            A2AMessage(
                message_id="message_1",
                role=A2ARole.ROLE_USER,
                context_id="session_1",
                parts=(A2APart(text="hello"),),
            ),
            configuration=A2ASendMessageConfiguration(return_immediately=True),
        ),
    )
    fixture.run_port.runs[(SCOPE.tenant_id, "session_1", "run_1")] = RunReadModel(
        SCOPE.tenant_id,
        "session_1",
        "run_1",
        status,
        WaitReason("human_input", "wait_1")
        if status is RunStatus.WAITING
        else None,
        2,
        None,
    )

    response = await _request(
        fixture.endpoint,
        A2ASubscribeToTaskParams("run_1"),
    )

    assert isinstance(response, HttpResponse)
    assert _decoded(response).error.code == -32004  # type: ignore[attr-defined]
    assert fixture.replay_port.subscriptions == []


@async_test
async def test_a2a_rejects_invalid_local_card_and_missing_extended_card() -> None:
    invalid_card = _fixture(public_card=_card("invalid", extensions=()))
    invalid = await _request(
        invalid_card.endpoint,
        A2AGetExtendedAgentCardParams(),
    )
    missing_card = _fixture(has_extended_card=False)
    missing = await _request(
        missing_card.endpoint,
        A2AGetExtendedAgentCardParams(),
    )

    assert isinstance(invalid, HttpResponse)
    assert _decoded(invalid).error.code == -32006  # type: ignore[attr-defined]
    assert isinstance(missing, HttpResponse)
    assert _decoded(missing).error.code == -32007  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "value",
    [True, float("nan"), float("inf"), float("-inf")],
)
def test_a2a_endpoint_rejects_invalid_heartbeat_interval(value: float) -> None:
    fixture = _fixture()

    with pytest.raises(ValueError, match="heartbeat_interval"):
        A2AEndpoint(
            fixture.endpoint.services,
            fixture.endpoint.authenticator,
            heartbeat_interval=value,
        )
