# AgentOS Phase 6 Task 6 Side Effect Contract Addendum

> 状态：已确认，作为 Phase 6 Task 6 的强制补充合同
>
> 日期：2026-07-20
>
> 上位规范：`2026-07-17-agentos-phase6-distributed-runtime-transport-contract.md`

## 1. 目的与取代关系

本文只补全 Phase 6 Contract 第 9 节在实现前必须冻结的 Side Effect 细节，不改变
Phase 6 的产品范围、状态真值、Claim/Fencing 或 Transport 边界。

若本文与上位规范第 9 节存在粒度差异，以本文对 Task 6 类型、状态和恢复语义的精化为准；
其他章节仍以上位规范为准。

Task 6 采用 breaking migration：不保留裸 `dict` handler、同步 Tool 执行 facade、旧
`execute_tool_call` 名称、参数签名去重或未声明 Side Effect Policy 的兼容路径。
公共 `agentos.capabilities.AsyncToolHandler` 同步删除；`ToolHandler` 统一接收
`ToolInvocation`，并新增 experimental 的 `SideEffectPolicy`、补偿输入和 Tool Result ref 类型。

## 2. Scope Contract

### 2.1 阶段与规范

- 阶段：Phase 6 Wave 2 Task 6；
- 总架构：下一代 SDK 架构设计；
- 子系统规范：单一异步 QueryLoop、Phase 5 Durable Profile、Phase 6 Contract 和本文；
- 实施计划：Phase 6 Distributed Runtime / Transport 实施计划 Task 6。

### 2.2 本任务验收项

- 冻结统一 `ToolInvocation`/compensation handler 合同；
- 冻结五类 `SideEffectPolicy`，并与 `ToolConcurrencyPolicy` 完全分离；
- 冻结稳定 ID、digest、attempt、Ledger DTO/Port 和恢复矩阵；
- 冻结 `result_ref`、WAITING 原子载荷、resolution、cancel safe-stop 和 compensation 矩阵；
- 全仓生产 Tool 显式声明 policy，handler 只接收 `ToolInvocation`；
- `wait_capable` 在任何 hook、handler 或 Ledger start 前完成整批预检；
- 删除参数签名去重和 Tool 同步执行 API；
- 通过目标测试、6A 回归、全量门禁和双层 Review。

### 2.3 允许修改

- `src/agentos/capabilities/` 的 Tool contract、router、executor、backend 和注册点；
- `src/agentos/runtime/` 的 Tool identity、plan、Ledger、batch、checkpoint 和 Run commit leaf；
- Artifact、Skill、Planner、MCP、Multi-agent、Team 的 Tool 注册与 handler；
- 对应测试、Public API policy/inventory 和 Task 6 文档。

### 2.4 禁止修改

- PostgreSQL、Redis、Worker、Transport 和 Channel Adapter；
- Context Protocol v1、Provider 双平面和 Artifact 生命周期；
- 与 Task 6 无关的旧模块清理、格式化和兼容 facade；
- 用户工作区中只有换行差异的既有未暂存文件。

### 2.5 依赖边界

`capabilities` 拥有 invocation 和 Tool 声明；`runtime` 拥有稳定 identity、Ledger domain、
恢复决策与执行协调；具体 Store Adapter 实现 canonical `SideEffectStore`，不能复制 DTO。
`QueryLoop` 只传递 plan 和协调 batch，不拥有 Ledger 状态机。

### 2.6 本任务明确完成

Task 6 完成 canonical contract、in-memory/fake contract evidence、Local/Durable 调用链迁移和
WAITING typed handoff。它不以 fake store 证明跨进程原子性。

### 2.7 明确延期

- Wave 3：PostgreSQL Ledger schema、行锁、fence、Run/Input/Outbox 原子事务、Worker resume；
- Wave 3：大型 Tool Result 写入共享 ArtifactStore 的真实 Adapter；
- Wave 6：真实进程 kill、数据库/Redis 重启、网络中断和三段 compensation 故障注入。

## 3. Canonical Tool Invocation

### 3.1 类型

