from dataclasses import dataclass
from typing import Literal

from agentos.runtime.turn import TurnState


SessionStatus = Literal["new", "running", "closed"]


@dataclass(slots=True)
class SessionState:
    """维护 session 生命周期和 turn 序号。"""

    id: str
    status: SessionStatus = "new"
    _next_turn_number: int = 1

    def start(self) -> None:
        """标记 session 已开始运行。"""

        self.status = "running"

    def close(self) -> None:
        """标记 session 已关闭。"""

        self.status = "closed"

    def new_turn(self, user_input: str) -> TurnState:
        """创建一个新的 running turn。"""

        if self.status == "new":
            self.start()
        turn = TurnState(
            id=f"turn_{self._next_turn_number}",
            user_input=user_input,
            status="running",
        )
        self._next_turn_number += 1
        return turn

    def next_turn_number(self) -> int:
        """返回下一轮 turn 将使用的数字序号。"""

        return self._next_turn_number

    @classmethod
    def from_snapshot(
        cls,
        id: str,
        status: SessionStatus,
        next_turn_number: int,
    ) -> "SessionState":
        """从持久化 snapshot 恢复 SessionState。"""

        return cls(
            id=id,
            status=status,
            _next_turn_number=next_turn_number,
        )
