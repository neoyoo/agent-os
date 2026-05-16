from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


AgentStatus = Literal["idle", "busy", "offline"]
AgentLifecycle = Literal["ephemeral", "persistent"]
AgentEnvelopeType = Literal["task_request", "task_result"]
TaskStatus = Literal[
    "queued",
    "running",
    "completed",
    "failed",
    "cancelled",
    "timeout",
]
CoordinationMode = Literal["spawn", "dispatch"]
ContextInitStrategy = Literal["isolated"]


@dataclass(frozen=True, slots=True)
class AgentCard:
    """可发现 agent 的声明，不持有本地 runtime 对象。"""

    agent_id: str
    name: str
    description: str
    capabilities: tuple[str, ...]
    version: str = "0.1.0"
    endpoint: str | None = None
    status: AgentStatus = "idle"
    lifecycle: AgentLifecycle = "persistent"
    max_concurrent_tasks: int = 1


@dataclass(frozen=True, slots=True)
class TaskRequest:
    """跨 agent 派发的任务请求。"""

    task_id: str
    instruction: str
    allowed_tool_names: tuple[str, ...] = ()
    timeout_seconds: float = 300
    trace_context: dict[str, str] | None = None


@dataclass(frozen=True, slots=True)
class TaskResult:
    """subagent 或 expert 回给父 agent 的任务结果。"""

    task_id: str
    status: TaskStatus
    summary: str
    artifacts: dict[str, object] = field(default_factory=dict)
    error: str | None = None
    elapsed_seconds: float = 0


@dataclass(frozen=True, slots=True)
class TaskHandle:
    """协调工具立即返回给主 agent 的任务句柄。"""

    task_id: str
    mode: CoordinationMode
    target_agent_id: str
    status: TaskStatus


@dataclass(frozen=True, slots=True)
class TaskRecord:
    """TaskTable 内部保存的任务状态事实。"""

    task_id: str
    mode: CoordinationMode
    parent_agent_id: str
    target_agent_id: str
    request: TaskRequest
    status: TaskStatus
    created_at: float
    deadline_at: float
    result: TaskResult | None = None
    late_result: TaskResult | None = None
    completed_at: float | None = None
    consumed_at: float | None = None


@dataclass(frozen=True, slots=True)
class SubagentInitRequest:
    """SubagentFactory 创建 isolated subagent 所需的输入。"""

    parent_agent_id: str
    child_agent_id: str
    task: TaskRequest
    context_strategy: ContextInitStrategy = "isolated"


@dataclass(frozen=True, slots=True)
class AgentEnvelope:
    """AgentInbox 中传递的执行消息。"""

    envelope_id: str
    from_agent_id: str
    to_agent_id: str
    type: AgentEnvelopeType
    payload: TaskRequest | TaskResult
    created_at: float
    correlation_id: str | None = None
