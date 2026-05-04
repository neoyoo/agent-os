from dataclasses import dataclass, field


@dataclass(slots=True)
class CompressionIndex:
    """保存 segment handle 到原始 message refs 的内部映射。"""

    _source_refs: dict[str, list[str]] = field(default_factory=dict)

    def record(self, segment_id: str, source_message_ids: list[str]) -> None:
        """记录压缩片段对应的原始消息 id。"""

        if segment_id in self._source_refs:
            raise ValueError(f"compressed segment already exists: {segment_id}")
        if not source_message_ids:
            raise ValueError("compressed segment requires source message ids")
        self._source_refs[segment_id] = list(source_message_ids)

    def source_refs(self, segment_id: str) -> list[str]:
        """读取 segment 对应的原始 message ids。"""

        try:
            return list(self._source_refs[segment_id])
        except KeyError as error:
            raise KeyError(segment_id) from error

    def snapshot(self) -> dict[str, tuple[str, ...]]:
        """返回 segment refs 的不可变快照。"""

        return {
            segment_id: tuple(source_refs)
            for segment_id, source_refs in self._source_refs.items()
        }

    @classmethod
    def from_snapshot(
        cls,
        snapshot: dict[str, list[str] | tuple[str, ...]],
    ) -> "CompressionIndex":
        """从持久化 snapshot 恢复 CompressionIndex。"""

        index = cls()
        for segment_id, source_refs in snapshot.items():
            index.record(segment_id, list(source_refs))
        return index
