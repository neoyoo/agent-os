from __future__ import annotations

import inspect

from agentos.multi.team_ports import (
    TeamApplicationPort,
    TeamDeliveryBootstrapPort,
    TeamDeliveryPort,
    TeamEventBootstrapPort,
    TeamEventReplayPort,
    TeamWorkspaceAuthorityPort,
)


def test_team_ports_expose_only_async_io_operations() -> None:
    for protocol in (TeamApplicationPort, TeamDeliveryPort, TeamEventReplayPort):
        for name, member in inspect.getmembers(protocol, inspect.isfunction):
            if name in {"__init__", "__subclasshook__", "follow"}:
                continue
            assert inspect.iscoroutinefunction(member), f"{protocol.__name__}.{name}"

    assert inspect.iscoroutinefunction(TeamDeliveryBootstrapPort.resolve_delivery)
    assert inspect.iscoroutinefunction(TeamEventBootstrapPort.resolve_event)
    assert inspect.iscoroutinefunction(
        TeamWorkspaceAuthorityPort.resolve_target_workspace,
    )
    assert inspect.iscoroutinefunction(
        TeamWorkspaceAuthorityPort.resolve_team_workspace,
    )


def test_team_scoped_port_operations_accept_explicit_scope() -> None:
    scoped_methods = (
        TeamApplicationPort.create_team,
        TeamApplicationPort.get_team,
        TeamApplicationPort.get_member,
        TeamApplicationPort.list_members,
        TeamApplicationPort.add_member,
        TeamApplicationPort.remove_member,
        TeamApplicationPort.send_message,
        TeamApplicationPort.list_messages,
        TeamApplicationPort.delete_team,
        TeamDeliveryPort.claim_pending,
        TeamDeliveryPort.heartbeat,
        TeamDeliveryPort.release,
        TeamDeliveryPort.commit_result,
        TeamEventReplayPort.append,
        TeamEventReplayPort.replay,
        TeamEventReplayPort.high_water,
        TeamEventReplayPort.follow,
        TeamWorkspaceAuthorityPort.resolve_target_workspace,
        TeamWorkspaceAuthorityPort.resolve_team_workspace,
    )

    for method in scoped_methods:
        assert "scope" in inspect.signature(method).parameters, method.__qualname__

    assert "limit" in inspect.signature(
        TeamApplicationPort.list_messages,
    ).parameters

    assert "scope" not in inspect.signature(
        TeamDeliveryBootstrapPort.resolve_delivery,
    ).parameters
    assert "scope" not in inspect.signature(
        TeamEventBootstrapPort.resolve_event,
    ).parameters


def test_member_originated_team_operations_require_access_context() -> None:
    for method in (
        TeamApplicationPort.add_member,
        TeamApplicationPort.remove_member,
        TeamApplicationPort.send_message,
        TeamApplicationPort.list_messages,
        TeamApplicationPort.delete_team,
    ):
        assert "access" in inspect.signature(method).parameters, method.__qualname__
