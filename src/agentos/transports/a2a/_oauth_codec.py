from __future__ import annotations

from agentos.transports.a2a._json_codec import require_string
from agentos.transports.a2a._protojson import proto_fields, required_field
from agentos.transports.a2a.card_security_types import (
    A2AAuthorizationCodeOAuthFlow,
    A2AClientCredentialsOAuthFlow,
    A2ADeviceCodeOAuthFlow,
    A2AImplicitOAuthFlow,
    A2AOAuthFlows,
    A2APasswordOAuthFlow,
)


def oauth_flows_to_dict(flows: A2AOAuthFlows) -> dict[str, object]:
    if flows.authorization_code is not None:
        return {"authorizationCode": _authorization_to_dict(flows.authorization_code)}
    if flows.client_credentials is not None:
        return {"clientCredentials": _client_to_dict(flows.client_credentials)}
    if flows.implicit is not None:
        return {"implicit": _implicit_to_dict(flows.implicit)}
    if flows.password is not None:
        return {"password": _password_to_dict(flows.password)}
    assert flows.device_code is not None
    return {"deviceCode": _device_to_dict(flows.device_code)}


def oauth_flows_from_dict(value: object) -> A2AOAuthFlows:
    fields = proto_fields(
        value,
        {
            "authorizationCode": "authorization_code",
            "clientCredentials": "client_credentials",
            "implicit": "implicit",
            "password": "password",
            "deviceCode": "device_code",
        },
        field_name="OAuth flows",
    )
    present = tuple(name for name, item in fields.items() if item is not None)
    if len(present) != 1:
        raise ValueError("OAuth flows must select exactly one flow")
    name = present[0]
    return A2AOAuthFlows(
        authorization_code=None
        if name != "authorizationCode"
        else _authorization_from_dict(fields[name]),
        client_credentials=None
        if name != "clientCredentials"
        else _client_from_dict(fields[name]),
        implicit=None if name != "implicit" else _implicit_from_dict(fields[name]),
        password=None if name != "password" else _password_from_dict(fields[name]),
        device_code=None if name != "deviceCode" else _device_from_dict(fields[name]),
    )


def _authorization_to_dict(flow: A2AAuthorizationCodeOAuthFlow) -> dict[str, object]:
    payload: dict[str, object] = {
        "authorizationUrl": flow.authorization_url,
        "tokenUrl": flow.token_url,
        "scopes": dict(flow.scopes),
    }
    _optional(payload, "refreshUrl", flow.refresh_url)
    if flow.pkce_required:
        payload["pkceRequired"] = True
    return payload


def _authorization_from_dict(value: object) -> A2AAuthorizationCodeOAuthFlow:
    fields = proto_fields(
        value,
        {
            "authorizationUrl": "authorization_url",
            "tokenUrl": "token_url",
            "refreshUrl": "refresh_url",
            "scopes": "scopes",
            "pkceRequired": "pkce_required",
        },
        field_name="authorization code flow",
    )
    return A2AAuthorizationCodeOAuthFlow(
        authorization_url=_required_string(fields, "authorizationUrl"),
        token_url=_required_string(fields, "tokenUrl"),
        refresh_url=_optional_string(fields.get("refreshUrl"), "refreshUrl"),
        scopes=_string_map(required_field(fields, "scopes", "scopes"), "scopes"),
        pkce_required=_bool(fields.get("pkceRequired"), "pkceRequired"),
    )


def _client_to_dict(flow: A2AClientCredentialsOAuthFlow) -> dict[str, object]:
    payload: dict[str, object] = {
        "tokenUrl": flow.token_url,
        "scopes": dict(flow.scopes),
    }
    _optional(payload, "refreshUrl", flow.refresh_url)
    return payload


def _client_from_dict(value: object) -> A2AClientCredentialsOAuthFlow:
    fields = proto_fields(
        value,
        {"tokenUrl": "token_url", "refreshUrl": "refresh_url", "scopes": "scopes"},
        field_name="client credentials flow",
    )
    return A2AClientCredentialsOAuthFlow(
        token_url=_required_string(fields, "tokenUrl"),
        refresh_url=_optional_string(fields.get("refreshUrl"), "refreshUrl"),
        scopes=_string_map(required_field(fields, "scopes", "scopes"), "scopes"),
    )


