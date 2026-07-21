from __future__ import annotations

from agentos._json_values import thaw_json_value
from agentos.transports.a2a._json_codec import require_string
from agentos.transports.a2a._message_codec import message_from_dict, message_to_dict
from agentos.transports.a2a._operation_params_helpers import (
    _optional,
    _optional_int,
    _optional_object,
    _optional_string,
    _tenant,
)
from agentos.transports.a2a._protojson import proto_fields, repeated, required_field
from agentos.transports.a2a._push_codec import (
    push_config_from_dict,
    push_config_to_dict,
)
from agentos.transports.a2a.operation_types import (
    A2AOperationParams,
    A2ASendMessageConfiguration,
    A2ASendMessageParams,
    A2ASendStreamingMessageParams,
)


def _send_to_dict(
    params: A2ASendMessageParams | A2ASendStreamingMessageParams,
) -> dict[str, object]:
    payload = _tenant(params.tenant)
    payload["message"] = message_to_dict(params.message)
    if params.configuration is not None:
        payload["configuration"] = _configuration_to_dict(params.configuration)
    if params.metadata is not None:
        payload["metadata"] = thaw_json_value(params.metadata)
    return payload


def _configuration_to_dict(config: A2ASendMessageConfiguration) -> dict[str, object]:
    payload: dict[str, object] = {}
    if config.accepted_output_modes:
        payload["acceptedOutputModes"] = list(config.accepted_output_modes)
    if config.task_push_notification_config is not None:
        payload["taskPushNotificationConfig"] = push_config_to_dict(
            config.task_push_notification_config
        )
    _optional(payload, "historyLength", config.history_length)
    if config.return_immediately:
        payload["returnImmediately"] = True
    return payload


def _send_from_dict(method: str, value: object) -> A2AOperationParams:
    fields = proto_fields(
        value,
        {
            "tenant": "tenant",
            "message": "message",
            "configuration": "configuration",
            "metadata": "metadata",
        },
        field_name=f"{method} params",
    )
    config = fields.get("configuration")
    kwargs = {
        "tenant": _optional_string(fields.get("tenant"), "tenant"),
        "message": message_from_dict(
            required_field(fields, "message", "message"), inbound=True, raw_counter=[0]
        ),
        "configuration": None if config is None else _configuration_from_dict(config),
        "metadata": _optional_object(fields.get("metadata"), "request metadata"),
    }
    cls = (
        A2ASendMessageParams
        if method == "SendMessage"
        else A2ASendStreamingMessageParams
    )
    return cls(**kwargs)


def _configuration_from_dict(value: object) -> A2ASendMessageConfiguration:
    fields = proto_fields(
        value,
        {
            "acceptedOutputModes": "accepted_output_modes",
            "taskPushNotificationConfig": "task_push_notification_config",
            "historyLength": "history_length",
            "returnImmediately": "return_immediately",
        },
        field_name="send configuration",
    )
    push = fields.get("taskPushNotificationConfig")
    immediately = fields.get("returnImmediately")
    if immediately is not None and type(immediately) is not bool:
        raise ValueError("returnImmediately must be bool")
    return A2ASendMessageConfiguration(
        accepted_output_modes=tuple(
            require_string(item, "acceptedOutputModes")
            for item in repeated(
                fields.get("acceptedOutputModes"), "acceptedOutputModes"
            )
        ),
        task_push_notification_config=None
        if push is None
        else push_config_from_dict(push),
        history_length=_optional_int(
            fields.get("historyLength"), "historyLength", minimum=0
        ),
        return_immediately=False if immediately is None else immediately,
    )
