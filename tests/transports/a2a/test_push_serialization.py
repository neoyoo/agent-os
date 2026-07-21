from __future__ import annotations

import json

from agentos.transports.a2a.operation_types import (
    A2ACreateTaskPushNotificationConfigParams,
    A2AListTaskPushNotificationConfigsResult,
    A2AOperationRequest,
    A2AOperationResponse,
)
from agentos.transports.a2a.push_types import (
    A2AAuthenticationInfo,
    A2ATaskPushNotificationConfig,
)
from agentos.transports.a2a.serialization import (
    decode_operation_request,
    decode_operation_response,
    decode_stream_response,
    encode_stream_response,
    encode_operation_request,
    encode_operation_response,
)
from agentos.transports.a2a.message_types import (
    A2ATaskState,
    A2ATaskStatus,
    A2ATaskStatusUpdateEvent,
)
from agentos.transports.a2a.operation_types import A2AStreamResponse


def test_create_push_params_are_the_direct_official_config_shape() -> None:
    request = A2AOperationRequest(
        request_id="rpc_push",
        params=A2ACreateTaskPushNotificationConfigParams(
            config=A2ATaskPushNotificationConfig(
                task_id="run_1",
                url="https://push.example/events",
                token="secret-token",
                authentication=A2AAuthenticationInfo(
                    scheme="Bearer",
                    credentials="secret-credential",
                ),
            ),
        ),
    )

    encoded = encode_operation_request(request)
    params = json.loads(encoded)["params"]

    assert params == {
        "taskId": "run_1",
        "url": "https://push.example/events",
        "token": "secret-token",
        "authentication": {
            "scheme": "Bearer",
            "credentials": "secret-credential",
        },
    }
    assert decode_operation_request(encoded) == request


def test_push_output_uses_same_proto_type_and_omits_secrets() -> None:
    response = A2AOperationResponse(
        request_id="rpc_push",
        result=A2ATaskPushNotificationConfig(
            id="push_1",
            task_id="run_1",
            url="https://push.example/events",
            authentication=A2AAuthenticationInfo(scheme="Bearer"),
        ),
    )

    encoded = encode_operation_response(response)
    payload = json.loads(encoded)["result"]

    assert payload == {
        "id": "push_1",
        "taskId": "run_1",
        "url": "https://push.example/events",
        "authentication": {"scheme": "Bearer"},
    }
    assert decode_operation_response(encoded) == response
    assert b"token" not in encoded
    assert b"credentials" not in encoded


def test_push_list_uses_official_configs_wrapper_and_page_token() -> None:
    response = A2AOperationResponse(
        request_id=9,
        result=A2AListTaskPushNotificationConfigsResult(
            configs=(
                A2ATaskPushNotificationConfig(
                    id="push_1",
                    task_id="run_1",
                    url="https://push.example/events",
                ),
            ),
            next_page_token="",
        ),
    )

    encoded = encode_operation_response(response)

    assert json.loads(encoded)["result"] == {
        "configs": [
            {
                "id": "push_1",
                "taskId": "run_1",
                "url": "https://push.example/events",
            },
        ],
        "nextPageToken": "",
    }
    assert decode_operation_response(encoded) == response


def test_push_payload_is_direct_protojson_stream_response() -> None:
    result = A2AStreamResponse(
        status_update=A2ATaskStatusUpdateEvent(
            task_id="run_1",
            context_id="session_1",
            status=A2ATaskStatus(A2ATaskState.TASK_STATE_COMPLETED),
        ),
    )

    encoded = encode_stream_response(result)

    assert json.loads(encoded) == {
        "statusUpdate": {
            "taskId": "run_1",
            "contextId": "session_1",
            "status": {"state": "TASK_STATE_COMPLETED"},
        },
    }
    assert b"jsonrpc" not in encoded
    assert decode_stream_response(encoded) == result
