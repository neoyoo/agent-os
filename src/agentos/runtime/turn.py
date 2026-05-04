from dataclasses import dataclass
from typing import Literal


TurnStatus = Literal["running", "completed", "failed"]


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

    def fail(self, error: str) -> None:
        """标记 turn 失败并保存错误摘要。"""

        self.status = "failed"
        self.error = error