```python
@dataclass(frozen=True, slots=True)
class ToolInvocationContext:
    invocation_id: str
    operation_id: str
    tenant_id: str | None
    session_id: str
    run_id: str
    turn_id: str
    tool_call_id: str
    attempt: int


@dataclass(frozen=True, slots=True, init=False)
class ToolInvocation:
    tool_name: str
    arguments: FrozenJsonObject
    context: ToolInvocationContext
```

`session_id` 在全部 Profile 中必填。`tenant_id=None` 只表示 Local/Durable 没有 tenant
authority；禁止伪造 `"local"` tenant。Distributed Profile 必须提供非空 tenant。

`attempt` 从 1 开始，只表示同一 operation 的 handler 调用次数。Fencing token、Provider
retry 和 Tool batch index 都不能写入 `attempt`。

Tool handler 的唯一签名为：

```python
ToolHandler = Callable[[ToolInvocation], ToolHandlerResult | Awaitable[ToolHandlerResult]]
ToolHandlerResult = str | WaitRequest
```

同步 handler 只能由 `ExecutionBackend` 内部受控 offload。Router、Executor 和 Backend
只暴露 async `execute(...)`；不保留同步 shadow method。

### 3.2 稳定 identity v1

`invocation_id` 使用以下 canonical JSON 的 UTF-8 SHA-256 前 32 个十六进制字符：

```json
{
  "provider_call_index": 0,
  "run_id": "run_1",
  "session_id": "session_1",
  "tenant_id": null,
  "tool_index": 0,
  "turn_id": "turn_1",
  "version": 1
}
```

JSON object key 按字典序排列，无多余空白，保留 Unicode。结果格式为
`invocation_<32-lower-hex>`。

`operation_id` 使用以下 canonical JSON 的同一算法：

```json
{
  "invocation_id": "invocation_...",
  "run_id": "run_1",
  "session_id": "session_1",
  "tenant_id": null,
  "turn_id": "turn_1",
  "version": 1
}
```

结果格式为 `operation_<32-lower-hex>`。原始 Provider `tool_call_id` 不参与两个 ID 的
identity，只用于 Provider tool pair。

同一份 `ToolInvocationPlan` 必须同时驱动 `pending_tools` checkpoint 和实际执行。Executor
不得重新计算 identity。

### 3.3 Invocation digest v1

invocation digest 覆盖 `version=1`、`invocation_id`、`tool_name` 和 frozen `arguments`，使用
相同 canonical JSON 和完整 SHA-256，格式为 `sha256:<64-lower-hex>`。Store 发现同一
operation 的 digest 改变时必须 fail closed。

## 4. Side Effect Declaration

```python
class SideEffectPolicy(str, Enum):
    PURE = "pure"
    IDEMPOTENT = "idempotent"
    DEDUPLICATED = "deduplicated"
    COMPENSATABLE = "compensatable"
    NON_RETRYABLE = "non_retryable"
```

`RegisteredTool.side_effect_policy` 必填且无默认值。`concurrency_policy` 继续独立声明。

- `compensatable` 必须声明 compensation handler；其他 policy 禁止声明；
- `wait_capable=True` 只允许 `pure + EXCLUSIVE`；
- 外部 Tool、Planner mutation、Team、spawn/dispatch 未显式 policy 时构造即失败；
- MCP annotation 只作为 advisory metadata。`MCPServerRegistration` 必须按 tool name 提供本地
  可信 policy；未声明时固定为 `non_retryable`。本地声明 `pure` 时还必须满足远端
  `read_only_hint=True`；MCP Tool 禁止声明 `compensatable`；
- Context 内部 mutation 由 Runtime 声明为 `idempotent`，依据不是工具名称，而是固定执行
  前提：mutation 只作用于 `pending_tools` checkpoint 之后的进程内 ContextState，直到
  `after_tools` 才首次持久提交；崩溃恢复总是从 mutation 前状态执行。只读 recall 为 `pure`；
- Artifact `list_attachments` 为 `pure`，`load_attachment` 为 `idempotent`，完成结果复用时
  必须重新建立当前 Turn mount，但不得重复外部副作用；
- Skill 读取为 `pure`，activation/disable 为 `idempotent`；
- Planner 查询为 `pure`，mutation 和 dispatch 默认为 `non_retryable`；
- Team 和 multi-agent mutation/dispatch 为 `non_retryable`。

