from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from agentos.transports.a2a._validation import require_text, require_url_syntax


@dataclass(frozen=True, slots=True)
class A2AApiKeySecurityScheme:
    location: str
    name: str
    description: str | None = None

    def __post_init__(self) -> None:
        if self.location not in {"query", "header", "cookie"}:
            raise ValueError("API key location is invalid")
        require_text(self.name, "API key name", allow_empty=False)
        _optional_text(self.description, "API key description")


@dataclass(frozen=True, slots=True)
class A2AHttpAuthSecurityScheme:
    scheme: str
    description: str | None = None
    bearer_format: str | None = None

    def __post_init__(self) -> None:
        require_text(self.scheme, "HTTP auth scheme", allow_empty=False)
        _optional_text(self.description, "HTTP auth description")
        _optional_text(self.bearer_format, "bearer format")


@dataclass(frozen=True, slots=True)
class A2AAuthorizationCodeOAuthFlow:
    authorization_url: str
    token_url: str
    scopes: Mapping[str, str] = field(default_factory=dict)
    refresh_url: str | None = None
    pkce_required: bool = False

    def __post_init__(self) -> None:
        require_url_syntax(self.authorization_url, "authorization_url")
        require_url_syntax(self.token_url, "token_url")
        _optional_url(self.refresh_url, "refresh_url")
        object.__setattr__(self, "scopes", _string_map(self.scopes, "OAuth scopes"))
        _bool(self.pkce_required, "pkce_required")


@dataclass(frozen=True, slots=True)
class A2AClientCredentialsOAuthFlow:
    token_url: str
    scopes: Mapping[str, str] = field(default_factory=dict)
    refresh_url: str | None = None

    def __post_init__(self) -> None:
        require_url_syntax(self.token_url, "token_url")
        _optional_url(self.refresh_url, "refresh_url")
        object.__setattr__(self, "scopes", _string_map(self.scopes, "OAuth scopes"))


@dataclass(frozen=True, slots=True)
class A2AImplicitOAuthFlow:
    authorization_url: str | None = None
    refresh_url: str | None = None
    scopes: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _optional_url(self.authorization_url, "authorization_url")
        _optional_url(self.refresh_url, "refresh_url")
        object.__setattr__(self, "scopes", _string_map(self.scopes, "OAuth scopes"))


@dataclass(frozen=True, slots=True)
class A2APasswordOAuthFlow:
    token_url: str | None = None
    refresh_url: str | None = None
    scopes: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _optional_url(self.token_url, "token_url")
        _optional_url(self.refresh_url, "refresh_url")
        object.__setattr__(self, "scopes", _string_map(self.scopes, "OAuth scopes"))


@dataclass(frozen=True, slots=True)
class A2ADeviceCodeOAuthFlow:
    device_authorization_url: str
    token_url: str
    scopes: Mapping[str, str] = field(default_factory=dict)
    refresh_url: str | None = None

    def __post_init__(self) -> None:
        require_url_syntax(self.device_authorization_url, "device_authorization_url")
        require_url_syntax(self.token_url, "token_url")
        _optional_url(self.refresh_url, "refresh_url")
        object.__setattr__(self, "scopes", _string_map(self.scopes, "OAuth scopes"))


@dataclass(frozen=True, slots=True)
class A2AOAuthFlows:
    authorization_code: A2AAuthorizationCodeOAuthFlow | None = None
    client_credentials: A2AClientCredentialsOAuthFlow | None = None
    implicit: A2AImplicitOAuthFlow | None = None
    password: A2APasswordOAuthFlow | None = None
    device_code: A2ADeviceCodeOAuthFlow | None = None

    def __post_init__(self) -> None:
        _oneof(
            (self.authorization_code, A2AAuthorizationCodeOAuthFlow),
            (self.client_credentials, A2AClientCredentialsOAuthFlow),
            (self.implicit, A2AImplicitOAuthFlow),
            (self.password, A2APasswordOAuthFlow),
            (self.device_code, A2ADeviceCodeOAuthFlow),
        )


@dataclass(frozen=True, slots=True)
class A2AOAuth2SecurityScheme:
    flows: A2AOAuthFlows
    description: str | None = None
    oauth2_metadata_url: str | None = None

    def __post_init__(self) -> None:
        if type(self.flows) is not A2AOAuthFlows:
            raise TypeError("OAuth flows are invalid")
        _optional_text(self.description, "OAuth description")
        _optional_url(self.oauth2_metadata_url, "oauth2_metadata_url")


@dataclass(frozen=True, slots=True)
class A2AOpenIdConnectSecurityScheme:
    open_id_connect_url: str
    description: str | None = None

    def __post_init__(self) -> None:
        require_url_syntax(self.open_id_connect_url, "open_id_connect_url")
        _optional_text(self.description, "OpenID description")


@dataclass(frozen=True, slots=True)
class A2AMutualTlsSecurityScheme:
    description: str | None = None

    def __post_init__(self) -> None:
        _optional_text(self.description, "mTLS description")


@dataclass(frozen=True, slots=True)
class A2ASecurityScheme:
    api_key: A2AApiKeySecurityScheme | None = None
    http_auth: A2AHttpAuthSecurityScheme | None = None
    oauth2: A2AOAuth2SecurityScheme | None = None
    open_id_connect: A2AOpenIdConnectSecurityScheme | None = None
    mtls: A2AMutualTlsSecurityScheme | None = None

    def __post_init__(self) -> None:
        _oneof(
            (self.api_key, A2AApiKeySecurityScheme),
            (self.http_auth, A2AHttpAuthSecurityScheme),
            (self.oauth2, A2AOAuth2SecurityScheme),
            (self.open_id_connect, A2AOpenIdConnectSecurityScheme),
            (self.mtls, A2AMutualTlsSecurityScheme),
        )


@dataclass(frozen=True, slots=True)
class A2ASecurityRequirement:
    schemes: Mapping[str, tuple[str, ...]]

    def __post_init__(self) -> None:
        if not isinstance(self.schemes, Mapping):
            raise TypeError("security requirement schemes must be a mapping")
        copied: dict[str, tuple[str, ...]] = {}
        for name, scopes in self.schemes.items():
            require_text(name, "security scheme name", allow_empty=False)
            values = tuple(scopes)
            if any(type(scope) is not str for scope in values):
                raise TypeError("security scopes must contain strings")
            copied[name] = values
        object.__setattr__(self, "schemes", MappingProxyType(copied))


def _oneof(*values: tuple[object | None, type[object]]) -> None:
    present = tuple(
        (value, expected) for value, expected in values if value is not None
    )
    if len(present) != 1 or type(present[0][0]) is not present[0][1]:
        raise ValueError("oneof must contain exactly one correctly typed value")


def _string_map(value: Mapping[str, str], field_name: str) -> Mapping[str, str]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a mapping")
    copied = dict(value)
    if any(
        type(key) is not str or type(item) is not str for key, item in copied.items()
    ):
        raise TypeError(f"{field_name} must map strings to strings")
    return MappingProxyType(copied)


def _optional_text(value: str | None, field_name: str) -> None:
    if value is not None:
        require_text(value, field_name, allow_empty=False)


def _optional_url(value: str | None, field_name: str) -> None:
    if value is not None:
        require_url_syntax(value, field_name)


def _bool(value: bool, field_name: str) -> None:
    if type(value) is not bool:
        raise TypeError(f"{field_name} must be bool")


__all__ = [name for name in globals() if name.startswith("A2A")]
