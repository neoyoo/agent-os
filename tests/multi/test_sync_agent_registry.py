import pytest

from agentos.multi.inbox import AgentInbox, AgentInboxMissingError
from agentos.multi.registry import InMemoryRegistry
from agentos.multi.sync_agent_registry import (
    CoordinatorSyncAgentRegistry,
    SyncAgentRegistry,
)
from agentos.multi.types import AgentCard
from agentos.sync import SyncAgent
from tests.multi.helpers import build_agent_with_response


class _AttachFailure(RuntimeError):
    pass


class _CleanupFailure(RuntimeError):
    pass


class _RecordingRegistry(InMemoryRegistry):
    def __init__(
        self,
        events: list[str],
        *,
        fail_unregister: bool = False,
    ) -> None:
        super().__init__()
        self._recorded_events = events
        self._fail_unregister = fail_unregister

    def register(self, card: AgentCard) -> None:
        self._recorded_events.append("register")
        super().register(card)

    def unregister(self, agent_id: str) -> None:
        self._recorded_events.append("unregister")
        super().unregister(agent_id)
        if self._fail_unregister:
            raise _CleanupFailure("unregister cleanup failed")


class _RecordingInbox(AgentInbox):
    def __init__(
        self,
        events: list[str],
        *,
        fail_create: bool = False,
        fail_remove: bool = False,
    ) -> None:
        super().__init__()
        self._recorded_events = events
        self._fail_create = fail_create
        self._fail_remove = fail_remove

    def create_inbox(self, agent_id: str) -> None:
        self._recorded_events.append("create_inbox")
        super().create_inbox(agent_id)
        if self._fail_create:
            raise _AttachFailure("create inbox failed")

    def remove_inbox(self, agent_id: str) -> None:
        self._recorded_events.append("remove_inbox")
        super().remove_inbox(agent_id)
        if self._fail_remove:
            raise _CleanupFailure("inbox cleanup failed")


def _agent_card(agent_id: str) -> AgentCard:
    return AgentCard(
        agent_id=agent_id,
        name=agent_id,
        description="test agent",
        capabilities=(),
    )


def test_registry_detaches_borrowed_sync_agent_without_closing_it() -> None:
    registry = SyncAgentRegistry()
    sync_agent = SyncAgent(build_agent_with_response("parent"))

    registry.attach_borrowed("parent", sync_agent)
    assert registry["parent"] is sync_agent

    assert registry.detach("parent") is sync_agent
    assert sync_agent.closed is False
    sync_agent.close()


def test_registry_closes_owned_sync_agent_when_detached() -> None:
    registry = SyncAgentRegistry()

    sync_agent = registry.attach_owned(
        "child",
        build_agent_with_response("child"),
    )

    assert registry.detach("child") is sync_agent
    assert sync_agent.closed is True


def test_registry_close_only_closes_owned_sync_agents() -> None:
    registry = SyncAgentRegistry()
    borrowed = SyncAgent(build_agent_with_response("parent"))
    owned = registry.attach_owned(
        "child",
        build_agent_with_response("child"),
    )
    registry.attach_borrowed("parent", borrowed)

    registry.close()

    assert owned.closed is True
    assert borrowed.closed is False
    assert list(registry) == []
    borrowed.close()


def test_coordinator_registry_rolls_back_borrowed_card_when_inbox_creation_fails() -> (
    None
):
    events: list[str] = []
    card_registry = _RecordingRegistry(events)
    inbox = _RecordingInbox(events, fail_create=True)
    registry = CoordinatorSyncAgentRegistry(
        registry=card_registry,
        inbox=inbox,
    )
    sync_agent = SyncAgent(build_agent_with_response("parent"))

    try:
        with pytest.raises(_AttachFailure, match="create inbox failed"):
            registry.attach_borrowed_card(_agent_card("parent"), sync_agent)

        assert events == [
            "register",
            "create_inbox",
            "remove_inbox",
            "unregister",
        ]
        assert list(registry) == []
        assert card_registry.resolve("parent") is None
        with pytest.raises(AgentInboxMissingError, match="missing inbox: parent"):
            inbox.wait("parent", timeout=0)
        assert sync_agent.closed is False
    finally:
        sync_agent.close()


def test_coordinator_registry_preserves_existing_card_when_registration_fails() -> None:
    events: list[str] = []
    card_registry = _RecordingRegistry(events)
    inbox = _RecordingInbox(events)
    registry = CoordinatorSyncAgentRegistry(
        registry=card_registry,
        inbox=inbox,
    )
    existing_card = _agent_card("parent")
    card_registry.register(existing_card)
    events.clear()
    sync_agent = SyncAgent(build_agent_with_response("parent"))

    try:
        with pytest.raises(ValueError, match="agent already registered: parent"):
            registry.attach_borrowed_card(_agent_card("parent"), sync_agent)

        assert events == ["register"]
        assert list(registry) == []
        assert card_registry.resolve("parent") is existing_card
        assert sync_agent.closed is False
    finally:
        sync_agent.close()


def test_coordinator_registry_preserves_owned_attach_error_during_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    created_agents: list[SyncAgent] = []
    card_registry = _RecordingRegistry(events, fail_unregister=True)
    inbox = _RecordingInbox(events, fail_create=True, fail_remove=True)
    registry = CoordinatorSyncAgentRegistry(
        registry=card_registry,
        inbox=inbox,
    )
    original_close = SyncAgent.close

    def close_then_fail(self: SyncAgent) -> None:
        events.append("close_agent")
        created_agents.append(self)
        original_close(self)
        raise _CleanupFailure("agent cleanup failed")

    monkeypatch.setattr(SyncAgent, "close", close_then_fail)

    with pytest.raises(_AttachFailure, match="create inbox failed") as raised:
        registry.attach_owned_card(
            _agent_card("child"),
            build_agent_with_response("child"),
        )

    assert events == [
        "register",
        "create_inbox",
        "remove_inbox",
        "unregister",
        "close_agent",
    ]
    assert list(registry) == []
    assert card_registry.resolve("child") is None
    with pytest.raises(AgentInboxMissingError, match="missing inbox: child"):
        inbox.wait("child", timeout=0)
    assert len(created_agents) == 1
    assert created_agents[0].closed is True
    assert raised.value.__notes__ == [
        "remove inbox rollback failed: _CleanupFailure('inbox cleanup failed')",
        "unregister card rollback failed: _CleanupFailure('unregister cleanup failed')",
        "detach agent rollback failed: _CleanupFailure('agent cleanup failed')",
    ]


def test_coordinator_registry_preserves_owned_binding_error_during_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    card_registry = InMemoryRegistry()
    inbox = AgentInbox()
    registry = CoordinatorSyncAgentRegistry(
        registry=card_registry,
        inbox=inbox,
    )
    card = _agent_card("child")
    existing_agent = registry.attach_owned_card(
        card,
        build_agent_with_response("existing"),
    )
    original_close = SyncAgent.close

    def close_then_fail(self: SyncAgent) -> None:
        original_close(self)
        raise _CleanupFailure("agent cleanup failed")

    monkeypatch.setattr(SyncAgent, "close", close_then_fail)

    with pytest.raises(ValueError, match="agent already attached: child") as raised:
        registry.attach_owned_card(
            _agent_card("child"),
            build_agent_with_response("replacement"),
        )

    assert registry["child"] is existing_agent
    assert card_registry.resolve("child") is card
    assert raised.value.__notes__ == [
        "close owned agent rollback failed: _CleanupFailure('agent cleanup failed')",
    ]

    monkeypatch.undo()
    assert registry.detach_card("child") is existing_agent
