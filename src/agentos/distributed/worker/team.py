from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256

from agentos.distributed._model_validation import require_identifier
from agentos.distributed.errors import (
    ActiveRunConflictError,
    CommandNotDueError,
    CommandStateError,
)
from agentos.distributed.internal_errors import (
    InternalSubmissionBindingRevokedError,
)
from agentos.distributed.internal_models import (
    InternalRunSubmission,
    InternalSubmissionAuthority,
)
from agentos.distributed.internal_services import InternalRunSubmissionService
from agentos.distributed.models import QueueDelivery, RequestScope, RunReadModel
from agentos.distributed.protocols import QueuePort
from agentos.distributed.services import RunQueryService
from agentos.multi.team_delivery_types import (
    ClaimedTeamDelivery,
    TeamDelivery,
    TeamDeliveryResult,
    TeamDeliveryTarget,
)
from agentos.multi.team_errors import TeamConflictError
from agentos.multi.team_identity import team_command_id, team_submission_id
from agentos.multi.team_ports import (
    TeamApplicationPort,
    TeamDeliveryBootstrapPort,
    TeamDeliveryPort,
)
from agentos.multi.team_types import TeamMemberRecord
from agentos.runtime.durable_commands import DurableRunCommand
from agentos.runtime.run_state import RunStatus


HeartbeatWait = Callable[[float], Awaitable[None]]
_TERMINAL_DELIVERY_STATES = frozenset({"applied", "rejected"})
_WAKEABLE_WAIT_KINDS = frozenset({"remote_result", "resource_availability"})


@dataclass(slots=True)
class _HeartbeatControl:
    stopping: bool = False
    call_in_progress: bool = False


