from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agentos.transports.a2a.message_types import (
    A2AMessage,
    A2APart,
    A2ARole,
    A2ATaskState,
)
from agentos.transports.a2a.operation_types import (
    A2ACancelTaskParams,
    A2ACreateTaskPushNotificationConfigParams,
    A2ADeleteTaskPushNotificationConfigParams,
    A2AGetExtendedAgentCardParams,
    A2AGetTaskParams,
    A2AGetTaskPushNotificationConfigParams,
    A2AListTaskPushNotificationConfigsParams,
    A2AListTasksParams,
    A2AOperationRequest,
    A2ASendMessageParams,
    A2ASendStreamingMessageParams,
    A2ASubscribeToTaskParams,
)
from agentos.transports.a2a.protocol import (
    A2AExtensionNegotiationError,
    A2AExtensionPolicy,
    A2AProtocolVersionError,
    A2AProtocolVersionPolicy,
    parse_extensions_header,
)
from agentos.transports.a2a.push_types import A2ATaskPushNotificationConfig
from agentos.transports.a2a.serialization import (
    A2AWireDecodeError,
    decode_operation_request,
    encode_operation_request,
)


def _message() -> A2AMessage:
    return A2AMessage(
        message_id="msg_1",
        role=A2ARole.ROLE_USER,
        parts=(A2APart(text="hello"),),
    )


@pytest.mark.parametrize(
    ("params", "method"),
    [
        (A2ASendMessageParams(message=_message()), "SendMessage"),
        (A2ASendStreamingMessageParams(message=_message()), "SendStreamingMessage"),
        (A2AGetTaskParams(id="run_1", history_length=0), "GetTask"),
        (
            A2AListTasksParams(
                context_id="session_1",
                status=A2ATaskState.TASK_STATE_WORKING,
                page_size=25,
                page_token="next",
                history_length=3,
                status_timestamp_after=datetime(2026, 7, 21, tzinfo=UTC),
                include_artifacts=False,
            ),
            "ListTasks",
        ),
        (A2ACancelTaskParams(id="run_1", metadata={"reason": "user"}), "CancelTask"),
        (A2ASubscribeToTaskParams(id="run_1"), "SubscribeToTask"),
        (
            A2ACreateTaskPushNotificationConfigParams(
                config=A2ATaskPushNotificationConfig(
                    task_id="run_1",
                    url="https://push.example/events",
                ),
            ),
            "CreateTaskPushNotificationConfig",
        ),
        (
            A2AGetTaskPushNotificationConfigParams(task_id="run_1", id="push_1"),
            "GetTaskPushNotificationConfig",
        ),
        (
            A2AListTaskPushNotificationConfigsParams(
                task_id="run_1",
                page_size=20,
                page_token="next",
            ),
            "ListTaskPushNotificationConfigs",
        ),
        (
            A2ADeleteTaskPushNotificationConfigParams(task_id="run_1", id="push_1"),
            "DeleteTaskPushNotificationConfig",
        ),
        (A2AGetExtendedAgentCardParams(), "GetExtendedAgentCard"),
    ],
)
def test_official_eleven_operations_have_typed_params_and_round_trip(
    params: object,
    method: str,
) -> None:
    request = A2AOperationRequest(request_id=7, params=params)  # type: ignore[arg-type]

    assert request.method == method
    assert decode_operation_request(encode_operation_request(request)) == request


def test_get_extended_card_omits_empty_params() -> None:
    request = A2AOperationRequest(
        request_id="rpc_card",
        params=A2AGetExtendedAgentCardParams(),
    )

    assert encode_operation_request(request) == (
        b'{"id":"rpc_card","jsonrpc":"2.0","method":"GetExtendedAgentCard"}'
    )
    assert decode_operation_request(encode_operation_request(request)) == request


@pytest.mark.parametrize("request_id", [None, "", True, -1.5])
def test_request_id_must_be_nonempty_string_or_non_bool_integer(
    request_id: object,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        A2AOperationRequest(
            request_id=request_id,  # type: ignore[arg-type]
            params=A2AGetTaskParams(id="run_1"),
        )


def test_version_policy_supports_only_normalized_1_0() -> None:
    policy = A2AProtocolVersionPolicy()

    assert policy.negotiate("1.0.7") == "1.0"
    with pytest.raises(A2AProtocolVersionError) as missing:
        policy.negotiate(None)
    with pytest.raises(A2AProtocolVersionError):
        policy.negotiate("0.3")
    assert missing.value.error.code == -32009
    assert str(missing.value) == "A2A protocol version is not supported"


def test_extension_header_parses_ows_deduplicates_and_rejects_invalid_uri() -> None:
    assert parse_extensions_header(" urn:a,\thttps://example.test/ext ,urn:a") == (
        "urn:a",
        "https://example.test/ext",
    )

    for invalid in ("", "urn:a,", "urn:a,,urn:b", "relative/path", "urn:a b"):
        with pytest.raises(ValueError):
            parse_extensions_header(invalid)

    with pytest.raises(ValueError):
        parse_extensions_header("https://example.test/" + "x" * 4076)


def test_decode_error_preserves_readable_json_rpc_request_id() -> None:
    with pytest.raises(A2AWireDecodeError) as error:
        decode_operation_request(
            b'{"jsonrpc":"2.0","id":"rpc_1","method":"GetTask","params":{}}',
        )

    assert error.value.error.code == -32602
    assert error.value.request_id == "rpc_1"


def test_extension_policy_uses_card_required_extensions() -> None:
    policy = A2AExtensionPolicy(
        supported=("urn:a", "urn:b"),
        required=("urn:b",),
    )

    result = policy.negotiate(requested=("urn:b", "urn:unknown"))
    assert result.accepted == ("urn:b",)
    assert result.unsupported == ("urn:unknown",)

    with pytest.raises(A2AExtensionNegotiationError) as error:
        policy.negotiate(requested=("urn:a",))
    assert error.value.error.code == -32008
    assert str(error.value) == "Extension support is required"