Hook 是拦截策略，不能拥有未声明的外部副作用。整批 wait preflight 必须发生在
`before_tool_call` 之前。Side Effect Runtime 包围 hook 和 handler：hook short-circuit 不调用
handler，但仍按同一 invocation 写入 `COMPLETED(provider_result)`，恢复时复用结果而不重新执行
hook。`mark_started` 表示该 invocation 的结果生产已开始，不等于业务 handler 一定已进入。

## 5. Ledger Attempt Model

### 5.1 Identity 与状态

Ledger attempt 的唯一 identity 为：

```text
(tenant_id, session_id, operation_id, attempt)
```

其中 `tenant_id` 在无 tenant Profile 中为 `None`。`get_current(operation_id)` 返回最大 attempt，
Store 同时保留旧 attempt 作为审计事实。

状态保持：

```text
RESERVED -> STARTED -> COMPLETED
    |          |
    |          +-> AMBIGUOUS -> COMPENSATING -> COMPENSATED
    |                       \-> RESOLVED
    \-> RESOLVED(cancelled_before_start)
```

`COMPLETED` 的 `outcome_kind` 必须是：

```text
provider_result
wait_control
handler_error
```

只有 Runtime 在持有有效 guard、Backend 已完全收敛且能确定不再有同步线程或异步 Task 运行时，
普通 handler exception 才属于已观察失败。`pure/idempotent` 的已观察失败进入
`COMPLETED(handler_error)`，保存固定、脱敏的 failure code，不保存异常文本、payload 或 stack；
恢复时不再次调用 handler，而是重新抛出稳定 `ToolExecutionError`。

`deduplicated/compensatable/non_retryable` 的 handler exception 不能证明外部效果未发生，必须
进入 AMBIGUOUS。timeout、cancellation、claim loss 或同步 offload 尚未收敛时不能写
`COMPLETED(handler_error)`：`pure/idempotent` 保持 STARTED 供新 claim 安全恢复，其他 policy
由持有新 fence 的恢复者转为 AMBIGUOUS。失去 fence 的旧 Worker 不得写 Ledger。

### 5.2 多 attempt

- 初始 reserve 创建 attempt 1；
- RESERVED 恢复执行同一 attempt，不递增；
- STARTED 的 `pure/idempotent` 自动恢复时，Store 在一个 Ledger transaction 中先把旧 attempt
  写为 `RESOLVED(superseded)`，再创建 attempt + 1；
- `retry_proven_safe` 在旧 AMBIGUOUS row 记录 `RESOLVED(retry_safe)` 后创建 attempt + 1；
- compensation attempt 使用独立稳定 compensation operation ID，规则见第 9 节；
- Ledger-only transaction 校验当前 `RunWriteGuard`，但绝不递增 Run aggregate version。

### 5.3 Canonical Record

```python
class SideEffectStatus(str, Enum):
    RESERVED = "reserved"
    STARTED = "started"
    COMPLETED = "completed"
    AMBIGUOUS = "ambiguous"
    COMPENSATING = "compensating"
    COMPENSATED = "compensated"
    RESOLVED = "resolved"


class SideEffectOutcomeKind(str, Enum):
    PROVIDER_RESULT = "provider_result"
    WAIT_CONTROL = "wait_control"
    HANDLER_ERROR = "handler_error"


class SideEffectResolutionOutcome(str, Enum):
    CANCELLED_BEFORE_START = "cancelled_before_start"
    SUPERSEDED = "superseded"
    ACCEPTED = "accepted"
    RETRY_SAFE = "retry_safe"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class SideEffectCompletion:
    outcome_kind: SideEffectOutcomeKind
    result_ref: ToolResultRef | None = None
    result_digest: str | None = None
    failure_code: str | None = None


@dataclass(frozen=True, slots=True)
class SideEffectAttemptId:
    tenant_id: str | None
    session_id: str
    operation_id: str
    attempt: int


@dataclass(frozen=True, slots=True)
class CompensationAttemptId:
    side_effect_attempt: SideEffectAttemptId
    compensation_operation_id: str
    compensation_attempt: int


@dataclass(frozen=True, slots=True)
class SideEffectRecord:
    attempt_id: SideEffectAttemptId
    run_id: str
    turn_id: str
    invocation_id: str
    tool_name: str
    policy: SideEffectPolicy
    status: SideEffectStatus
    invocation_digest: str
    invocation_ref: ProtectedPayloadRef | None
    result_ref: ToolResultRef | None
    result_digest: str | None
    outcome_kind: SideEffectOutcomeKind | None
    failure_code: str | None
    wait_reason_digest: str | None
    resolution: SideEffectResolutionOutcome | None
    attestation_ref: ProtectedPayloadRef | None
    attestation_digest: str | None
    compensation_operation_id: str | None
    compensation_attempt: int | None
    claim_id: str | None
    fencing_token: int | None
```

