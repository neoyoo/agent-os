from __future__ import annotations

from agentos.transports.a2a._card_components_codec import (
    _capabilities_from_dict,
    _capabilities_to_dict,
    _interface_from_dict,
    _interface_to_dict,
    _optional,
    _optional_string,
    _provider_from_dict,
    _required_string,
    _required_strings,
    _signature_from_dict,
    _signature_to_dict,
    _skill_from_dict,
    _skill_to_dict,
)
from agentos.transports.a2a._card_security_codec import (
    security_requirement_from_dict,
    security_requirement_to_dict,
    security_scheme_from_dict,
    security_scheme_to_dict,
)
from agentos.transports.a2a._jcs import jcs_bytes
from agentos.transports.a2a._protojson import proto_fields, repeated, required_field
from agentos.transports.a2a.card_types import A2AAgentCard


def agent_card_to_dict(card: A2AAgentCard) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": card.name,
        "description": card.description,
        "supportedInterfaces": [
            _interface_to_dict(item) for item in card.supported_interfaces
        ],
    }
    if card.provider is not None:
        payload["provider"] = {
            "url": card.provider.url,
            "organization": card.provider.organization,
        }
    payload["version"] = card.version
    _optional(payload, "documentationUrl", card.documentation_url)
    payload["capabilities"] = _capabilities_to_dict(card.capabilities)
    if card.security_schemes is not None:
        payload["securitySchemes"] = {
            name: security_scheme_to_dict(scheme)
            for name, scheme in card.security_schemes.items()
        }
    if card.security_requirements:
        payload["securityRequirements"] = [
            security_requirement_to_dict(item) for item in card.security_requirements
        ]
    payload["defaultInputModes"] = list(card.default_input_modes)
    payload["defaultOutputModes"] = list(card.default_output_modes)
    payload["skills"] = [_skill_to_dict(skill) for skill in card.skills]
    if card.signatures:
        payload["signatures"] = [_signature_to_dict(item) for item in card.signatures]
    _optional(payload, "iconUrl", card.icon_url)
    return payload


def agent_card_from_dict(value: object) -> A2AAgentCard:
    fields = proto_fields(
        value,
        {
            "name": "name",
            "description": "description",
            "supportedInterfaces": "supported_interfaces",
            "provider": "provider",
            "version": "version",
            "documentationUrl": "documentation_url",
            "capabilities": "capabilities",
            "securitySchemes": "security_schemes",
            "securityRequirements": "security_requirements",
            "defaultInputModes": "default_input_modes",
            "defaultOutputModes": "default_output_modes",
            "skills": "skills",
            "signatures": "signatures",
            "iconUrl": "icon_url",
        },
        field_name="AgentCard",
    )
    provider = fields.get("provider")
    security_schemes = fields.get("securitySchemes")
    if security_schemes is not None and type(security_schemes) is not dict:
        raise ValueError("securitySchemes must be an object")
    return A2AAgentCard(
        name=_required_string(fields, "name"),
        description=_required_string(fields, "description"),
        supported_interfaces=tuple(
            _interface_from_dict(item)
            for item in repeated(
                required_field(fields, "supportedInterfaces", "supportedInterfaces"),
                "supportedInterfaces",
            )
        ),
        provider=None if provider is None else _provider_from_dict(provider),
        version=_required_string(fields, "version"),
        documentation_url=_optional_string(
            fields.get("documentationUrl"), "documentationUrl"
        ),
        capabilities=_capabilities_from_dict(
            required_field(fields, "capabilities", "capabilities")
        ),
        security_schemes=None
        if security_schemes is None
        else {
            name: security_scheme_from_dict(item)
            for name, item in security_schemes.items()
        },
        security_requirements=tuple(
            security_requirement_from_dict(item)
            for item in repeated(
                fields.get("securityRequirements"), "securityRequirements"
            )
        ),
        default_input_modes=_required_strings(fields, "defaultInputModes"),
        default_output_modes=_required_strings(fields, "defaultOutputModes"),
        skills=tuple(
            _skill_from_dict(item)
            for item in repeated(required_field(fields, "skills", "skills"), "skills")
        ),
        signatures=tuple(
            _signature_from_dict(item)
            for item in repeated(fields.get("signatures"), "signatures")
        ),
        icon_url=_optional_string(fields.get("iconUrl"), "iconUrl"),
    )


def agent_card_signing_payload(card: A2AAgentCard) -> bytes:
    payload = agent_card_to_dict(card)
    payload.pop("signatures", None)
    return jcs_bytes(payload)


__all__ = ["agent_card_from_dict", "agent_card_signing_payload", "agent_card_to_dict"]
