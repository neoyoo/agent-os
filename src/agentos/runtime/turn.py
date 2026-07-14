from dataclasses import dataclass
from typing import Literal


TurnStatus = Literal["running", "completed", "waiting", "failed", "cancelled"]


@dataclass(slots=True)
class TurnState:
    """维护单个 user turn 的生命周期。"""

    id: str
    user_input: str
    status: TurnStatus = "running"
    tool_iterations: int = 0
    error: str | None = None

    def increment_tool_iteration(self) -> None:
        """记录一次 provider tool-call loop 迭代。"""

        self.tool_iterations += 1

    def complete(self) -> None:
        """标记 turn 成功完成。"""

        self.status = "completed"

    def mark_waiting(self) -> None:
        """标记当前执行切片已进入权威等待状态。"""

        self.status = "waiting"

    def cancel(self) -> None:
        """标记当前 turn 已取消。"""

        self.status = "cancelled"

    def fail(self, error: str) -> None:
        """标记 turn 失败并保存错误摘要。"""

        self.status = "failed"
        self.error = error