`SideEffectCompletion` 只能形成三种组合：provider_result 必须且只能有 result ref/digest；
wait_control 不携带其他字段；handler_error 必须且只能有固定 failure code。

`claim_id/fencing_token` 必须同时存在或同时为 None，并记录最近一次合法状态写使用的 fence。
Local in-memory record 可以没有 `invocation_ref`；任何跨进程 Store 必须保存受保护 ref。

字段不变量：

- RESERVED/STARTED 不携带 result、outcome、failure、wait reason、resolution、attestation 或
  compensation 字段；
- COMPLETED(provider_result) 必须有匹配的 result ref/digest；
- COMPLETED(wait_control) 必须有 `wait_reason_digest`，不得有 result/failure；digest 覆盖
  WaitReason 的 `version=1/kind/handle/detail/not_before` canonical JSON；
- COMPLETED(handler_error) 必须只有固定 failure code；
- AMBIGUOUS 不得伪造 result 或 resolution；
- COMPENSATING/COMPENSATED 必须有稳定 compensation operation ID 和正整数 attempt；
- RESOLVED 必须有 resolution；`accepted` 还必须有 result ref/digest；`retry_safe` 必须且只能
  有 attestation ref/digest；其他状态和 resolution 必须没有 attestation；
- `superseded` 只能用于 pure/idempotent 的旧 STARTED attempt；
- DTO 不保存原 arguments、异常文本、attestation 明文、`RunWriteGuard` 或数据库对象。

### 5.4 Canonical Store Port

```python
class SideEffectStore(Protocol):
    async def reserve(
        self,
        *,
        invocation: ToolInvocation,
        policy: SideEffectPolicy,
        invocation_digest: str,
        invocation_ref: ProtectedPayloadRef | None,
        guard: RunWriteGuard,
        supersedes: SideEffectAttemptId | None = None,
    ) -> SideEffectRecord: ...

    async def get(
        self,
        *,
        tenant_id: str | None,
        session_id: str,
        operation_id: str,
        attempt: int | None,
        guard: RunWriteGuard,
    ) -> SideEffectRecord | None: ...

    async def mark_started(
        self, *, attempt_id: SideEffectAttemptId, guard: RunWriteGuard
    ) -> SideEffectRecord: ...

    async def complete(
        self,
        *,
        attempt_id: SideEffectAttemptId,
        completion: SideEffectCompletion,
        guard: RunWriteGuard,
    ) -> SideEffectRecord: ...

    async def mark_ambiguous(
        self, *, attempt_id: SideEffectAttemptId, guard: RunWriteGuard
    ) -> SideEffectRecord: ...

    async def begin_compensation(
        self, *, attempt_id: SideEffectAttemptId, guard: RunWriteGuard
    ) -> SideEffectRecord: ...

    async def complete_compensation(
        self, *, attempt_id: CompensationAttemptId, guard: RunWriteGuard
    ) -> SideEffectRecord: ...

    async def resolve(
        self,
        *,
        attempt_id: SideEffectAttemptId,
        resolution: SideEffectResolution,
        guard: RunWriteGuard,
    ) -> SideEffectRecord: ...
```

`attempt=None` 只表示读取 current 最大 attempt。`reserve` 对同 identity/digest/policy 的重复调用
返回原 record；任一字段冲突抛出固定 `SideEffectRecordConflictError`。带 `supersedes` 的 reserve
必须原子校验旧 row 为同 operation 的 current STARTED pure/idempotent、写
`RESOLVED(superseded)` 并创建恰好 next attempt；任一步失败整体零写入。

