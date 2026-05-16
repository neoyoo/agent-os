from __future__ import annotations

from agentos import AgentBuilder
from agentos.providers import FakeProvider


def build_agent():
    """构建一个可 streaming 的示例 agent。"""

    return AgentBuilder().provider(FakeProvider(["streaming response"])).build()


def main() -> None:
    """打印 typed stream events。"""

    agent = build_agent()
    for event in agent.stream("hello"):
        print(event)


if __name__ == "__main__":
    main()
