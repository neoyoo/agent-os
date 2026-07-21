from __future__ import annotations

from agentos._json_values import thaw_json_value
from agentos.transports.a2a._card_security_codec import (
    security_requirement_from_dict,
    security_requirement_to_dict,
)
from agentos.transports.a2a._json_codec import require_string
from agentos.transports.a2a._protojson import proto_fields, repeated, required_field
from agentos.transports.a2a.card_types import (
    A2AAgentCapabilities,
    A2AAgentCardSignature,
    A2AAgentExtension,
    A2AAgentInterface,
    A2AAgentProvider,
    A2AAgentSkill,
)


def _interface_to_dict(value: A2AAgentInterface) -> dict[str, object]:
    payload = {"url": value.url, "protocolBinding": value.protocol_binding}
    _optional(payload, "tenant", value.tenant)
    payload["protocolVersion"] = value.protocol_version
    return payload


def _interface_from_dict(value: object) -> A2AAgentInterface:
    fields = proto_fields(
        value,
        {
            "url": "url",
            "protocolBinding": "protocol_binding",
            "tenant": "tenant",
            "protocolVersion": "protocol_version",
        },
        field_name="AgentInterface",
    )
    return A2AAgentInterface(
        url=_required_string(fields, "url"),
        protocol_binding=_required_string(fields, "protocolBinding"),
        tenant=_optional_string(fields.get("tenant"), "tenant"),
        protocol_version=_required_string(fields, "protocolVersion"),
    )


def _provider_from_dict(value: object) -> A2AAgentProvider:
    fields = proto_fields(
        value,
        {"url": "url", "organization": "organization"},
        field_name="AgentProvider",
    )
    return A2AAgentProvider(
        url=_required_string(fields, "url"),
        organization=_required_string(fields, "organization"),
    )


def _capabilities_to_dict(value: A2AAgentCapabilities) -> dict[str, object]:
    payload: dict[str, object] = {}
    _optional(payload, "streaming", value.streaming)
    _optional(payload, "pushNotifications", value.push_notifications)
    if value.extensions:
        payload["extensions"] = [_extension_to_dict(item) for item in value.extensions]
    _optional(payload, "extendedAgentCard", value.extended_agent_card)
    return payload


def _capabilities_from_dict(value: object) -> A2AAgentCapabilities:
    fields = proto_fields(
        value,
        {
            "streaming": "streaming",
            "pushNotifications": "push_notifications",
            "extensions": "extensions",
            "extendedAgentCard": "extended_agent_card",
        },
        field_name="AgentCapabilities",
    )
    return A2AAgentCapabilities(
        streaming=_optional_bool(fields.get("streaming"), "streaming"),
        push_notifications=_optional_bool(
            fields.get("pushNotifications"), "pushNotifications"
        ),
        extensions=tuple(
            _extension_from_dict(item)
            for item in repeated(fields.get("extensions"), "extensions")
        ),
        extended_agent_card=_optional_bool(
            fields.get("extendedAgentCard"), "extendedAgentCard"
        ),
    )


def _extension_to_dict(value: A2AAgentExtension) -> dict[str, object]:
    payload: dict[str, object] = {"uri": value.uri}
    _optional(payload, "description", value.description)
    if value.required:
        payload["required"] = True
    if value.params is not None:
        payload["params"] = thaw_json_value(value.params)
    return payload


def _extension_from_dict(value: object) -> A2AAgentExtension:
    fields = proto_fields(
        value,
        {
            "uri": "uri",
            "description": "description",
            "required": "required",
            "params": "params",
        },
        field_name="AgentExtension",
    )
    params = fields.get("params")
    if params is not None and type(params) is not dict:
        raise ValueError("extension params must be an object")
    return A2AAgentExtension(
        uri=_required_string(fields, "uri"),
        description=_optional_string(fields.get("description"), "description"),
        required=False
        if fields.get("required") is None
        else _required_bool(fields["required"], "required"),
        params=params,
    )


def _skill_to_dict(value: A2AAgentSkill) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": value.id,
        "name": value.name,
        "description": value.description,
        "tags": list(value.tags),
    }
    if value.examples:
        payload["examples"] = list(value.examples)
    if value.input_modes:
        payload["inputModes"] = list(value.input_modes)
    if value.output_modes:
        payload["outputModes"] = list(value.output_modes)
    if value.security_requirements:
        payload["securityRequirements"] = [
            security_requirement_to_dict(item) for item in value.security_requirements
        ]
    return payload


def _skill_from_dict(value: object) -> A2AAgentSkill:
    fields = proto_fields(
        value,
        {
            "id": "id",
            "name": "name",
            "description": "description",
            "tags": "tags",
            "examples": "examples",
            "inputModes": "input_modes",
            "outputModes": "output_modes",
            "securityRequirements": "security_requirements",
        },
        field_name="AgentSkill",
    )
    return A2AAgentSkill(
        id=_required_string(fields, "id"),
        name=_required_string(fields, "name"),
        description=_required_string(fields, "description"),
        tags=_required_strings(fields, "tags"),
        examples=_strings(fields.get("examples"), "examples"),
        input_modes=_strings(fields.get("inputModes"), "inputModes"),
        output_modes=_strings(fields.get("outputModes"), "outputModes"),
        security_requirements=tuple(
            security_requirement_from_dict(item)
            for item in repeated(
                fields.get("securityRequirements"), "securityRequirements"
            )
        ),
    )


def _signature_to_dict(value: A2AAgentCardSignature) -> dict[str, object]:
    payload: dict[str, object] = {
        "protected": value.protected,
        "signature": value.signature,
    }
    if value.header is not None:
        payload["header"] = thaw_json_value(value.header)
    return payload


def _signature_from_dict(value: object) -> A2AAgentCardSignature:
    fields = proto_fields(
        value,
        {"protected": "protected", "signature": "signature", "header": "header"},
        field_name="AgentCardSignature",
    )
    header = fields.get("header")
    if header is not None and type(header) is not dict:
        raise ValueError("signature header must be an object")
    return A2AAgentCardSignature(
        _required_string(fields, "protected"),
        _required_string(fields, "signature"),
        header,
    )


def _required_strings(fields: dict[str, object], name: str) -> tuple[str, ...]:
    return _strings(required_field(fields, name, name), name)


def _strings(value: object, name: str) -> tuple[str, ...]:
    return tuple(
        require_string(item, name, empty=True) for item in repeated(value, name)
    )


def _required_string(fields: dict[str, object], name: str) -> str:
    return require_string(required_field(fields, name, name), name)


def _optional_string(value: object, name: str) -> str | None:
    return None if value is None or value == "" else require_string(value, name)


def _required_bool(value: object, name: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{name} must be bool")
    return value


def _optional_bool(value: object, name: str) -> bool | None:
    return None if value is None else _required_bool(value, name)


def _optional(payload: dict[str, object], name: str, value: object) -> None:
    if value is not None:
        payload[name] = value
