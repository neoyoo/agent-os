from __future__ import annotations

import json

from agentos.multi import AgentEnvelope, TaskRequest
from agentos.multi.redis_queue import RedisAgentMessageQueue
from agentos.multi.serializers import envelope_to_dict


class PendingRedis:
    def __init__(self) -> None:
        self.claimed: list[str] = []
        self.dead_letters: list[tuple[str, dict[str, str]]] = []

    def xpending_range(self, stream: str, group: str, min: str, max: str, count: int):
        return [
            {"message_id": b"1-0", "consumer": b"old", "time_since_delivered": 10_000, "times_delivered": 1},
            {"message_id": b"2-0", "consumer": b"old", "time_since_delivered": 10_000, "times_delivered": 4},
        ]

    def xclaim(self, stream: str, group: str, consumer: str, min_idle_time: int, message_ids: list[str]):
        self.claimed.extend(message_ids)
        envelope = AgentEnvelope(
            envelope_id="env_1",
            from_agent_id="parent",
            to_agent_id="expert",
            type="task_request",
            payload=TaskRequest(task_id="task_1", instruction="work"),
            created_at=1,
            correlation_id="task_1",
        )
        return [(b"1-0", {"payload": json.dumps(envelope_to_dict(envelope))})]

    def xadd(self, stream: str, fields: dict[str, str], maxlen: int, approximate: bool) -> str:
        self.dead_letters.append((stream, fields))
        return "dead_1"

    def xack(self, stream: str, group: str, delivery_id: str) -> int:
        return 1


def test_redis_queue_claims_idle_pending_and_dead_letters_exhausted_messages() -> None:
    client = PendingRedis()
    queue = RedisAgentMessageQueue("redis://local", client=client)

    deliveries = queue.reclaim_pending(
        "expert",
        idle_threshold_ms=5_000,
        max_retries=3,
    )

    assert [delivery.delivery_id for delivery in deliveries] == ["1-0"]
    assert client.claimed == ["1-0"]
    assert client.dead_letters[0][0].endswith(":dead")
