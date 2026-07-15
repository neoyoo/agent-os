# AgentOS Level 1 并行工具调用设计

> **SUPERSEDED FOR LOOP TOPOLOGY:** 本文关于同步/异步双 Loop 的条款已被
> `2026-07-12-agentos-single-async-query-loop-design.md` 取代。历史正文保留，
> Tool 并发契约仍有效。

> **2026-07-15 TASK 14 RE-BASELINE:** 第 4.2 节共享深不可变 JSON 值的
> Owner 修订为 Provider 无关的私有内核模块 `agentos._json_values`。
> `agentos.providers.json_values` 与已删除的 `agentos.providers.messages` 不再是
> 有效架构路径，也不得为它们增加兼容层；本修订不改变 Tool 并发语义。

> 状态：已批准，进入实施规划
>
> 日期：2026-07-11
>
> 适用基线：`review/agentos-sdk-architecture-20260611`
>
> 上位设计：[AgentOS 下一代 SDK 架构设计](2026-07-10-agentos-next-generation-sdk-architecture-design.md)

## 1. 结论

AgentOS Level 1 基础 Loop 采用普通 Provider Tool Call 模式，不引入 DAG、Code Mode 或显式依赖表达式。模型可以在同一个 `ProviderResponse` 中返回多个 Tool Call，Runtime 根据工具声明决定并行或串行执行。

并发模型固定为：

```text
ProviderResponse.tool_calls（保持原始顺序）
                |
                v
        ToolCallScheduler
          |           |
          |           +-- PARALLEL_SAFE：最多并行 8 个，超限 FIFO 排队
          |
          +-------------- EXCLUSIVE：独占屏障，不能与同批任何调用重叠
                |
                v
      按 Provider 原始顺序写回 Tool Result
                |
                v
          下一次 Provider 调用
```

`max_parallel_calls` 可以配置，默认值为 `8`。超过上限的调用进入队列等待，不报错、不丢弃，也不创建无界工作线程。

工具默认采用 `EXCLUSIVE`。只有工具作者或受信任 Adapter 显式声明 `PARALLEL_SAFE` 后，Runtime 才允许它与其他 `PARALLEL_SAFE` 工具重叠执行。

## 2. 范围契约

### 2.1 本阶段完成

- 定义工具级并发策略 `ToolConcurrencyPolicy`；
- 在 `RegisteredTool` 上增加强类型并发声明；
- 增加统一的 `ToolCallScheduler` 批次调度边界；
- 同时支持 `QueryLoop` 和 `AsyncQueryLoop`；
- 默认最大并发数为 `8`，支持显式配置；
- 超限调用按原始顺序排队；
- 定义 `EXCLUSIVE` 独占屏障；
- 保证 Tool Result 按 Provider 原始 Tool Call 顺序写回；
- 定义 Hook、Event、Stream Event、错误和取消时序；
- 定义 OpenAI、OpenAI-Compatible、Anthropic 和 MCP 的保守能力映射；
- 保持现有 Tool Result 大小限制、重复调用保护和 ActiveWindow 一致性。

### 2.2 明确不做

- 不构建 DAG；
- 不增加 `depends_on`、`ResultRef` 或调用间变量替换；
- 不让模型在同一批调用中引用前一个调用的运行结果；
- 不做资源级锁，例如按文件、数据库表、租户或工作区加锁；
- 不做进程级、租户级或集群级全局限流；
- 不做分布式 Tool Scheduler；
- 不做 Tool 自动幂等、事务回滚或副作用补偿；
- 不引入 JavaScript Code Mode、`Promise.all()` 或沙箱代码解释器；
- 不修改 Level 2 Planner 和 Level 3 分布式 Runtime 的调度语义。

### 2.3 关键语义限制

`EXCLUSIVE` 只表示“不能重叠执行”，不表示调用之间存在数据依赖。

如果 Tool B 的参数必须使用 Tool A 的运行结果，模型必须采用两轮调用：

```text
Provider 调用 1 -> Tool A -> Tool Result A
Provider 调用 2 -> 模型读取 Result A -> Tool B
```

Runtime 不会推断隐藏依赖，也不会把 Tool A 的结果注入同一批 Tool B 的参数。工具描述应明确告诉模型哪些调用需要等待结果后再继续。

## 3. 设计依据

### 3.1 AgentOS 当前行为

当前 `QueryLoop` 与 `AsyncQueryLoop` 都按 `response.tool_calls` 顺序逐个执行并等待，每一批 Tool Call 完全串行。`ToolCallRouter` 和 `ToolExecutor` 都只负责单个调用，`RegisteredTool` 没有正式并发字段，`ProviderRequest` 也没有并行 Tool Call 意图。

