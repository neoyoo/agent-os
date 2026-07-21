from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from agentos.transports.a2a._validation import freeze_string_tuple
from agentos.transports.a2a.operation_types import A2AOperationError


A2A_VERSION_HEADER = "A2A-Version"
A2A_EXTENSIONS_HEADER = "A2A-Extensions"
A2A_SUPPORTED_VERSION = "1.0"
A2A_LEGACY_MISSING_VERSION = "0.3"
A2A_JSON_RPC_MEDIA_TYPE = "application/json"
A2A_SSE_MEDIA_TYPE = "text/event-stream"
A2A_PUSH_MEDIA_TYPE = "application/a2a+json"
A2A_SNAPSHOT_RESUME_EXTENSION = "https://agentos.dev/a2a/extensions/snapshot-resume/v1"


class A2AProtocolVersionError(ValueError):
    def __init__(self) -> None:
        self.error = A2AOperationError(-32009)
        super().__init__(self.error.message)


class A2AExtensionNegotiationError(ValueError):
    def __init__(self) -> None:
        self.error = A2AOperationError(-32008)
        super().__init__(self.error.message)


@dataclass(frozen=True, slots=True)
class A2AProtocolVersionPolicy:
    supported: tuple[str, ...] = (A2A_SUPPORTED_VERSION,)

    def __post_init__(self) -> None:
        normalized = tuple(_normalize_version(value) for value in self.supported)
        if normalized != (A2A_SUPPORTED_VERSION,):
            raise ValueError("A2A supported versions must be exactly ('1.0',)")
        object.__setattr__(self, "supported", normalized)

    def negotiate(self, requested: str | None) -> str:
        candidate = (
            A2A_LEGACY_MISSING_VERSION
            if requested is None or requested == ""
            else _normalize_version(requested)
        )
        if candidate not in self.supported:
            raise A2AProtocolVersionError
        return candidate


@dataclass(frozen=True, slots=True)
class A2AExtensionNegotiation:
    requested: tuple[str, ...]
    accepted: tuple[str, ...]
    unsupported: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class A2AExtensionPolicy:
    supported: tuple[str, ...] = ()
    required: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        supported = _uri_tuple(self.supported, "supported extensions")
        required = _uri_tuple(self.required, "required extensions")
        if any(uri not in supported for uri in required):
            raise ValueError("required extensions must be supported")
        object.__setattr__(self, "supported", supported)
        object.__setattr__(self, "required", required)

    def negotiate(
        self,
        *,
        requested: tuple[str, ...] = (),
    ) -> A2AExtensionNegotiation:
        requested_values = _uri_tuple(requested, "requested extensions")
        if any(value not in requested_values for value in self.required):
            raise A2AExtensionNegotiationError
        return A2AExtensionNegotiation(
            requested=requested_values,
            accepted=tuple(
                value for value in requested_values if value in self.supported
            ),
            unsupported=tuple(
                value for value in requested_values if value not in self.supported
            ),
        )


def parse_extensions_header(value: str | None) -> tuple[str, ...]:
    if value is None:
        return ()
    try:
        encoded = value.encode("ascii")
    except (AttributeError, UnicodeEncodeError):
        raise ValueError("A2A-Extensions header is invalid") from None
    if type(value) is not str or not value or len(encoded) > 4096:
        raise ValueError("A2A-Extensions header is invalid")
    result: list[str] = []
    for raw_item in value.split(","):
        item = raw_item.strip(" \t")
        _require_absolute_uri(item)
        if item not in result:
            result.append(item)
    return tuple(result)


def _normalize_version(value: object) -> str:
    if type(value) is not str:
        raise A2AProtocolVersionError
    parts = value.strip().split(".")
    if len(parts) < 2 or not parts[0].isdigit() or not parts[1].isdigit():
        raise A2AProtocolVersionError
    return f"{int(parts[0])}.{int(parts[1])}"


def _uri_tuple(values: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    result = freeze_string_tuple(values, field_name)
    for value in result:
        _require_absolute_uri(value)
    return result


def _require_absolute_uri(value: str) -> None:
    if (
        not value
        or value != value.strip(" \t")
        or any(char.isspace() for char in value)
    ):
        raise ValueError("extension URI is invalid")
    parsed = urlsplit(value)
    if not parsed.scheme or len(parsed.scheme) == 1:
        raise ValueError("extension URI must be absolute")


__all__ = [
    "A2A_EXTENSIONS_HEADER",
    "A2A_JSON_RPC_MEDIA_TYPE",
    "A2A_LEGACY_MISSING_VERSION",
    "A2A_PUSH_MEDIA_TYPE",
    "A2A_SNAPSHOT_RESUME_EXTENSION",
    "A2A_SUPPORTED_VERSION",
    "A2A_SSE_MEDIA_TYPE",
    "A2A_VERSION_HEADER",
    "A2AExtensionNegotiation",
    "A2AExtensionNegotiationError",
    "A2AExtensionPolicy",
    "A2AProtocolVersionError",
    "A2AProtocolVersionPolicy",
    "parse_extensions_header",
]
