from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agentos.multi.types import AgentEnvelope, AgentEnvelopeType


@dataclass(frozen=True, slots=True)
class QueueDelivery:
    """Envelope delivery returned by a message queue."""

    delivery_id: str
    envelope: AgentEnvelope


class AgentMessageQueue(Protocol):
    """Boundary for distributed agent point-to-point message delivery."""

    def create_inbox(self, agent_id: str) -> None:
        """Create a target inbox."""

    def remove_inbox(self, agent_id: str) -> None:
        """Remove a target inbox."""

    def send(self, envelope: AgentEnvelope) -> str:
        """Send an envelope and return the delivery id."""

    def collect(
        self,
        agent_id: str,
        *,
        envelope_types: tuple[AgentEnvelopeType, ...] | None = None,
    ) -> list[QueueDelivery]:
        """Return currently deliverable messages."""

    def wait(self, agent_id: str, timeout: float | None = None) -> bool:
        """Wait until an inbox has deliverable messages."""

    def ack(self, agent_id: str, delivery_id: str) -> bool:
        """Acknowledge that a delivery has been processed."""

    def requeue(self, agent_id: str, delivery: QueueDelivery) -> None:
        """Return an unhandled delivery to the queue or leave it pending."""
