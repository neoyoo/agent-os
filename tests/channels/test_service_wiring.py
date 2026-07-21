from __future__ import annotations

import pytest

from agentos.channels.auth import ChannelAuthContext
from agentos.channels.service_wiring import (
    A2AAgentCardProvider,
    AuthenticationRequiredError,
    ChannelServices,
    FixedScopeAuthenticator,
    RejectAllChannelAuthenticator,
)
from agentos.distributed.a2a_services import A2APushService, A2ATaskService
from agentos.distributed.models import RequestScope
from agentos.distributed.services import (
    ArtifactService,
    RunCommandService,
    RunEventStream,
    RunQueryService,
    RunSubmissionService,
)
from agentos.transports.a2a import A2AAgentCard
from agentos.transports.http.request_types import HttpHeaders
from tests.planning._async import async_test


SCOPE = RequestScope("tenant_1", "principal_1")
CONTEXT = ChannelAuthContext(
    operation="submit_run",
    method="POST",
    path="/v1/sessions/session_1/runs",
    session_id="session_1",
    resource_type="session",
    resource_id="session_1",
)


class CardProvider:
    async def get_public_card(self, *, scope: RequestScope) -> A2AAgentCard:
        raise AssertionError(scope)

    async def get_extended_card(
        self,
        *,
        scope: RequestScope,
    ) -> A2AAgentCard | None:
        raise AssertionError(scope)


@async_test
async def test_default_authenticator_fails_closed_without_leaking_reason() -> None:
    authenticator = RejectAllChannelAuthenticator()

    with pytest.raises(
        AuthenticationRequiredError,
        match="^authentication required$",
    ):
        await authenticator.authenticate(HttpHeaders(()), context=CONTEXT)


@async_test
async def test_fixed_scope_authenticator_is_explicit_and_ignores_tenant_header() -> None:
    authenticator = FixedScopeAuthenticator(SCOPE)
    headers = HttpHeaders((("X-Tenant-ID", "attacker-tenant"),))

    assert await authenticator.authenticate(headers, context=CONTEXT) == SCOPE


def test_channel_services_accept_only_application_boundaries() -> None:
    run_port = object()
    query_port = object()
    replay_port = object()
    artifact_port = object()
    task_port = object()
    push_port = object()
    services = ChannelServices(
        run_submissions=RunSubmissionService(run_port),  # type: ignore[arg-type]
        run_commands=RunCommandService(run_port),  # type: ignore[arg-type]
        run_queries=RunQueryService(query_port),  # type: ignore[arg-type]
        run_events=RunEventStream(query_port, replay_port),  # type: ignore[arg-type]
        artifacts=ArtifactService(artifact_port),  # type: ignore[arg-type]
        a2a_tasks=A2ATaskService(task_port),  # type: ignore[arg-type]
        a2a_push=A2APushService(  # type: ignore[arg-type]
            push_port,
            object(),
            object(),
        ),
    )

    assert services.run_submissions.port is run_port
    assert services.a2a_cards is None
    with pytest.raises(TypeError, match="run_submissions"):
        ChannelServices(
            run_submissions=object(),  # type: ignore[arg-type]
            run_commands=services.run_commands,
            run_queries=services.run_queries,
            run_events=services.run_events,
            artifacts=services.artifacts,
        )


def test_channel_services_accepts_structural_a2a_card_provider() -> None:
    provider = CardProvider()
    services = ChannelServices(
        run_submissions=RunSubmissionService(object()),  # type: ignore[arg-type]
        run_commands=RunCommandService(object()),  # type: ignore[arg-type]
        run_queries=RunQueryService(object()),  # type: ignore[arg-type]
        run_events=RunEventStream(object(), object()),  # type: ignore[arg-type]
        artifacts=ArtifactService(object()),  # type: ignore[arg-type]
        a2a_cards=provider,
    )

    assert isinstance(provider, A2AAgentCardProvider)
    assert services.a2a_cards is provider


def test_channel_services_rejects_invalid_a2a_card_provider() -> None:
    with pytest.raises(TypeError, match="a2a_cards"):
        ChannelServices(
            run_submissions=RunSubmissionService(object()),  # type: ignore[arg-type]
            run_commands=RunCommandService(object()),  # type: ignore[arg-type]
            run_queries=RunQueryService(object()),  # type: ignore[arg-type]
            run_events=RunEventStream(object(), object()),  # type: ignore[arg-type]
            artifacts=ArtifactService(object()),  # type: ignore[arg-type]
            a2a_cards=object(),  # type: ignore[arg-type]
        )
