# AgentOS 单一异步 QueryLoop 设计规范

> 状态：已确认
>
> 日期：2026-07-12
>
> 目标版本：`0.2.0a1`
>
> 上位规范：`2026-07-10-agentos-next-generation-sdk-architecture-design.md`、`2026-07-10-agentos-context-protocol-v1-design.md`、`2026-07-11-agentos-message-provider-boundary-contract-addendum.md`

## 1. 目的

AgentOS 当前同时维护同步 `QueryLoop` 和异步 `AsyncQueryLoop`。两者都拥有 Provider/Tool 控制流，异步实现还通过 `sync_loop` 调用同步实现的私有方法。这造成以下问题：

- 同一 Turn 语义存在两个实现真值源；
- retry、stream、hook、事件、取消和资源释放需要双份维护；
- `Agent` 必须在运行时判断 Loop 是同步还是异步，并提供 `run/async_run/stream/async_stream` 四套入口；
- `AgentBuilder` 和 Runtime Profile 需要暴露 `build/build_async`、`loop_mode` 等组装分支；
- 同步控制流限制了 Provider、Tool、MCP、Skill、Channel 和分布式运行时的自然组合；
- `query_loop.py` 与 `async_query_loop.py` 已分别达到 790 行和 496 行，重复职责继续增长会放大架构漂移。

本规范把 AgentOS Kernel 收敛为一个原生异步执行核心。Streaming 与非 Streaming 只是同一事件流的两种输出投影，不再是两条执行路径。

## 2. 设计结论

冻结以下决策：

1. Kernel 只保留一个 `QueryLoop`，它是原生异步实现。
2. 删除完整的同步 `QueryLoop` 控制流，不保留兼容副本。
3. 删除 `AsyncQueryLoop` 名称及其 `sync_loop`、同步私有方法委托和双 Loop parity 结构。
4. `QueryLoop` 只提供一个执行入口：`execute(request)`。
5. `Agent` 只提供一个执行入口：`await agent.run(input, stream=...)`。
6. `stream=False` 必须消费与 `stream=True` 完全相同的 `TurnStreamEvent` 流。
7. `AgentBuilder` 只保留 `build()`；构建结果天然是 async-first Agent。
8. Provider 与 Tool 可以是原生异步实现，也可以是同步实现；同步实现通过 Kernel 边界的 executor/bridge 适配，不产生第二个 Loop。
9. 同步用户体验放在 `agentos.sync` 适配层，不进入 Kernel，也不恢复同步 QueryLoop。
10. 同一 QueryLoop/Agent 实例同一时间只允许一个活跃执行，冲突立即抛出 `AgentBusyError`，不做隐藏排队。

## 3. 非目标

本次迁移不负责：

- 改变 Context Protocol、SystemEnvelope 或 ContextSnapshot；
- 改变 ProviderRequest 每次 attempt 重建、receipt consumption 或 retry 规则；
- 引入 Planner、Skill、ArtifactStore、DAG 或分布式执行新语义；
- 推断 Tool 之间的隐藏依赖；
- 修改 Level 1 并行 Tool 的 `EXCLUSIVE/PARALLEL_SAFE` 声明模型；
- 新增 continuation notice 的 LLM 可见协议；
- 宣称能够强制终止已经在线程中运行的同步 Provider/Tool；
- 为被删除的旧 API 建立长期兼容层。

## 4. 架构边界

```text
Application / Channel / Multi-agent
                 |
                 v
              Agent.run
                 |
                 v
        QueryLoop.execute(RunRequest)
                 |
                 v
             AgentStream
                 |
       +---------+----------+
       |                    |
       v                    v
ProviderAttemptRunner   ToolCallScheduler
       |                    |
       v                    v
Provider async/sync     Tool async/sync
adapter boundary        adapter boundary
```

所有 Provider、Tool、Hook、Message 和 Context 生命周期都只经过 `QueryLoop.execute()` 这一条路径。

职责保持不变：

- `Agent`：公共 API、输入标准化和结果投影；
- `QueryLoop`：Turn 编排、Provider 循环和 Tool 批次交接；
- `AgentStream`：单次执行的事件消费、关闭、取消和执行租约；
- `ProviderAttemptRunner`：单个 Provider attempt、fallback、retry 和 request receipt；
- `ToolCallScheduler`：一个 Tool 批次的顺序、并发上限和结果排序；
- `ProviderRequestBuilder`：每次 Provider attempt 从权威状态重新构建请求。

`QueryLoop` 仍然不得拼 Prompt、直接执行具体 Tool、访问基础设施 Adapter、拥有持久化真值或定义分布式语义。

## 5. Run 输入模型

### 5.1 公共输入

普通文本保留最短调用形式：

```python
result = await agent.run("分析当前任务")
```

需要附件时使用强类型输入，不再把 `list[object]` 暴露为稳定 API：

```python
@dataclass(frozen=True, slots=True)
class UserTurnInput:
    content: str
    attachments: tuple[Attachment, ...] = ()


@dataclass(frozen=True, slots=True)
class LocalContinuationInput:
    pass


RunInput: TypeAlias = str | UserTurnInput | LocalContinuationInput
```

规则：

