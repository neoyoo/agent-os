from dataclasses import dataclass, field
from typing import Protocol

from agentos.events.types import AgentEvent


class EventSubscriber(Protocol):
    """观察 runtime typed events 的订阅者。"""

    def record(self, event: AgentEvent) -> None:
        """记录 runtime event。"""


@dataclass(slots=True)
class EventBus:
    """记录并广播 typed runtime events。"""

    subscribers: list[EventSubscriber] = field(default_factory=list)
    events: list[AgentEvent] = field(default_factory=list)
    subscriber_errors: list[str] = field(default_factory=list)

    def emit(self, event: AgentEvent) -> AgentEvent:
        """记录并广播一个 typed runtime event。"""

        self.events.append(event)
        for subscriber in self.subscribers:
            try:
                subscriber.record(event)
            except Exception as error:
                self.subscriber_errors.append(str(error))
        return event
