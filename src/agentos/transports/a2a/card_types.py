from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from urllib.parse import urlsplit

from agentos._json_values import FrozenJsonObject
from agentos.transports.a2a._validation import (
    freeze_json_object,
    freeze_string_tuple,
    require_identifier,
    require_text,
    require_url_syntax,
)
from agentos.transports.a2a.card_security_types import *  # noqa: F403
from agentos.transports.a2a.card_security_types import (
    A2ASecurityRequirement,
    A2ASecurityScheme,
)


@dataclass(frozen=True, slots=True)
class A2AAgentInterface:
    url: str
    protocol_binding: str
    protocol_version: str
    tenant: str | None = None

    def __post_init__(self) -> None:
        require_url_syntax(self.url, "interface url")
        require_identifier(self.protocol_binding, "protocol_binding")
        require_identifier(self.protocol_version, "protocol_version")
        if self.tenant is not None:
            require_identifier(self.tenant, "tenant")


@dataclass(frozen=True, slots=True)
class A2AAgentProvider:
    url: str
    organization: str

    def __post_init__(self) -> None:
        require_url_syntax(self.url, "provider url")
        require_text(self.organization, "provider organization", allow_empty=False)


@dataclass(frozen=True, slots=True)
class A2AAgentExtension:
    uri: str
    description: str | None = None
    required: bool = False
    params: FrozenJsonObject | Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        _require_uri(self.uri)
        if self.description is not None:
            require_text(self.description, "extension description", allow_empty=False)
        if type(self.required) is not bool:
            raise TypeError("extension required must be bool")
        if self.params is not None:
            object.__setattr__(
                self, "params", freeze_json_object(self.params, field_name="params")
            )


@dataclass(frozen=True, slots=True)
class A2AAgentCapabilities:
    streaming: bool | None = None
    push_notifications: bool | None = None
    extensions: tuple[A2AAgentExtension, ...] = ()
    extended_agent_card: bool | None = None

    def __post_init__(self) -> None:
        for name in ("streaming", "push_notifications", "extended_agent_card"):
            value = getattr(self, name)
            if value is not None and type(value) is not bool:
                raise TypeError(f"{name} must be bool or None")
        extensions = _typed_tuple(self.extensions, A2AAgentExtension, "extensions")
        uris = tuple(extension.uri for extension in extensions)
        if len(set(uris)) != len(uris):
            raise ValueError("extension URI must be unique")
        object.__setattr__(self, "extensions", extensions)


@dataclass(frozen=True, slots=True)
class A2AAgentSkill:
    id: str
    name: str
    description: str
    tags: tuple[str, ...]
    examples: tuple[str, ...] = ()
    input_modes: tuple[str, ...] = ()
    output_modes: tuple[str, ...] = ()
    security_requirements: tuple[A2ASecurityRequirement, ...] = ()

    def __post_init__(self) -> None:
        require_identifier(self.id, "skill id")
        require_text(self.name, "skill name", allow_empty=False)
        require_text(self.description, "skill description", allow_empty=False)
        object.__setattr__(
            self, "tags", freeze_string_tuple(self.tags, "tags", allow_empty=False)
        )
        object.__setattr__(self, "examples", _text_tuple(self.examples, "examples"))
        object.__setattr__(
            self, "input_modes", freeze_string_tuple(self.input_modes, "input_modes")
        )
        object.__setattr__(
            self, "output_modes", freeze_string_tuple(self.output_modes, "output_modes")
        )
        object.__setattr__(
            self,
            "security_requirements",
            _typed_tuple(
                self.security_requirements,
                A2ASecurityRequirement,
                "security_requirements",
            ),
        )


@dataclass(frozen=True, slots=True)
class A2AAgentCardSignature:
    protected: str
    signature: str
    header: FrozenJsonObject | Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        require_text(self.protected, "protected", allow_empty=False)
        require_text(self.signature, "signature", allow_empty=False)
        if self.header is not None:
            object.__setattr__(
                self, "header", freeze_json_object(self.header, field_name="header")
            )