- `str` 在 `Agent` 边界标准化为 `UserTurnInput(content=value)`；
- `attachments` 只能由 `UserTurnInput` 携带；
- 空普通文本是否允许沿用现有 MessageRuntime 规则，不在本规范重新定义；
- `LocalContinuationInput` 只表示当前 Level 1 本地 notice continuation，不追加 user StoredMessage；
- continuation notice 仍从当前权威 `TurnNoticeProvider` 读取；
- 没有待处理 notice 时抛出 `ContinuationUnavailableError`，不得返回一个看似成功的空结果；
- 本规范不批准新的 continuation notice Provider 投影格式。现有投影若有缺口，必须由独立协议 Spec 解决。

`LocalContinuationInput` 不能用于 Durable Resume/Wakeup。持久化恢复使用独立命令边界：

```python
RunCommandKind = Literal["resume", "wakeup", "retry", "hitl_answer"]


@dataclass(frozen=True, slots=True)
class DurableRunCommand:
    run_id: str
    command_id: str
    kind: RunCommandKind
    payload: FrozenJsonObject
```

Durable Runtime 必须先通过权威 Run Store 原子执行以下动作：按 `command_id` 去重、校验当前 Run 为 `WAITING`、拒绝终态、应用 `WAITING -> QUEUED`、重新 hydrate Session/Run 状态。成功后生成内部 `AcceptedContinuationInput`，至少携带 `run_id`、`command_id`、`kind` 和已接受的 aggregate version，再交给 Agent 创建 Continuation Turn 并执行 `QUEUED -> RUNNING`。

```python
@dataclass(frozen=True, slots=True)
class AcceptedContinuationInput:
    run_id: str
    command_id: str
    kind: RunCommandKind
    aggregate_version: int
```

原始 `DurableRunCommand` 不直接传给 QueryLoop，QueryLoop 不访问具体 Store，也不重新实现命令幂等。`RunCommandRuntime`/权威 Store 的具体公共协议和 Adapter 实现在 Distributed/Durable 阶段的独立 Spec 中落地；届时 `Agent.run()` 的 `RunInput` 才按 minor 版本增加已实现的 durable command 输入，并由 Agent 在进入 QueryLoop 前换取 `AcceptedContinuationInput`。本次单 Loop 迁移不导出这些尚未实现的 durable 类型，只冻结上述接入边界，不伪装已经实现 durable resume。

### 5.2 Kernel 请求

`Agent` 把公共输入标准化为不可变请求：

```python
@dataclass(frozen=True, slots=True)
class RunRequest:
    input: UserTurnInput | LocalContinuationInput
    options: RunOptions = field(default_factory=RunOptions)
```

`RunRequest` 只描述一次 QueryLoop 执行切片/Turn 请求。durable Run 的身份、聚合状态和跨切片生命周期属于权威 Run Runtime。Provider attempt 的 `ProviderRequest` 仍由 `ProviderRequestBuilder` 在每次物理调用前重新构建，三者不能合并。

### 5.3 Run outcome 与 WAITING

`WAITING` 是当前执行切片的非终态结果，不能伪装为 completed，也不能触发 `RunProtocolError`：

```python
WaitReasonKind = Literal[
    "human_input",
    "timer",
    "remote_result",
    "resource_availability",
    "retry_backoff",
]


@dataclass(frozen=True, slots=True)
class WaitReason:
    kind: WaitReasonKind
    handle: str
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class AgentResult:
    content: str


@dataclass(frozen=True, slots=True)
class AgentWaiting:
    run_id: str
    reason: WaitReason


RunOutcome: TypeAlias = AgentResult | AgentWaiting
```

事件层新增：

```python
@dataclass(frozen=True, slots=True)
class TurnStreamWaiting:
    run_id: str
    reason: WaitReason
```

当 Extension 请求等待时，拥有 Run 状态的 Runtime 必须先把类型化原因提交到权威状态并完成 `RUNNING -> WAITING`，QueryLoop 随后发出 `TurnStreamWaiting`、退出当前执行切片、清理 Turn 资源并释放执行租约。Level 1 没有权威 Run Store 时，不得声称 durable WAITING；对应能力未配置却请求等待时抛出稳定的 `WaitingUnsupportedError`。

`TurnStatus` 同步扩展为 `running/completed/waiting/failed/cancelled`。进入 WAITING 时当前 Turn 标记为 `waiting` 并结束；唤醒后创建新的 Continuation Turn，不复用原 Turn 对象或暂停前的 Provider 对话。

## 6. QueryLoop 契约

唯一入口为：

```python
class QueryLoop:
    async def execute(
        self,
        request: RunRequest,
    ) -> AgentStream:
        ...
```

`AgentStream` 是公开的具体返回类型，同时实现 `AsyncIterator[TurnStreamEvent]`、`AsyncContextManager` 和 `aclose()`。不得把公共返回注解降级为无法表达关闭契约的裸 `AsyncIterator`。

`execute()` 在返回前完成：

1. 校验 `RunRequest`；
2. 尝试获得该 QueryLoop 的执行租约；
3. 若已有活跃 Run，立即抛出 `AgentBusyError`；
4. 创建本次执行的内部异步事件生成器；
5. 返回持有执行租约的 `AgentStream`。

