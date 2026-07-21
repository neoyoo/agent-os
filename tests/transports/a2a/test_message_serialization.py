from __future__ import annotations

import base64
import json

import pytest

from agentos.transports.a2a.message_types import (
    A2AMessage,
    A2APart,
    A2ARole,
)
from agentos.transports.a2a.operation_types import (
    A2AOperationRequest,
    A2ASendMessageParams,
)
from agentos.transports.a2a.serialization import (
    A2AWireDecodeError,
    decode_operation_request,
    encode_operation_request,
)


def _message() -> A2AMessage:
    metadata = {"nested": {"items": [1, 2]}}
    message = A2AMessage(
        message_id="msg_1",
        role=A2ARole.ROLE_USER,
        context_id="session_1",
        parts=(A2APart(text="hello"),),
        metadata=metadata,
    )
    metadata["nested"]["items"].append(3)
    return message


def test_message_metadata_is_deeply_frozen_and_encoder_uses_protojson() -> None:
    request = A2AOperationRequest(
        request_id="rpc_1",
        params=A2ASendMessageParams(message=_message()),
    )

    encoded = encode_operation_request(request)
    decoded = decode_operation_request(encoded)

    assert decoded == request
    assert decoded.params.message.metadata["nested"]["items"] == (1, 2)
    with pytest.raises(TypeError):
        decoded.params.message.metadata["new"] = "value"  # type: ignore[index]
    assert json.loads(encoded) == {
        "jsonrpc": "2.0",
        "id": "rpc_1",
        "method": "SendMessage",
        "params": {
            "message": {
                "messageId": "msg_1",
                "contextId": "session_1",
                "role": "ROLE_USER",
                "parts": [{"text": "hello"}],
                "metadata": {"nested": {"items": [1, 2]}},
            },
        },
    }


def test_decoder_accepts_proto_field_names_integer_enum_and_unknown_fields() -> None:
    payload = {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "SendMessage",
        "params": {
            "unknown_future_request_field": True,
            "message": {
                "message_id": "msg_1",
                "role": 1,
                "parts": [{"text": "hello", "futurePartField": 3}],
                "futureMessageField": {"value": 1},
            },
        },
    }

    decoded = decode_operation_request(json.dumps(payload).encode())

    assert decoded.params.message.message_id == "msg_1"
    assert decoded.params.message.role is A2ARole.ROLE_USER
    assert decoded.params.message.parts == (A2APart(text="hello"),)
    assert json.loads(encode_operation_request(decoded))["params"]["message"] == {
        "messageId": "msg_1",
        "role": "ROLE_USER",
        "parts": [{"text": "hello"}],
    }


def test_decoder_rejects_protojson_alias_collision() -> None:
    payload = {
        "jsonrpc": "2.0",
        "id": "rpc_1",
        "method": "SendMessage",
        "params": {
            "message": {
                "messageId": "msg_1",
                "message_id": "msg_2",
                "role": "ROLE_USER",
                "parts": [{"text": "hello"}],
            },
        },
    }

    with pytest.raises(A2AWireDecodeError) as error:
        decode_operation_request(json.dumps(payload).encode())
    assert error.value.error.code == -32602


@pytest.mark.parametrize("raw_text", ["-_8=", "-_8", "+/8=", "+/8"])
def test_decoder_accepts_protojson_base64_variants_and_encodes_standard(
    raw_text: str,
) -> None:
    payload = {
        "jsonrpc": "2.0",
        "id": "rpc_raw",
        "method": "SendMessage",
        "params": {
            "message": {
                "messageId": "msg_raw",
                "role": "ROLE_USER",
                "parts": [{"raw": raw_text, "media_type": "application/octet-stream"}],
            },
        },
    }

    decoded = decode_operation_request(json.dumps(payload).encode())

    assert decoded.params.message.parts[0].raw == b"\xfb\xff"
    encoded_part = json.loads(encode_operation_request(decoded))["params"]["message"][
        "parts"
    ][0]
    assert encoded_part == {"raw": "+/8=", "mediaType": "application/octet-stream"}


