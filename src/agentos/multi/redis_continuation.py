from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

from agentos.multi.continuation import AgentTaskNoticeStore
from agentos.multi.types import TaskResult


class RedisPublisher(Protocol):
    """Redis Pub/Sub publish 边界。"""

    def publish(self, channel: str, payload: str) -> int:
        """发布一条消息。"""


class ResultPollingStore(Protocol):
    """无 Redis 时 fallback polling 的 TaskStore 子集。"""

    def consume_results_for_agent(self, agent_id: str) -> list[TaskResult]:
        """消费指定 parent agent 的终态结果。"""


@dataclass(slots=True)
class RedisContinuationTrigger:
    """通过 Redis Pub/Sub 跨节点通知 parent continuation。"""

    redis_client: RedisPublisher | None
    notice_store: AgentTaskNoticeStore
    task_store: ResultPollingStore | None = None
    key_prefix: str = "agentos"

    def on_task_completed(self, parent_agent_id: str, task_id: str) -> None:
        """写本地 notice，并向 Redis channel 发布 task completed 通知。"""

        self.notice_store.add_task_completed(parent_agent_id, task_id)
        if self.redis_client is None:
            return
        self.redis_client.publish(
            self._channel(parent_agent_id),
            json.dumps(
                {"parent_agent_id": parent_agent_id, "task_id": task_id},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        )

    def poll_task_store(self, parent_agent_id: str) -> int:
        """无 Redis 部署下，通过 TaskStore polling 生成 continuation notices。"""

        if self.task_store is None:
            return 0
        results = self.task_store.consume_results_for_agent(parent_agent_id)
        for result in results:
            self.notice_store.add_task_completed(parent_agent_id, result.task_id)
        return len(results)

    def _channel(self, parent_agent_id: str) -> str:
        return f"{self.key_prefix}:continuation:{parent_agent_id}"