本设计保持以下现有边界：

- `QueryLoop` 只协调一次 Provider 响应产生的 Tool Call 批次；
- `ToolCallRouter` 仍只路由单个 Tool Call；
- `ToolExecutor` 仍只执行单个 Tool Call；
- MessageRuntime 只在协调线程或事件循环中更新；
- Tool Result 在进入 MessageRuntime 前继续应用 token budget；
- Tool 执行异常继续使当前 Turn 失败，不隐式转换为成功文本。

### 3.2 Codex 普通模式参考

Codex 普通 Tool Call 模式采用“工具声明是否支持并发，Runtime 统一控制重叠执行”的方案：默认工具不并发，支持并发的工具可以共享执行许可，不支持并发的工具形成独占边界；普通模式不构建 DAG，依赖通过后续模型轮次完成。

AgentOS 借鉴这一执行思想，但增加一个明确的有界并发数。Codex 普通模式的关键参考位于：

- `codex-rs/tools/src/tool_executor.rs`；
- `codex-rs/core/src/tools/parallel.rs`；
- `codex-rs/core/src/session/turn.rs`；
- `codex-rs/core/src/tools/handlers/mcp.rs`；
- `codex-rs/core/tests/suite/tool_parallelism.rs`。

## 4. 核心类型

### 4.1 ToolConcurrencyPolicy

并发策略使用强类型枚举，不使用裸布尔值，也不从自由格式 `metadata` 中读取：

```python
class ToolConcurrencyPolicy(str, Enum):
    EXCLUSIVE = "exclusive"
    PARALLEL_SAFE = "parallel_safe"
```

语义如下：

| 策略 | 语义 |
|---|---|
| `EXCLUSIVE` | 调用执行期间不能与同一批中的任何其他 Tool Call 重叠。 |
| `PARALLEL_SAFE` | 可以与同批其他 `PARALLEL_SAFE` 调用重叠，但仍受 `max_parallel_calls` 限制。 |

第一阶段不增加第三种策略。特别是，不增加 `READ_ONLY`、`IDEMPOTENT` 或 `RESOURCE_LOCKED`，因为只读、幂等和可并发不是同一个概念。

### 4.2 RegisteredTool

`RegisteredTool` 增加正式字段：

```python
@dataclass(frozen=True, slots=True)
class RegisteredTool:
    name: str
    description: str
    parameters: Mapping[str, FrozenJsonValue]
    handler: ToolHandler | AsyncToolHandler
    kind: ToolKind = "external"
    concurrency_policy: ToolConcurrencyPolicy = ToolConcurrencyPolicy.EXCLUSIVE
    metadata: Mapping[str, FrozenJsonValue] = field(default_factory=frozen_mapping)

    def __post_init__(self) -> None:
        object.__setattr__(self, "parameters", freeze_json_mapping(self.parameters))
        object.__setattr__(self, "metadata", freeze_json_mapping(self.metadata))
```

`FrozenJsonValue` 只允许 JSON 标量、递归只读 `Mapping` 和不可变 `tuple`。`freeze_json_mapping()` 必须 defensive-copy 整个嵌套结构：输入 dict 的后续修改不能改变注册结果，嵌套 list 转为 tuple，嵌套 mapping 复制后只读暴露。Provider Adapter 在序列化边界按需物化新的 JSON dict/list，不得把内部只读对象反向暴露给调用方。

canonical helper 的 Owner 固定为 Provider 无关的私有内核叶子模块 `agentos._json_values`，该模块只依赖 Python 标准库，导出内部使用的 `FrozenJsonValue`、`freeze_json_value()`、`freeze_json_mapping()` 和 `thaw_json_value()`。`agentos.capabilities`、`agentos.messages`、`agentos.providers` 及其他需要冻结 JSON 值的边界可以共同依赖该模块；`messages` 不得反向依赖 `providers`，`providers` 也不得反向依赖 `capabilities`。

Provider 工具 Schema 必须保持同一递归不可变边界：

```python
@dataclass(frozen=True, slots=True)
class ProviderFunctionSpec:
    name: str
    description: str
    parameters: Mapping[str, FrozenJsonValue] = field(default_factory=frozen_mapping)

    def __post_init__(self) -> None:
        object.__setattr__(self, "parameters", freeze_json_mapping(self.parameters))


@dataclass(frozen=True, slots=True)
class ProviderToolSpec:
    function: ProviderFunctionSpec
    type: Literal["function"] = "function"
```

