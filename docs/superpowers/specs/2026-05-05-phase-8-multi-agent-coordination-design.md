---
name: agentos phase 8 multi-agent coordination design
description: 统一多 Agent 协调架构——通过 AgentRegistry + AgentInbox 支持 Spawn 和 Discover & Dispatch 两种模式。
type: design-spec
status: draft
date: 2026-05-05
relates_to:
  - AGENTS.md
  - docs/design/sdk-architecture.md
  - docs/design/llm-context-only-example.md
  - docs/superpowers/specs/2026-05-05-phase-7-memory-compression-middleware-design.md
  - ai-knowledge/wiki/multi-agent.md
  - ai-knowledge/wiki/agent-registry-discovery.md
  - ai-knowledge/wiki/tool-system.md
---

# Phase 8 统一多 Agent 协调架构设计

## 背景

Phase 7 第一块完成了生产级 memory recall 和 compression middleware。Phase 7 后续项明确把 AgentCard、AgentRegistry、AgentResolver 和 subagent orchestration 放到下一块能力中，并要求：

- subagent 不直接读写主 agent working state。
- 主 agent 只能通过 tool result 吸收 subagent 发现。
- subagent context 初始化策略必须显式。

Phase 8 第一块在这个边界上引入本地单进程多 Agent 协调能力，通过 `AgentRegistry` + `AgentInbox` + `AgentCoordinator` 支持两种模式：

- **Spawn（主 -> 子）**：主 agent 非阻塞派发临时 subagent，subagent 完成后自动注销。
- **Discover & Dispatch（专家派发）**：主 agent 按 capability 发现常驻 expert agent，派发任务，expert 完成后回送结果。

两种模式共用同一套 `AgentCard`、`AgentEnvelope`、`TaskRequest`、`TaskResult` 和 `TaskTable`。

## Scope Contract

本设计属于 Phase 8 `multi-agent` / `agent-registry-discovery` 的第一块。目标是在不破坏现有同步 `QueryLoop` 和单 agent 运行链路的前提下，设计一个本地单进程、可测试、可后续替换为远程 resolver/A2A 的协调内核。

本设计要完成的验收项：

- `InMemoryRegistry` 只注册、更新、发现 `AgentCard`，不保存 `Agent` 或 runtime 实例。
- `discover` 按 capability 全量匹配：请求的 capabilities 必须全部包含在 `AgentCard.capabilities` 中。
- `AgentInbox` 能 create/remove/send/collect/wait，使用 `threading.Event` 或等价机制实现无轮询唤醒。
- `AgentCoordinator.spawn()` 同步返回 `TaskHandle`，后台运行 isolated subagent，完成后结果进入父 agent inbox。
- `AgentCoordinator.dispatch()` 按 capability 找到未饱和 expert，发送 `task_request`，返回 `TaskHandle`。
- `TaskTable` 维护任务状态机、deadline、result 和 late stale result，状态更新必须是 compare-and-set 风格。
- `ExpertAgentRunner` 阻塞等待自己的 inbox，收到 `task_request` 后执行 expert agent 并回送 `task_result`。
- Spawned subagent 生命周期正确：创建 card -> 注册 -> 运行 -> 完成/失败/取消 -> 注销 -> 移除 inbox。
- Spawn 默认最多 3 个并发；超过上限的任务进入 `TaskTable` 的 `queued` 状态。
- `cancel(task_id)` 能取消 queued task，并对 running subagent 发出 best-effort cancellation。
- `AgentInbox` 有明确容量上限和 backpressure 行为，不能让父 agent 长期不收结果时无限增长。
- 主 agent 通过 `spawn_subagent`、`dispatch_to_expert`、`check_agent_tasks`、`cancel_agent_task` 四个工具使用协调能力；这些工具必须来自 `ToolRegistry`，不能渲染假 capability。
- subagent/expert 进入终态后触发 parent continuation，continuation turn 注入一次性 runtime notice，提示主 agent 调用 `check_agent_tasks`。
- continuation 只有消费到 runtime notice 时才运行 provider；空 notice 必须 no-op，避免无用户消息、无 notice 的空请求。
- runtime notice 只能作为 `ContextState.runtime_notices` 独立 projection slot 渲染，不写入 `MessageStore`、working state、inherited state 或 memory context。
- parent user turn 与 continuation turn 必须互斥，避免并发进入同一个 `QueryLoop` / `MessageRuntime`。
- continuation turn 的 typed event 必须能与普通 user turn 区分。
- parent continuation 执行失败不能被后台线程静默吞掉，必须记录失败事实并可通过 typed event 观察。
- `ContinuationTrigger.on_task_completed()` 唤醒失败不能反向改变或打断已完成的 task 状态转换。
- 集成测试覆盖 FakeProvider + Spawn 模式端到端。
- 集成测试覆盖 FakeProvider + Discover & Dispatch 模式端到端。

