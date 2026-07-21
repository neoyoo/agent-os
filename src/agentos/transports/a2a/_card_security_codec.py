from __future__ import annotations

from agentos.transports.a2a._json_codec import require_string
from agentos.transports.a2a._oauth_codec import (
    oauth_flows_from_dict,
    oauth_flows_to_dict,
)
from agentos.transports.a2a._protojson import proto_fields, repeated, required_field
from agentos.transports.a2a.card_security_types import (
    A2AApiKeySecurityScheme,
    A2AHttpAuthSecurityScheme,
    A2AMutualTlsSecurityScheme,
    A2AOAuth2SecurityScheme,
    A2AOpenIdConnectSecurityScheme,
    A2ASecurityRequirement,
    A2ASecurityScheme,
)


def security_scheme_to_dict(scheme: A2ASecurityScheme) -> dict[str, object]:
    if scheme.api_key is not None:
        value = scheme.api_key
        payload = {"location": value.location, "name": value.name}
        _optional(payload, "description", value.description)
        return {"apiKeySecurityScheme": payload}
    if scheme.http_auth is not None:
        value = scheme.http_auth
        payload = {"scheme": value.scheme}
        _optional(payload, "description", value.description)
        _optional(payload, "bearerFormat", value.bearer_format)
        return {"httpAuthSecurityScheme": payload}
    if scheme.oauth2 is not None:
        value = scheme.oauth2
        payload = {"flows": oauth_flows_to_dict(value.flows)}
        _optional(payload, "description", value.description)
        _optional(payload, "oauth2MetadataUrl", value.oauth2_metadata_url)
        return {"oauth2SecurityScheme": payload}
    if scheme.open_id_connect is not None:
        value = scheme.open_id_connect
        payload = {"openIdConnectUrl": value.open_id_connect_url}
        _optional(payload, "description", value.description)
        return {"openIdConnectSecurityScheme": payload}
    assert scheme.mtls is not None
    payload = {}
    _optional(payload, "description", scheme.mtls.description)
    return {"mtlsSecurityScheme": payload}


def security_scheme_from_dict(value: object) -> A2ASecurityScheme:
    fields = proto_fields(
        value,
        {
            "apiKeySecurityScheme": "api_key_security_scheme",
            "httpAuthSecurityScheme": "http_auth_security_scheme",
            "oauth2SecurityScheme": "oauth2_security_scheme",
            "openIdConnectSecurityScheme": "open_id_connect_security_scheme",
            "mtlsSecurityScheme": "mtls_security_scheme",
        },
        field_name="security scheme",
    )
    present = tuple(name for name, item in fields.items() if item is not None)
    if len(present) != 1:
        raise ValueError("security scheme must select exactly one value")
    name = present[0]
    parser = {
        "apiKeySecurityScheme": _api_key_from_dict,
        "httpAuthSecurityScheme": _http_from_dict,
        "oauth2SecurityScheme": _oauth2_from_dict,
        "openIdConnectSecurityScheme": _openid_from_dict,
        "mtlsSecurityScheme": _mtls_from_dict,
    }[name]
    keyword = {
        "apiKeySecurityScheme": "api_key",
        "httpAuthSecurityScheme": "http_auth",
        "oauth2SecurityScheme": "oauth2",
        "openIdConnectSecurityScheme": "open_id_connect",
        "mtlsSecurityScheme": "mtls",
    }[name]
    return A2ASecurityScheme(**{keyword: parser(fields[name])})


def security_requirement_to_dict(value: A2ASecurityRequirement) -> dict[str, object]:
    return {
        "schemes": {
            name: {"list": list(scopes)} for name, scopes in value.schemes.items()
        }
    }


def security_requirement_from_dict(value: object) -> A2ASecurityRequirement:
    fields = proto_fields(
        value, {"schemes": "schemes"}, field_name="security requirement"
    )
    schemes = fields.get("schemes")
    if schemes is None:
        schemes = {}
    if type(schemes) is not dict:
        raise ValueError("security requirement schemes must be an object")
    result: dict[str, tuple[str, ...]] = {}
    for name, scopes_value in schemes.items():
        if type(name) is not str:
            raise ValueError("security scheme name must be str")
        scope_fields = proto_fields(
            scopes_value, {"list": "list"}, field_name="security scopes"
        )
        result[name] = tuple(
            require_string(item, "security scope", empty=True)
            for item in repeated(scope_fields.get("list"), "security scopes")
        )
    return A2ASecurityRequirement(result)


def _api_key_from_dict(value: object) -> A2AApiKeySecurityScheme:
    fields = proto_fields(
        value,
        {"description": "description", "location": "location", "name": "name"},
        field_name="API key scheme",
    )
    return A2AApiKeySecurityScheme(
        description=_optional_string(fields.get("description"), "description"),
        location=_required_string(fields, "location"),
        name=_required_string(fields, "name"),
    )


def _http_from_dict(value: object) -> A2AHttpAuthSecurityScheme:
    fields = proto_fields(
        value,
        {
            "description": "description",
            "scheme": "scheme",
            "bearerFormat": "bearer_format",
        },
        field_name="HTTP auth scheme",
    )
    return A2AHttpAuthSecurityScheme(
        description=_optional_string(fields.get("description"), "description"),
        scheme=_required_string(fields, "scheme"),
        bearer_format=_optional_string(fields.get("bearerFormat"), "bearerFormat"),
    )


def _oauth2_from_dict(value: object) -> A2AOAuth2SecurityScheme:
    fields = proto_fields(
        value,
        {
            "description": "description",
            "flows": "flows",
            "oauth2MetadataUrl": "oauth2_metadata_url",
        },
        field_name="OAuth2 scheme",
    )
    return A2AOAuth2SecurityScheme(
        description=_optional_string(fields.get("description"), "description"),
        flows=oauth_flows_from_dict(required_field(fields, "flows", "flows")),
        oauth2_metadata_url=_optional_string(
            fields.get("oauth2MetadataUrl"), "oauth2MetadataUrl"
        ),
    )


def _openid_from_dict(value: object) -> A2AOpenIdConnectSecurityScheme:
    fields = proto_fields(
        value,
        {"description": "description", "openIdConnectUrl": "open_id_connect_url"},
        field_name="OpenID scheme",
    )
    return A2AOpenIdConnectSecurityScheme(
        description=_optional_string(fields.get("description"), "description"),
        open_id_connect_url=_required_string(fields, "openIdConnectUrl"),
    )


def _mtls_from_dict(value: object) -> A2AMutualTlsSecurityScheme:
    fields = proto_fields(
        value, {"description": "description"}, field_name="mTLS scheme"
    )
    return A2AMutualTlsSecurityScheme(
        _optional_string(fields.get("description"), "description")
    )


def _required_string(fields: dict[str, object], name: str) -> str:
    return require_string(required_field(fields, name, name), name)


def _optional_string(value: object, field_name: str) -> str | None:
    return None if value is None or value == "" else require_string(value, field_name)


def _optional(payload: dict[str, object], name: str, value: object) -> None:
    if value is not None:
        payload[name] = value


__all__ = [
    "security_requirement_from_dict",
    "security_requirement_to_dict",
    "security_scheme_from_dict",
    "security_scheme_to_dict",
]
