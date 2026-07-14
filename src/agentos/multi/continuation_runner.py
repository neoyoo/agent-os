from __future__ import annotations

from agentos.runtime.errors import AgentBusyError
from agentos.runtime.run import LocalContinuationInput, RunOutcome
from agentos.sync.agent import SyncAgent


def run_local_continuation(agent: SyncAgent) -> RunOutcome:
    """通过同步适配边界执行一次本地 continuation turn。"""

    while True:
        try:
            return agent.run(LocalContinuationInput())
        except AgentBusyError:
            agent._wait_until_idle()