本设计明确不做：

- 不做跨进程/网络远程 agent 通信、`A2AAdapter` 或 `ServiceResolver`。
- 不做持久化 registry、Redis/etcd/Nacos、心跳 TTL 或版本灰度。
- 不做 agent 热更新、滚动升级或 session sticky routing。
- 不引入 async `QueryLoop`；第一版保持现有同步 provider/tool loop。
- 不把 subagent result 正文自动注入 prompt 或消息历史；结果正文只通过协调工具以 tool result 暴露给主 agent。
- 不做完整 SecurityPolicy 降级系统，但必须定义第一版最小工具 allowlist 策略。

如果后续实现低于这些验收项，必须标记为 **partially complete**，不能把可运行 demo 当成 Phase 8 完成。

## 设计选择

### 方案 A：Async 执行消息通道 + MessageRuntime notification 注入

原 draft 使用 async 执行消息通道，并计划在 `QueryLoop.run_turn` 入口 collect inbox 后 append notification。

这个方案不采用为第一版默认方案：

- 当前 `QueryLoop`、`Agent.run()`、`Provider`、`ToolCallRouter` 都是同步接口。
- `MessageRole` 只有 `user` / `assistant` / `tool`，没有 provider-safe 的 `notification` role。
- 把 notification 写入 `MessageRuntime` 会污染消息真值源，后续压缩、记忆提取和审计会误把 runtime 事件当作用户或 assistant 消息。
- `Bus` 在项目命名规则中只用于观察型 pub/sub，执行消息通道不能叫 bus。

### 方案 B：同步 AgentInbox + 主动 continuation notice + tool result 收取结果（采用）

采用同步优先的本地实现：

- `AgentInbox` 是执行消息队列，不是 observation bus。
- `EventBus` 只记录 typed events，不参与执行流控制。
- 后台 subagent/expert 使用 `ThreadPoolExecutor` 或 dedicated worker thread 运行。
- subagent/expert 终态触发 `ContinuationTrigger`，本地实现把 task notice 放入 parent 的一次性 `TurnNoticeProvider`。
- `Agent.run_continuation()` 运行 runtime continuation turn，不追加 user 消息。
- continuation turn 通过 `ContextState.runtime_notices` 渲染短提示，提示主 agent 调用 `check_agent_tasks`。
- 若 `TurnNoticeProvider.consume_notices()` 返回空，`run_continuation()` 直接返回空结果，不创建 turn、不调用 provider。
- continuation turn 已设置 runtime notice 后，即使 stream 在首个 provider request 前被关闭，也必须清理一次性 notice。
- `TurnStartedEvent` 通过 `is_continuation=True` 标记 runtime-driven continuation turn，观测层不能把它误判为普通空 user turn。
- `LocalContinuationTrigger` 记录 parent continuation 失败，并通过 `AgentContinuationFailedEvent` 发布 observation-only 事件；该事件不改变 task 状态，不把错误写入 prompt。
- `AgentCoordinator` 调用 `ContinuationTrigger` 时必须隔离 trigger 异常，并通过 `AgentContinuationFailedEvent(parent_agent_id, task_id, error)` 记录唤醒失败。
- `check_agent_tasks` 读取 `TaskTable` 未消费终态结果，结果作为普通 tool result 进入 `MessageRuntime`。

这个方案与现有 SDK 边界最匹配：`QueryLoop` 只依赖 `TurnNoticeProvider` 协议，不直接依赖 `AgentInbox`、`AgentCoordinator` 或 `multi` 模块。后续也可以把本地 trigger 替换为远程 channel adapter，而不改变 LLM 看到的协调工具。

### 方案 C：AgentCard + A2A / remote resolver

这是后续远程阶段的方向，不进入第一版。第一版仍在 `AgentCard` 中保留 `version` 和 `endpoint` 字段，让远程 resolver 有稳定扩展点。

