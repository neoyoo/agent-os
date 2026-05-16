from __future__ import annotations

from agentos import AgentBuilder
from agentos.multi import AgentCoordinator, AgentInbox, InMemoryRegistry, SpawnExecutor, SubagentInitRequest, TaskTable
from agentos.providers import FakeProvider


class StaticSubagentFactory:
    """为示例创建固定响应 subagent。"""

    def create_subagent(self, request: SubagentInitRequest):
        return AgentBuilder().provider(FakeProvider(["done"])).build()


def build_coordinator() -> AgentCoordinator:
    """构建本地 multi-agent dispatch 示例 coordinator。"""

    return AgentCoordinator(
        registry=InMemoryRegistry(),
        inbox=AgentInbox(),
        task_table=TaskTable(),
        spawn_executor=SpawnExecutor(max_workers=1),
        subagent_factory=StaticSubagentFactory(),
    )


def main() -> None:
    """展示 coordinator 可被创建。"""

    coordinator = build_coordinator()
    print(type(coordinator).__name__)


if __name__ == "__main__":
    main()
