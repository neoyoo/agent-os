from __future__ import annotations

import json

import pytest

from agentos.transports.a2a.message_types import (
    A2ATask,
    A2ATaskState,
    A2ATaskStatus,
)
from agentos.transports.a2a.operation_types import (
    A2AEmptyResult,
    A2AOperationError,
    A2AOperationResponse,
    A2ASendMessageResult,
    A2AStreamResponse,
)
from agentos.transports.a2a.serialization import (
    A2AWireDecodeError,
    decode_operation_request,
    decode_operation_response,
    decode_stream_operation_response,
    encode_operation_response,
)
from agentos.transports.a2a._json_codec import (
    MAX_A2A_JSON_DEPTH,
    compact_json_bytes,
)


def _task() -> A2ATask:
    return A2ATask(
        id="run_1",
        context_id="session_1",
        status=A2ATaskStatus(A2ATaskState.TASK_STATE_SUBMITTED),
    )


def test_task_success_result_is_direct_and_uses_symbolic_enum() -> None:
    response = A2AOperationResponse(request_id="rpc_1", result=_task())

    encoded = encode_operation_response(response)

    assert json.loads(encoded)["result"] == {
        "id": "run_1",
        "contextId": "session_1",
        "status": {"state": "TASK_STATE_SUBMITTED"},
    }
    assert decode_operation_response(encoded) == response


def test_send_success_uses_official_oneof_wrapper() -> None:
    response = A2AOperationResponse(
        request_id="rpc_1",
        result=A2ASendMessageResult(task=_task()),
    )

    assert json.loads(encode_operation_response(response))["result"] == {
        "task": {
            "id": "run_1",
            "contextId": "session_1",
            "status": {"state": "TASK_STATE_SUBMITTED"},
        },
    }
    assert decode_operation_response(encode_operation_response(response)) == response


def test_stream_decoder_preserves_task_wrapper_as_stream_response() -> None:
    response = A2AOperationResponse(
        request_id="rpc_stream",
        result=A2AStreamResponse(task=_task()),
    )

    decoded = decode_stream_operation_response(encode_operation_response(response))

    assert decoded == response
    assert type(decoded.result) is A2AStreamResponse


def test_response_rejects_non_protocol_result_type() -> None:
    with pytest.raises(TypeError):
        A2AOperationResponse(request_id="rpc_1", result={"task": "not typed"})


def test_delete_success_uses_empty_object_result() -> None:
    response = A2AOperationResponse(request_id=4, result=A2AEmptyResult())

    encoded = encode_operation_response(response)

    assert json.loads(encoded) == {"jsonrpc": "2.0", "id": 4, "result": {}}
    assert decode_operation_response(encoded) == response


def test_error_uses_official_message_and_proto_any_details() -> None:
    response = A2AOperationResponse(
        request_id="rpc_error",
        error=A2AOperationError(
            -32001,
            data=(
                {
                    "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                    "reason": "TASK_NOT_FOUND",
                    "domain": "a2a-protocol.org",
                    "metadata": {"requestId": "opaque"},
                },
            ),
        ),
    )

    payload = json.loads(encode_operation_response(response))

    assert payload["error"]["message"] == "Task not found"
    assert type(payload["error"]["data"]) is list
    assert decode_operation_response(encode_operation_response(response)) == response


def test_parse_error_can_use_null_id_and_fixed_official_message() -> None:
    response = A2AOperationResponse(
        request_id=None,
        error=A2AOperationError(-32700),
    )
    assert json.loads(encode_operation_response(response)) == {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": -32700, "message": "Invalid JSON payload"},
    }


def test_a2a_json_codec_uses_contract_depth_and_deterministic_key_order() -> None:
    assert MAX_A2A_JSON_DEPTH == 32
    assert compact_json_bytes({"z": 1, "a": 2}) == b'{"a":2,"z":1}'


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        (b'{"jsonrpc":', -32700),
        (b'{"jsonrpc":"2.0","id":"1","method":"Unknown","params":{}}', -32601),
        (b'{"jsonrpc":"2.0","id":"1","method":"GetTask","params":{}}', -32602),
        (
            b'{"jsonrpc":"1.0","id":"1","method":"GetTask","params":{"id":"run_1"}}',
            -32600,
        ),
    ],
)
def test_decode_errors_use_fixed_json_rpc_codes(payload: bytes, code: int) -> None:
    with pytest.raises(A2AWireDecodeError) as error:
        decode_operation_request(payload)
    assert error.value.error.code == code
    assert str(error.value) == error.value.error.message