## 架构边界

### `registry/` 与 `multi/`

`AgentRegistry` 是声明和发现边界，只保存 `AgentCard`：

- 可以注册、注销、按名称/ID 解析 card。
- 可以按 capability 枚举候选 card。
- 可以更新 card 的 status。
- 不保存 `Agent`、`QueryLoop`、provider、tool registry 或任何本地 Python runtime 对象。

`multi/` 是协调和执行边界：

- 持有本地 agent 实例引用。
- 维护任务状态表、并发控制、取消状态。
- 使用 `AgentInbox` 收发 `AgentEnvelope`。
- 负责把协调工具注册进 `ToolRegistry`。

### `runtime/query_loop.py`

第一版不修改 `QueryLoop.run_turn` 的消息注入逻辑。`QueryLoop` 仍只负责：

- append user message。
- build provider request。
- call provider。
- route provider tool calls。
- append assistant/tool messages。

多 agent 结果通过 `check_agent_tasks` 工具返回，由现有 tool-call loop 写入 tool result。主 agent 若要吸收结果，必须再显式调用 `update_state`。

### `events/`

`EventBus` 保持 observation-only。新增 subagent/expert typed events 仅用于可观测性和测试断言，不唤醒 worker，不修改任务状态，不拦截执行。

## 核心类型

### AgentCard

```python
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

AgentStatus = Literal["idle", "busy", "offline"]
AgentLifecycle = Literal["ephemeral", "persistent"]
```

字段约束：

- `endpoint=None` 表示本地单进程 agent；远程 resolver 后续使用 URL、A2A address 或 service name。
- `capabilities` 是能力声明，不是 prompt 指令。
- `max_concurrent_tasks` 表示该 agent 可同时处理的任务上限；是否饱和由 `AgentCoordinator` 的任务表判断，不由 registry 单独判断。

### TaskRequest / TaskResult / TaskHandle / TaskRecord

```python
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
class TaskRequest:
    """跨 agent 派发的任务请求。"""

    task_id: str
    instruction: str
    allowed_tool_names: tuple[str, ...] = ()
    timeout_seconds: float = 300

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

@dataclass(frozen=True, slots=True)
class SubagentInitRequest:
    """SubagentFactory 创建 isolated subagent 所需的输入。"""

    parent_agent_id: str
    child_agent_id: str
    task: TaskRequest
    context_strategy: ContextInitStrategy = "isolated"
```

`TaskRequest.allowed_tool_names` 使用工具名 allowlist，而不是把 `RegisteredTool` 或 handler 放进 envelope。实际工具对象从父 agent 的 `ToolRegistry` 过滤得到，并再次经过 `SecurityPolicy`。

`TaskRecord.late_result` 只用于 timeout 或 cancelled 之后到达的结果。它不能覆盖 `status` 或 `result`，`check_agent_tasks` 默认不把 late result 当作正常完成结果展示。

`ContextInitStrategy` 第一版只有 `"isolated"`。后续如果增加 compressed summary fork 或 inherited state fork，只扩展 `SubagentInitRequest`，不破坏 `SubagentFactory` 的调用形态。

### AgentEnvelope

```python
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

AgentEnvelopeType = Literal["task_request", "task_result"]
```

第一版不提供 `notification` envelope type。需要通知 LLM 时，通过 `check_agent_tasks` 工具返回结果。

## 核心组件

### AgentRegistry / InMemoryRegistry

```python
class AgentRegistry(Protocol):
    def register(self, card: AgentCard) -> None: ...
    def unregister(self, agent_id: str) -> None: ...
    def resolve(self, agent_id: str) -> AgentCard | None: ...
    def discover(self, capabilities: tuple[str, ...]) -> list[AgentCard]: ...
    def update_status(self, agent_id: str, status: AgentStatus) -> None: ...

    @property
    def all_agents(self) -> list[AgentCard]: ...
```

`InMemoryRegistry` 行为：

- `register` 要求 `agent_id` 唯一。
- `discover(())` 返回所有 card。
- `discover(("code_review", "tests"))` 只返回同时具备这两个 capability 的 card。
- `update_status` 用 replace-copy 更新 frozen `AgentCard`，不允许外部持有可变 card 后绕过 registry。
- `unregister` 移除 card；ephemeral subagent 完成后必须调用。

