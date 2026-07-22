from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from agentos.distributed._model_validation import require_identifier
from agentos.distributed.models import QueueDelivery, RequestScope
from agentos.distributed.protocols import QueuePort
from agentos.multi.team_event_types import TeamEventReplayItem, TeamEventTarget
from agentos.multi.team_ports import TeamEventBootstrapPort, TeamEventReplayPort


@dataclass(frozen=True, slots=True)
class TeamEventDeliveryRunner:
    """把 PostgreSQL 权威 Team event 投影到 Redis replay。"""

    bootstrap: TeamEventBootstrapPort
    replay: TeamEventReplayPort
    queue: QueuePort
    principal_id: str
    topic: str
    claim_ttl: timedelta
    heartbeat_interval: timedelta

    def __post_init__(self) -> None:
        require_identifier(self.principal_id, "principal_id")
        require_identifier(self.topic, "topic")
        for value, field_name in (
            (self.claim_ttl, "claim_ttl"),
            (self.heartbeat_interval, "heartbeat_interval"),
        ):
            if type(value) is not timedelta or value <= timedelta(0):
                raise ValueError(f"{field_name} must be a positive timedelta")

    async def run_delivery(self, delivery: QueueDelivery) -> bool:
        """仅在权威 event 已成功写入 replay 后 ACK delivery。"""

        if type(delivery) is not QueueDelivery:
            raise TypeError("delivery must be QueueDelivery")
        target = await self.bootstrap.resolve_event(outbox_id=delivery.outbox_id)
        if target is None:
            return False
        if type(target) is not TeamEventTarget or target.outbox_id != delivery.outbox_id:
            raise RuntimeError("Team event target does not match queue delivery")
        item = await self.replay.append(
            scope=RequestScope(target.tenant_id, self.principal_id),
            event=target.event,
        )
        if type(item) is not TeamEventReplayItem or item.event != target.event:
            raise RuntimeError("Team event replay returned another event")
        await self.queue.ack(topic=self.topic, delivery=delivery)
        return True


__all__ = ["TeamEventDeliveryRunner"]