`RegisteredTool.provider_spec()` 把已冻结 schema 交给 `ProviderFunctionSpec`，不得调用浅层 `dict(self.parameters)` 提前 thaw。`provider_tool_spec_to_dict()` 以及各 Adapter payload builder 是唯一允许调用 `thaw_json_value()` 的边界；每次调用都必须递归生成全新的 dict/list，任何一次 payload 修改不得影响 ProviderRequest、RegisteredTool 或下一次序列化结果。

规则：

- 默认值必须是 `EXCLUSIVE`；
- `metadata={"parallel": True}` 等非正式写法不生效；
- `PARALLEL_SAFE` 是工具作者对线程安全、协程安全和副作用隔离的明确承诺；
- 该承诺表示工具可以与任意其他 `PARALLEL_SAFE` 工具重叠，而不只是与同名工具重叠；
- 如果工具依赖共享可变状态、当前工作目录、非线程安全客户端、隐式事务或全局环境变量，应保持 `EXCLUSIVE`；
- 幂等工具不一定可并发，可并发工具也不一定幂等。
- `parameters` 和 `metadata` 在构造时递归复制并冻结；`frozen=True` 不能只冻结字段绑定而保留可变 dict 内容。

示例：

```python
RegisteredTool(
    name="read_document",
    description="读取指定文档的文本内容。",
    parameters={...},
    handler=read_document,
    concurrency_policy=ToolConcurrencyPolicy.PARALLEL_SAFE,
)
```

### 4.3 ToolCallScheduler

新增 `ToolCallScheduler`，它是 Tool 批次执行顺序和并发上限的唯一所有者。

第一阶段公共形态固定为：

```python
@dataclass(slots=True)
class ToolCallScheduler:
    max_parallel_calls: int = 8

    def execute_batch(...) -> tuple[ScheduledToolCallResult, ...]: ...
    async def async_execute_batch(...) -> tuple[ScheduledToolCallResult, ...]: ...
```

构造约束：

- `max_parallel_calls` 必须是大于等于 `1` 的整数；
- `bool` 不视为合法整数配置；
- 默认值为 `8`；
- 非法值在构建 Agent 时立即报 `ValueError`，不能等到第一批 Tool Call 才失败。

Scheduler 负责：

- 保留 Tool Call 原始索引；
- 查询每个调用的并发策略；
- 形成并行段和独占屏障；
- 应用 FIFO 排队和并发上限；
- 管理同步 Future 或异步 Task；
- 传播取消；
- 收集成功、失败和生命周期观测；
- 返回按原始索引排列的结果。

Scheduler 不负责：

- 解析或修改 Tool 参数；
- 执行具体 Tool handler；
- 写 MessageRuntime；
- 拼装 ProviderRequest；
- 推断 Tool 依赖；
- 回滚外部副作用；
- 做跨 Turn 或跨 Agent 的全局公平调度。

### 4.4 ScheduledToolCallResult

Scheduler 返回内部强类型结果，不返回无结构的字典：

```python
@dataclass(frozen=True, slots=True)
class ScheduledToolCallResult:
    index: int
    tool_call: ProviderToolCall
    result: ToolExecutionResult
```

失败通过异常和失败观测传播，不把异常对象伪装成成功的 `ToolExecutionResult`。现有 Tool handler 主动返回错误文本的行为不变，该文本仍视为一次成功执行。

## 5. 调度算法

### 5.1 批次定义

一个批次等于一次 `ProviderResponse` 中的完整 `tool_calls` 元组。批次边界不会跨 Provider 响应、Turn 或 Agent 实例合并。

Scheduler 首先按原始顺序为每个调用分配稳定索引，然后查询策略。它不能按工具名、预计耗时或优先级重排。

### 5.2 独占屏障

`EXCLUSIVE` 调用把批次切分成多个执行段：

```text
[P1, P2, E3, P4, P5, E6, P7]

执行顺序：
1. P1、P2 并行
2. 等待 P1、P2 全部结束
3. E3 独占执行
4. P4、P5 并行
5. 等待 P4、P5 全部结束
6. E6 独占执行
7. P7 执行
```

其中 `P` 表示 `PARALLEL_SAFE`，`E` 表示 `EXCLUSIVE`。

独占调用不能与前面的未完成调用或后面的调用重叠。后续调用不能越过独占调用提前执行。

### 5.3 有界并行和排队

一个连续 `PARALLEL_SAFE` 段最多同时运行 `max_parallel_calls` 个调用。超出的调用按 Provider 原始顺序进入内存 FIFO 队列。

例如上限为 `2`：

```text
[P1, P2, P3, P4]

先启动 P1、P2；
任意一个完成后启动 P3；
再次释放槽位后启动 P4。
```

公平性范围只覆盖当前批次：