### AgentInbox

```python
class AgentInbox:
    def __init__(
        self,
        max_pending_envelopes: int = 100,
        event_bus: EventBus | None = None,
    ) -> None: ...
    def create_inbox(self, agent_id: str) -> None: ...
    def remove_inbox(self, agent_id: str) -> None: ...
    def send(self, envelope: AgentEnvelope) -> None: ...
    def collect(self, agent_id: str) -> list[AgentEnvelope]: ...
    def wait(self, agent_id: str, timeout: float | None = None) -> bool: ...
    def has_pending(self, agent_id: str) -> bool: ...
```

实现要求：

- 每个 agent 一个 `queue.Queue[AgentEnvelope]`。
- 每个 agent 一个 `threading.Event` 或等价 condition。
- `send` 将 envelope 放入目标 inbox 并 set event。
- `collect` drain 当前队列；队列空后 clear event。
- `wait` 用于 expert runner 阻塞等待，无轮询。
- 对不存在 inbox 的 `send` 必须 fail-closed，抛出明确异常，避免消息静默丢失。
- `max_pending_envelopes` 默认 100；超过上限时 `send` 必须拒绝入队并抛出明确异常。如果配置了 `event_bus`，同时 emit `AgentInboxBackpressureEvent`。
- 第一版不做 oldest-result eviction。静默丢弃结果会破坏任务正确性；如果调用者需要保留更多结果，应提高容量或更频繁调用 `check_agent_tasks`。

### TaskTable

```python
class TaskTable:
    def create(self, record: TaskRecord) -> TaskHandle: ...
    def mark_running(self, task_id: str) -> bool: ...
    def mark_completed(self, task_id: str, result: TaskResult) -> bool: ...
    def mark_failed(self, task_id: str, result: TaskResult) -> bool: ...
    def mark_cancelled(self, task_id: str, result: TaskResult) -> bool: ...
    def mark_timed_out(self, task_id: str, result: TaskResult) -> bool: ...
    def store_late_result(self, task_id: str, result: TaskResult) -> bool: ...
    def due_timeouts(self, now: float) -> list[TaskRecord]: ...
    def active_for_agent(self, agent_id: str | None = None) -> list[TaskHandle]: ...
    def completed_for_agent(self, agent_id: str) -> list[TaskResult]: ...
```

`TaskTable` 是纯任务状态机：

- 只管理 `TaskRecord` 和状态转换，不创建 agent，不发 inbox，不调用 provider。
- 状态转换必须使用 compare-and-set 语义：只有预期状态匹配时才更新。
- `queued -> running -> completed|failed|cancelled|timeout` 是主路径。
- `queued -> cancelled` 合法。
- `running -> timeout` 合法。
- `timeout|cancelled -> completed` 不合法；这种 late completion 只能写入 `late_result`。
- `due_timeouts(now)` 返回 deadline 已到且仍为 `queued|running` 的记录，由 coordinator 或 runner 在安全检查点调用。

### SpawnExecutor

```python
class SpawnExecutor:
    def __init__(self, max_workers: int = 3) -> None: ...
    def submit(self, task_id: str, run: Callable[[], TaskResult]) -> None: ...
    def shutdown(self) -> None: ...
```

`SpawnExecutor` 只负责运行 ephemeral subagent callable。它不选择 expert、不读写 registry、不格式化 tool result。子 agent 必须在主线程或 coordinator 线程串行构造完成后再提交，避免在 worker thread 中并发构造运行时对象引发竞态。

### AgentCoordinator

```python
class AgentCoordinator:
    def __init__(
        self,
        registry: AgentRegistry,
        inbox: AgentInbox,
        task_table: TaskTable,
        spawn_executor: SpawnExecutor,
        subagent_factory: SubagentFactory,
    ) -> None: ...

    def attach_agent(self, card: AgentCard, agent: Agent) -> None: ...

    def spawn(
        self,
        instruction: str,
        allowed_tool_names: tuple[str, ...],
        parent_agent_id: str,
    ) -> TaskHandle: ...

    def dispatch(
        self,
        instruction: str,
        capabilities: tuple[str, ...],
        allowed_tool_names: tuple[str, ...],
        from_agent_id: str,
    ) -> TaskHandle: ...

    def cancel(self, task_id: str) -> bool: ...
    def collect_results(self, agent_id: str) -> list[TaskResult]: ...
    def active_tasks(self, agent_id: str | None = None) -> list[TaskHandle]: ...

class SubagentFactory(Protocol):
    def create_subagent(
        self,
        request: SubagentInitRequest,
    ) -> Agent: ...
```