执行租约由 QueryLoop 拥有，`Agent` 不维护第二把语义重复的锁。执行门闩必须是 event-loop-neutral 的立即 try-acquire 状态，不能使用会把空闲 Agent 永久绑定到某个 event loop 的隐藏 `asyncio.Lock`。活跃执行单独记录消费 Task 与所属 event loop，用于 thread-safe interrupt。这样直接调用低层 `QueryLoop.execute()` 与通过 `Agent.run()` 调用具有相同并发保证，并允许空闲 Agent 被长生命周期 `SyncAgent` 正确托管。

`execute()` 返回前不得启动后台 Provider/Tool 生产任务。事件流采用 pull-based backpressure：第一次 `__anext__()` 才开始 Turn 控制流，后续每次消费推进执行；实现不得用无界后台队列预取事件。`stream=False` collector 在获得 Stream 后立即开始消费。

内部事件生成器是唯一控制流真值源，负责：

```text
校验并分派已标准化的 Turn 输入
-> 创建 TurnState
-> 写入 user message 或加载 continuation notice
-> Provider attempt
-> 写入 assistant/tool messages
-> 调度 Tool 批次
-> 重复 Provider attempt
-> 完成/失败/取消 Turn
-> 清理 Turn 级临时资源
```

禁止重新引入：

- `run_turn()`；
- `run_turn_stream()`；
- `run_continuation_stream()`；
- 同步 iterator 版本的执行方法；
- 通过继承共享两套可变控制流；
- 从另一种 Loop 调用私有方法的 facade。

## 7. Agent 公共 API

### 7.1 统一 run 方法

```python
@overload
async def run(
    self,
    input: RunInput,
    *,
    stream: Literal[False] = False,
    options: RunOptions | None = None,
) -> RunOutcome: ...


@overload
async def run(
    self,
    input: RunInput,
    *,
    stream: Literal[True],
    options: RunOptions | None = None,
) -> AgentStream: ...


@overload
async def run(
    self,
    input: RunInput,
    *,
    stream: bool,
    options: RunOptions | None = None,
) -> RunOutcome | AgentStream: ...


async def run(
    self,
    input: RunInput,
    *,
    stream: bool = False,
    options: RunOptions | None = None,
) -> RunOutcome | AgentStream: ...
```

使用方式：

```python
outcome = await agent.run("分析代码", stream=False)

stream = await agent.run("分析代码", stream=True)
async with stream:
    async for event in stream:
        ...
```

`stream` 的默认值是 `False`。`thinking/show_thinking` 等交互策略统一进入 `RunOptions`，不继续扩张 `Agent.run()` 的关键字参数列表。

### 7.2 单一执行路径

`stream=False` 的实现必须是：

```text
await QueryLoop.execute(request)
-> 消费 AgentStream 的全部 TurnStreamEvent
-> 从 TurnStreamCompleted 生成 AgentResult
-> 或从 TurnStreamWaiting 生成 AgentWaiting
-> finally: aclose()
```

它不得调用 Provider `complete()` 建立捷径，也不得绕过 delta、Tool、Hook、EventBus、retry、receipt 或 cleanup 事件。

成功流必须恰好包含一个 outcome terminal event：`TurnStreamCompleted` 或 `TurnStreamWaiting`。两者都没有、重复出现或同时出现时，collector 抛出 `RunProtocolError`。

失败顺序在本规范中固定为：非 cancellation 异常先产生且仅产生一个 `TurnStreamFailed(error)`，随后向消费者重新抛出同一异常对象；`stream=False` collector 也向调用方传播该异常，不返回 `AgentResult`。外部 `CancelledError` 按第 9 节直接传播，不强制补发 terminal event。

### 7.3 删除的 Agent 方法

以下方法删除且不保留同名转发：

- `async_run()`；
- `stream()`；
- `async_stream()`；
- `run_continuation()`；
- `stream_continuation()`；
- `stream_jsonl()`；
- `stream_sse()`；
- `run_with_callbacks()`。
- `clear_interrupt()` 以及 sticky `interrupted` 状态属性。

替代方式：

- 普通结果：`await agent.run(input)`；
- 事件流：`await agent.run(input, stream=True)`；
- 本地 notice continuation：`await agent.run(LocalContinuationInput())`；
- SSE/JSONL：对 `AgentStream` 使用 `iter_sse()`/`iter_jsonl()` 异步序列化函数；
- callback：消费 `AgentStream` 并由应用分发事件。
- interrupt：`agent.interrupt()` 返回本次是否命中活跃 Run，无需手工 clear。

序列化与 callback 不拥有执行，只投影现有事件流。

`agentos.runtime.stream_serializers` 保留现有稳定的逐事件 `event_to_json()`/`event_to_sse()`，并新增以下稳定异步 iterator：

```python
async def iter_jsonl(
    events: AsyncIterator[TurnStreamEvent],
    *,
    show_thinking: bool = False,
) -> AsyncIterator[str]: ...


async def iter_sse(
    events: AsyncIterator[TurnStreamEvent],
    *,
    show_thinking: bool = False,
) -> AsyncIterator[str]: ...
```

两者从 `agentos.runtime` 稳定导出，只消费调用方传入的事件流，不创建 Agent、Run、Task 或后台队列。调用方提前停止序列化时仍负责关闭原始 `AgentStream`，标准示例必须把 serializer 放在 `async with stream` 内。

