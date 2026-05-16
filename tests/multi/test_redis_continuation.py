from __future__ import annotations

import json

from agentos.multi import AgentTaskNoticeStore, TaskResult
from agentos.multi.redis_continuation import RedisContinuationTrigger


class PubSubRedis:
    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []

    def publish(self, channel: str, payload: str) -> int:
        self.published.append((channel, payload))
        return 1


class ResultStore:
    def consume_results_for_agent(self, agent_id: str) -> list[TaskResult]:
        return [TaskResult(task_id="task_1", status="completed", summary="done")]


def test_redis_continuation_trigger_publishes_task_completed_notice() -> None:
    client = PubSubRedis()
    trigger = RedisContinuationTrigger(
        redis_client=client,
        notice_store=AgentTaskNoticeStore(),
    )

    trigger.on_task_completed("parent", "task_1")

    channel, payload = client.published[0]
    assert channel == "agentos:continuation:parent"
    assert json.loads(payload) == {"parent_agent_id": "parent", "task_id": "task_1"}


def test_redis_continuation_trigger_can_poll_task_store_without_redis() -> None:
    notices = AgentTaskNoticeStore()
    trigger = RedisContinuationTrigger(
        redis_client=None,
        notice_store=notices,
        task_store=ResultStore(),
    )

    assert trigger.poll_task_store("parent") == 1
    assert notices.consume_notices("parent")
