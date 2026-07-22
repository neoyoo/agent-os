from __future__ import annotations

import asyncio
import sys
from types import ModuleType

import pytest

from agentos.cli.application import (
    CliFactoryConfigurationError,
    _wait_for_shutdown_signal,
    load_cli_host_factory,
)
import agentos.cli.application as application
from agentos.cli.auth import (
    CliResource,
    resolve_cli_scope,
)
from agentos.distributed.models import RequestScope
from tests.planning._async import async_test


def test_internal_lifecycle_signal_is_not_a_public_application_api() -> None:
    assert "CliInterruptedError" not in application.__all__


class _Resolver:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[tuple[str, CliResource, str]] = []

    async def resolve(
        self,
        operation: str,
        resource: CliResource,
        tenant_hint: str,
    ) -> object:
        self.calls.append((operation, resource, tenant_hint))
        return self.result


@pytest.mark.parametrize(
    ("operation", "resource"),
    [
        ("run_submit", CliResource(session_id="session_1")),
        (
            "run_command",
            CliResource(session_id="session_1", run_id="run_1"),
        ),
        (
            "side_effect_resolve",
            CliResource(session_id="session_1", run_id="run_1"),
        ),
        ("run_query", CliResource(session_id="session_1", run_id="run_1")),
        ("run_watch", CliResource(session_id="session_1", run_id="run_1")),
        ("artifact_upload", CliResource(session_id="session_1")),
        ("artifact_list", CliResource(session_id="session_1")),
        (
            "artifact_read",
            CliResource(session_id="session_1", artifact_id="art_1"),
        ),
        (
            "artifact_delete",
            CliResource(session_id="session_1", artifact_id="art_1"),
        ),
    ],
)
@async_test
async def test_scope_resolution_authorizes_exact_operation_resource_shape(
    operation: str,
    resource: CliResource,
) -> None:
    scope = RequestScope("tenant_authoritative", "cli_service")
    resolver = _Resolver(scope)

    resolved = await resolve_cli_scope(
        resolver,  # type: ignore[arg-type]
        operation,  # type: ignore[arg-type]
        resource,
        "tenant_route_hint",
    )

    assert resolved is scope
    assert resolver.calls == [(operation, resource, "tenant_route_hint")]


@pytest.mark.parametrize(
    ("operation", "resource"),
    [
        (
            "run_submit",
            CliResource(session_id="session_1", run_id="run_1"),
        ),
        ("run_command", CliResource(session_id="session_1")),
        (
            "artifact_upload",
            CliResource(session_id="session_1", artifact_id="art_1"),
        ),
        ("artifact_read", CliResource(session_id="session_1")),
    ],
)
@async_test
async def test_invalid_resource_shape_is_rejected_before_authorization(
    operation: str,
    resource: CliResource,
) -> None:
    resolver = _Resolver(RequestScope("tenant_1", "principal_1"))

    with pytest.raises(ValueError, match="resource"):
        await resolve_cli_scope(
            resolver,  # type: ignore[arg-type]
            operation,  # type: ignore[arg-type]
            resource,
            "tenant_1",
        )

    assert resolver.calls == []


@async_test
async def test_scope_resolver_must_return_deployment_owned_request_scope() -> None:
    resolver = _Resolver(object())

    with pytest.raises(RuntimeError, match="RequestScope"):
        await resolve_cli_scope(
            resolver,  # type: ignore[arg-type]
            "run_submit",
            CliResource(session_id="session_1"),
            "tenant_1",
        )


class _HostFactory:
    def open_service_host(self):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    def open_server_host(self):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    def open_worker_host(self):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    def open_relay_host(self):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    def open_migration_host(self):  # type: ignore[no-untyped-def]
        raise NotImplementedError


def test_explicit_factory_has_priority_over_environment(monkeypatch) -> None:
    explicit = ModuleType("explicit_cli_factory")
    expected = _HostFactory()
    explicit.create = lambda: expected  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, explicit.__name__, explicit)

    loaded = load_cli_host_factory(
        "explicit_cli_factory:create",
        {"AGENTOS_CLI_FACTORY": "ignored:create"},
    )

    assert loaded is expected


@pytest.mark.parametrize(
    "factory_spec",
    [None, "", "module", "module:", ":factory", "module:factory:extra"],
)
def test_invalid_or_missing_factory_configuration_is_stable(
    factory_spec: str | None,
) -> None:
    with pytest.raises(CliFactoryConfigurationError) as raised:
        load_cli_host_factory(factory_spec, {})

    assert raised.value.code == "invalid_cli_factory"
    assert str(raised.value) == "CLI application factory is invalid"


def test_factory_must_be_zero_argument_and_return_complete_host_factory(
    monkeypatch,
) -> None:
    module = ModuleType("invalid_cli_factory")
    module.with_argument = lambda value: value  # type: ignore[attr-defined]
    module.incomplete = lambda: object()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, module.__name__, module)

    for spec in (
        "invalid_cli_factory:with_argument",
        "invalid_cli_factory:incomplete",
    ):
        with pytest.raises(CliFactoryConfigurationError):
            load_cli_host_factory(spec, {})


def test_shutdown_signal_waiter_restores_handlers_after_signal(
    monkeypatch,
) -> None:
    async def scenario() -> None:
        previous = {
            application.signal.SIGINT: object(),
            application.signal.SIGTERM: object(),
        }
        installed: dict[object, object] = {}
        calls: list[tuple[object, object]] = []

        def getsignal(signum: object) -> object:
            return previous[signum]

        def set_signal(signum: object, handler: object) -> None:
            calls.append((signum, handler))
            installed[signum] = handler

        monkeypatch.setattr(application.signal, "getsignal", getsignal)
        monkeypatch.setattr(application.signal, "signal", set_signal)

        waiter = asyncio.create_task(_wait_for_shutdown_signal())
        while len(calls) < 2:
            await asyncio.sleep(0)
        installed[application.signal.SIGTERM](0, None)  # type: ignore[operator]
        await waiter

        assert calls[-2:] == [
            (application.signal.SIGINT, previous[application.signal.SIGINT]),
            (application.signal.SIGTERM, previous[application.signal.SIGTERM]),
        ]

    asyncio.run(scenario())


def test_shutdown_signal_waiter_restores_handlers_when_cancelled(
    monkeypatch,
) -> None:
    async def scenario() -> None:
        previous = {
            application.signal.SIGINT: object(),
            application.signal.SIGTERM: object(),
        }
        calls: list[tuple[object, object]] = []

        monkeypatch.setattr(
            application.signal,
            "getsignal",
            lambda signum: previous[signum],
        )
        monkeypatch.setattr(
            application.signal,
            "signal",
            lambda signum, handler: calls.append((signum, handler)),
        )

        waiter = asyncio.create_task(_wait_for_shutdown_signal())
        while len(calls) < 2:
            await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter

        assert calls[-2:] == [
            (application.signal.SIGINT, previous[application.signal.SIGINT]),
            (application.signal.SIGTERM, previous[application.signal.SIGTERM]),
        ]

    asyncio.run(scenario())