@dataclass(frozen=True, slots=True)
class A2AAgentCard:
    name: str
    description: str
    supported_interfaces: tuple[A2AAgentInterface, ...]
    version: str
    capabilities: A2AAgentCapabilities
    default_input_modes: tuple[str, ...]
    default_output_modes: tuple[str, ...]
    skills: tuple[A2AAgentSkill, ...]
    provider: A2AAgentProvider | None = None
    documentation_url: str | None = None
    security_schemes: Mapping[str, A2ASecurityScheme] | None = None
    security_requirements: tuple[A2ASecurityRequirement, ...] = ()
    signatures: tuple[A2AAgentCardSignature, ...] = ()
    icon_url: str | None = None

    def __post_init__(self) -> None:
        require_text(self.name, "card name", allow_empty=False)
        require_text(self.description, "card description", allow_empty=False)
        interfaces = _typed_tuple(
            self.supported_interfaces,
            A2AAgentInterface,
            "supported_interfaces",
            required=True,
        )
        if not any(
            interface.protocol_binding == "JSONRPC"
            and interface.protocol_version == "1.0"
            for interface in interfaces
        ):
            raise ValueError("card must publish a JSONRPC 1.0 interface")
        object.__setattr__(self, "supported_interfaces", interfaces)
        require_identifier(self.version, "card version")
        if type(self.capabilities) is not A2AAgentCapabilities:
            raise TypeError("capabilities must be A2AAgentCapabilities")
        object.__setattr__(
            self,
            "default_input_modes",
            freeze_string_tuple(
                self.default_input_modes, "default_input_modes", allow_empty=False
            ),
        )
        object.__setattr__(
            self,
            "default_output_modes",
            freeze_string_tuple(
                self.default_output_modes, "default_output_modes", allow_empty=False
            ),
        )
        object.__setattr__(
            self,
            "skills",
            _typed_tuple(self.skills, A2AAgentSkill, "skills", required=True),
        )
        if self.provider is not None and type(self.provider) is not A2AAgentProvider:
            raise TypeError("provider must be A2AAgentProvider or None")
        for value, name in (
            (self.documentation_url, "documentation_url"),
            (self.icon_url, "icon_url"),
        ):
            if value is not None:
                require_url_syntax(value, name)
        if self.security_schemes is not None:
            object.__setattr__(
                self, "security_schemes", _security_schemes(self.security_schemes)
            )
        object.__setattr__(
            self,
            "security_requirements",
            _typed_tuple(
                self.security_requirements,
                A2ASecurityRequirement,
                "security_requirements",
            ),
        )
        object.__setattr__(
            self,
            "signatures",
            _typed_tuple(self.signatures, A2AAgentCardSignature, "signatures"),
        )


def _security_schemes(
    value: Mapping[str, A2ASecurityScheme],
) -> Mapping[str, A2ASecurityScheme]:
    if not isinstance(value, Mapping):
        raise TypeError("security_schemes must be a mapping")
    copied = dict(value)
    for name, scheme in copied.items():
        require_identifier(name, "security scheme name")
        if type(scheme) is not A2ASecurityScheme:
            raise TypeError("security_schemes contains an invalid value")
    return MappingProxyType(copied)


def _typed_tuple(
    values: object, expected: type[object], field_name: str, *, required: bool = False
) -> tuple[object, ...]:
    result = tuple(values)  # type: ignore[arg-type]
    if required and not result:
        raise ValueError(f"{field_name} must not be empty")
    if any(type(value) is not expected for value in result):
        raise TypeError(f"{field_name} contains an invalid value")
    return result


def _text_tuple(values: object, field_name: str) -> tuple[str, ...]:
    result = tuple(values)  # type: ignore[arg-type]
    if any(type(value) is not str for value in result):
        raise TypeError(f"{field_name} must contain strings")
    return result


def _require_uri(value: str) -> None:
    if (
        type(value) is not str
        or not value
        or any(char.isspace() for char in value)
        or not urlsplit(value).scheme
    ):
        raise ValueError("extension URI is invalid")


__all__ = [name for name in globals() if name.startswith("A2A")]