- 同一并行段中，等待调用不允许后来的调用插队；
- 独占调用保持原始位置；
- Level 1 不承诺多个并发 Turn、Agent 或进程之间的公平性；
- `max_parallel_calls` 是每个 Scheduler 实例的限制，不是整个 Python 进程或集群的全局限制。

### 5.4 重复调用保护

现有“同一 Turn 内工具名和参数完全相同的调用只应用一次”语义继续保留。

批次预处理按原始顺序预留调用签名：

- 第一个未执行过的签名进入 Scheduler；
- 已经在本 Turn 的更早批次成功应用过的签名，直接生成现有 duplicate Tool Result；
- 同一批后续相同签名关联到本批第一个调用，只有第一个调用成功后才生成“已在本批调度”的 duplicate Tool Result；
- duplicate 不占用并发槽位，不调用 Hook 和真实 Tool handler；
- duplicate 解析成功后仍拥有完整的 requested、started、completed 和 result-appended 可观察生命周期，以保持对外事件配对；
- 如果同批第一个调用失败，关联的 duplicate 不生成成功结果，整个批次按失败处理；
- 如果批次最终失败，预留签名不提交到 Turn 已应用集合。

## 6. QueryLoop、Router 与 Executor 边界

### 6.1 QueryLoop

`QueryLoop` 和 `AsyncQueryLoop` 的职责收敛为：

1. 接收 ProviderResponse 并持久化 assistant tool-use message；
2. 把完整 Tool Call 批次交给 Scheduler；
3. 消费 Scheduler 的生命周期观测；
4. 在每个成功结果离开执行边界时立即应用 Tool Result budget；
5. 等待完整批次成功；
6. 按原始顺序追加全部 Tool Result；
7. 重新构建下一次 ProviderRequest。

Loop 不能直接创建线程、Task、Semaphore 或读写锁，也不能自行判断哪些 Tool 可以并发。

### 6.2 ToolCallRouter

`ToolCallRouter` 继续只执行单个调用，并新增只读策略查询：

```python
def concurrency_policy_for(
    self,
    tool_call: ProviderToolCall,
) -> ToolConcurrencyPolicy: ...
```

策略来源：

| Tool 来源 | 第一阶段策略 |
|---|---|
| Context Protocol Tool | 固定 `EXCLUSIVE`。 |
| `RegisteredTool` external/skill | 使用正式 `concurrency_policy` 字段。 |
| MCP Tool | 默认 `EXCLUSIVE`，仅按第 10 节显式提升。 |
| 未知 Tool | 保守返回 `EXCLUSIVE`，随后由正常执行路径报告 unknown tool。 |

Context Tool 即使看起来是读取操作，第一阶段也保持独占，因为 `recall_context`、`load_attachment` 等调用会影响当前 Turn 的临时上下文或消息投影。

### 6.3 ToolExecutor

`ToolExecutor.execute()` 和 `ToolExecutor.async_execute()` 保持单调用接口。它们继续负责安全策略、参数校验、Sandbox Policy、ExecutionBackend 和 ToolExecutionResult 标准化。

并发控制不能下沉到 `ToolExecutor`，否则 Context Tool、MCP Tool 和其他 Router 分支会出现不同调度语义。

## 7. 同步与异步执行

### 7.1 QueryLoop

同步 Scheduler 使用有界 `ThreadPoolExecutor` 执行 `PARALLEL_SAFE` 段：

- worker 数不超过 `max_parallel_calls`；
- `EXCLUSIVE` 调用在没有其他运行中 Future 时单独执行；
- MessageRuntime、ActiveWindow、Tool Result budget 和结果追加只在协调线程执行；
- worker 线程只执行单个 Router 调用，不修改 Loop 内部集合；
- 每批完成或失败后必须回收本批 executor 资源，不留下无界后台线程。

### 7.2 AsyncQueryLoop

异步 Scheduler 使用 `asyncio.Task` 和 `asyncio.Semaphore(max_parallel_calls)`：

- 原生 async handler 在事件循环中执行；
- 同步阻塞 handler 继续通过现有 async backend/to-thread 边界执行；
- Semaphore 限制逻辑调用数，即使底层默认线程池更大也不能突破配置；
- `EXCLUSIVE` 调用开始前必须等待当前并行段所有 Task 完成；
- 所有 Task 必须被 await 或显式取消并回收，不能产生 orphan task。

### 7.3 行为一致性

同步和异步 Loop 必须通过同一组调度契约测试。允许底层取消能力不同，但以下结果必须一致：

- 策略判定；
- 独占屏障；
- 最大并发数；
- FIFO 启动资格；
- Tool Result 顺序；
- 批次失败后的 ActiveWindow 状态；
- Provider 下一轮看到的消息序列。

## 8. Hook、Event 与 Stream 时序