## 8. AgentStream 生命周期

`AgentStream` 是一次 Run 的资源句柄，必须提供：

```python
class AgentStream(AsyncIterator[TurnStreamEvent]):
    async def __aenter__(self) -> "AgentStream": ...
    async def __aexit__(self, exc_type, exc, tb) -> None: ...
    def __aiter__(self) -> "AgentStream": ...
    async def __anext__(self) -> TurnStreamEvent: ...
    async def aclose(self) -> None: ...
```

内部状态机固定为：

```text
CREATED -- first __anext__ --> RUNNING
CREATED -- aclose/interrupt --> CLOSING --> CLOSED
RUNNING -- complete/fail/wait --> CLOSING --> CLOSED
RUNNING -- aclose/interrupt/cancel --> CLOSING --> CLOSED
```

状态转换、consumer Task 注册、pre-start cancellation marker 和 cleanup coordinator/completion 创建必须在同一个原子临界区完成。`__anext__()` 与 `aclose()` 竞态的胜负固定为：

- `__anext__()` 先把 `CREATED -> RUNNING` 并注册 consumer Task 时，若 close 由同一个 consumer Task 调用，则只原子进入 `CLOSING`，由当前 Task 内联关闭 generator、完成资源 cleanup 并解析共享 completion；不得取消、创建 Task 等待或 await 自己；
- 若 close 来自其他 Task，则原子进入 `CLOSING`、取消 consumer Task，并等待同一个 cleanup completion；consumer 的 `finally` 是首选 cleanup owner，无法接管时才由外部 closer 接管；
- close/interrupt 先把 `CREATED -> CLOSING` 时，后到的 `__anext__()` 不得启动 Turn，等待或观察同一个 cleanup 后结束；
- 所有 close/cancel/fail 路径共享一个幂等 cleanup coordinator 和 completion，不允许各自重复清理；
- cleanup 未完成前状态不能进入 `CLOSED`，执行租约不能释放；
- 已提交到 executor 的同步 Provider/Tool Future 必须被跟踪并收敛，才能完成 cleanup 和释放租约。

冻结以下语义：

- `AgentStream` 创建时已经持有 QueryLoop 执行租约；
- Provider/Tool 控制流在第一次 `__anext__()` 时启动，不在 `AgentStream` 构造时后台运行；
- 正常消费到末尾时自动关闭并释放租约；
- 提前退出必须通过 `async with` 或显式 `await aclose()` 释放；
- `aclose()` 幂等；
- 未开始消费的 Stream 也必须可以关闭并释放租约；
- 一个 Stream 只允许一个消费 Task 调用 `__anext__()`；其他 Task 尝试消费时抛出 `AgentStreamConsumerError`；
- 其他 Task 可以调用 `aclose()`，此时请求取消当前消费者并等待清理完成；
- 关闭后再次迭代直接结束，不重新启动 Run；
- 已关闭 Stream 再次进入 `async with` 时抛出 `AgentStreamClosedError`；
- `AgentStream` 不进入 MessageStore、SessionSnapshot 或前端 Read Model。

应用持有 `stream=True` 返回值但既不消费也不关闭属于调用方资源泄漏。SDK 文档和测试必须把 `async with` 作为标准用法。

## 9. 并发与取消

### 9.1 Agent/Session 并发

- 一个 QueryLoop/Agent 实例同一时间只允许一个活跃 Run；
- 冲突立即抛出 `AgentBusyError`，不等待、不排队；
- 跨进程或多个 Agent 实例的 Session 互斥仍由 Lease/Session Provider 负责；
- Tool 批次内部并发不受该限制影响，仍由 `ToolCallScheduler` 管理；
- 不允许通过创建第二个 Loop 绕过 Session 所有权。

### 9.2 取消

取消来源包括：

- 消费 Task 被取消；
- `AgentStream.aclose()`；
- `Agent.interrupt()`；
- Channel 或上层 Runtime 的显式取消。

`Agent.interrupt()` 只作用于当前执行租约，返回 `True` 表示已向活跃 Run 发出中断，返回 `False` 表示当前空闲。若 Stream 尚处于 `CREATED`，interrupt 原子设置 pre-start cancellation、进入 `CLOSING` 并在 execute 时记录的 event loop 上调度同一个 cleanup task，后续消费不得启动 Turn。中断状态属于本次 Run 的 cancellation scope，成功清理后自动销毁；不得保留会污染下一次 Run 的 sticky `_interrupted` 标志，因此删除公共 `clear_interrupt()`。

规则：

- 取消必须停止启动新的 Provider attempt 和新的 Tool 调用；
- 已创建的 asyncio Task 必须取消并 await；
- 原生异步 Provider/Tool 必须收到 asyncio cancellation；
- 同步 Provider/Tool 在线程中运行时只支持协作式取消，SDK 不承诺强制杀线程；
- 活跃同步调用结束后必须完成 bridge 回收，但取消后不得继续启动新的副作用；
- Turn 级 attachment/context mount、runtime notice 和临时资源必须在 `finally` 清理；
- 执行租约必须在成功、失败、取消和未消费关闭四条路径释放；
- `Agent.interrupt()` 从其他线程调用时必须通过事件循环的 thread-safe 调度取消活跃 Task。