状态不合法抛出固定 `SideEffectTransitionError`；result digest/ref 不匹配抛出
`SideEffectResultIntegrityError`；Store 不存在任意文本构造的 Adapter exception。上述错误不得
包含 arguments、ref token、result content、attestation、guard、DSN 或 SQL。

所有 mutation 必须在自身 transaction 内验证 guard/fence。返回的 record 必须是 transaction
提交后的 canonical immutable record。Store 不能递增 Run aggregate version；WAITING 和跨
Run/Input/Outbox 的原子提交只能走第 8/9 节的 composite Port。

`resolve` 的返回语义固定：`accept_result/fail` 返回原 attempt 的 RESOLVED record；
`retry_proven_safe` 在同一 Ledger transaction 中把原 attempt 写为 RESOLVED(retry_safe)、保存
attestation ref/digest，并创建 attempt + 1 的 RESERVED record，返回新的 current record；
`compensate` 在同一 Ledger transaction 中把原 attempt 写为 COMPENSATING、写入稳定
compensation operation ID 和 `compensation_attempt=1`，返回 COMPENSATING record。
`begin_compensation` 只用于恢复已有 COMPENSATING record：保持 operation ID 并把
compensation attempt 原子递增 1。Runtime 必须用 Store 返回的 record 构造精确
`CompensationAttemptId`；`complete_compensation` 同时比较原 Side Effect attempt、compensation
operation ID 和 compensation attempt。旧 handler 的迟到 completion 必须以固定
`SideEffectTransitionError` 失败，不能完成新的 compensation attempt。

## 6. 固定恢复矩阵

| Current attempt | pure | idempotent | deduplicated | compensatable | non_retryable |
|---|---|---|---|---|---|
| RESERVED | execute same attempt | execute same attempt | execute same attempt | execute same attempt | execute same attempt |
| STARTED | new attempt, auto retry | new attempt, same operation ID | mark AMBIGUOUS, reconcile | mark AMBIGUOUS, reconcile | mark AMBIGUOUS, reconcile |
| COMPLETED(provider_result) | reuse | reuse | reuse | reuse | reuse |
| COMPLETED(wait_control) | batch already consumed | batch already consumed | invalid | invalid | invalid |
| COMPLETED(handler_error) | raise stable error | raise stable error | invalid | invalid | invalid |
| AMBIGUOUS | invalid | invalid | reconcile | reconcile | reconcile |
| COMPENSATING | invalid | invalid | invalid | retry compensation | invalid |
| COMPENSATED | invalid | invalid | invalid | commit FAILED only | invalid |
| RESOLVED | follow typed resolution | follow typed resolution | follow typed resolution | follow typed resolution | follow typed resolution |

未解决 AMBIGUOUS 必须产生 `side_effect_reconciliation` WaitReason，禁止继续 Provider。

## 7. Result Reference

```python
@dataclass(frozen=True, slots=True)
class InlineToolResultRef:
    content: str


@dataclass(frozen=True, slots=True)
class ArtifactToolResultRef:
    artifact: ArtifactRef
    preview: str


ToolResultRef = InlineToolResultRef | ArtifactToolResultRef
```

两种 ref 互斥。`InlineToolResultRef` 必须满足 Tool Result Budget；超限内容由 Adapter 写入
同 tenant/session ArtifactStore，再保存 `ArtifactToolResultRef`。Task 6 的 in-memory
实现只需完成 inline path；真实 Artifact 持久化在 Wave 3。

result digest 覆盖 `version=1`、ref kind 和全部 ref 字段，使用 canonical JSON 和完整
SHA-256。读取时必须重新计算并匹配。Provider 收到 inline content 或 artifact preview，不能
收到 bytes、Base64、blob key 或路径。

对于会改变 SDK 临时投影的内置 Tool，Tool runtime 使用私有、封闭的 typed projector 重建
投影；它不是 `RegisteredTool` 字段或公共扩展面，不能成为第二个 handler。Task 6 必须覆盖
Context mutation 和 `load_attachment` mount 的恢复，projector 不得执行外部副作用。

## 8. WAITING 原子载荷

handler 返回 `WaitRequest` 后，Ledger 保持 STARTED，Tool runtime 返回：

```python
@dataclass(frozen=True, slots=True)
class WaitingToolCompletion:
    invocation_id: str
    operation_id: str
    attempt: int
    wait_reason_digest: str
```

