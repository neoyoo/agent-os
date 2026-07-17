from __future__ import annotations

from collections.abc import Sequence

from agentos import AgentBuilder
from agentos.compression import CompressionIndex, CompressionRuntime
from agentos.context import ContextRuntime
from agentos.persistence.base import SessionSnapshot
from agentos.policies import BudgetPolicy
from agentos.providers import FakeProvider
from agentos.runtime import SessionState


class ReferenceSnapshotAgentFactory:
    """Durable Session 示例使用的确定性 Agent Factory。"""

    def create_agent(
        self,
        *,
        session_id: str,
        snapshot: SessionSnapshot | None,
    ):
        context = ContextRuntime(session_id=session_id)
        message_runtime = None
        session_state = SessionState(id=session_id)
        compression_index = CompressionIndex()
        next_segment_number = 1
        if snapshot is not None:
            context = ContextRuntime(
                state=snapshot.context_state,
                session_id=session_id,
            )
            message_runtime = snapshot.message_runtime
            session_state = snapshot.session_state
            compression_index = snapshot.compression_index
            next_segment_number = snapshot.next_segment_number
        builder = (
            AgentBuilder()
            .provider(FakeProvider([f"ok:{session_id}"]))
            .context_runtime(context)
        )
        if message_runtime is not None:
            builder = builder.message_runtime(message_runtime)
        agent = builder.build(session_id=session_id)
        agent.query_loop.session_state = session_state
        agent.query_loop.compression_runtime = CompressionRuntime(
            context_runtime=context,
            message_runtime=agent.query_loop.message_runtime,
            budget_policy=BudgetPolicy(max_active_messages=1000),
            index=compression_index,
            session_id=session_id,
            next_segment_number=next_segment_number,
        )
        return agent

    def create_snapshot(self, *, session_id: str, agent) -> SessionSnapshot:
        compression_runtime = agent.query_loop.compression_runtime
        return SessionSnapshot(
            session_state=agent.query_loop.session_state or SessionState(id=session_id),
            context_state=agent.query_loop.context_runtime.snapshot(),
            message_runtime=agent.query_loop.message_runtime,
            compression_index=(
                compression_runtime.index
                if compression_runtime is not None
                else CompressionIndex()
            ),
            next_segment_number=(
                compression_runtime.next_segment_number()
                if compression_runtime is not None
                else 1
            ),
        )


class ReferenceNacosClient:
    """仅用于 Adapter 身份证据的无网络 Nacos Client。"""

    agentos_reference_no_network = True

    def register_instance(self, **kwargs: object) -> None:
        return None

    def deregister_instance(self, **kwargs: object) -> None:
        return None

    def list_instances(self, **kwargs: object) -> Sequence[object]:
        return ()


class ReferenceRedisClient:
    """仅用于 Adapter 身份证据的无网络 Redis Client。"""

    agentos_reference_no_network = True

    def xgroup_create(self, *args: object, **kwargs: object) -> None:
        return None

    def xadd(self, *args: object, **kwargs: object) -> str:
        return "0-1"

    def xreadgroup(self, *args: object, **kwargs: object) -> list[object]:
        return []

    def xack(self, *args: object, **kwargs: object) -> int:
        return 1


class ReferencePostgresConnection:
    """仅用于 Adapter 身份证据的无数据库 Postgres Connection。"""

    agentos_reference_no_network = True

    def execute(
        self,
        sql: str,
        params: tuple[object, ...] = (),
    ) -> object:
        raise RuntimeError("reference example does not execute SQL")

    def commit(self) -> None:
        return None
