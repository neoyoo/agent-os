from __future__ import annotations

from agentos.compression import CompressionIndex, CompressionRuntime
from agentos.messages import MessageRuntime
from agentos.recall import RecallRuntime, SegmentRepository


def assemble_recall_runtime(
    *,
    compression_runtime: CompressionRuntime | None,
    message_runtime: MessageRuntime,
) -> RecallRuntime:
    """为 AgentBuilder 组装 compression segment repository 与 recall runtime。"""

    compression_index = (
        compression_runtime.index
        if compression_runtime is not None
        else CompressionIndex()
    )
    session_id = (
        compression_runtime.session_id or ""
        if compression_runtime is not None
        else ""
    )
    configured_memory_sink = (
        compression_runtime.memory_sink
        if compression_runtime is not None
        else None
    )
    if configured_memory_sink is not None and not isinstance(
        configured_memory_sink,
        SegmentRepository,
    ):
        raise ValueError(
            "CompressionRuntime.memory_sink must be a SegmentRepository "
            "when assembled by AgentBuilder",
        )
    if configured_memory_sink is not None and not compression_runtime.session_id:
        raise ValueError(
            "CompressionRuntime.session_id is required for a configured "
            "SegmentRepository",
        )

    segment_repository = configured_memory_sink or SegmentRepository.from_runtime(
        compression_index,
        message_runtime,
        session_id=session_id,
    )
    if compression_runtime is not None and compression_runtime.memory_sink is None:
        compression_runtime.memory_sink = segment_repository

    # 空 scope 只用于上面的单 Agent、进程内 Level 1 repository。
    return RecallRuntime(
        message_runtime=message_runtime,
        segment_repository=segment_repository,
        session_id=session_id,
    )