`WaitingCheckpointRequest` 必须同时携带 `WaitReason`、ActiveWindow 投影和该 completion。
Runtime 在创建 completion 时计算 digest；composite Store 必须从同一 WaitReason 重新计算并严格
匹配，禁止信任调用方提供的不一致 digest。
`RunCommitRuntime` 把它原样交给 `commit_waiting(...)`。持久 Store 在同一 transaction 中完成：

```text
Ledger STARTED -> COMPLETED(wait_control)
+ SessionCheckpoint
+ accepted input COMMITTED
+ running cursor cleared
+ Run RUNNING -> WAITING
+ Outbox
```

不得先调用 `SideEffectStore.complete()` 再调用 `commit_waiting()`。无持久 Store 的 Local
Profile 可以在同一个事件循环临界区提交内存 Run/Ledger，但不宣传跨进程原子性。

wait-capable batch 必须恰好包含一个 Provider call。配置不满足 `pure + EXCLUSIVE`、batch
混合调用或多个 wait-capable call 时，在任何 hook、handler、Ledger reserve/start 前失败。

## 9. Resolution 与 Compensation

### 9.1 类型化 resolution

`WaitReasonKind` 增加 `side_effect_reconciliation`，其 handle 固定为 operation ID。
`DurableRunCommandKind` 和 continuation kind 增加 `resolve_side_effect`。

```python
SideEffectResolutionKind = Literal[
    "accept_result",
    "retry_proven_safe",
    "compensate",
    "fail",
]


@dataclass(frozen=True, slots=True)
class SideEffectResolution:
    operation_id: str
    kind: SideEffectResolutionKind
    result_ref: ToolResultRef | None = None
    result_digest: str | None = None
    attestation_ref: ProtectedPayloadRef | None = None
    attestation_digest: str | None = None
```

`accept_result` 必须且只能提供 result ref/digest；`retry_proven_safe` 必须且只能提供受保护的
attestation ref/digest；`compensate/fail` 不接受额外载荷。自由文本 attestation 禁止。

`retry_proven_safe`、`accept_result` 和 `compensate` 都是受权 Application operation：Channel
从认证主体构造 RequestScope，Application policy 校验 principal 是否具有对应 resolution 权限，
通过后才调用 Command Port。Store 仍必须校验 scope、wait reason、operation、状态、ref/digest 和
attestation 完整性，但不能把“同 tenant”当作授权证明。

### 9.2 Resume payload

Store 接受 resolution 后，Worker claim 必须提供精确 DTO：

```python
@dataclass(frozen=True, slots=True)
class SideEffectResume:
    tenant_id: str | None
    session_id: str
    run_id: str
    continuation_turn_id: str
    source_cursor: RunExecutionCursor
    record: SideEffectRecord
    resolution: SideEffectResolution
```

`source_cursor` 必须为 `pending_tools`，其 assistant message ID 必填，且必须包含与
`record.invocation_id` 唯一匹配的受保护 `PendingToolInvocation`。record 必须属于同
tenant/session/run、是 current attempt，并与 resolution operation 相同。`continuation_turn_id`
与 source turn 不同；operation identity 仍使用 source cursor turn，不为 continuation 重算。

resolution payload 不直接投影给 Provider。`resolve_side_effect` 的 command payload 使用
`side_effect_resolution_to_payload()` 生成的 versioned canonical JSON，并由
`side_effect_resolution_from_payload()` 严格解析；`DurableRunCommand` 构造该 kind 时必须接受
`SideEffectResolution` 或严格 canonical payload，`AcceptedContinuationInput` 保存同一 frozen
编码，不接受额外字段。

QueryLoop 只有在 RunDriver 验证该 resume 与当前
reconciliation WaitReason、operation、checkpoint 和 guard 一致后，才恢复原 tool pair。
Worker/PostgreSQL 的具体装配延期到 Wave 3；Task 6 冻结 DTO 和纯状态矩阵。

### 9.3 Compensation handler

```python
@dataclass(frozen=True, slots=True)
class ToolCompensationContext:
    original_operation_id: str
    compensation_operation_id: str
    tenant_id: str | None
    session_id: str
    run_id: str
    turn_id: str
    invocation_id: str
    attempt: int


@dataclass(frozen=True, slots=True)
class ToolCompensationInvocation:
    arguments: FrozenJsonObject
    context: ToolCompensationContext
    result_ref: ToolResultRef | None
```