Coordinator 责任：

- 通过 `attach_agent` 保存本地 `Agent` 引用，registry 不保存。
- 协调 `TaskTable`、`SpawnExecutor`、`AgentRegistry` 和 `AgentInbox`。
- 通过 `SubagentFactory` 创建 isolated subagent，不把 provider/config 写入 `AgentCard`。
- 为 persistent expert 维护 running count，确保不超过 `AgentCard.max_concurrent_tasks`。
- 在任务完成、失败、timeout 或取消后发送 `task_result` 到父 agent inbox。
- 对 ephemerals 做自动 unregister 和 inbox cleanup。
- 不直接保存复杂状态机逻辑；任务状态规则必须留在 `TaskTable`。
- `collect_results(agent_id)` 先 drain `AgentInbox` 作为唤醒/通知通道，再以 `TaskTable` 为结果真值源返回 completed/failed/cancelled/timeout result。

### ExpertAgentRunner

```python
class ExpertAgentRunner:
    def __init__(
        self,
        agent_id: str,
        agent: Agent,
        inbox: AgentInbox,
        coordinator: AgentCoordinator,
        registry: AgentRegistry,
    ) -> None: ...

    def run_forever(self) -> None: ...
    def stop(self) -> None: ...
```

执行循环：

```python
def run_forever(self):
    while self._running:
        if not self.inbox.wait(self.agent_id, timeout=1.0):
            continue
        envelopes = self.inbox.collect(self.agent_id)
        for envelope in envelopes:
            if envelope.type == "task_request":
                self._execute_and_reply(envelope)
```

`timeout=1.0` 只用于 stop 检查，不用于轮询业务消息。业务唤醒由 `AgentInbox.send()` set event 完成。

## Spawn 流程

```text
1. 主 agent 调用 spawn_subagent(instruction, allowed_tool_names)
2. AgentCoordinator 创建 task_id 和 ephemeral child_agent_id
3. AgentCoordinator 创建 SubagentInitRequest(context_strategy="isolated")
4. SubagentFactory 创建 isolated Agent：
   - 独立 ContextRuntime
   - 独立 MessageRuntime
   - 独立 ToolCallRouter
   - 不共享父 active messages
   - 不共享父 working state
5. AgentCoordinator 注册 AgentCard(lifecycle="ephemeral")
6. AgentCoordinator create child inbox，并在 TaskTable 中创建 queued TaskRecord
7. SpawnExecutor 有容量时调用 TaskTable.mark_running(task_id)
8. child agent 执行 Agent.run(instruction)
9. 完成后通过 TaskTable 写入 result 或 late_result，再发送 task_result 到 parent inbox
10. child AgentCard unregister，child inbox remove
11. 主 agent 后续调用 check_agent_tasks()，以 tool result 读取 TaskResult
```

第一版 subagent context 初始化策略：

- 默认独立初始化，不 fork 父 active messages。
- 可以复用父 provider 实例或 provider factory，但 provider 不进入 `AgentCard`。
- 可以继承父 runtime contract 和 capability plane 渲染规则。
- 只能获得 `allowed_tool_names` 中列出的工具。
- 不能默认获得 `spawn_subagent` / `dispatch_to_expert`，避免递归派发爆炸。
- 第一版 `SubagentInitRequest.context_strategy` 只能是 `"isolated"`；其他策略必须等后续 spec 明确后再加入。

## Dispatch 流程

```text
1. 主 agent 调用 dispatch_to_expert(instruction, capabilities, allowed_tool_names)
2. AgentCoordinator 调 registry.discover(capabilities)
3. Coordinator 过滤 offline card，并排除已达到 max_concurrent_tasks 的 expert
4. Coordinator 用 round-robin 选择 candidate
5. Coordinator 发送 task_request envelope 到 expert inbox
6. ExpertAgentRunner 被 AgentInbox 唤醒
7. ExpertAgentRunner 执行 expert Agent.run(instruction)
8. ExpertAgentRunner 发送 task_result 到 from_agent_id inbox
9. 主 agent 后续调用 check_agent_tasks()，以 tool result 读取 TaskResult
```

