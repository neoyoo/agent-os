from __future__ import annotations

from collections.abc import Mapping

from agentos.transports.a2a._card_codec import (
    agent_card_from_dict,
    agent_card_signing_payload,
    agent_card_to_dict,
)
from agentos.transports.a2a._json_codec import (
    A2AWireDecodeError,
    MAX_A2A_JSON_BYTES,
    compact_json_bytes,
    strict_json_object,
)
from agentos.transports.a2a._message_codec import (
    MAX_INLINE_FILE_BYTES,
    artifact_from_dict,
    artifact_to_dict,
    message_from_dict,
    message_to_dict,
    part_from_dict,
    part_to_dict,
)
from agentos.transports.a2a._operation_codec import (
    A2AInvalidParams,
    A2AInvalidRequest,
    A2AMethodNotFound,
    artifact_update_from_dict,
    artifact_update_to_dict,
    operation_request_from_dict,
    operation_request_to_dict,
    operation_response_from_dict,
    operation_response_to_dict,
    status_update_from_dict,
    status_update_to_dict,
    task_from_dict,
    task_to_dict,
)
from agentos.transports.a2a._push_codec import (
    push_config_from_dict,
    push_config_to_dict,
)
from agentos.transports.a2a._response_codec import (
    stream_operation_response_from_dict,
)
from agentos.transports.a2a._result_codec import (
    stream_response_from_dict,
    stream_response_to_dict,
)
from agentos.transports.a2a.card_types import A2AAgentCard
from agentos.transports.a2a.operation_types import (
    A2AOperationError,
    A2AOperationRequest,
    A2AOperationResponse,
    A2AStreamResponse,
)


def decode_operation_request(
    data: bytes | str,
    *,
    max_bytes: int = MAX_A2A_JSON_BYTES,
) -> A2AOperationRequest:
    payload = strict_json_object(data, max_bytes=max_bytes)
    request_id = _readable_request_id(payload)
    try:
        return operation_request_from_dict(payload)
    except A2AWireDecodeError:
        raise
    except A2AMethodNotFound as error:
        raise A2AWireDecodeError(
            A2AOperationError(-32601),
            request_id=request_id,
        ) from error
    except A2AInvalidParams as error:
        raise A2AWireDecodeError(
            A2AOperationError(-32602),
            request_id=request_id,
        ) from error
    except A2AInvalidRequest as error:
        raise A2AWireDecodeError(
            A2AOperationError(-32600),
            request_id=request_id,
        ) from error
    except (TypeError, ValueError) as error:
        raise A2AWireDecodeError(
            A2AOperationError(-32600),
            request_id=request_id,
        ) from error


def encode_operation_request(request: A2AOperationRequest) -> bytes:
    return _bounded_encode(operation_request_to_dict(request))


def decode_operation_response(
    data: bytes | str,
    *,
    max_bytes: int = MAX_A2A_JSON_BYTES,
) -> A2AOperationResponse:
    try:
        return operation_response_from_dict(
            strict_json_object(data, max_bytes=max_bytes)
        )
    except A2AWireDecodeError:
        raise
    except (TypeError, ValueError) as error:
        raise A2AWireDecodeError(A2AOperationError(-32006)) from error


def encode_operation_response(response: A2AOperationResponse) -> bytes:
    return _bounded_encode(operation_response_to_dict(response))


def decode_stream_operation_response(
    data: bytes | str,
    *,
    max_bytes: int = MAX_A2A_JSON_BYTES,
) -> A2AOperationResponse:
    try:
        return stream_operation_response_from_dict(
            strict_json_object(data, max_bytes=max_bytes),
        )
    except A2AWireDecodeError:
        raise
    except (TypeError, ValueError) as error:
        raise A2AWireDecodeError(A2AOperationError(-32006)) from error


def encode_stream_response(response: A2AStreamResponse) -> bytes:
    """Encode the direct ProtoJSON body used by outbound A2A Push."""

    if type(response) is not A2AStreamResponse:
        raise TypeError("response must be A2AStreamResponse")
    return _bounded_encode(stream_response_to_dict(response))


def decode_stream_response(
    data: bytes | str,
    *,
    max_bytes: int = MAX_A2A_JSON_BYTES,
) -> A2AStreamResponse:
    try:
        return stream_response_from_dict(
            strict_json_object(data, max_bytes=max_bytes),
        )
    except A2AWireDecodeError:
        raise
    except (TypeError, ValueError) as error:
        raise A2AWireDecodeError(A2AOperationError(-32006)) from error


def decode_agent_card(
    data: bytes | str,
    *,
    max_bytes: int = MAX_A2A_JSON_BYTES,
) -> A2AAgentCard:
    try:
        return agent_card_from_dict(strict_json_object(data, max_bytes=max_bytes))
    except A2AWireDecodeError:
        raise
    except (TypeError, ValueError) as error:
        raise A2AWireDecodeError(A2AOperationError(-32006)) from error


def encode_agent_card(card: A2AAgentCard) -> bytes:
    return _bounded_encode(agent_card_to_dict(card))


def _bounded_encode(value: Mapping[str, object]) -> bytes:
    encoded = compact_json_bytes(value)
    if len(encoded) > MAX_A2A_JSON_BYTES:
        raise ValueError("A2A JSON body exceeds the hard limit")
    return encoded


def _readable_request_id(value: Mapping[str, object]) -> str | int | None:
    request_id = value.get("id")
    if type(request_id) is str and request_id:
        return request_id
    if type(request_id) is int:
        return request_id
    return None


__all__ = [
    "A2AWireDecodeError",
    "MAX_A2A_JSON_BYTES",
    "MAX_INLINE_FILE_BYTES",
    "agent_card_from_dict",
    "agent_card_signing_payload",
    "agent_card_to_dict",
    "artifact_from_dict",
    "artifact_to_dict",
    "artifact_update_from_dict",
    "artifact_update_to_dict",
    "decode_agent_card",
    "decode_operation_request",
    "decode_operation_response",
    "decode_stream_operation_response",
    "decode_stream_response",
    "encode_agent_card",
    "encode_operation_request",
    "encode_operation_response",
    "encode_stream_response",
    "message_from_dict",
    "message_to_dict",
    "operation_request_from_dict",
    "operation_request_to_dict",
    "operation_response_from_dict",
    "operation_response_to_dict",
    "part_from_dict",
    "part_to_dict",
    "push_config_from_dict",
    "push_config_to_dict",
    "status_update_from_dict",
    "status_update_to_dict",
    "task_from_dict",
    "task_to_dict",
]