compensation operation ID 由 canonical identity
`{version:1, original_operation_id, purpose:"compensation"}` 派生，格式
`operation_<32-lower-hex>`，不得通过字符串截断反推原 operation。

`resolve(compensate)` 在 AMBIGUOUS 上原子写入 COMPENSATING、稳定 operation ID 和
`compensation_attempt=1`；`begin_compensation` 在已有 COMPENSATING 上只把 compensation
attempt 递增 1，operation ID 不变。`complete_compensation` 只接受 current COMPENSATING
attempt，并原子返回
COMPENSATED record。原 Side Effect attempt identity 始终不变，compensation attempt 不占用原
operation 的 handler attempt 序列。

compensation handler 只接收原 frozen arguments、稳定 context 和可选原 result ref；不能接收
`RunWriteGuard`、数据库连接、任意 metadata 或明文 attestation。handler 必须满足以
`compensation_operation_id` 为幂等键的契约。

故障窗口保持：COMPENSATING 前可重试开始；外部效果后到 COMPENSATED 前使用同一 ID 幂等
重试；COMPENSATED 后只重试 FAILED terminal commit。

## 10. Cancel Safe-stop

cancel transaction 锁定当前 operation attempts 后按以下矩阵决定：

| Ledger state | Cancel decision |
|---|---|
| no row | allow |
| RESERVED | 原子 RESOLVED(cancelled_before_start)，然后 allow |
| STARTED current attempt | reject `side_effect_in_flight`，全部状态零写入 |
| AMBIGUOUS | reject `side_effect_in_flight`，全部状态零写入 |
| COMPENSATING | reject `side_effect_in_flight`，全部状态零写入 |
| COMPLETED | preserve row，allow |
| COMPENSATED | preserve row，allow |
| RESOLVED（含旧 superseded attempt） | preserve row，allow |

Task 6 提供纯 `SideEffectCancelPlan` 和 fake contract tests。PostgreSQL 的锁、fence 轮转、
Run/Input/cursor/checkpoint/Outbox 原子写在 Wave 3 实现。

## 11. 模块边界

| 模块 | 唯一职责 |
|---|---|
| `capabilities/invocation.py` | invocation、handler 和 compensation handler 类型 |
| `capabilities/result_refs.py` | canonical inline/artifact Tool Result ref |
| `capabilities/tools.py` | policy、RegisteredTool 声明和 wait/compensation 不变量 |
| `runtime/tool_identity.py` | versioned ID/digest 纯函数 |
| `runtime/tool_invocations.py` | Provider batch 到 canonical plan 的映射 |
| `runtime/side_effect_types.py` | Ledger/result/resolution immutable DTO |
| `runtime/side_effect_cancel.py` | cancel immutable DTO 与纯 safe-stop 计划 |
| `runtime/side_effect_resume.py` | 精确 reconciliation resume DTO 与 cursor 校验 |
| `runtime/side_effect_store.py` | async `SideEffectStore` Protocol |
| `runtime/side_effect_runtime.py` | reserve/start/complete/recover/compensate 协调 |

`query_loop.py`、`run_driver.py` 和 `tool_payloads.py` 只保留薄协调入口，不新增独立 Ledger
状态机。

## 12. Task 6 验证

至少验证：

- ID/digest golden vectors、跨 retry 稳定和 attempt 递增；
- 五类 policy、全部恢复状态、result ref digest 和复用；
- `handler_error` 不留 STARTED；
- 四种 resolution、cancel plan 和三段 compensation 纯状态矩阵；
- wait 配置与 batch preflight 在 hook/handler/Ledger 前失败；
- WAITING typed completion 进入单一 commit boundary；
- handler 只收到 `ToolInvocation`，compensation handler 只收到 typed invocation；
- context/artifact 完成复用时恢复必要 Runtime 投影；
- 并发 policy 与 Side Effect policy 独立；
- 错误、日志、Event 和 read model 不包含 arguments、attestation、guard 或 secret；
- 全仓不存在参数签名去重、Tool 同步执行 facade 和裸 arguments handler。