如果没有候选 expert：

- `dispatch_to_expert` 工具返回明确失败 tool result。
- Coordinator 不创建 running task。
- EventBus 可记录 `AgentTaskFailedEvent(error="no_available_agent")`。

## 主 Agent 可用工具

主 agent 通过 `AgentCoordinationTools` 注册四个外部工具到同一个 `ToolRegistry`：

```text
spawn_subagent
  非阻塞派发临时子任务
  参数: instruction, allowed_tool_names
  返回: task_id, child_agent_id, status

dispatch_to_expert
  按 capability 发现并派发给常驻 expert
  参数: instruction, capabilities, allowed_tool_names
  返回: task_id, expert_agent_id, status

check_agent_tasks
  查看当前 agent 相关任务状态，并收取已完成结果
  参数: include_completed=true
  返回: task handles + completed TaskResult summaries
  结果真值源: TaskTable；AgentInbox 只作为 result-ready 通知通道

cancel_agent_task
  取消 queued 或 running task
  参数: task_id
  返回: cancelled=true/false
```

工具注册规则：

- 这些工具必须通过 `ToolRegistry.register(RegisteredTool(...))` 注册。
- `ToolCallRouter.tool_specs()` 和 Capability Plane 都从同一个 `ToolRegistry` 投影。
- 未配置 `AgentCoordinator` 时，不注册这些工具。
- subagent 默认不继承这些协调工具，除非父 agent 显式 allowlist。

## 工具权限和隔离

第一版最小策略：

- `allowed_tool_names` 为空时，subagent/expert 不获得任何外部工具。
- Spawn 模式下，`allowed_tool_names` 从父 agent `ToolRegistry` 解析；不存在的工具名必须报错。
- Dispatch 模式下，`allowed_tool_names` 从目标 expert 的 `ToolRegistry` 解析；不存在的工具名必须报错。
- context protocol tools 可用于 subagent 自己的 `ContextRuntime`，但不能作用于父 context。
- `recall_context` 只能访问 subagent 自己的 `RecallRuntime`。
- `SecurityPolicy.ensure_tool_allowed` 必须在构造子 router 和执行工具时继续生效。
- 默认 block coordination tools，避免 subagent 递归派发。

完整权限降级、只读工具分级、跨用户 sandbox isolation 属于后续 `sandbox-isolation` / production policy 设计。

## 并发、超时和取消

Spawn 并发：

- `SpawnExecutor(max_workers=3)` 是第一版默认并发上限。
- 超过上限的 spawn task 状态为 `queued`。
- worker slot 可用后，runner 必须通过 `TaskTable.mark_running(task_id)` 切换为 `running`。
- 如果 task 在排队期间已被取消或 timeout，`mark_running` 返回 `False`，runner 不得执行 agent。

Expert 并发：

- 每个 expert 按 `AgentCard.max_concurrent_tasks` 控制。
- `AgentCoordinator` 维护 running count。
- candidate 均饱和时，dispatch 返回失败，而不是无界排队。
- 第一版不做 busy expert queue。后续如要支持，必须显式增加 bounded queue 和 `queue_if_busy` 参数，不能静默改变 dispatch 语义。

取消：

- queued task 通过 `TaskTable.mark_cancelled` 直接标记为 `cancelled`，并发送 cancelled result。
- running task 使用 best-effort cancellation：设置 task cancellation flag，并调用 `Agent.interrupt()`（如果后续实现该 API）。
- 当前 `Agent` 没有 interrupt API 时，running cancellation 必须在 result error 中标记为 best-effort，不得假装强取消成功。
- running task 后续如果自然完成，结果写入 `TaskRecord.late_result`，不能覆盖 `cancelled` 状态。

Timeout：

- `TaskTable.create` 根据 `TaskRequest.timeout_seconds` 写入 `deadline_at`。
- `AgentCoordinator`、`SpawnExecutor` 和 `ExpertAgentRunner` 在任务开始前、任务结束后、`check_agent_tasks` 前调用 `TaskTable.due_timeouts(now)` 并把到期任务标记为 `timeout`。
- timeout result 必须进入父 agent inbox。
- 后台执行若无法强停，后续 late result 必须写入 `TaskRecord.late_result`，不能覆盖 `timeout` 状态。
- 第一版不使用 `Future.result(timeout=...)` 作为强制超时机制，因为 Python worker thread 无法被安全强杀；timeout 是任务状态事实，不是强制中断保证。

