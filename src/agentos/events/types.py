from dataclasses import dataclass


@dataclass(frozen=True, slots=True, kw_only=True)
class AgentEvent:
    """运行时 typed event 的基类，不进入默认 prompt。"""

    session_id: str | None = None
    turn_id: str | None = None


@dataclass(frozen=True, slots=True)
class TurnStartedEvent(AgentEvent):
    """turn 已开始。"""

    user_input: str = ""


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
    """recall_context 已注入 temporary refs。"""

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
