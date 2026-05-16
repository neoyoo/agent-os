from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agentos.multi.types import AgentEnvelope


@dataclass(frozen=True, slots=True)
class QueueDelivery:
    """message queue 返回的 envelope delivery。"""

    delivery_id: str
    envelope: AgentEnvelope


class AgentMessageQueue(Protocol):
    """分布式 agent 点对点消息和通知投递边界。"""

    def create_inbox(self, agent_id: str) -> None:
        """创建目标 inbox。"""

    def remove_inbox(self, agent_id: str) -> None:
        """移除目标 inbox。"""

    def send(self, envelope: AgentEnvelope) -> str:
        """发送 envelope，并返回 delivery id。"""

    def collect(self, agent_id: str) -> list[QueueDelivery]:
        """读取当前可处理 deliveries。"""

    def wait(self, agent_id: str, timeout: float | None = None) -> bool:
        """等待 inbox 出现可处理消息。"""

    def ack(self, agent_id: str, delivery_id: str) -> bool:
        """确认 delivery 已处理。"""
