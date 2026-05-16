from __future__ import annotations

from agentos import AgentBuilder
from agentos.compression import CompressionIndex
from agentos.context import ContextState
from agentos.messages import MessageRuntime
from agentos.persistence import MemoryPersistence, SessionSnapshot
from agentos.providers import FakeProvider
from agentos.runtime import SessionState


def build_agent():
    """构建可与 persistence 层组合的示例 agent。"""

    return AgentBuilder().provider(FakeProvider(["persisted"])).build()


def main() -> None:
    """保存一个最小 session snapshot。"""

    store = MemoryPersistence()
    store.save(
        SessionSnapshot(
            session_state=SessionState(id="example"),
            context_state=ContextState(),
            message_runtime=MessageRuntime(),
            compression_index=CompressionIndex(),
        ),
    )
    print(build_agent().run("hello").content)


if __name__ == "__main__":
    main()
