from __future__ import annotations

from agentos import AgentBuilder
from agentos.providers import FakeProvider
from agentos.sync import SyncAgent


def build_agent():
    """构建一个可 streaming 的示例 agent。"""

    return AgentBuilder().provider(FakeProvider(["streaming response"])).build()


def main() -> None:
    """打印 typed stream events。"""

    agent = build_agent()
    with SyncAgent(agent) as sync_agent:
        with sync_agent.run("hello", stream=True) as stream:
            for event in stream:
                print(event)


if __name__ == "__main__":
    main()