def test_data_null_selects_the_data_oneof_and_round_trips() -> None:
    payload = {
        "jsonrpc": "2.0",
        "id": "rpc_null",
        "method": "SendMessage",
        "params": {
            "message": {
                "messageId": "msg_null",
                "role": "ROLE_USER",
                "contextId": None,
                "parts": [{"data": None, "metadata": None}],
            },
        },
    }

    decoded = decode_operation_request(json.dumps(payload).encode())

    assert decoded.params.message.context_id is None
    assert decoded.params.message.parts == (A2APart(data=None),)
    assert json.loads(encode_operation_request(decoded))["params"]["message"][
        "parts"
    ] == [{"data": None}]


@pytest.mark.parametrize(
    "payload",
    [
        b'{"jsonrpc":"2.0","id":"1","method":"message/send","params":{}}',
        b'{"jsonrpc":"2.0","id":"1","method":"SendMessage","params":{"message":{"role":"ROLE_USER","parts":[{"kind":"text","text":"x"}],"messageId":"m"}}}',
        b'{"jsonrpc":"2.0","id":"1","method":"SendMessage","params":{"message":{"role":"ROLE_USER","parts":[{"text":"x","raw":"eA=="}],"messageId":"m"}}}',
        b'{"jsonrpc":"2.0","id":"1","method":"SendMessage","params":{"message":{"role":"ROLE_USER","parts":[{"text":"x"}]}}}',
        b'{"jsonrpc":"2.0","id":"1","method":"SendMessage","params":{"message":{"role":"ROLE_USER","parts":[null],"messageId":"m"}}}',
        b'{"jsonrpc":"2.0","id":"1","method":"SendMessage","params":{"message":{"role":"user","parts":[{"text":"x"}],"messageId":"m"}}}',
    ],
)
def test_decoder_rejects_legacy_or_invalid_message_shapes(payload: bytes) -> None:
    with pytest.raises(A2AWireDecodeError):
        decode_operation_request(payload)


def test_decoder_rejects_duplicate_envelope_keys_and_nonfinite_numbers() -> None:
    invalid_payloads = (
        b'{"jsonrpc":"2.0","id":"1","id":"2","method":"GetTask","params":{"id":"run_1"}}',
        b'{"jsonrpc":"2.0","id":"1","method":"GetTask","params":{"id":"run_1"},"extra":true}',
        b'{"jsonrpc":"2.0","id":"1","method":"GetTask","params":{"id":"run_1","historyLength":NaN}}',
    )

    for payload in invalid_payloads:
        with pytest.raises(A2AWireDecodeError):
            decode_operation_request(payload)


def test_inline_raw_uses_decoded_cumulative_hard_limit() -> None:
    raw = b"x" * (256 * 1024 + 1)
    encoded = base64.b64encode(raw).decode("ascii")
    request = {
        "jsonrpc": "2.0",
        "id": "rpc_raw",
        "method": "SendMessage",
        "params": {
            "message": {
                "messageId": "msg_raw",
                "role": "ROLE_USER",
                "parts": [{"raw": encoded}, {"raw": encoded}],
            },
        },
    }

    with pytest.raises(A2AWireDecodeError):
        decode_operation_request(json.dumps(request).encode())


def test_decoder_rejects_inbound_url_part() -> None:
    payload = {
        "jsonrpc": "2.0",
        "id": "rpc_1",
        "method": "SendMessage",
        "params": {
            "message": {
                "messageId": "msg_1",
                "role": "ROLE_USER",
                "parts": [{"url": "https://example.test/a"}],
            },
        },
    }
    with pytest.raises(A2AWireDecodeError) as error:
        decode_operation_request(json.dumps(payload).encode())
    assert error.value.error.code == -32602