### 8.1 原则

Tool handler 可以并行，但 Loop 的业务状态写入保持串行。Hook 和 Event 必须以 `tool_call_id` 关联，不能依赖全局事件顺序判断同一个调用的状态。

Scheduler 向协调层报告“获得执行资格”和“执行已收敛”等生命周期观测，协调层通过 Scheduler 提供的串行回调边界调用 Hook、发布 Event 和产生 Stream Event。同步 worker 不能直接并发调用 HookManager、MessageRuntime 或用户 EventBus handler。

### 8.2 单调用时序

一个正常调用的逻辑时序为：

```text
ToolCallRequestedEvent
ToolStreamStarted
ToolExecutionStartedEvent
before_tool_call hook
Router.execute / async_execute
after_tool_call hook
ToolExecutionCompletedEvent
Tool Result budget
ToolStreamCompleted

完整批次成功后，按原始顺序执行：
MessageRuntime.append_tool_result
ToolResultAppendedEvent
```

规则：

- `ToolCallRequestedEvent` 按 Provider 原始顺序发布；
- `ToolExecutionStartedEvent` 表示调用进入执行管线，在调用真正获得执行资格时发布，排队期间不能提前发布；
- 并行调用的 Completed/Failed Event 可以按实际完成顺序出现；
- `ToolStreamCompleted` 在结果经过 Tool Result budget 后按实际完成顺序出现；
- `ToolResultAppendedEvent` 必须按 Provider 原始顺序出现；
- 下游 UI 必须用 `tool_call_id` 关联生命周期，不能假设完成顺序等于请求顺序。

### 8.3 Hook 串行化

第一阶段 Hook 调度保持串行和确定性：

- 同一并行段的 `before_tool_call` 按原始调用顺序执行，然后启动相应 Tool handler；
- `after_tool_call` 在结果到达协调层后按实际完成顺序执行；
- Hook 本身不占用 Tool 并发槽位，但慢 Hook 会延迟调用启动或结果完成；
- Hook deny 生成现有的合成 Tool Result，不调用真实 handler；
- 第一阶段保持现有 Hook 语义：before hook 负责 allow/deny，after hook 可以修改对应结果；
- Hook 不得直接修改兄弟调用；
- Hook 异常视为批次失败。

如果部署方的 Hook 需要读取同批前一个 Tool 的副作用，应把相关 Tool 声明为 `EXCLUSIVE`，或者让模型跨 Provider 轮次执行。

### 8.4 Stream Event

并行模式下，多个 `ToolStreamStarted` 和 `ToolStreamCompleted` 可以交错。Stream 协议必须保证：

- 每个 started 最终对应 completed 或 failed；
- `tool_call_id` 稳定；
- 排队调用在真正获得执行资格前只允许有 requested/status，不允许有 started；
- Turn 失败前，已经成功执行的调用可以先产生 completed 事件和经过大小限制的 Stream Result，但不会产生部分 Provider Tool Result 写回；
- 最终 Tool Result 写回顺序不由 Stream Event 完成顺序决定。

## 9. Tool Result 有序写回与错误语义

### 9.1 有序写回

无论实际完成顺序如何，Scheduler 都按原始索引返回结果。Loop 必须按以下顺序执行：

```text
assistant(tool_calls=[call_1, call_2, call_3])
tool_result(call_1)
tool_result(call_2)
tool_result(call_3)
```

这保证 Provider 协议稳定、测试确定、Transcript 可重放，也避免快速调用的结果越过前面的慢调用。

### 9.2 批次原子写入

Tool 执行不是事务，但 ActiveWindow 写入采用批次原子语义：

- 只有完整批次成功后，才按原始顺序追加全部 Tool Result；
- 任意调用抛出异常时，不向 ActiveWindow 追加本批任何 Tool Result；
- 已追加的本批 assistant tool-use ref 从 ActiveWindow 移除，保持下一次请求不会出现残缺 tool-use/tool-result 对；
- MessageStore 保持 append-only，已保存的 assistant 消息不物理删除；
- 已经发生的外部副作用不能自动回滚，必须通过 Event/Trace 保留事实；
- Runtime 不宣传 exactly-once 或事务性 Tool 执行。

### 9.3 失败处理

一旦批次中出现第一个失败：

1. 停止启动尚未获得执行资格的调用；
2. 尝试取消排队 Future 或 Task；
3. 对已运行调用执行第 11 节的收敛处理；
4. 记录具体失败调用；
5. 不追加任何批次 Tool Result；
6. 让当前 Turn 进入失败状态并传播原始异常。

第一阶段不把异常自动转换成“请模型重试”的 Tool Result，因为这会改变现有 QueryLoop 失败语义。需要容错的 Tool 应在自己的受控边界内返回明确、有限的错误结果。

