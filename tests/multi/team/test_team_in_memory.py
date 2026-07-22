from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from agentos.distributed.models import RequestScope
from agentos.multi.team_delivery_types import TeamMessageReceipt
from agentos.multi.team_identity import (
    team_delivery_id,
    team_delivery_source_digest,
    team_message_id,
    team_message_request_digest,
)
from agentos.multi.team_in_memory import InMemoryTeamStore
from agentos.multi.team_errors import (
    TeamConflictError,
    TeamCursorError,
    TeamMembershipError,
    TeamNotFoundError,
)
from agentos.multi.team_types import (
    TeamAccessContext,
    TeamAddressingKind,
    TeamMemberRecord,
    TeamMemberRole,
    TeamMessagePage,
    TeamMessageRequest,
    TeamRecord,
)
from tests.planning._async import async_test


NOW = datetime(2026, 7, 22, 8, 30, tzinfo=UTC)


def _scope(tenant_id: str = "tenant_1") -> RequestScope:
    return RequestScope(tenant_id=tenant_id, principal_id="principal_1")


def _access(
    *,
    team_id: str = "team_1",
    agent_id: str = "agent_1",
    session_id: str = "session_1",
) -> TeamAccessContext:
    return TeamAccessContext(
        tenant_id="tenant_1",
        team_id=team_id,
        recipient_agent_id=agent_id,
        target_session_id=session_id,
    )


def _team(
    team_id: str = "team_1",
    leader_agent_id: str = "agent_1",
) -> TeamRecord:
    return TeamRecord(
        team_id=team_id,
        leader_agent_id=leader_agent_id,
        workspace=None,
        created_at=NOW,
    )


def _member(
    agent_id: str,
    session_id: str,
    *,
    team_id: str = "team_1",
    role: TeamMemberRole = "worker",
) -> TeamMemberRecord:
    return TeamMemberRecord(
        team_id=team_id,
        recipient_agent_id=agent_id,
        role=role,
        target_session_id=session_id,
        created_at=NOW,
    )


def _request(
    *,
    team_id: str = "team_1",
    sender: str = "agent_1",
    operation_id: str = "operation_1",
    addressing_kind: TeamAddressingKind = "direct",
    addressed_agent_id: str | None = "agent_2",
    content: str = "inspect drawing",
    created_at: datetime = NOW,
) -> TeamMessageRequest:
    return TeamMessageRequest(
        team_id=team_id,
        sender_agent_id=sender,
        operation_id=operation_id,
        message_kind="instruction",
        content=content,
        correlation_id="wait_1",
        addressing_kind=addressing_kind,
        addressed_agent_id=addressed_agent_id,
        created_at=created_at,
    )


async def _create_team(
    store: InMemoryTeamStore,
    *,
    scope: RequestScope | None = None,
    team_id: str = "team_1",
    leader_agent_id: str = "agent_1",
    leader_session_id: str = "session_1",
) -> None:
    await store.create_team(
        scope=scope or _scope(),
        team=_team(team_id, leader_agent_id),
        leader=_member(
            leader_agent_id,
            leader_session_id,
            team_id=team_id,
            role="leader",
        ),
    )


async def _member_access(
    store: InMemoryTeamStore,
    *,
    scope: RequestScope,
    team_id: str,
    recipient_agent_id: str,
) -> TeamAccessContext:
    member = await store.get_member(
        scope=scope,
        team_id=team_id,
        recipient_agent_id=recipient_agent_id,
    )
    assert member is not None
    return TeamAccessContext(
        tenant_id=scope.tenant_id,
        team_id=team_id,
        recipient_agent_id=recipient_agent_id,
        target_session_id=member.target_session_id,
    )


async def _add_member(
    store: InMemoryTeamStore,
    *,
    scope: RequestScope,
    actor_agent_id: str,
    member: TeamMemberRecord,
) -> TeamMemberRecord:
    access = await _member_access(
        store,
        scope=scope,
        team_id=member.team_id,
        recipient_agent_id=actor_agent_id,
    )
    return await store.add_member(scope=scope, access=access, member=member)


async def _remove_member(
    store: InMemoryTeamStore,
    *,
    scope: RequestScope,
    team_id: str,
    actor_agent_id: str,
    recipient_agent_id: str,
    deleted_at: datetime,
) -> TeamMemberRecord:
    access = await _member_access(
        store,
        scope=scope,
        team_id=team_id,
        recipient_agent_id=actor_agent_id,
    )
    return await store.remove_member(
        scope=scope,
        access=access,
        recipient_agent_id=recipient_agent_id,
        deleted_at=deleted_at,
    )


