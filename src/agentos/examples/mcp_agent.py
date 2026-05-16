from __future__ import annotations

from agentos import AgentBuilder
from agentos.providers import FakeProvider


def build_agent():
    """MCP 示例占位：真实 MCP client 由应用注入 capabilities 层。"""

    return AgentBuilder().provider(FakeProvider(["mcp-ready"])).build()


def main() -> None:
    """运行 MCP-ready agent 示例。"""

    print(build_agent().run("hello").content)


if __name__ == "__main__":
    main()