### 9.4 Tool Result budget

Tool Result token budget 在每个调用离开 Tool 执行边界、进入 Stream Event 和批次结果缓存前立即应用，确保原始超大结果不会通过流式事件绕过限制。被截断的结果继续产生 `ToolResultCappedEvent`；并行模式下该事件可以按实际完成顺序出现。批次全部成功后，已经受限的结果再按原始调用顺序写入 MessageRuntime。

大型结果仍应写入 ArtifactStore 或 Workspace，只把 handle 和有限 preview 返回给模型。并发能力不能绕过结果大小限制。

## 10. Provider 与 MCP 能力映射

### 10.1 ProviderRequest

`ProviderRequest` 增加 Provider 无关的意图字段：

```python
@dataclass(frozen=True, slots=True)
class ProviderRequest:
    system: str
    messages: tuple[ProviderInputItem, ...]
    tools: tuple[ProviderToolSpec, ...] = ()
    parallel_tool_calls: bool | None = None
```

该定义扩展总体计划已冻结的不可变 Provider 边界，不得重新引入 `ProviderMessage` 平面模型或可变 `list`。`parallel_tool_calls` 只增加 Provider 意图，不改变 `ProviderInputItem`、ContextSnapshot 或 Tool Pair 的协议。

语义：

- `True`：允许 Provider 在一个响应中生成多个 Tool Call；
- `False`：请求 Provider 每次最多生成一个 Tool Call；
- `None`：Adapter 不发送相关参数；
- 该字段不授权 Runtime 并行执行具体工具；实际执行仍完全由 ToolConcurrencyPolicy 决定；
- 即使 Provider 忽略配置并返回多个调用，Runtime 仍必须正确调度。

`ProviderRequestBuilder` 的 `parallel_tool_calls` 构造参数默认是 `True`，并把该值写入每次不可变 ProviderRequest；`AgentBuilder` 使用这个默认值。这样模型可以一次提出多个独立调用，同时 Scheduler 对其中的独占调用保持串行。`max_parallel_calls=1` 只限制 Runtime 执行并发，不自动把 Provider 请求意图改成 `False`。高级用户可以直接构造 `ProviderRequestBuilder(parallel_tool_calls=False)` 禁止 Provider 批量生成 Tool Call，或传入 `None` 让 Adapter 省略参数。

### 10.2 OpenAIProvider

官方 OpenAI Chat Completions Adapter 在存在 Tool Schema 时转发：

```python
parallel_tool_calls=request.parallel_tool_calls
```

当字段为 `None` 或没有 Tool Schema 时省略该参数。

### 10.3 OpenAICompatibleProvider

OpenAI-Compatible 服务不保证实现该参数，因此 Adapter 增加显式能力开关，默认关闭参数转发：

```python
supports_parallel_tool_calls_parameter: bool = False
```

只有该能力为真且 `ProviderRequest.parallel_tool_calls` 非 `None` 时才发送 `parallel_tool_calls`。这避免旧服务或非完整兼容服务因未知参数直接拒绝请求。

无论是否转发参数，Adapter 都必须能解析响应中的多个 Tool Call。

### 10.4 AnthropicProvider

Anthropic 可以在一个 assistant message 中返回多个 tool-use block，但没有必要映射 OpenAI 同名参数。Adapter 不发送虚构字段，Runtime 仍按统一 Scheduler 执行多个调用。

### 10.5 MCP

MCP 默认策略为 `EXCLUSIVE`。只有同时满足以下两个显式条件时，MCP Tool 才映射为 `PARALLEL_SAFE`：

- Tool 注册信息显式声明 `parallel_safe=true`；
- `MCPServerRegistration.supports_parallel_tool_calls=true`。

MCP `readOnlyHint=true` 只能作为工具作者判断副作用范围的辅助信息，不能单独或自动提升并发策略。

为承载该信息，第一阶段在 MCP 类型中增加：

```python
@dataclass(frozen=True, slots=True)
class MCPToolInfo:
    ...
    read_only_hint: bool = False
    parallel_safe: bool = False

@dataclass(frozen=True, slots=True)
class MCPServerRegistration:
    ...
    supports_parallel_tool_calls: bool = False
```

规则：

