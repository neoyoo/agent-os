from __future__ import annotations

from threading import Event

from agentos.multi.coordinator import AgentCoordinator


class ExpertAgentRunner:
    """常驻 expert agent 的 inbox 消费循环。"""

    def __init__(self, *, coordinator: AgentCoordinator, agent_id: str) -> None:
        """绑定 coordinator 和 expert agent id。"""

        self.coordinator = coordinator
        self.agent_id = agent_id
        self._stopped = Event()

    def run_once(self, timeout: float | None = None) -> bool:
        """等待并处理当前 inbox 中的一批 task_request。"""

        if not self.coordinator.inbox.wait(self.agent_id, timeout):
            return False
        handled = False
        for delivery in self.coordinator.inbox.collect(self.agent_id):
            result = self.coordinator.execute_expert_envelope(delivery.envelope)
            self.coordinator.inbox.ack(self.agent_id, delivery.delivery_id)
            if result is not None:
                handled = True
        return handled

    def run_forever(self, timeout: float = 0.1) -> None:
        """持续消费 inbox，直到 stop 被调用。"""

        while not self._stopped.is_set():
            self.run_once(timeout=timeout)

    def stop(self) -> None:
        """请求 runner 停止。"""

        self._stopped.set()
