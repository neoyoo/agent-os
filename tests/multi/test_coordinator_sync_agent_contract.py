from agentos.multi import (
    AgentCard,
    AgentCoordinator,
    AgentInbox,
    InMemoryRegistry,
    SpawnExecutor,
    TaskTable,
)
from agentos.sync import SyncAgent
from tests.multi.helpers import build_agent_with_response


class StaticSubagentFactory:
    def create_subagent(self, request):
        return build_agent_with_response("child")


def test_coordinator_borrows_attached_sync_agent() -> None:
    executor = SpawnExecutor(max_workers=1)
    coordinator = AgentCoordinator(
        registry=InMemoryRegistry(),
        inbox=AgentInbox(),
        task_table=TaskTable(),
        spawn_executor=executor,
        subagent_factory=StaticSubagentFactory(),
    )
    sync_agent = SyncAgent(build_agent_with_response("parent"))
    card = AgentCard(
        agent_id="parent",
        name="Parent",
        description="Parent agent.",
        capabilities=("coordinate",),
    )

    coordinator.attach_agent(card, sync_agent)
    assert coordinator.agents["parent"] is sync_agent

    coordinator.detach_agent("parent")

    assert sync_agent.closed is False
    assert "parent" not in coordinator.agents
    sync_agent.close()
    executor.shutdown()