- 缺少 tool-level `parallel_safe` 或 Server opt-in 任一项时均保持 `EXCLUSIVE`；
- Server opt-in 是部署方对 MCP client/server 并发安全的承诺；
- Tool-level `parallel_safe` 是工具注册方对该 Tool 协程/线程安全和副作用隔离的承诺，不提升同 Server 的其他 Tool；
- `readOnlyHint` 不能推导并发安全、幂等或无外部读取成本；
- MCP Adapter 必须保证 client 调用边界并发安全；底层 client 不支持并发时，Adapter 使用 per-server lock 保守串行化；
- 如果实际 MCP client 或 server 不能安全处理并发，注册方不得开启 Server opt-in；
- MCP 名称、Schema、Sandbox 和 Security Policy 继续由现有 Adapter/Router 校验。

## 11. 取消与中断

### 11.1 通用规则

- Scheduler 在启动批次、启动每个并行段和从队列取下一个调用前检查取消状态；
- 取消后不再启动新调用；
- 已完成结果在取消 Turn 中不写回 Provider 消息序列；
- 取消不回滚已经发生的外部副作用；
- 所有已经创建的 Future/Task 都必须被回收，避免资源泄漏和“Task exception was never retrieved”。

### 11.2 异步取消

`AsyncQueryLoop` 收到取消后：

- 取消尚未完成的 Task；
- 等待所有 Task 收敛，并收集 `CancelledError` 或真实异常；
- 如果 Tool handler 吞掉取消，仍受现有 ResourcePolicy/timeout 边界约束；
- 原始 Turn 取消原因优先，不被后续兄弟 Task 的取消异常覆盖。

### 11.3 同步取消

Python 线程不能安全强杀。同步 Loop 收到取消后：

- 取消尚未开始的 Future；
- 不再提交新 Future；
- 已经运行的 handler 只能等待其返回或由现有超时/ExecutionBackend 终止；
- Scheduler 收敛运行中 Future 后再结束批次；
- 文档必须明确同步取消是协作式的，不能承诺立即终止任意第三方阻塞函数。

需要强终止保证的工具应使用支持取消的进程、容器或远程 ExecutionBackend，这不属于本阶段。

## 12. 配置与默认行为

AgentBuilder 增加最小配置入口：

```python
agent = (
    AgentBuilder()
    .provider(provider)
    .tools(tools)
    .max_parallel_calls(8)
    .build()
)
```

规则：

- 未调用 `.max_parallel_calls()` 时默认 `8`；
- 重复调用遵循现有 Builder 风格，立即抛出 `ValueError`；
- 值为 `1` 时所有调用实际串行，但仍保留独占屏障和有序写回语义；
- 该配置同时传给默认同步和异步 Scheduler；
- Level 1 不要求 Redis、PostgreSQL、外部 Queue 或分布式 Semaphore；
- Runtime Profile 后续可以暴露同一字段，但不能重新定义语义。

直接构造 QueryLoop 的高级用户可以注入 `ToolCallScheduler`。Builder 第一阶段不增加多套互相冲突的 scheduler 配置入口。

## 13. 安全与开发者责任

将工具标记为 `PARALLEL_SAFE` 前，开发者必须确认：

- handler 和其使用的 client 支持并发调用；
- 不依赖进程级当前工作目录；
- 不通过无锁全局变量传递临时状态；
- 不会并发写入同一个非事务文件；
- 不会共享不可重入数据库 cursor/session；
- 并发时仍满足 SecurityPolicy、SandboxPolicy 和 ResourcePolicy；
- 日志和错误信息不会因并发泄露其他调用的敏感参数。

SDK 不能通过静态分析证明这些条件。默认 `EXCLUSIVE` 是保守安全边界，显式 opt-in 是 API 契约。

## 14. 可观测性

第一阶段复用现有 typed events，并保证并发下仍可关联：

- `ToolCallRequestedEvent`；
- `ToolExecutionStartedEvent`；
- `ToolExecutionCompletedEvent`；
- `ToolResultAppendedEvent`；
- `ToolResultCappedEvent`；
- `TurnFailedEvent`。

现有事件至少已经包含 `tool_call_id`。实现时应补充或确认以下调度元数据进入结构化日志或 Trace，而不是默认 LLM 上下文：

- 原始 batch index；
- concurrency policy；
- queue wait duration；
- execution duration；
- max_parallel_calls；
- batch size；
- cancellation/failure reason。

本阶段不要求新增一整套 Scheduler Event 类型。只有现有事件无法无歧义表达排队或批次失败时，实施计划才允许增加最小 typed event，不能使用自由字符串事件替代。

## 15. 测试策略

### 15.1 Tool 声明和 Router