async def _send_message(
    store: InMemoryTeamStore,
    *,
    scope: RequestScope,
    request: TeamMessageRequest,
) -> TeamMessageReceipt:
    access = await _member_access(
        store,
        scope=scope,
        team_id=request.team_id,
        recipient_agent_id=request.sender_agent_id,
    )
    return await store.send_message(scope=scope, access=access, request=request)


async def _list_messages(
    store: InMemoryTeamStore,
    *,
    scope: RequestScope,
    team_id: str,
    recipient_agent_id: str,
    after_message_id: str | None = None,
    limit: int,
) -> TeamMessagePage:
    access = await _member_access(
        store,
        scope=scope,
        team_id=team_id,
        recipient_agent_id=recipient_agent_id,
    )
    return await store.list_messages(
        scope=scope,
        access=access,
        after_message_id=after_message_id,
        limit=limit,
    )


async def _delete_team(
    store: InMemoryTeamStore,
    *,
    scope: RequestScope,
    team_id: str,
    actor_agent_id: str,
    deleted_at: datetime,
) -> TeamRecord:
    access = await _member_access(
        store,
        scope=scope,
        team_id=team_id,
        recipient_agent_id=actor_agent_id,
    )
    return await store.delete_team(scope=scope, access=access, deleted_at=deleted_at)


@async_test
async def test_create_team_is_atomic_tenant_scoped_and_session_unique() -> None:
    store = InMemoryTeamStore()

    with pytest.raises(TeamConflictError):
        await store.create_team(
            scope=_scope(),
            team=_team(),
            leader=_member("different_leader", "session_1", role="leader"),
        )
    assert await store.get_team(scope=_scope(), team_id="team_1") is None

    await _create_team(store)
    await _create_team(store, scope=_scope("tenant_2"))

    assert await store.get_team(scope=_scope(), team_id="team_1") == _team()
    assert await store.get_team(scope=_scope("tenant_2"), team_id="team_1") == _team()

    with pytest.raises(TeamConflictError):
        await _create_team(
            store,
            team_id="team_2",
            leader_agent_id="agent_2",
            leader_session_id="session_1",
        )
    assert await store.get_team(scope=_scope(), team_id="team_2") is None


@async_test
async def test_member_operation_rejects_same_agent_from_another_session() -> None:
    store = InMemoryTeamStore()
    await _create_team(store)
    await _create_team(
        store,
        team_id="team_2",
        leader_agent_id="agent_2",
        leader_session_id="session_2",
    )
    await _add_member(store,
        scope=_scope(),
        actor_agent_id="agent_2",
        member=_member(
            "agent_1",
            "session_3",
            team_id="team_2",
        ),
    )

    with pytest.raises(TeamMembershipError):
        await store.send_message(
            scope=_scope(),
            access=_access(team_id="team_2"),
            request=_request(
                team_id="team_2",
                sender="agent_1",
                addressed_agent_id="agent_2",
            ),
        )


@async_test
async def test_add_member_requires_leader_and_unique_active_binding() -> None:
    store = InMemoryTeamStore()
    await _create_team(store)
    worker = _member("agent_2", "session_2")
    added = await _add_member(store,
        scope=_scope(),
        actor_agent_id="agent_1",
        member=worker,
    )

    with pytest.raises(TeamMembershipError):
        await _add_member(store,
            scope=_scope(),
            actor_agent_id="agent_2",
            member=_member("agent_3", "session_3"),
        )
    with pytest.raises(TeamConflictError):
        await _add_member(store,
            scope=_scope(),
            actor_agent_id="agent_1",
            member=_member("agent_3", "session_2"),
        )
    with pytest.raises(TeamConflictError):
        await _add_member(store,
            scope=_scope(),
            actor_agent_id="agent_1",
            member=_member("agent_2", "session_3"),
        )
    with pytest.raises(TeamConflictError):
        await _add_member(store,
            scope=_scope(),
            actor_agent_id="agent_1",
            member=_member("agent_3", "session_3", role="leader"),
        )

    assert added == worker
    assert await store.list_members(scope=_scope(), team_id="team_1") == (
        _member("agent_1", "session_1", role="leader"),
        worker,
    )


