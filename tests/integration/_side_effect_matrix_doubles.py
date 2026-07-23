from __future__ import annotations

from dataclasses import dataclass, field

from agentos.capabilities import ToolCompensationInvocation, ToolInvocation
from agentos.distributed.models import QueueDelivery, RequestScope
from agentos.distributed.postgres._database import PostgresPool, Row, fetchone
from agentos.distributed.postgres._outbox_records import STATUS_TOPIC
from agentos.distributed.postgres.side_effects import PostgresSideEffectStore
from agentos.distributed.redis.queue import RedisQueueAdapter
from agentos.providers import ProviderRequest, ProviderResponse, ProviderToolCall
from agentos.runtime.run_runtime import RunWriteGuard
from agentos.runtime.side_effect_types import (
    SideEffectAttemptId,
    SideEffectCompletion,
    SideEffectRecord,
)
from tests.integration._distributed_failure_support import ProcessCrash


@dataclass(slots=True)
class EffectProbe:
    crash_in_handler: bool
    handler_attempts: list[int] = field(default_factory=list)
    compensation_calls: int = 0

    async def invoke(self, invocation: ToolInvocation) -> str:
        self.handler_attempts.append(invocation.context.attempt)
        if self.crash_in_handler and len(self.handler_attempts) == 1:
            raise ProcessCrash
        return "effect-result"

    async def compensate(self, _invocation: ToolCompensationInvocation) -> None:
        self.compensation_calls += 1


class ToolCallProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def async_complete(self, _request: ProviderRequest) -> ProviderResponse:
        self.calls += 1
        if self.calls != 1:
            raise AssertionError("crashing worker must stop after the first provider call")
        return ProviderResponse(
            tool_calls=(ProviderToolCall("call_effect", "external_effect", {}),),
        )


class FinalProvider:
    def __init__(self) -> None:
        self.calls = 0
        self.requests: list[ProviderRequest] = []

    async def async_complete(self, request: ProviderRequest) -> ProviderResponse:
        self.calls += 1
        self.requests.append(request)
        return ProviderResponse("recovered after side effect")


class CrashBeforeStartedStore(PostgresSideEffectStore):
    def __init__(self, database: PostgresPool) -> None:
        super().__init__(database)
        self._crashed = False

    async def mark_started(
        self,
        *,
        attempt_id: SideEffectAttemptId,
        guard: RunWriteGuard,
    ) -> SideEffectRecord:
        if not self._crashed:
            self._crashed = True
            raise ProcessCrash
        return await super().mark_started(attempt_id=attempt_id, guard=guard)


class CrashAfterCompletedStore(PostgresSideEffectStore):
    def __init__(self, database: PostgresPool) -> None:
        super().__init__(database)
        self._crashed = False

    async def complete(
        self,
        *,
        attempt_id: SideEffectAttemptId,
        completion: SideEffectCompletion,
        guard: RunWriteGuard,
    ) -> SideEffectRecord:
        completed = await super().complete(
            attempt_id=attempt_id,
            completion=completion,
            guard=guard,
        )
        if not self._crashed:
            self._crashed = True
            raise ProcessCrash
        return completed


@dataclass(slots=True)
class AckRecordingQueue:
    delegate: RedisQueueAdapter
    pool: PostgresPool
    scope: RequestScope
    session_id: str
    run_id: str
    outbox_ids: list[str] = field(default_factory=list)
    truth_before_ack: list[Row] = field(default_factory=list)

    async def ack(self, *, topic: str, delivery: QueueDelivery) -> None:
        async with self.pool.connection() as connection:
            truth = await fetchone(
                connection,
                """
                SELECT r.status AS run_status, input.status AS input_status,
                       session.active_claim_id,
                       (SELECT COUNT(*)
                        FROM agentos_distributed_execution_cursors AS cursor
                        WHERE cursor.tenant_id = r.tenant_id
                          AND cursor.session_id = r.session_id
                          AND cursor.run_id = r.run_id) AS cursor_count,
                       (SELECT COUNT(*)
                        FROM agentos_distributed_outbox AS outbox
                        WHERE outbox.tenant_id = r.tenant_id
                          AND outbox.session_id = r.session_id
                          AND outbox.run_id = r.run_id
                          AND outbox.topic = %s
                          AND outbox.payload->>'status' = r.status)
                         AS status_outbox_count
                FROM agentos_distributed_runs AS r
                JOIN agentos_distributed_accepted_inputs AS input
                  ON input.tenant_id = r.tenant_id
                 AND input.session_id = r.session_id
                 AND input.run_id = r.run_id
                JOIN agentos_distributed_sessions AS session
                  ON session.tenant_id = r.tenant_id
                 AND session.session_id = r.session_id
                WHERE r.tenant_id = %s AND r.session_id = %s AND r.run_id = %s
                """,
                (STATUS_TOPIC, self.scope.tenant_id, self.session_id, self.run_id),
            )
        assert truth is not None
        assert truth["run_status"] in {"completed", "waiting"}
        assert truth["input_status"] == "committed"
        assert truth["active_claim_id"] is None
        assert truth["cursor_count"] == 0
        assert truth["status_outbox_count"] == 1
        self.truth_before_ack.append(truth)
        await self.delegate.ack(topic=topic, delivery=delivery)
        self.outbox_ids.append(delivery.outbox_id)


__all__ = [
    "AckRecordingQueue",
    "CrashAfterCompletedStore",
    "CrashBeforeStartedStore",
    "EffectProbe",
    "FinalProvider",
    "ToolCallProvider",
]
