from __future__ import annotations

from agentos.transports.a2a._json_codec import require_string
from agentos.transports.a2a._protojson import proto_fields, required_field
from agentos.transports.a2a.push_types import (
    A2AAuthenticationInfo,
    A2ATaskPushNotificationConfig,
)


def push_config_to_dict(config: A2ATaskPushNotificationConfig) -> dict[str, object]:
    payload: dict[str, object] = {}
    if config.tenant is not None:
        payload["tenant"] = config.tenant
    if config.id is not None:
        payload["id"] = config.id
    if config.task_id is not None:
        payload["taskId"] = config.task_id
    payload["url"] = config.url
    if config.token is not None:
        payload["token"] = config.token
    if config.authentication is not None:
        authentication: dict[str, object] = {
            "scheme": config.authentication.scheme,
        }
        if config.authentication.credentials is not None:
            authentication["credentials"] = config.authentication.credentials
        payload["authentication"] = authentication
    return payload


def push_config_from_dict(value: object) -> A2ATaskPushNotificationConfig:
    fields = proto_fields(
        value,
        {
            "tenant": "tenant",
            "id": "id",
            "taskId": "task_id",
            "url": "url",
            "token": "token",
            "authentication": "authentication",
        },
        field_name="push notification config",
    )
    authentication = fields.get("authentication")
    return A2ATaskPushNotificationConfig(
        tenant=_optional_string(fields.get("tenant"), "tenant"),
        id=_optional_string(fields.get("id"), "push config id"),
        task_id=_optional_string(fields.get("taskId"), "taskId"),
        url=require_string(required_field(fields, "url", "push url"), "push url"),
        token=_optional_string(fields.get("token"), "push token"),
        authentication=None
        if authentication is None
        else authentication_from_dict(authentication),
    )


def authentication_from_dict(value: object) -> A2AAuthenticationInfo:
    fields = proto_fields(
        value,
        {"scheme": "scheme", "credentials": "credentials"},
        field_name="push authentication",
    )
    return A2AAuthenticationInfo(
        scheme=require_string(
            required_field(fields, "scheme", "authentication scheme"),
            "authentication scheme",
        ),
        credentials=_optional_string(fields.get("credentials"), "credentials"),
    )


def _optional_string(value: object, field_name: str) -> str | None:
    if value is None or value == "":
        return None
    return require_string(value, field_name)


__all__ = [
    "authentication_from_dict",
    "push_config_from_dict",
    "push_config_to_dict",
]
