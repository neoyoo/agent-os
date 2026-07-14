import asyncio
import inspect

from agentos.multi import AgentInbox
from agentos.multi.team import TeamWorkerRunner
from agentos.runtime import AgentResult, LocalContinuationInput
from tests.multi.test_team_worker_runner import (
    MapAgentProvider,
    RecordingAgent,
    make_session_provider,
    send_team_message,
)


def test_team_worker_runner_awaits_typed_agent_continuation() -> None:
    assert inspect.iscoroutinefunction(TeamWorkerRunner.run_pending)

    async def run() -> None:
        inbox = AgentInbox()
        inbox.create_inbox("worker")
        send_team_message(inbox)
        agent = RecordingAgent()
        runner = TeamWorkerRunner(
            session_provider=make_session_provider(),
            agent_provider=MapAgentProvider({"session_worker": agent}),
            message_queue=inbox,
        )

        results = await runner.run_pending(team_id="team_1")

        assert [result.status for result in results] == ["completed"]
        assert agent.inputs == [LocalContinuationInput()]
        assert agent.outcomes == [AgentResult("continued")]

    asyncio.run(run())