- `RegisteredTool` 默认是 `EXCLUSIVE`；
- 显式 `PARALLEL_SAFE` 可以被 Registry/Router 查询；
- metadata 中的非正式并发值不生效；
- 构造后修改原始 `parameters`/`metadata` 及其嵌套 dict/list 不会改变 `RegisteredTool`；
- 注册对象的嵌套 schema 和 metadata 不能原地修改，Provider 序列化得到独立可变副本；
- `ProviderFunctionSpec` 和 `ProviderRequest.tools` 继续保持递归冻结，不能在 `RegisteredTool.provider_spec()` 中提前 thaw；
- 连续两次 `provider_tool_spec_to_dict()` 返回互不共享嵌套容器的 payload，修改第一次结果不影响第二次结果或 canonical schema；
- Context Tool 固定独占；
- 未知 Tool 保守独占并在执行时报原有错误；
- MCP 默认独占；
- MCP `readOnlyHint` 单独、Server opt-in 单独、Tool `parallel_safe` 单独均保持 `EXCLUSIVE`；
- 只有 Tool `parallel_safe=true` 与 Server `supports_parallel_tool_calls=true` 同时成立时才映射为 `PARALLEL_SAFE`。

### 15.2 Scheduler 单元测试

使用 Barrier/Event/Fake Clock 等确定性同步原语验证，不依赖脆弱的 `sleep` 时间猜测：

- 两个 `PARALLEL_SAFE` 调用确实重叠；
- 默认并发峰值不超过 `8`；
- 自定义上限严格生效；
- 超限调用 FIFO 获得启动资格；
- `EXCLUSIVE` 调用与任何兄弟调用都不重叠；
- 独占调用前后不存在越过屏障；
- 结果按原始索引返回而不是按完成顺序；
- duplicate 不执行 handler；
- 非法 `max_parallel_calls` 构建失败；
- 同步和异步契约一致。

### 15.3 QueryLoop 集成测试

- Provider 返回多个 Tool Call 时可以并行执行；
- assistant tool-use message 后的 Tool Result 顺序稳定；
- 下一次 ProviderRequest 包含完整、正确配对的 Tool Result；
- Tool Result budget 在 Stream Result 前应用，且写回顺序保持稳定；
- 完成 Event 可以乱序，但 result-appended Event 保持原始顺序；
- 任一 Tool 失败时不部分追加 Tool Result；
- 失败时本批 assistant ref 从 ActiveWindow 移除；
- MessageStore 保持 append-only；
- 已运行兄弟调用的副作用不会被错误宣称为已回滚；
- interrupt/cancel 不留下 Future 或 Task；
- max tool iterations 和重复调用保护不回归。

### 15.4 Provider 测试

- OpenAI 在 Tool 存在且字段非空时正确发送 `parallel_tool_calls`；
- `None` 时省略参数；
- OpenAI-Compatible 默认不发送；
- 显式能力开启后发送；
- Anthropic 不发送 OpenAI 私有字段；
- 所有 Adapter 都能标准化多个 Tool Call；
- streaming 聚合多个 Tool Call 时顺序和 ID 不丢失。

### 15.5 回归验证

实施完成前必须通过：

```text
目标 Scheduler/Loop/Provider 测试
完整 pytest
python -m compileall -q src tests
git diff --check
```

## 16. 验收标准

满足以下全部条件才视为 Level 1 并行 Tool Call 完成：

1. Tool 默认不可并发，只有显式 `PARALLEL_SAFE` 才并发；
2. 同一响应中的安全 Tool 可以真实重叠执行；
3. `max_parallel_calls` 可配置且默认等于 `8`；
4. 超限调用排队而不是报错；
5. `EXCLUSIVE` 形成严格前后屏障；
6. 同步和异步 Loop 行为一致；
7. Tool Result 始终按 Provider 原始顺序写回；
8. 任一调用失败时不产生残缺 tool-use/tool-result 消息序列；
9. 取消后不启动新调用，所有 Future/Task 最终被回收；
10. Hook、Event 和 Stream 可以用 `tool_call_id` 正确关联；
11. Provider 参数映射保持能力感知和向后兼容；
12. MCP 未显式声明时保持串行；
13. Level 1 不引入 PostgreSQL、Redis、DAG 或分布式依赖；
14. 全量测试、compileall 和 diff check 通过。

## 17. 后续演进

以下能力记录为后续目标，但不应提前进入本阶段实现：

- 资源键锁，例如按 workspace/file/database connection 控制冲突；
- 每 Provider、Tenant、Tool 或远程 Server 的独立并发配额；
- 基于成本、速率限制和优先级的自适应调度；
- 可持久化的分布式 Tool Queue；
- Planner DAG、显式 `depends_on` 和结果引用；
- Code Mode 中用语言原生 `await`/`Promise.all()` 表达依赖；
- Tool 幂等键、补偿动作和副作用恢复协议。

这些能力属于 Level 2/Level 3 或独立 Spec，不能改变本设计已经确定的 ToolConcurrencyPolicy、批次顺序和默认保守语义。
