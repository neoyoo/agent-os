from __future__ import annotations

import json

from agentos.channels.a2a_stream import A2ASseResponse
from agentos.runtime.run_state import RunStatus
from agentos.transports.a2a import (
    A2AAuthenticationInfo,
    A2ACancelTaskParams,
    A2ACreateTaskPushNotificationConfigParams,
    A2ADeleteTaskPushNotificationConfigParams,
    A2AGetExtendedAgentCardParams,
    A2AGetTaskParams,
    A2AGetTaskPushNotificationConfigParams,
    A2AListTaskPushNotificationConfigsParams,
    A2AListTasksParams,
    A2AMessage,
    A2AOperationRequest,
    A2APart,
    A2ARole,
    A2ASendMessageConfiguration,
    A2ASendMessageParams,
    A2ASendStreamingMessageParams,
    A2ASubscribeToTaskParams,
    A2ATaskPushNotificationConfig,
    decode_stream_operation_response,
    encode_operation_request,
)
from agentos.transports.http.response_types import HttpResponse
from tests.channels._a2a_endpoint_support import (
    SCOPE,
    _decoded,
    _fixture,
    _headers,
    _request,
    _run,
)
from tests.planning._async import async_test


@async_test
async def test_a2a_endpoint_dispatches_all_eleven_official_operations() -> None:
    fixture = _fixture()
    message = A2AMessage(
        message_id="message_send",
        role=A2ARole.ROLE_USER,
        context_id="session_1",
        parts=(A2APart(text="hello"),),
    )
    sent = await _request(
        fixture.endpoint,
        A2ASendMessageParams(
            message=message,
            configuration=A2ASendMessageConfiguration(return_immediately=True),
        ),
    )
    assert isinstance(sent, HttpResponse)
    send_result = _decoded(sent).result  # type: ignore[attr-defined]
    assert send_result.task.id == "run_1"

    streaming = await _request(
        fixture.endpoint,
        A2ASendStreamingMessageParams(
            message=A2AMessage(
                message_id="message_stream",
                role=A2ARole.ROLE_USER,
                context_id="session_2",
                parts=(A2APart(text="stream"),),
            ),
        ),
    )
    assert type(streaming) is A2ASseResponse
    initial = await streaming.next_frame()
    decoded = decode_stream_operation_response(
        initial.removeprefix(b"data: ").strip(),
    )
    assert decoded.result.task.id == "run_2"  # type: ignore[union-attr]
    await streaming.aclose()

    fixture.run_port.runs[(SCOPE.tenant_id, "session_1", "run_1")] = _run(
        "session_1",
        "run_1",
        RunStatus.RUNNING,
        version=2,
    )
    get_task = await _request(fixture.endpoint, A2AGetTaskParams(id="run_1"))
    assert isinstance(get_task, HttpResponse)
    assert _decoded(get_task).result.id == "run_1"  # type: ignore[attr-defined]

    listed = await _request(fixture.endpoint, A2AListTasksParams(page_size=50))
    assert isinstance(listed, HttpResponse)
    assert {task.id for task in _decoded(listed).result.tasks} == {  # type: ignore[attr-defined]
        "run_1",
        "run_2",
    }

    subscribed = await _request(
        fixture.endpoint,
        A2ASubscribeToTaskParams(id="run_1"),
    )
    assert type(subscribed) is A2ASseResponse
    assert b'"id":"run_1"' in await subscribed.next_frame()
    await subscribed.aclose()

    cancelled = await _request(fixture.endpoint, A2ACancelTaskParams(id="run_1"))
    assert isinstance(cancelled, HttpResponse)
    assert _decoded(cancelled).result.status.state.name == "TASK_STATE_CANCELED"  # type: ignore[attr-defined]

    created = await _request(
        fixture.endpoint,
        A2ACreateTaskPushNotificationConfigParams(
            A2ATaskPushNotificationConfig(
                task_id="run_1",
                id="push_1",
                url="https://push.example/events",
                token="secret-token",
                authentication=A2AAuthenticationInfo("Bearer", "secret-credential"),
            ),
        ),
    )
    assert isinstance(created, HttpResponse)
    created_config = _decoded(created).result  # type: ignore[attr-defined]
    assert created_config.id == "push_1"
    assert created_config.token is None
    assert created_config.authentication.credentials is None

    fetched = await _request(
        fixture.endpoint,
        A2AGetTaskPushNotificationConfigParams("run_1", "push_1"),
    )
    assert isinstance(fetched, HttpResponse)
    assert _decoded(fetched).result.id == "push_1"  # type: ignore[attr-defined]

    push_page = await _request(
        fixture.endpoint,
        A2AListTaskPushNotificationConfigsParams(task_id="run_1"),
    )
    assert isinstance(push_page, HttpResponse)
    assert len(_decoded(push_page).result.configs) == 1  # type: ignore[attr-defined]

    deleted = await _request(
        fixture.endpoint,
        A2ADeleteTaskPushNotificationConfigParams("run_1", "push_1"),
    )
    assert isinstance(deleted, HttpResponse)
    assert type(_decoded(deleted).result).__name__ == "A2AEmptyResult"  # type: ignore[attr-defined]

    card = await _request(fixture.endpoint, A2AGetExtendedAgentCardParams())
    assert isinstance(card, HttpResponse)
    assert _decoded(card).result.name == "extended"  # type: ignore[attr-defined]


@async_test
async def test_a2a_endpoint_authenticates_before_dispatch_and_then_negotiates() -> None:
    rejected = _fixture(reject_auth=True)
    response = await _request(rejected.endpoint, A2AGetTaskParams(id="missing"))

    assert isinstance(response, HttpResponse)
    assert response.status_code == 401
    assert json.loads(response.body) == {
        "code": "authentication_required",
        "message": "authentication required",
        "request_id": "opaque_request",
    }
    assert rejected.task_port.bindings == {}

    fixture = _fixture()
    body = encode_operation_request(
        A2AOperationRequest("rpc_version", A2AGetExtendedAgentCardParams()),
    )
    missing_version = await fixture.endpoint.handle(
        headers=_headers(version=None),
        body=body,
        request_id="opaque_request",
    )
    assert isinstance(missing_version, HttpResponse)
    assert _decoded(missing_version).error.code == -32009  # type: ignore[attr-defined]
