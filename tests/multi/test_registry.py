import pytest

from agentos.multi import AgentCard, InMemoryRegistry


def test_registry_registers_resolves_and_unregisters_agent_cards_only() -> None:
    registry = InMemoryRegistry()
    card = AgentCard(
        agent_id="agent_1",
        name="Reviewer",
        description="Reviews code.",
        capabilities=("code_review", "tests"),
    )

    registry.register(card)

    assert registry.resolve("agent_1") == card
    assert registry.all_agents == [card]

    registry.unregister("agent_1")

    assert registry.resolve("agent_1") is None
    assert registry.all_agents == []


def test_registry_rejects_duplicate_agent_ids() -> None:
    registry = InMemoryRegistry()
    card = AgentCard(
        agent_id="agent_1",
        name="Reviewer",
        description="Reviews code.",
        capabilities=("code_review",),
    )

    registry.register(card)

    with pytest.raises(ValueError, match="agent already registered"):
        registry.register(card)


def test_registry_discover_requires_all_requested_capabilities() -> None:
    registry = InMemoryRegistry()
    reviewer = AgentCard(
        agent_id="reviewer",
        name="Reviewer",
        description="Reviews code.",
        capabilities=("code_review", "tests"),
    )
    searcher = AgentCard(
        agent_id="searcher",
        name="Searcher",
        description="Searches docs.",
        capabilities=("search",),
    )
    registry.register(reviewer)
    registry.register(searcher)

    assert registry.discover(("code_review", "tests")) == [reviewer]
    assert registry.discover(("code_review", "search")) == []
    assert registry.discover(()) == [reviewer, searcher]


def test_registry_update_status_replaces_frozen_card() -> None:
    registry = InMemoryRegistry()
    card = AgentCard(
        agent_id="agent_1",
        name="Reviewer",
        description="Reviews code.",
        capabilities=("code_review",),
        status="idle",
    )
    registry.register(card)

    registry.update_status("agent_1", "busy")

    updated = registry.resolve("agent_1")
    assert updated is not None
    assert updated.status == "busy"
    assert card.status == "idle"