Python 原生 Task cancellation 可以直接传播 `CancelledError`。只有在 Runtime 主动、安全地检测到中断且仍能产生事件时才发出 `TurnStreamCancelled`；不得为了补发事件吞掉外部 cancellation。

## 10. Provider 统一适配

删除同步 `ProviderAttemptRunner`，把当前异步 Runner 收敛为唯一 `ProviderAttemptRunner`。

Provider 能力优先级保持：

```text
async_stream
-> async_complete
-> sync stream（executor/iterator bridge）
-> sync complete（executor）
```

无论使用哪种 Provider 能力，都必须遵守相同的 attempt 时序：

```text
request_factory
-> before_provider_call
-> Provider call/stream
-> after_provider_call
-> usability validation
-> receipt consumption
```

以下 Phase 2 契约不变：

- 每次 retry attempt 重新构建 ProviderRequest；
- before hook 替换请求对象时清空 receipt；
- 发出首个用户可见 delta 后不得 retry；
- 成功流最后一个 Provider 事件是携带最终已校验响应的 `ProviderStreamCompleted`；
- 无 completion、timeout、cancel、hook deny 和不可用响应不消费 temporary refs。

不得保留 `AsyncProviderAttemptRunner` alias，也不得保留同步 Runner 作为 fallback。

## 11. Tool 统一适配

QueryLoop 只调用异步 Tool 路由边界。标准顺序为：

```text
async_execute_tool_call
-> 若只有同步 execute_tool_call，则放入受控 executor
```

Tool 仍可以由同步函数实现。同步实现的适配发生在 `ToolExecutor/ExecutionBackend` 边界，不发生在 QueryLoop 中，也不产生同步 Tool control loop。

Level 1 Tool 批次规则保持：

- 默认 `EXCLUSIVE`；
- 显式 `PARALLEL_SAFE` 才允许并发；
- 并发上限有界，默认 8；
- Runtime 不推断隐藏依赖或构建 DAG；
- Tool Result 按 Provider 原始 Tool Call 顺序写回；
- MessageRuntime 只在协调边界串行修改。

## 12. Builder 与 Profile

`AgentBuilder`：

- `build()` 构建唯一 async-first Agent；
- 删除 `build_async()`；
- 不增加 `async_mode`、`sync_mode` 或 Loop class 选择参数；
- Builder 只组装依赖，不创建或拥有 event loop。

Runtime Profile：

- `LocalRuntimeProfile.loop_mode` 删除；
- `LocalRuntimeProfile.build_agent()` 始终返回 async-first Agent；
- `AgentBuilderLike` 删除 `build_async()` 协议成员；
- Web、Distributed、Multi-agent Profile 不得重新引入 Loop 模式分支。

## 13. 同步适配层

为了保留 CLI、脚本和非 asyncio 应用的易用性，本阶段必须提供 `agentos.sync`，但它不是 Kernel API。

推荐公共形式：

```python
from agentos.sync import SyncAgent

with SyncAgent(agent) as sync_agent:
    result = sync_agent.run("hello", stream=False)

    with sync_agent.run("hello", stream=True) as stream:
        for event in stream:
            ...
```

同时必须提供一次性 convenience function：

```python
from agentos.sync import run

result = run(agent, "hello")
```

返回重载固定为：

```python
class SyncAgent:
    @overload
    def run(
        self,
        input: RunInput,
        *,
        stream: Literal[False] = False,
        options: RunOptions | None = None,
    ) -> RunOutcome: ...

    @overload
    def run(
        self,
        input: RunInput,
        *,
        stream: Literal[True],
        options: RunOptions | None = None,
    ) -> SyncAgentStream: ...

    @overload
    def run(
        self,
        input: RunInput,
        *,
        stream: bool,
        options: RunOptions | None = None,
    ) -> RunOutcome | SyncAgentStream: ...

    def run(
        self,
        input: RunInput,
        *,
        stream: bool = False,
        options: RunOptions | None = None,
    ) -> RunOutcome | SyncAgentStream: ...


@overload
def run(
    agent: Agent,
    input: RunInput,
    *,
    stream: Literal[False] = False,
    options: RunOptions | None = None,
) -> RunOutcome: ...


@overload
def run(
    agent: Agent,
    input: RunInput,
    *,
    stream: Literal[True],
    options: RunOptions | None = None,
) -> SyncAgentStream: ...


@overload
def run(
    agent: Agent,
    input: RunInput,
    *,
    stream: bool,
    options: RunOptions | None = None,
) -> RunOutcome | SyncAgentStream: ...


def run(
    agent: Agent,
    input: RunInput,
    *,
    stream: bool = False,
    options: RunOptions | None = None,
) -> RunOutcome | SyncAgentStream: ...
```

同步层规则：

