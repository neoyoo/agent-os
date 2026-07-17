class AgentRunError(RuntimeError):
    pass


class AgentBusyError(AgentRunError):
    pass


class AgentStreamConsumerError(AgentRunError):
    pass


class AgentStreamClosedError(AgentRunError):
    pass


class ContinuationUnavailableError(AgentRunError):
    pass


class WaitingUnsupportedError(AgentRunError):
    pass


class RunProtocolError(AgentRunError):
    pass


class DurableCommandUnsupportedError(AgentRunError):
    """当前 Agent 未配置 Durable Command Runtime。"""


class CommandConflictError(AgentRunError):
    """同一 command_id 对应了不同的不可变命令内容。"""


class CommandStateError(AgentRunError):
    """Durable Command 不适用于 Run 当前状态或等待原因。"""


class CommandNotDueError(CommandStateError):
    """定时或退避 Command 尚未到达 not_before。"""


class CheckpointConflictError(AgentRunError):
    """Checkpoint 写入与当前 Run 聚合版本冲突。"""


class CheckpointCorruptedError(AgentRunError):
    """持久化恢复状态无法通过严格校验。"""


class DurableStoreClosedError(AgentRunError):
    """Durable Store 或其 Profile 已关闭。"""


class DurableUnsafeDataError(AgentRunError):
    """待持久化状态包含禁止进入 Durable Store 的表示。"""


class SyncAdapterEventLoopError(AgentRunError):
    pass


class SyncAdapterReentryError(AgentRunError):
    pass


class SyncAgentClosedError(AgentRunError):
    pass


class SyncStreamConsumerError(AgentRunError):
    pass
