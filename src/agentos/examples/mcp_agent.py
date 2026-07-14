from __future__ import annotations

from agentos import AgentBuilder
from agentos.providers import FakeProvider
from agentos.runtime import AgentResult
from agentos.sync import SyncAgent


def build_agent():
    """MCP 示例占位：真实 MCP client 由应用注入 capabilities 层。"""

    return AgentBuilder().provider(FakeProvider(["mcp-ready"])).build()


def main() -> None:
    """运行 MCP-ready agent 示例。"""

    with SyncAgent(build_agent()) as sync_agent:
        result = sync_agent.run("hello")
    if not isinstance(result, AgentResult):
        raise RuntimeError("example agent unexpectedly entered waiting state")
    print(result.content)


if __name__ == "__main__":
    main()