- `SyncAgent` 在专用 owner thread 中启动一个长生命周期 `asyncio.Runner` 和 dispatcher coroutine；所有调用通过 thread-safe submission 进入该 event loop；
- 非 asyncio 调用线程可以安全提交；两个并发 Run 仍由 QueryLoop 的单执行租约决定，后到者得到 `AgentBusyError`，不能并发驱动 Runner；
- 从 owner thread 内重入同步 API 抛出 `SyncAdapterReentryError`；从已经运行 event loop 的其他调用线程进入同步 API 抛出 `SyncAdapterEventLoopError`；
- `SyncAgentStream` 拥有对应 `AgentStream`，并提供同步 iterator/context manager/幂等 `close()`；
- `SyncAgentStream` 在第一次 `__next__()` 时绑定唯一消费线程，其他线程消费抛出 `SyncStreamConsumerError`；跨线程 `close()` 允许并等待 owner loop 上的 cleanup；
- `SyncAgent.close()` 必须先关闭活跃 Stream，再关闭 Runner；
- convenience `run()` 的非流式调用创建临时 `SyncAgent`，并在返回或抛错前关闭；
- convenience `run(..., stream=True)` 返回的 `SyncAgentStream` 必须拥有临时 `SyncAgent`，直到 Stream 关闭后再关闭其 Runner 和 owner thread；
- 一次性 convenience function 每次调用都创建临时 `SyncAgent`；需要复用 loop-bound Provider client 或其他异步资源时必须使用长生命周期 `SyncAgent`；
- `SyncAgent.close()` 和 `SyncAgentStream.close()` 都必须线程安全且幂等；关闭后的调用抛出 `SyncAgentClosedError`；
- 同步层不得构造 QueryLoop、复制 Provider/Tool 控制流或调用 QueryLoop 私有方法；
- `agentos.sync` 不从 root `agentos` facade 导出，避免把 Kernel 重新描述为同步/异步双形态。

`agentos.sync` 是受 Public API inventory 管理的稳定子模块，稳定导出包含 `SyncAgent`、`SyncAgentStream`、`run` 及其 `Sync*Error` 类型，不导出 Kernel 组件。

## 14. Channel 与 Multi-agent 迁移

所有消费者必须迁移到统一入口：

- `HttpAgentChannel.handle_turn()` 改为 `async def`，直接 `await agent.run(..., stream=False)`；
- `SseAgentChannel.stream_turn()` 改为 `AsyncIterator[str]`，在 `async with AgentStream` 内使用 `iter_sse()`；
- `A2ATaskRunner.run_task()` 与 `A2AServerAdapter.handle_task()` 改为 async；ASGI/A2A operation 路径直接 await；
- 本地线程型 `AgentCoordinator` 不再保存裸 `Agent`，`attach_agent()` 接收应用创建并共享的长生命周期 `SyncAgent`；SubagentFactory 创建的 ephemeral `Agent` 由 Coordinator 包装为自己拥有的 `SyncAgent`，detach 时关闭；
- `LocalContinuationTrigger` 接收与 Coordinator 相同的 `Mapping[str, SyncAgent]`，只借用、不关闭；`shutdown()` 只回收自身 executor；
- 分布式 async worker 持有裸 `Agent` 并直接 await，不经过 `agentos.sync`；
- stream serializer 只转换 `AsyncIterator[TurnStreamEvent]`，不拥有 Agent 执行；
- Channel 断连必须关闭 `AgentStream`，不能只停止读取；
- Durable session release 必须发生在 Stream 关闭和 QueryLoop 执行租约释放之后。

迁移不能通过 `hasattr()` 探测 `run_turn_stream`、`async_stream` 或同步 fallback。新的调用契约必须是静态可读、强类型且唯一的。

## 15. 公共 API 与版本策略

当前文档把 `QueryLoop` 与 `AsyncQueryLoop` 都列为 stable，但项目尚未生产推广，且目标架构仍处于 `0.2.0a1` 预发布收敛阶段。本次采用明确的 breaking reset：

- 保留 `QueryLoop` 名称，但把其语义改为唯一异步 Loop；
- 删除 `AsyncQueryLoop`；
- 删除 `AgentBuilder.build_async()`；
- 删除 Agent 的同步/异步重复方法；
- 新增 `RunRequest`、`UserTurnInput`、`LocalContinuationInput`、`RunOutcome`、`AgentStream`、WAITING 类型和稳定错误类型；
- 同步更新 `docs/api-stability.md`、`docs/public-api-stability.json` 和生成的 inventory；
- 同步更新 `CHANGELOG.md` 和迁移文档；
- `pyproject.toml` 与 `agentos.__version__` 统一为目标 `0.2.0a1`；
- 不提供 deprecated alias、`__getattr__` 动态兼容或旧方法转发。

Root `agentos` facade 继续只导出 `Agent`、`AgentBuilder` 和 `QueryLoop` 等最小入口；`AgentResult`、`AgentWaiting`、`RunOutcome`、`WaitReason`、`TurnStreamWaiting`、`AgentStream`、`RunOptions`、已实现的输入类型和执行错误从稳定的 `agentos.runtime` 导入。同步适配类型从稳定的 `agentos.sync` 导入。

迁移表至少包含：

| 旧 API | 新 API |
|---|---|
| `AgentBuilder.build_async()` | `AgentBuilder.build()` |
| `agent.async_run(x)` | `await agent.run(x)` |
| `agent.async_stream(x)` | `await agent.run(x, stream=True)` |
| `agent.run(x)` | `await agent.run(x)` 或 `SyncAgent.run(x)` |
| `agent.stream(x)` | `await agent.run(x, stream=True)` 或 `SyncAgent.run(x, stream=True)` |
| `agent.run_continuation()` | `await agent.run(LocalContinuationInput())` |
| `agent.clear_interrupt()` | 删除；每次 Run 的 cancellation scope 自动清理 |
| `AsyncQueryLoop` | `QueryLoop` |
| `HttpAgentChannel.handle_turn()` | `await channel.handle_turn()` |
| `SseAgentChannel.stream_turn()` | `async for chunk in channel.stream_turn()` |
| `A2AServerAdapter.handle_task()` | `await adapter.handle_task()` |
| `AgentCoordinator.attach_agent(..., Agent)` | `attach_agent(..., SyncAgent)` |