Backpressure：

- `AgentInbox(max_pending_envelopes=100)` 是第一版默认容量。
- 父 agent 长期不调用 `check_agent_tasks` 时，completed result 不能无限堆积。
- 超过容量时，`AgentInbox.send` 必须抛出明确异常并 emit `AgentInboxBackpressureEvent`；任务状态保留在 `TaskTable`，后续仍可由 `check_agent_tasks` 读取。
- 第一版不做 oldest-result eviction，避免静默丢失 result。

## Typed Events

新增事件放在 `events/types.py`，通过 `EventBus.emit()` 记录：

```python
@dataclass(frozen=True, slots=True)
class SubagentSpawnedEvent(AgentEvent):
    parent_agent_id: str = ""
    child_agent_id: str = ""
    task_id: str = ""

@dataclass(frozen=True, slots=True)
class AgentTaskDispatchedEvent(AgentEvent):
    from_agent_id: str = ""
    to_agent_id: str = ""
    task_id: str = ""

@dataclass(frozen=True, slots=True)
class AgentTaskCompletedEvent(AgentEvent):
    agent_id: str = ""
    task_id: str = ""
    status: Literal["completed", "failed", "cancelled", "timeout"] = "completed"
    elapsed_seconds: float = 0

@dataclass(frozen=True, slots=True)
class AgentTaskFailedEvent(AgentEvent):
    agent_id: str = ""
    task_id: str = ""
    error: str = ""

@dataclass(frozen=True, slots=True)
class AgentTaskCancelledEvent(AgentEvent):
    agent_id: str = ""
    task_id: str = ""

@dataclass(frozen=True, slots=True)
class AgentInboxBackpressureEvent(AgentEvent):
    agent_id: str = ""
    pending_count: int = 0
    max_pending_envelopes: int = 0

@dataclass(frozen=True, slots=True)
class AgentTaskLateResultReceivedEvent(AgentEvent):
    agent_id: str = ""
    task_id: str = ""
    final_status: Literal["cancelled", "timeout"] = "timeout"

@dataclass(frozen=True, slots=True)
class AgentContinuationFailedEvent(AgentEvent):
    parent_agent_id: str = ""
    task_id: str = ""
    error: str = ""
```

事件不进入默认 prompt，不作为 worker 唤醒机制，不修改 coordinator task table。

## 新增文件结构

```text
src/agentos/multi/
├── __init__.py
├── types.py          # AgentCard, AgentEnvelope, TaskRequest, TaskResult, TaskHandle, TaskRecord
├── registry.py       # AgentRegistry Protocol + InMemoryRegistry
├── inbox.py          # AgentInbox
├── tasks.py          # TaskTable
├── spawn.py          # SpawnExecutor
├── continuation.py   # TurnNoticeProvider store + local continuation trigger
├── coordinator.py    # AgentCoordinator 薄编排门面
├── tools.py          # AgentCoordinationTools 注册 spawn/dispatch/check/cancel
└── expert.py         # ExpertAgentRunner
```

新增测试文件：

```text
tests/multi/
├── test_registry.py
├── test_inbox.py
├── test_task_table.py
├── test_spawn_executor.py
├── test_coordinator_spawn.py
├── test_coordinator_dispatch.py
├── test_continuation.py
├── test_coordination_tools.py
└── test_expert_runner.py
```

需要更新的现有测试：

```text
tests/architecture/test_public_api.py
tests/runtime/test_typed_events.py
```

## 验收标准

