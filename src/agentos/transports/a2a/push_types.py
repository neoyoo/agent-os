from __future__ import annotations

from dataclasses import dataclass

from agentos.transports.a2a._validation import (
    require_identifier,
    require_text,
    require_url_syntax,
)


@dataclass(frozen=True, repr=False, slots=True)
class A2AAuthenticationInfo:
    scheme: str
    credentials: str | None = None

    def __post_init__(self) -> None:
        require_text(self.scheme, "authentication scheme", allow_empty=False)
        if self.credentials is not None:
            require_text(
                self.credentials, "authentication credentials", allow_empty=False
            )

    def __repr__(self) -> str:
        return f"{type(self).__name__}(scheme={self.scheme!r}, credentials=<redacted>)"


@dataclass(frozen=True, repr=False, slots=True)
class A2ATaskPushNotificationConfig:
    url: str
    tenant: str | None = None
    id: str | None = None
    task_id: str | None = None
    token: str | None = None
    authentication: A2AAuthenticationInfo | None = None

    def __post_init__(self) -> None:
        require_url_syntax(self.url, "push url")
        for value, name in (
            (self.tenant, "tenant"),
            (self.id, "push config id"),
            (self.task_id, "task_id"),
        ):
            if value is not None:
                require_identifier(value, name)
        if self.token is not None:
            require_text(self.token, "push token", allow_empty=False)
        if (
            self.authentication is not None
            and type(self.authentication) is not A2AAuthenticationInfo
        ):
            raise TypeError("authentication must be A2AAuthenticationInfo or None")

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(url={self.url!r}, tenant={self.tenant!r}, "
            f"id={self.id!r}, task_id={self.task_id!r}, token=<redacted>, "
            "authentication=<redacted>)"
        )


__all__ = ["A2AAuthenticationInfo", "A2ATaskPushNotificationConfig"]
