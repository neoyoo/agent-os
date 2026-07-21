from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from agentos.channels.auth import ChannelAuthContext
from agentos.distributed.a2a_services import (
    A2APushService,
    A2ATaskCatalogService,
    A2ATaskService,
)
from agentos.distributed.models import RequestScope
from agentos.distributed.services import (
    ArtifactService,
    RunCommandService,
    RunEventStream,
    RunQueryService,
    RunSubmissionService,
)
from agentos.transports.a2a.card_types import A2AAgentCard
from agentos.transports.http.errors import AuthenticationRequiredError
from agentos.transports.http.request_types import HttpHeaders


@runtime_checkable
class A2AAgentCardProvider(Protocol):
    """为当前请求范围提供不可变的 public/extended AgentCard。"""

    async def get_public_card(self, *, scope: RequestScope) -> A2AAgentCard: ...

    async def get_extended_card(
        self,
        *,
        scope: RequestScope,
    ) -> A2AAgentCard | None: ...


class ChannelAuthenticator(Protocol):
    """Only boundary allowed to create a RequestScope from ingress auth."""

    async def authenticate(
        self,
        headers: HttpHeaders,
        *,
        context: ChannelAuthContext,
    ) -> RequestScope: ...


class RejectAllChannelAuthenticator:
    """Production-facing default that fails closed."""

    async def authenticate(
        self,
        headers: HttpHeaders,
        *,
        context: ChannelAuthContext,
    ) -> RequestScope:
        _validate_auth_input(headers, context)
        raise AuthenticationRequiredError()


@dataclass(frozen=True, slots=True)
class FixedScopeAuthenticator:
    """Explicit local/test authenticator with a preconfigured scope."""

    scope: RequestScope

    def __post_init__(self) -> None:
        if type(self.scope) is not RequestScope:
            raise TypeError("scope must be RequestScope")

    async def authenticate(
        self,
        headers: HttpHeaders,
        *,
        context: ChannelAuthContext,
    ) -> RequestScope:
        _validate_auth_input(headers, context)
        return self.scope


@dataclass(frozen=True, slots=True)
class ChannelServices:
    """Application Service bundle injected into stateless Channels."""

    run_submissions: RunSubmissionService
    run_commands: RunCommandService
    run_queries: RunQueryService
    run_events: RunEventStream
    artifacts: ArtifactService
    a2a_tasks: A2ATaskService | None = None
    a2a_catalog: A2ATaskCatalogService | None = None
    a2a_push: A2APushService | None = None
    a2a_cards: A2AAgentCardProvider | None = None

    def __post_init__(self) -> None:
        required = (
            ("run_submissions", self.run_submissions, RunSubmissionService),
            ("run_commands", self.run_commands, RunCommandService),
            ("run_queries", self.run_queries, RunQueryService),
            ("run_events", self.run_events, RunEventStream),
            ("artifacts", self.artifacts, ArtifactService),
        )
        for name, value, expected in required:
            if type(value) is not expected:
                raise TypeError(f"{name} must be {expected.__name__}")
        optional = (
            ("a2a_tasks", self.a2a_tasks, A2ATaskService),
            ("a2a_catalog", self.a2a_catalog, A2ATaskCatalogService),
            ("a2a_push", self.a2a_push, A2APushService),
        )
        for name, value, expected in optional:
            if value is not None and type(value) is not expected:
                raise TypeError(f"{name} must be {expected.__name__} or None")
        if self.a2a_cards is not None and not isinstance(
            self.a2a_cards,
            A2AAgentCardProvider,
        ):
            raise TypeError("a2a_cards must be A2AAgentCardProvider or None")


def _validate_auth_input(headers: object, context: object) -> None:
    if type(headers) is not HttpHeaders:
        raise TypeError("headers must be HttpHeaders")
    if type(context) is not ChannelAuthContext:
        raise TypeError("context must be ChannelAuthContext")


__all__ = [
    "A2AAgentCardProvider",
    "AuthenticationRequiredError",
    "ChannelAuthenticator",
    "ChannelServices",
    "FixedScopeAuthenticator",
    "RejectAllChannelAuthenticator",
]