## 16. 文件与职责目标

实现后目标：

| 文件 | 目标 |
|---|---|
| `runtime/query_loop.py` | 唯一 Loop，少于 500 行，只有 Turn/Provider/Tool 编排 |
| `runtime/agent.py` | 少于 250 行，只含 facade、输入标准化和结果 collector |
| `runtime/agent_stream.py` | 少于 250 行，只含 Stream/租约/取消生命周期 |
| `runtime/provider_attempt.py` | 少于 250 行，唯一异步 attempt runner 和 sync Provider bridge |
| `builder.py` | 不增加运行时职责，不超过实施前 287 行基线 |

删除：

- `runtime/async_query_loop.py`；
- `runtime/async_provider_attempt.py`；
- 仅服务同步 QueryLoop 的 bridge、字段和测试 fixture；
- 双 Loop parity 测试。

允许按以下独立职责保留或提取：

- 纯事件映射；
- Hook 调用适配；
- Provider capability adapter；
- Turn lifecycle helper；
- Stream serializer。

不得为了达成行数目标把无关职责集中到新的 `utils.py`，也不得把完整控制流搬到一个新的超大 support 文件。

## 17. 错误契约

至少定义以下稳定错误：

```python
class AgentRunError(RuntimeError): ...
class AgentBusyError(AgentRunError): ...
class AgentStreamConsumerError(AgentRunError): ...
class AgentStreamClosedError(AgentRunError): ...
class ContinuationUnavailableError(AgentRunError): ...
class WaitingUnsupportedError(AgentRunError): ...
class RunProtocolError(AgentRunError): ...
class SyncAdapterEventLoopError(AgentRunError): ...
class SyncAdapterReentryError(AgentRunError): ...
class SyncAgentClosedError(AgentRunError): ...
class SyncStreamConsumerError(AgentRunError): ...
```

Provider、Tool、Hook 和 Policy 的既有领域错误继续保留，不全部包装成 `AgentRunError`。稳定错误只描述 Agent 执行契约本身。

错误文本不得包含 secret、原始附件、本地敏感路径、完整 Provider payload 或未脱敏 Tool arguments。

## 18. 测试要求

### 18.1 核心执行契约

- `QueryLoop.execute()` 返回可关闭的 `AgentStream`；
- `stream=False` 与 `stream=True` 产生相同事件顺序和相同最终内容；
- Tool、多轮 Provider、Hook、retry、receipt 和 compression 路径只执行一次；
- 不存在 Provider complete 快捷路径；
- user turn 与 continuation turn 使用同一 Loop；
- 无 continuation notice 抛出稳定错误。
- `TurnStreamCompleted` 与 `TurnStreamWaiting` 是互斥 outcome；
- WAITING 在提交权威状态后退出并释放资源；未配置等待能力时抛出 `WaitingUnsupportedError`；
- durable command 的重复 `command_id`、终态拒绝和 hydrate-before-run 由后续 Durable contract tests 覆盖，本次不得用 LocalContinuation 冒充。

### 18.2 Stream 生命周期

- 正常耗尽自动释放；
- 早退 `aclose()` 释放；
- 未消费关闭释放；
- CREATED 状态 interrupt 不得启动 Turn；
- `__anext__()`/`aclose()` 竞态只有一个 cleanup coordinator/completion；
- consumer Task 在 `async for` break 后经 `__aexit__` 内联清理，不自取消或自等待；
- consumer Task 内显式 `await stream.aclose()` 可完成清理；
- 重复关闭幂等；
- 第二消费者被拒绝；
- 已关闭 Stream 不能重新进入 context manager；
- 外部 Task 关闭会取消消费者并等待 cleanup；
- 成功、失败、取消都清理 Turn 临时资源；
- Stream 未关闭时第二次 Run 立即 `AgentBusyError`。

### 18.3 Provider/Tool 适配

- native async stream；
- native async complete；
- sync stream bridge；
- sync complete executor；
- async Tool；
- sync Tool executor；
- 同步阻塞任务的协作式取消限制；
- 可见 delta 后不 retry；
- retry 前重建请求；
- completion 缺失与 cleanup。

### 18.4 Consumer 迁移

- Builder 只有 `build()`；
- Local profile 无 `loop_mode`；
- ASGI 断连关闭 Stream；
- SSE/JSONL 序列化不触发新 Run；
- A2A、Coordinator、child agent 和 continuation worker 走统一入口；
- SyncAgent 生命周期与 running-loop 拒绝；
- SyncAgent owner thread、thread-safe submission、reentry、并发 busy 和 SyncAgentStream 单消费者线程；
- root/runtime `__all__` 不再包含 `AsyncQueryLoop`；
- drift search 不存在旧方法和双 Loop import。

### 18.5 验证命令