@async_test
async def test_delete_team_requires_leader_and_releases_active_bindings() -> None:
    store = InMemoryTeamStore()
    await _create_team(store)
    await _add_member(store,
        scope=_scope(),
        actor_agent_id="agent_1",
        member=_member("agent_2", "session_2"),
    )

    with pytest.raises(TeamMembershipError):
        await _delete_team(store,
            scope=_scope(),
            team_id="team_1",
            actor_agent_id="agent_2",
            deleted_at=NOW + timedelta(minutes=1),
        )

    deleted_at = NOW + timedelta(minutes=2)
    deleted = await _delete_team(store,
        scope=_scope(),
        team_id="team_1",
        actor_agent_id="agent_1",
        deleted_at=deleted_at,
    )

    assert deleted.status == "deleted"
    assert deleted.deleted_at == deleted_at
    assert {
        (member.status, member.deleted_at)
        for member in await store.list_members(scope=_scope(), team_id="team_1")
    } == {("deleted", deleted_at)}

    await _create_team(
        store,
        team_id="team_2",
        leader_agent_id="agent_3",
        leader_session_id="session_2",
    )
    with pytest.raises(TeamNotFoundError):
        await _add_member(store,
            scope=_scope(),
            actor_agent_id="agent_1",
            member=_member("agent_4", "session_4"),
        )


@async_test
async def test_remove_member_soft_deletes_only_worker_binding() -> None:
    store = InMemoryTeamStore()
    await _create_team(store)
    await _add_member(store,
        scope=_scope(),
        actor_agent_id="agent_1",
        member=_member("agent_2", "session_2"),
    )

    with pytest.raises(TeamMembershipError):
        await _remove_member(store,
            scope=_scope(),
            team_id="team_1",
            actor_agent_id="agent_1",
            recipient_agent_id="agent_1",
            deleted_at=NOW,
        )
    removed = await _remove_member(store,
        scope=_scope(),
        team_id="team_1",
        actor_agent_id="agent_1",
        recipient_agent_id="agent_2",
        deleted_at=NOW,
    )

    assert removed.status == "deleted"
    assert removed.deleted_at == NOW
    await _create_team(
        store,
        team_id="team_2",
        leader_agent_id="agent_3",
        leader_session_id="session_2",
    )


@async_test
async def test_delete_team_preserves_prior_member_deletion_time() -> None:
    store = InMemoryTeamStore()
    await _create_team(store)
    await _add_member(store,
        scope=_scope(),
        actor_agent_id="agent_1",
        member=_member("agent_2", "session_2"),
    )
    removed_at = NOW + timedelta(minutes=1)
    await _remove_member(store,
        scope=_scope(),
        team_id="team_1",
        actor_agent_id="agent_1",
        recipient_agent_id="agent_2",
        deleted_at=removed_at,
    )

    await _delete_team(store,
        scope=_scope(),
        team_id="team_1",
        actor_agent_id="agent_1",
        deleted_at=NOW + timedelta(minutes=2),
    )

    members = await store.list_members(scope=_scope(), team_id="team_1")
    worker = next(member for member in members if member.recipient_agent_id == "agent_2")
    assert worker.deleted_at == removed_at


@async_test
async def test_send_message_enforces_fanout_visibility_and_cursor() -> None:
    store = InMemoryTeamStore()
    await _create_team(store)
    for agent_id, session_id in (
        ("agent_2", "session_2"),
        ("agent_3", "session_3"),
    ):
        await _add_member(store,
            scope=_scope(),
            actor_agent_id="agent_1",
            member=_member(agent_id, session_id),
        )

    direct = await _send_message(store, scope=_scope(), request=_request())
    broadcast = await _send_message(store,
        scope=_scope(),
        request=_request(
            sender="agent_2",
            operation_id="operation_2",
            addressing_kind="broadcast",
            addressed_agent_id=None,
            created_at=NOW + timedelta(seconds=1),
        ),
    )

    assert tuple(
        recipient.recipient_agent_id
        for recipient in broadcast.message.recipient_snapshot
    ) == ("agent_1", "agent_3")
    assert (await _list_messages(store,
        scope=_scope(),
        team_id="team_1",
        recipient_agent_id="agent_1",
        limit=10,
    )).messages == (broadcast.message,)
    assert (await _list_messages(store,
        scope=_scope(),
        team_id="team_1",
        recipient_agent_id="agent_2",
        after_message_id=direct.message.message_id,
        limit=10,
    )).messages == ()
    assert (await _list_messages(store,
        scope=_scope(),
        team_id="team_1",
        recipient_agent_id="agent_3",
        limit=10,
    )).messages == (broadcast.message,)

    with pytest.raises(TeamCursorError):
        await _list_messages(store,
            scope=_scope(),
            team_id="team_1",
            recipient_agent_id="agent_3",
            after_message_id=direct.message.message_id,
            limit=10,
        )