@dataclass(frozen=True, slots=True)
class TeamDeliveryRunner:
    """编排 Team delivery；PostgreSQL 与 Run Service 保持唯一真值。"""

    bootstrap: TeamDeliveryBootstrapPort
    deliveries: TeamDeliveryPort
    queue: QueuePort
    teams: TeamApplicationPort
    run_queries: RunQueryService
    internal_submissions: InternalRunSubmissionService
    worker_id: str
    principal_id: str
    topic: str
    claim_ttl: timedelta
    heartbeat_interval: timedelta
    heartbeat_wait: HeartbeatWait = asyncio.sleep

    def __post_init__(self) -> None:
        require_identifier(self.worker_id, "worker_id")
        require_identifier(self.principal_id, "principal_id")
        require_identifier(self.topic, "topic")
        for value, field_name in (
            (self.claim_ttl, "claim_ttl"),
            (self.heartbeat_interval, "heartbeat_interval"),
        ):
            if type(value) is not timedelta or value <= timedelta(0):
                raise ValueError(f"{field_name} must be a positive timedelta")
        if self.heartbeat_interval * 3 > self.claim_ttl:
            raise ValueError("heartbeat_interval must not exceed one third of claim_ttl")

    async def run_delivery(self, delivery: QueueDelivery) -> bool:
        """路由一条 delivery；只在 durable result 已提交后 ACK。"""

        if type(delivery) is not QueueDelivery:
            raise TypeError("delivery must be QueueDelivery")
        target = await self.bootstrap.resolve_delivery(outbox_id=delivery.outbox_id)
        if target is None:
            return False
        self._validate_target(target, delivery)
        if target.delivery.state in _TERMINAL_DELIVERY_STATES:
            await self._ack(delivery)
            return True

        scope = RequestScope(target.tenant_id, self.principal_id)
        claimed = await self.deliveries.claim_pending(
            scope=scope,
            outbox_id=delivery.outbox_id,
            claim_id=_claim_id(self.worker_id, delivery.delivery_id),
            ttl=self.claim_ttl,
        )
        if claimed is None:
            return await self._refresh_after_claim_race(delivery)
        _validate_claimed_target(target, claimed)
        return await self._run_claimed(delivery, scope, claimed)

    async def _run_claimed(
        self,
        delivery: QueueDelivery,
        scope: RequestScope,
        claimed: ClaimedTeamDelivery,
    ) -> bool:
        heartbeat_control = _HeartbeatControl()
        heartbeat = asyncio.create_task(
            self._heartbeat(scope, claimed, heartbeat_control)
        )
        route = asyncio.create_task(self._route(scope, claimed, heartbeat))
        try:
            done, _ = await asyncio.wait(
                (heartbeat, route),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if heartbeat in done:
                heartbeat_error = await _task_outcome(heartbeat)
                route_error = await _task_outcome(route)
                if heartbeat_error is not None:
                    raise heartbeat_error
                if route_error is not None:
                    raise route_error
                raise RuntimeError("team delivery heartbeat stopped unexpectedly")
            try:
                result = await route
            finally:
                await _stop_heartbeat(heartbeat, heartbeat_control)
            if result is None:
                await self.deliveries.release(scope=scope, claim=claimed.claim)
                return False
            try:
                committed = await self.deliveries.commit_result(
                    scope=scope,
                    claim=claimed.claim,
                    result=result,
                )
            except TeamConflictError:
                await self.deliveries.release(scope=scope, claim=claimed.claim)
                return False
            _validate_committed_result(claimed, result, committed)
            await self._ack(delivery)
            return True
        finally:
            await _cancel_and_wait(route)
            await _cancel_and_wait(heartbeat)

    async def _route(
        self,
        scope: RequestScope,
        claimed: ClaimedTeamDelivery,
        heartbeat: asyncio.Task[None],
    ) -> TeamDeliveryResult | None:
        target = claimed.target
        applied = await self.internal_submissions.get_applied_input(
            scope,
            _authority(claimed),
        )
        await _ensure_heartbeat(heartbeat)
        if applied is not None:
            if applied.session_id != target.delivery.target_session_id:
                raise RuntimeError("internal submission port returned another session")
            return TeamDeliveryResult(
                applied.input_kind,
                applied.run_id,
                applied.aggregate_version,
                RunStatus.QUEUED,
            )
        member = await self.teams.get_member(
            scope=scope,
            team_id=target.delivery.team_id,
            recipient_agent_id=target.delivery.recipient_agent_id,
        )
        await _ensure_heartbeat(heartbeat)
        if member is not None and (
            type(member) is not TeamMemberRecord
            or member.team_id != target.delivery.team_id
            or member.recipient_agent_id != target.delivery.recipient_agent_id
        ):
            raise RuntimeError("Team member port returned another binding")
        if member is None or (
            member.status != "active"
            or member.target_session_id != target.delivery.target_session_id
        ):
            return TeamDeliveryResult("rejected_binding_revoked", None, None, None)

        for evaluation in range(2):
            active = await self.run_queries.get_active(
                scope,
                target.delivery.target_session_id,
            )
            await _ensure_heartbeat(heartbeat)
            try:
                if active is None:
                    try:
                        return await self._submit_internal(scope, claimed, heartbeat)
                    except InternalSubmissionBindingRevokedError:
                        return TeamDeliveryResult(
                            "rejected_binding_revoked", None, None, None
                        )
                if _matches_wait(target, active):
                    return await self._submit_wakeup(scope, claimed, active, heartbeat)
                return TeamDeliveryResult(
                    "rejected_nonterminal",
                    active.run_id,
                    active.aggregate_version,
                    active.status,
                )
            except (ActiveRunConflictError, CommandStateError, CommandNotDueError):
                if evaluation == 1:
                    return None
                await _ensure_heartbeat(heartbeat)
        raise AssertionError("route evaluation must return")

    async def _submit_internal(
        self,
        scope: RequestScope,
        claimed: ClaimedTeamDelivery,
        heartbeat: asyncio.Task[None],
    ) -> TeamDeliveryResult:
        delivery = claimed.target.delivery
        receipt = await self.internal_submissions.submit(
            scope,
            InternalRunSubmission(
                delivery.target_session_id,
                team_submission_id(
                    scope=scope,
                    delivery_id=delivery.delivery_id,
                    target_session_id=delivery.target_session_id,
                ),
                "team_message",
                _source_payload(claimed),
            ),
            _authority(claimed),
        )
        await _ensure_heartbeat(heartbeat)
        return TeamDeliveryResult(
            "internal_start",
            receipt.run_id,
            receipt.aggregate_version,
            RunStatus.QUEUED,
        )

    async def _submit_wakeup(
        self,
        scope: RequestScope,
        claimed: ClaimedTeamDelivery,
        active: RunReadModel,
        heartbeat: asyncio.Task[None],
    ) -> TeamDeliveryResult:
        delivery = claimed.target.delivery
        receipt = await self.internal_submissions.submit_wakeup(
            scope,
            delivery.target_session_id,
            DurableRunCommand(
                active.run_id,
                team_command_id(
                    scope=scope,
                    delivery_id=delivery.delivery_id,
                    target_session_id=delivery.target_session_id,
                    run_id=active.run_id,
                ),
                "wakeup",
                _source_payload(claimed),
            ),
            _authority(claimed),
        )
        await _ensure_heartbeat(heartbeat)
        return TeamDeliveryResult(
            "wakeup",
            receipt.run_id,
            receipt.aggregate_version,
            RunStatus.QUEUED,
        )

    async def _heartbeat(
        self,
        scope: RequestScope,
        claimed: ClaimedTeamDelivery,
        control: _HeartbeatControl,
    ) -> None:
        current = claimed.claim
        while True:
            await self.heartbeat_wait(self.heartbeat_interval.total_seconds())
            if control.stopping:
                return
            control.call_in_progress = True
            try:
                current = await self.deliveries.heartbeat(
                    scope=scope,
                    claim=current,
                    ttl=self.claim_ttl,
                )
            finally:
                control.call_in_progress = False
            if control.stopping:
                return

    async def _refresh_after_claim_race(self, delivery: QueueDelivery) -> bool:
        target = await self.bootstrap.resolve_delivery(outbox_id=delivery.outbox_id)
        if target is None:
            return False
        self._validate_target(target, delivery)
        if target.delivery.state not in _TERMINAL_DELIVERY_STATES:
            return False
        await self._ack(delivery)
        return True

    @staticmethod
    def _validate_target(target: TeamDeliveryTarget, delivery: QueueDelivery) -> None:
        if type(target) is not TeamDeliveryTarget or target.outbox_id != delivery.outbox_id:
            raise RuntimeError("team delivery target does not match queue delivery")

    async def _ack(self, delivery: QueueDelivery) -> None:
        await self.queue.ack(topic=self.topic, delivery=delivery)


def _source_payload(claimed: ClaimedTeamDelivery) -> dict[str, str]:
    return {
        "team_id": claimed.target.delivery.team_id,
        "message_id": claimed.target.delivery.message_id,
        "recipient_agent_id": claimed.target.delivery.recipient_agent_id,
        "action": "team_read_messages",
    }


def _authority(claimed: ClaimedTeamDelivery) -> InternalSubmissionAuthority:
    return InternalSubmissionAuthority(
        claimed.target.delivery.delivery_id,
        claimed.claim.claim_id,
        claimed.claim.fencing_token,
    )


def _matches_wait(target: TeamDeliveryTarget, active: RunReadModel) -> bool:
    reason = active.wait_reason
    return bool(
        active.status is RunStatus.WAITING
        and reason is not None
        and reason.kind in _WAKEABLE_WAIT_KINDS
        and target.message.correlation_id is not None
        and target.message.correlation_id == reason.handle
    )


def _validate_claimed_target(
    resolved: TeamDeliveryTarget,
    claimed: ClaimedTeamDelivery,
) -> None:
    if (
        claimed.target.tenant_id != resolved.tenant_id
        or claimed.target.outbox_id != resolved.outbox_id
        or claimed.target.message != resolved.message
        or claimed.target.delivery.delivery_id != resolved.delivery.delivery_id
        or claimed.target.delivery.source_sha256 != resolved.delivery.source_sha256
    ):
        raise RuntimeError("claimed Team delivery does not match resolved truth")


def _validate_committed_result(
    claimed: ClaimedTeamDelivery,
    result: TeamDeliveryResult,
    committed: TeamDelivery,
) -> None:
    expected_state = "applied" if result.result_kind in {"internal_start", "wakeup"} else "rejected"
    if (
        type(committed) is not TeamDelivery
        or committed.delivery_id != claimed.target.delivery.delivery_id
        or committed.state != expected_state
        or committed.result_kind != result.result_kind
        or committed.observed_run_id != result.observed_run_id
        or committed.observed_aggregate_version != result.observed_aggregate_version
        or committed.observed_run_status is not result.observed_run_status
    ):
        raise RuntimeError("Team delivery port returned another result")


def _claim_id(worker_id: str, delivery_id: str) -> str:
    digest = sha256(f"{worker_id}\x00{delivery_id}".encode("utf-8")).hexdigest()
    return f"team_claim_{digest}"


async def _ensure_heartbeat(heartbeat: asyncio.Task[None]) -> None:
    if not heartbeat.done():
        return
    error = await _task_outcome(heartbeat)
    if error is not None:
        raise error
    raise RuntimeError("team delivery heartbeat stopped unexpectedly")


async def _stop_heartbeat(
    heartbeat: asyncio.Task[None],
    control: _HeartbeatControl,
) -> None:
    control.stopping = True
    if not heartbeat.done() and not control.call_in_progress:
        heartbeat.cancel()
    result = (await asyncio.gather(heartbeat, return_exceptions=True))[0]
    if isinstance(result, BaseException) and not isinstance(
        result,
        asyncio.CancelledError,
    ):
        raise result


async def _task_outcome(task: asyncio.Task[object]) -> BaseException | None:
    result = (await asyncio.gather(task, return_exceptions=True))[0]
    return result if isinstance(result, BaseException) else None


async def _cancel_and_wait(task: asyncio.Task[object]) -> None:
    if not task.done():
        task.cancel()
    await asyncio.gather(task, return_exceptions=True)


__all__ = ["TeamDeliveryRunner"]