实施计划必须包含目标测试、受影响模块测试和以下全量验证：

```powershell
python -m pytest -q
python -m compileall -q src tests
python -m ruff check src tests
python scripts/check_module_size_baseline.py
python scripts/generate_public_api_inventory.py --policy docs/public-api-stability.json --output docs/public-api-inventory.json
python -m pytest tests/architecture/test_public_api.py tests/architecture/test_public_api_inventory.py -q
git diff --check
```

并执行旧名漂移检查，目标代码和当前文档不得再把 `AsyncQueryLoop`、`build_async`、`async_run` 或 `async_stream` 描述为可用 API。历史设计文档允许保留原文，但必须有被本规范替代的明确标记。

## 19. 分阶段迁移约束

详细任务拆分由后续 Implementation Plan 定义，但提交边界必须满足：

1. 可以先以未导出、未接管正式执行的私有模块引入输入值类型、Stream 状态机、sync host 和 contract tests；这些基础提交不得形成第二个可调用 Loop，也不得改变 Public API。
2. Public cutover 必须是一个原子提交：同时切换 canonical `ProviderAttemptRunner`、唯一 `QueryLoop`、`Agent`、Builder/Profile、所有直接 Channel/A2A/Multi-agent/Observability 消费者、示例和测试；同一提交删除同步 Loop、`AsyncQueryLoop`、旧 Runner、旧 Agent 方法和探测 fallback。
3. 上述原子提交同时更新 root/runtime/sync 导出、API stability policy、生成 inventory、版本、CHANGELOG 和迁移文档，保证 public API gate 在提交结束时 Green。
4. 后续提交只允许做已通过统一契约后的质量修正、职责拆分和文档补充，不得恢复旧入口。

核心共享文件由单一 Owner 串行修改。Channel、Multi-agent、文档与 Sync adapter 只有在新公共契约冻结后才允许并行开发。

每个提交结束时必须保持仓库 Green，不允许在多个提交之间留下同时存在但语义分叉的两套正式 Loop，也不允许提交一个新 QueryLoop 却让 Agent/Builder 仍构造旧 Loop。

## 20. 被替代的旧决策

本规范只替代旧文档中关于同步/异步双 Loop 拓扑、双 Runner、`build_async()` 和 Agent 重复执行入口的条款。以下文档中的相关描述不再是未来实现依据：

- `2026-07-11-agentos-level1-parallel-tool-calls-design.md` 中要求同步和异步 Loop 双实现的部分；
- `2026-07-10-agentos-message-provider-boundary-implementation-plan.md` 中“双 Loop 同步迁移”和双 Runner 的实施约束；
- `2026-07-10-agentos-context-first-sdk-master-implementation-plan.md` 中保留双 Loop API 的阶段性描述；
- 旧 roadmap、quickstart 和 API 文档中的 `build_async/async_run/async_stream` 示例。

以下契约不被替代：

- Context-first 与双平面 Provider 输入；
- 每次 Provider attempt 重建请求；
- Message/Provider 类型边界；
- Tool Scheduler 的有界 opt-in 并发；
- EventBus 与 HookManager 分离；
- WAITING 退出 Loop、唤醒后创建 Continuation Turn；
- Kernel、Port、Adapter 的依赖方向；
- 文件规模、TDD、双层 Review 和完成纪律。

## 21. 验收标准

本规范落地完成时必须满足：

- 仓库只有一个 `QueryLoop` 控制流实现；
- 不存在 `AsyncQueryLoop`、`sync_loop` 或同步 QueryLoop；
- `Agent` 只有一个 `run()` 执行入口；
- Streaming 与非 Streaming 共享同一事件流；
- completed 与 WAITING 都有独立、互斥、可类型检查的 outcome；
- LocalContinuation 与 Durable Run Command 边界分离，后者保留 `run_id/command_id/kind` 和权威 Store 幂等校验；
- QueryLoop 拥有单一执行租约并显式拒绝并发；
- `AgentStream` 的消费、关闭、取消和 cleanup 契约有确定性测试；
- Provider/Tool 同步实现通过 async 边界适配；
- Builder/Profile 不再暴露 Loop 模式选择；
- Channel、A2A 与 Multi-agent 全部迁移到统一入口；
- 同步便利层位于 `agentos.sync`，且没有第二个 Loop；
- 旧 API 无兼容 alias，迁移说明和 Public API inventory 已同步；
- 目标测试、全量测试、Ruff、compileall、规模门禁和 diff-check 全部通过；
- Spec Compliance Review 与 Code Quality Review 均无阻断项。

## 22. 最终结论

AgentOS 的执行模型应当是“一个异步 Kernel，一条事件流，多个输出适配”，而不是“同步 Kernel 与异步 Kernel 各自实现一次”。

最终形态为：

```text
await agent.run(input, stream=False) -> RunOutcome
await agent.run(input, stream=True)  -> AgentStream

两者
-> 同一个 RunRequest
-> 同一个 QueryLoop.execute
-> 同一个 Provider/Tool 控制流
-> 同一组生命周期、事件、取消和清理语义
```

这为后续 Skill、Plan、Memory、Artifact、HITL、Multi-agent 和分布式 Runtime 提供稳定内核，同时避免未来每增加一种能力都要在同步和异步 Loop 中实现两次。
