from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol


class ChannelAuthError(PermissionError):
    """Channel request failed authorization."""


@dataclass(frozen=True, slots=True)
class ChannelAuthContext:
    """Resource context for web channel authorization decisions."""

    operation: str
    method: str
    path: str
    session_id: str | None = None
    resource_type: str | None = None
    resource_id: str | None = None


class ChannelAuthPolicy(Protocol):
    """Authorization boundary for web channel requests."""

    def authorize(self, headers: Mapping[str, str]) -> None:
        """Raise ChannelAuthError when the headers are not authorized."""


class ResourceAwareChannelAuthPolicy(Protocol):
    """Authorization boundary for web channel requests with resource context."""

    def authorize_channel(
        self,
        headers: Mapping[str, str],
        *,
        context: ChannelAuthContext,
    ) -> None:
        """Raise ChannelAuthError when the request context is not authorized."""


class AllowAllChannelAuthPolicy:
    """Explicit local/dev policy that permits every channel request."""

    def authorize(self, headers: Mapping[str, str]) -> None:
        """Allow every request."""

    def authorize_channel(
        self,
        headers: Mapping[str, str],
        *,
        context: ChannelAuthContext,
    ) -> None:
        """Allow every request with resource context."""


class RejectAllChannelAuthPolicy:
    """Fail-closed channel policy for production-facing defaults."""

    def __init__(self, reason: str = "channel auth policy required") -> None:
        self.reason = reason

    def authorize(self, headers: Mapping[str, str]) -> None:
        """Reject every request until the host explicitly configures auth."""

        raise ChannelAuthError(self.reason)

    def authorize_channel(
        self,
        headers: Mapping[str, str],
        *,
        context: ChannelAuthContext,
    ) -> None:
        """Reject every contextual request until the host configures auth."""

        raise ChannelAuthError(self.reason)