| # | 验收项 | 涉及模块 |
|---|--------|---------|
| 1 | `InMemoryRegistry` 注册/注销/发现 `AgentCard`，不保存 runtime 对象 | `multi/registry.py` |
| 2 | `discover` 要求 capability 全量匹配，空 capability 返回所有 card | `multi/registry.py` |
| 3 | `AgentInbox` send/collect/wait，无轮询唤醒，缺失 inbox fail-closed | `multi/inbox.py` |
| 4 | `AgentInbox` 容量上限和 backpressure 行为明确，溢出不静默丢 result | `multi/inbox.py` |
| 5 | `TaskTable` 覆盖 queued/running/completed/failed/cancelled/timeout 状态转换 | `multi/tasks.py` |
| 6 | timeout 后 late result 写入 `TaskRecord.late_result`，不覆盖 final status | `multi/tasks.py` |
| 7 | `SubagentFactory` 接收 `SubagentInitRequest(context_strategy="isolated")` | `multi/types.py`, `multi/coordinator.py` |
| 8 | `AgentCoordinator.spawn()` 同步返回 `TaskHandle`，后台运行并回送 result | `multi/coordinator.py`, `multi/spawn.py` |
| 9 | Spawned subagent 自动注册、运行、完成后注销并清理 inbox | `multi/coordinator.py`, `multi/spawn.py` |
| 10 | Spawn 超过 3 个时排队，不超过并发上限 | `multi/spawn.py`, `multi/tasks.py` |
| 11 | `AgentCoordinator.dispatch()` 按 capability 找到未饱和 expert 并发送 task | `multi/coordinator.py` |
| 12 | `ExpertAgentRunner` 阻塞等待 inbox，收到 task 后回送 result | `multi/expert.py` |
| 13 | `check_agent_tasks` 通过 tool result 暴露 task 状态和结果 | `multi/tools.py` |
| 14 | `cancel_agent_task` 能取消 queued task，并对 running task 标记 best-effort | `multi/tasks.py`, `multi/tools.py` |
| 15 | 协调工具从 `ToolRegistry` 投影到 provider tools 和 Capability Plane | `multi/tools.py`, `capabilities/registry.py` |
| 16 | continuation turn 不追加 user message，runtime notice 作为独立 transient projection 渲染并在 early close 时清理 | `runtime/query_loop.py`, `context/runtime.py`, `context/renderer.py` |
| 17 | parent user turn 与 continuation turn 互斥，避免并发进入同一 `QueryLoop` | `runtime/agent.py` |
| 18 | `ContinuationTrigger` 在 task 终态后触发 parent continuation，late result 不触发 | `multi/continuation.py`, `multi/coordinator.py` |
| 19 | continuation 空 notice no-op，不创建空 turn、不调用 provider | `runtime/query_loop.py`, `tests/runtime/test_agent_stream_api.py` |
| 20 | continuation turn 的 `TurnStartedEvent.is_continuation=True`，普通 turn 默认为 false | `events/types.py`, `runtime/query_loop.py` |
| 21 | `LocalContinuationTrigger` 记录 continuation 失败，并可 emit `AgentContinuationFailedEvent` | `multi/continuation.py`, `events/types.py` |
| 22 | `AgentCoordinator` 隔离 continuation trigger 唤醒失败，不改变 terminal task result | `multi/coordinator.py`, `tests/multi/test_continuation.py` |
| 23 | FakeProvider + Spawn 模式端到端测试通过 | `tests/multi/test_coordinator_spawn.py` |
| 24 | FakeProvider + Spawn completion -> continuation -> `check_agent_tasks` 端到端测试通过 | `tests/multi/test_continuation.py` |
| 25 | FakeProvider + Discover & Dispatch 模式端到端测试通过 | `tests/multi/test_coordinator_dispatch.py` |
| 26 | 默认 prompt 不出现 `agent_id`、`task_id`、`envelope_id` 等 runtime metadata，除非作为 tool result 或短 runtime notice handle | renderer golden / integration tests |

## Out Of Scope

- `A2AAdapter`、`StaticResolver`、`ServiceResolver`、remote channel。
- 持久化 registry、心跳 TTL、健康检查、session affinity。
- 完整权限降级策略、只读/写入工具分类、sandbox 隔离执行。
- async provider/tool loop。
- peer-to-peer 对等多 agent 辩论。
- MoA 纯推理合成。
- 把 subagent result 正文自动注入消息历史或 working state。
- 远程 worker 完成后的跨进程 continuation wakeup。

## 后续实现验证

实现完成前必须运行：

```text
pytest tests/multi tests/runtime/test_typed_events.py tests/architecture/test_public_api.py
pytest
python -m compileall -q src tests
git diff --check
rg -n "append[_]notification|Provider[C]onfig|Tool[D]efinition|Agent[R]untime" src/agentos tests
rg -n "class Message[B]us|src/agentos/multi/bus[.]py|test[_]bus" src/agentos tests
```

如果最后一条 drift search 有命中，必须确认它是刻意保留还是旧 draft 残留。