@async_test
async def test_send_message_rejects_empty_broadcast() -> None:
    store = InMemoryTeamStore()
    await _create_team(store)

    with pytest.raises(TeamMembershipError):
        await _send_message(store,
            scope=_scope(),
            request=_request(
                addressing_kind="broadcast",
                addressed_agent_id=None,
            ),
        )


@async_test
async def test_message_history_uses_canonical_timestamp_and_identity_order() -> None:
    store = InMemoryTeamStore()
    await _create_team(store)
    await _add_member(store,
        scope=_scope(),
        actor_agent_id="agent_1",
        member=_member("agent_2", "session_2"),
    )

    later = await _send_message(store,
        scope=_scope(),
        request=_request(
            operation_id="operation_later",
            created_at=NOW + timedelta(seconds=2),
        ),
    )
    earlier = await _send_message(store,
        scope=_scope(),
        request=_request(
            operation_id="operation_earlier",
            created_at=NOW + timedelta(seconds=1),
        ),
    )

    messages = await _list_messages(store,
        scope=_scope(),
        team_id="team_1",
        recipient_agent_id="agent_2",
        limit=10,
    )
    assert messages.messages == (earlier.message, later.message)


@async_test
async def test_message_history_applies_keyset_limit_before_materializing_page() -> None:
    store = InMemoryTeamStore()
    await _create_team(store)
    await _add_member(store,
        scope=_scope(),
        actor_agent_id="agent_1",
        member=_member("agent_2", "session_2"),
    )
    for index in range(12):
        await _send_message(store,
            scope=_scope(),
            request=_request(
                operation_id=f"operation_{index}",
                created_at=NOW + timedelta(seconds=index),
            ),
        )

    first = await _list_messages(store,
        scope=_scope(),
        team_id="team_1",
        recipient_agent_id="agent_2",
        limit=10,
    )
    second = await _list_messages(store,
        scope=_scope(),
        team_id="team_1",
        recipient_agent_id="agent_2",
        after_message_id=first.next_cursor,
        limit=10,
    )

    assert len(first.messages) == 10
    assert first.next_cursor == first.messages[-1].message_id
    assert len(second.messages) == 2
    assert second.next_cursor is None


@async_test
async def test_message_history_stops_after_page_lookahead() -> None:
    class IterationGuard(list[object]):
        def __iter__(self):  # type: ignore[no-untyped-def]
            for index, item in enumerate(super().__iter__()):
                if index > 10:
                    raise AssertionError("message history was fully materialized")
                yield item

    store = InMemoryTeamStore()
    await _create_team(store)
    await _add_member(store,
        scope=_scope(),
        actor_agent_id="agent_1",
        member=_member("agent_2", "session_2"),
    )
    for index in range(20):
        await _send_message(store,
            scope=_scope(),
            request=_request(
                operation_id=f"bounded_operation_{index}",
                created_at=NOW + timedelta(seconds=index),
            ),
        )

    key = ("tenant_1", "team_1")
    store._messages[key] = IterationGuard(store._messages[key])  # type: ignore[assignment]

    page = await _list_messages(store,
        scope=_scope(),
        team_id="team_1",
        recipient_agent_id="agent_2",
        limit=10,
    )

    assert len(page.messages) == 10
    assert page.next_cursor == page.messages[-1].message_id