def _implicit_to_dict(flow: A2AImplicitOAuthFlow) -> dict[str, object]:
    payload: dict[str, object] = {}
    _optional(payload, "authorizationUrl", flow.authorization_url)
    _optional(payload, "refreshUrl", flow.refresh_url)
    if flow.scopes:
        payload["scopes"] = dict(flow.scopes)
    return payload


def _implicit_from_dict(value: object) -> A2AImplicitOAuthFlow:
    fields = proto_fields(
        value,
        {
            "authorizationUrl": "authorization_url",
            "refreshUrl": "refresh_url",
            "scopes": "scopes",
        },
        field_name="implicit flow",
    )
    return A2AImplicitOAuthFlow(
        authorization_url=_optional_string(
            fields.get("authorizationUrl"), "authorizationUrl"
        ),
        refresh_url=_optional_string(fields.get("refreshUrl"), "refreshUrl"),
        scopes=_string_map(fields.get("scopes"), "scopes"),
    )


def _password_to_dict(flow: A2APasswordOAuthFlow) -> dict[str, object]:
    payload: dict[str, object] = {}
    _optional(payload, "tokenUrl", flow.token_url)
    _optional(payload, "refreshUrl", flow.refresh_url)
    if flow.scopes:
        payload["scopes"] = dict(flow.scopes)
    return payload


def _password_from_dict(value: object) -> A2APasswordOAuthFlow:
    fields = proto_fields(
        value,
        {"tokenUrl": "token_url", "refreshUrl": "refresh_url", "scopes": "scopes"},
        field_name="password flow",
    )
    return A2APasswordOAuthFlow(
        token_url=_optional_string(fields.get("tokenUrl"), "tokenUrl"),
        refresh_url=_optional_string(fields.get("refreshUrl"), "refreshUrl"),
        scopes=_string_map(fields.get("scopes"), "scopes"),
    )


def _device_to_dict(flow: A2ADeviceCodeOAuthFlow) -> dict[str, object]:
    payload: dict[str, object] = {
        "deviceAuthorizationUrl": flow.device_authorization_url,
        "tokenUrl": flow.token_url,
        "scopes": dict(flow.scopes),
    }
    _optional(payload, "refreshUrl", flow.refresh_url)
    return payload


def _device_from_dict(value: object) -> A2ADeviceCodeOAuthFlow:
    fields = proto_fields(
        value,
        {
            "deviceAuthorizationUrl": "device_authorization_url",
            "tokenUrl": "token_url",
            "refreshUrl": "refresh_url",
            "scopes": "scopes",
        },
        field_name="device code flow",
    )
    return A2ADeviceCodeOAuthFlow(
        device_authorization_url=_required_string(fields, "deviceAuthorizationUrl"),
        token_url=_required_string(fields, "tokenUrl"),
        refresh_url=_optional_string(fields.get("refreshUrl"), "refreshUrl"),
        scopes=_string_map(required_field(fields, "scopes", "scopes"), "scopes"),
    )


def _string_map(value: object, field_name: str) -> dict[str, str]:
    if value is None:
        return {}
    if type(value) is not dict or any(
        type(key) is not str or type(item) is not str for key, item in value.items()
    ):
        raise ValueError(f"{field_name} must map strings to strings")
    return value


def _required_string(fields: dict[str, object], name: str) -> str:
    return require_string(required_field(fields, name, name), name)


def _optional_string(value: object, field_name: str) -> str | None:
    return None if value is None or value == "" else require_string(value, field_name)


def _bool(value: object, field_name: str) -> bool:
    if value is None:
        return False
    if type(value) is not bool:
        raise ValueError(f"{field_name} must be bool")
    return value


def _optional(payload: dict[str, object], name: str, value: object) -> None:
    if value is not None:
        payload[name] = value


__all__ = ["oauth_flows_from_dict", "oauth_flows_to_dict"]
