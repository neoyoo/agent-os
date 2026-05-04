from agentos.persistence.base import SessionSnapshot
from agentos.persistence.serializers import (
    session_snapshot_from_dict,
    session_snapshot_to_dict,
)


class MemoryPersistence:
    """用于测试和嵌入场景的内存 snapshot 存储。"""

    def __init__(self) -> None:
        """创建空内存存储。"""

        self._snapshots: dict[str, dict[str, object]] = {}

    def save(self, snapshot: SessionSnapshot) -> None:
        """保存一个 session snapshot。"""

        self._snapshots[snapshot.session_state.id] = session_snapshot_to_dict(snapshot)

    def load(self, session_id: str) -> SessionSnapshot:
        """读取一个 session snapshot。"""

        try:
            data = self._snapshots[session_id]
        except KeyError as error:
            raise KeyError(session_id) from error
        return session_snapshot_from_dict(data)

    def list_ids(self) -> list[str]:
        """列出已保存的 session ids。"""

        return sorted(self._snapshots)

    def delete(self, session_id: str) -> None:
        """删除一个 session snapshot。"""

        self._snapshots.pop(session_id, None)
