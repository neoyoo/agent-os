from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True, kw_only=True)
class AgentEvent:
    """运行时 typed event 的基类，不进入默认 prompt。"""

    session_id: str | None = None
    turn_id: str | None = None


@dataclass(frozen=True, slots=True)
class TurnStartedEvent(AgentEvent):
    """turn 已开始。"""

    user_input: str = ""
    is_continuation: bool = False


@dataclass(frozen=True, slots=True)
class UserMessageAppendedEvent(AgentEvent):
    """user 消息已追加到 MessageRuntime。"""

    message_id: str = ""


@dataclass(frozen=True, slots=True)
class ContextRenderedEvent(AgentEvent):
    """context 已渲染为 provider system。"""


@dataclass(frozen=True, slots=True)
class ProviderRequestBuiltEvent(AgentEvent):
    """provider request 已构建。"""


@dataclass(frozen=True, slots=True)
class ProviderResponseReceivedEvent(AgentEvent):
    """provider response 已收到。"""


@dataclass(frozen=True, slots=True)
class ProviderRetryEvent(AgentEvent):
    """provider 调用失败后准备 retry。"""

    attempt: int = 0
    max_retries: int = 0
    error: str = ""
    delay_seconds: float = 0


@dataclass(frozen=True, slots=True)
class AssistantMessageAppendedEvent(AgentEvent):
    """assistant 消息已追加到 MessageRuntime。"""

    message_id: str = ""


@dataclass(frozen=True, slots=True)
class ToolCallRequestedEvent(AgentEvent):
    """provider 请求执行工具。"""

    tool_name: str = ""
    tool_call_id: str = ""


@dataclass(frozen=True, slots=True)
class ToolExecutionStartedEvent(AgentEvent):
    """工具执行已开始。"""

    tool_name: str = ""
    tool_call_id: str = ""


@dataclass(frozen=True, slots=True)
class ToolExecutionCompletedEvent(AgentEvent):
    """工具执行已完成。"""

    tool_name: str = ""
    tool_call_id: str = ""


@dataclass(frozen=True, slots=True)
class ToolResultAppendedEvent(AgentEvent):
    """tool result 已追加到 MessageRuntime。"""

    tool_name: str = ""
    tool_call_id: str = ""
    message_id: str = ""


@dataclass(frozen=True, slots=True)
class TurnCompletedEvent(AgentEvent):
    """turn 已完成。"""


@dataclass(frozen=True, slots=True)
class TurnFailedEvent(AgentEvent):
    """turn 执行失败。"""

    error: str = ""


@dataclass(frozen=True, slots=True)
class WorkingStateSchemaDeclaredEvent(AgentEvent):
    """working state schema 已声明。"""

    fields: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WorkingStateUpdatedEvent(AgentEvent):
    """working state 字段已更新。"""

    field_name: str = ""


@dataclass(frozen=True, slots=True)
class WorkingStateSchemaExtendedEvent(AgentEvent):
    """working state schema 已扩展。"""

    fields: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ChapterStartedEvent(AgentEvent):
    """新 chapter 已开始。"""

    fields: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class InheritedStateSetEvent(AgentEvent):
    """inherited state 投影已设置。"""

    item_count: int = 0


@dataclass(frozen=True, slots=True)
class MemoryContextSetEvent(AgentEvent):
    """memory context 投影已设置。"""

    item_count: int = 0


@dataclass(frozen=True, slots=True)
class CompressedSegmentAppendedEvent(AgentEvent):
    """compressed segment 已追加到 context state。"""

    segment_id: str = ""


@dataclass(frozen=True, slots=True)
class CompressionSkippedEvent(AgentEvent):
    """压缩被跳过。"""

    reason: str = ""


@dataclass(frozen=True, slots=True)
class CompressionCompletedEvent(AgentEvent):
    """压缩已完成。"""

    segment_id: str = ""
    source_message_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RecallContextRequestedEvent(AgentEvent):
    """recall_context 已请求。"""

    handle: str = ""


@dataclass(frozen=True, slots=True)
class RecallContextFailedEvent(AgentEvent):
    """recall_context 失败。"""

    handle: str = ""
    error: str = ""


@dataclass(frozen=True, slots=True)
class RecallContextInjectedEvent(AgentEvent):
    """recall_context 已解析出可返回消息。"""

    handle: str = ""
    message_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SnapshotSavedEvent(AgentEvent):
    """session snapshot 已保存。"""

    snapshot_session_id: str = ""


@dataclass(frozen=True, slots=True)
class SnapshotLoadedEvent(AgentEvent):
    """session snapshot 已读取。"""

    snapshot_session_id: str = ""


@dataclass(frozen=True, slots=True)
class SubagentSpawnedEvent(AgentEvent):
    """ephemeral subagent 已创建并开始排队或运行。"""

    parent_agent_id: str = ""
    child_agent_id: str = ""
    task_id: str = ""


@dataclass(frozen=True, slots=True)
class AgentTaskDispatchedEvent(AgentEvent):
    """任务已派发给目标 agent。"""

    from_agent_id: str = ""
    to_agent_id: str = ""
    task_id: str = ""


@dataclass(frozen=True, slots=True)
class AgentTaskCompletedEvent(AgentEvent):
    """multi-agent 任务已进入终态。"""

    agent_id: str = ""
    task_id: str = ""
    status: Literal["completed", "failed", "cancelled", "timeout"] = "completed"
    elapsed_seconds: float = 0


@dataclass(frozen=True, slots=True)
class AgentTaskFailedEvent(AgentEvent):
    """multi-agent 任务执行失败。"""

    agent_id: str = ""
    task_id: str = ""
    error: str = ""


@dataclass(frozen=True, slots=True)
class AgentTaskCancelledEvent(AgentEvent):
    """multi-agent 任务已取消。"""

    agent_id: str = ""
    task_id: str = ""


@dataclass(frozen=True, slots=True)
class AgentInboxBackpressureEvent(AgentEvent):
    """agent inbox 达到容量上限。"""

    agent_id: str = ""
    pending_count: int = 0
    max_pending_envelopes: int = 0


@dataclass(frozen=True, slots=True)
class AgentTaskLateResultReceivedEvent(AgentEvent):
    """终态之后收到 late result。"""

    agent_id: str = ""
    task_id: str = ""
    final_status: Literal["cancelled", "timeout"] = "timeout"


@dataclass(frozen=True, slots=True)
class AgentContinuationFailedEvent(AgentEvent):
    """parent continuation turn 执行失败。"""

    parent_agent_id: str = ""
    task_id: str = ""
    error: str = ""