@async_test
async def test_duplicate_message_reuses_first_snapshot_and_delivery_identity() -> None:
    store = InMemoryTeamStore()
    await _create_team(store)
    await _add_member(store,
        scope=_scope(),
        actor_agent_id="agent_1",
        member=_member("agent_2", "session_2"),
    )
    request = _request(addressing_kind="broadcast", addressed_agent_id=None)

    first = await _send_message(store, scope=_scope(), request=request)
    await _add_member(store,
        scope=_scope(),
        actor_agent_id="agent_1",
        member=_member("agent_3", "session_3"),
    )
    duplicate = await _send_message(store,
        scope=_scope(),
        request=replace(request, created_at=NOW + timedelta(minutes=5)),
    )

    expected_message_id = team_message_id(
        scope=_scope(),
        team_id="team_1",
        sender_agent_id="agent_1",
        operation_id="operation_1",
    )
    expected_request_digest = team_message_request_digest(
        scope=_scope(),
        team_id="team_1",
        message_id=expected_message_id,
        sender_agent_id="agent_1",
        message_kind="instruction",
        content="inspect drawing",
        correlation_id="wait_1",
        addressing_kind="broadcast",
        addressed_agent_id=None,
    )
    expected_delivery_id = team_delivery_id(
        scope=_scope(),
        team_id="team_1",
        message_id=expected_message_id,
        recipient_agent_id="agent_2",
        target_session_id="session_2",
    )
    expected_source_digest = team_delivery_source_digest(
        scope=_scope(),
        team_id="team_1",
        message_id=expected_message_id,
        sender_agent_id="agent_1",
        message_kind="instruction",
        content="inspect drawing",
        correlation_id="wait_1",
        addressing_kind="broadcast",
        addressed_agent_id=None,
        recipient_agent_id="agent_2",
        target_session_id="session_2",
    )

    assert first.duplicate is False
    assert duplicate.duplicate is True
    assert duplicate.message == first.message
    assert duplicate.deliveries == first.deliveries
    assert first.message.message_id == expected_message_id
    assert first.message.request_sha256 == expected_request_digest
    assert first.deliveries[0].delivery_id == expected_delivery_id
    assert first.deliveries[0].source_sha256 == expected_source_digest
    assert (await _list_messages(store,
        scope=_scope(),
        team_id="team_1",
        recipient_agent_id="agent_3",
        limit=10,
    )).messages == ()


@async_test
async def test_same_operation_with_changed_semantics_conflicts() -> None:
    store = InMemoryTeamStore()
    await _create_team(store)
    await _add_member(store,
        scope=_scope(),
        actor_agent_id="agent_1",
        member=_member("agent_2", "session_2"),
    )
    request = _request(addressing_kind="broadcast", addressed_agent_id=None)
    await _send_message(store, scope=_scope(), request=request)

    for changed in (
        replace(request, content="different content"),
        replace(
            request,
            addressing_kind="direct",
            addressed_agent_id="agent_2",
        ),
    ):
        with pytest.raises(TeamConflictError):
            await _send_message(store, scope=_scope(), request=changed)


@async_test
async def test_same_operation_id_is_independent_between_teams() -> None:
    store = InMemoryTeamStore()
    await _create_team(store)
    await _add_member(store,
        scope=_scope(),
        actor_agent_id="agent_1",
        member=_member("agent_2", "session_2"),
    )
    await _create_team(
        store,
        team_id="team_2",
        leader_agent_id="agent_3",
        leader_session_id="session_3",
    )
    await _add_member(store,
        scope=_scope(),
        actor_agent_id="agent_3",
        member=_member("agent_4", "session_4", team_id="team_2"),
    )

    first = await _send_message(store, scope=_scope(), request=_request())
    second = await _send_message(store,
        scope=_scope(),
        request=_request(
            team_id="team_2",
            sender="agent_3",
            addressed_agent_id="agent_4",
        ),
    )

    assert first.duplicate is False
    assert second.duplicate is False
    assert first.message.message_id != second.message.message_id


@async_test
async def test_concurrent_duplicate_message_converges_to_one_write() -> None:
    store = InMemoryTeamStore()
    await _create_team(store)
    await _add_member(store,
        scope=_scope(),
        actor_agent_id="agent_1",
        member=_member("agent_2", "session_2"),
    )
    request = _request()

    receipts = await asyncio.gather(
        *(_send_message(store, scope=_scope(), request=request) for _ in range(16)),
    )

    assert sum(not receipt.duplicate for receipt in receipts) == 1
    assert len({receipt.message for receipt in receipts}) == 1
    messages = await _list_messages(store,
        scope=_scope(),
        team_id="team_1",
        recipient_agent_id="agent_2",
        limit=10,
    )
    assert messages.messages == (receipts[0].message,)
