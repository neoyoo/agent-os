from __future__ import annotations

from agentos._json_values import thaw_json_value
from agentos.transports.a2a._json_codec import require_fields
from agentos.transports.a2a._result_codec import (
    result_from_dict,
    result_to_dict,
    stream_response_from_dict,
)
from agentos.transports.a2a.operation_types import (
    A2AOperationError,
    A2AOperationResponse,
)


def operation_response_to_dict(response: A2AOperationResponse) -> dict[str, object]:
    payload: dict[str, object] = {
        "jsonrpc": response.jsonrpc,
        "id": response.request_id,
    }
    if response.error is not None:
        error: dict[str, object] = {
            "code": response.error.code,
            "message": response.error.message,
        }
        if response.error.data is not None:
            error["data"] = [thaw_json_value(item) for item in response.error.data]
        payload["error"] = error
    else:
        payload["result"] = result_to_dict(response.result)
    return payload


def operation_response_from_dict(value: object) -> A2AOperationResponse:
    payload = _response_fields(value)
    if "result" in payload:
        return A2AOperationResponse(
            request_id=_response_id(payload["id"]),
            result=result_from_dict(payload["result"]),
        )
    return _error_response(payload)


def stream_operation_response_from_dict(value: object) -> A2AOperationResponse:
    payload = _response_fields(value)
    if "result" in payload:
        return A2AOperationResponse(
            request_id=_response_id(payload["id"]),
            result=stream_response_from_dict(payload["result"]),
        )
    return _error_response(payload)


def _response_fields(value: object) -> dict[str, object]:
    payload = require_fields(
        value,
        required=frozenset({"jsonrpc", "id"}),
        optional=frozenset({"result", "error"}),
        field_name="operation response",
    )
    if payload["jsonrpc"] != "2.0" or (("result" in payload) == ("error" in payload)):
        raise ValueError("operation response is invalid")
    return dict(payload)


def _error_response(payload: dict[str, object]) -> A2AOperationResponse:
    request_id = _response_id(payload["id"])
    error_fields = require_fields(
        payload["error"],
        required=frozenset({"code", "message"}),
        optional=frozenset({"data"}),
        field_name="operation error",
    )
    if type(error_fields["code"]) is not int:
        raise ValueError("operation error code is invalid")
    details = error_fields.get("data")
    if details is not None and (
        type(details) is not list or any(type(item) is not dict for item in details)
    ):
        raise ValueError("operation error data must be an Any detail array")
    error = A2AOperationError(
        error_fields["code"],
        data=None if details is None else tuple(details),
    )
    if error_fields["message"] != error.message:
        raise ValueError("operation error message is invalid")
    return A2AOperationResponse(request_id=request_id, error=error)


def _response_id(value: object) -> str | int | None:
    if value is None:
        return None
    if type(value) is str and value:
        return value
    if type(value) is int:
        return value
    raise ValueError("response id is invalid")


__all__ = [
    "operation_response_from_dict",
    "operation_response_to_dict",
    "stream_operation_response_from_dict",
]
