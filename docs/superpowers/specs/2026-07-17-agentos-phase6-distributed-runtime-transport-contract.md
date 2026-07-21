# AgentOS Phase 6 Distributed Runtime / Transport Contract

> 状态：已确认，进入实现
>
> 日期：2026-07-17
>
> 目标里程碑：M5 Distributed Runtime
>
> 上位规范：`2026-07-10-agentos-next-generation-sdk-architecture-design.md`、
> `2026-07-10-agentos-context-protocol-v1-design.md`、
> `2026-07-12-agentos-single-async-query-loop-design.md`、
> `2026-07-17-agentos-phase5-durable-profile-contract.md`
>
> Task 6 补充合同：
> `2026-07-20-agentos-phase6-task6-side-effect-contract-addendum.md`
>
> Wave 3 Profile 补充合同：
> `2026-07-20-agentos-phase6-wave3-profile-contract-addendum.md`
>
> Wave 4 Transport 补充合同：
> `2026-07-21-agentos-phase6-wave4-transport-contract-addendum.md`

## 1. 目的

Phase 6 在 Phase 5 的同一套 `RunState`、`DurableRunCommand`、`Checkpoint` 和
`Agent -> QueryLoop -> RunDriver` 上增加企业级分布式执行能力，并完成 Transport、
Channel、Team 和部署边界的收敛。

本阶段不创建第二个 QueryLoop，不为 PostgreSQL/Redis 复制 Run 状态机，也不把旧
`SessionSnapshot` 包装成新 Profile。分布式层只提供异步 Store、Claim/Fencing、
Queue、Worker、Event Replay 和部署组合。

## 2. 范围

Phase 6 分为四个可独立验收的子阶段：

- **6A Distributed Runtime Core**：唯一异步 Port、首次 Run 提交、PostgreSQL 真值、
  Redis Lease/Queue、Claim/Fencing、Outbox、Worker、Side Effect Policy；
- **6B HTTP/SSE/A2A Transport**：纯 wire type/mapping、HTTP command ingress、SSE
  replay、A2A operation mapping 和对应 Channel composition；
- **6C WebSocket/CLI/Team**：WebSocket frame/endpoint、CLI application command、
  Team 类型/Port/Worker 接入同一 Durable Command 路径；
- **6D Failure Injection/Readiness/Release**：真实后端故障注入、Drain、恢复、导入边界、
  Public API、部署证据和发布门禁。

本阶段必须完成：

- `RunStore` 和 `DurableStateStore` 成为唯一的原生异步执行 Port；
- Local、Durable、Distributed 三种 Profile 继续运行同一 Kernel Contract；
- PostgreSQL 保存 Session、Run、Command、Checkpoint、Claim、Fencing Token、Outbox
  和 Side Effect Ledger 真值；
- Redis 只保存可重投的 Lease、Queue/Inbox/Wakeup 和短期 Stream Replay；
- 一个 Session 可以包含多个历史 Run，但数据库约束保证同时最多一个非终态 Run；
- 首次用户输入通过幂等 `RunSubmission` 入库并排队，Channel 节点不直接执行 Loop；
- `WAITING -> QUEUED` 只通过幂等 Durable Command；
- Worker Claim 成功并在 PostgreSQL 激活 Fencing Token 后才允许 `QUEUED -> RUNNING`；
- 每次 Worker-owned PostgreSQL 写在同一事务中校验当前 Fence；Ingress/Reconciler 写
  在事务内锁定当前 Fence，并仅在失效活动 Claim 时轮转；
- at-least-once delivery、状态层 effective-once 和显式副作用策略；
- HTTP/SSE/WebSocket/A2A 只映射 wire，不持有 Run/Session 真值；
- Distributed Artifact metadata 使用 PostgreSQL，bytes 使用跨节点共享 BlobStore；
- 删除新路径上的 Legacy SessionSnapshot、同步 Redis/PostgreSQL 和旧线程 Worker；
- 拆分并删除已登记的超大混合模块；
- `agentos[distributed]` 才安装 PostgreSQL/Redis Client；
- Local 基础安装与 Durable 安装不导入 PostgreSQL/Redis Client。

本阶段明确不包含：

- OCR、图纸字段提取、文档识别或自动附件摘要；
- Vector Retrieval、Embedding、跨 Session Artifact 搜索；
- Provider 托管 Transcript/Conversation ID 的恢复；
- 全局 exactly-once 宣称；
- 自动 Leader Election、跨 Region 一致性或多主数据库；
- 未经 Side Effect Policy 允许的 Tool 自动重放；
- Transport 自己创建 Store、Worker 或领域状态机。

OCR 永久不属于 AgentOS SDK 路线图。

## 3. 不可变架构决策

### 3.1 一个执行内核

```text
Ingress Service -> PostgreSQL Command/Submission + Outbox
                                         |
                                         v
Redis Delivery -> Distributed Worker -> Agent -> QueryLoop -> RunDriver
                                         |
                                         v
                     PostgreSQL Checkpoint/Terminal + Outbox
```

规则：

- QueryLoop 不出现 `if distributed`、PostgreSQL、Redis、Queue 或 Worker 分支；
- Distributed Worker 只负责取得执行权、Hydrate、调用标准 Agent、等待权威提交和 ACK 协调；
- 除 4.4 明确列出的 cancel breaking safe-stop override 外，Phase 5 的 Run、Turn、WaitReason、
  其余 Command 和 Checkpoint 语义不被重新定义；
- Planner、Team、A2A Task 可以拥有各自业务状态，但 Agent 执行真值仍是 Run 聚合根。

### 3.2 唯一异步 Port

`RunStore` 保留当前名称，但其方法直接改为 `async def`。不新增并存的
`AsyncRunStore`，也不保留同步执行 Port。

```python
class RunStore(Protocol):
    async def create(self, state: RunState) -> RunState: ...

    async def get(
        self,
        *,
        session_id: str,
        run_id: str,
    ) -> RunState | None: ...

    async def transition(
        self,
        *,
        session_id: str,
        run_id: str,
        status: RunStatus,
        wait_reason: WaitReason | None,
        guard: RunWriteGuard,
        turn_id: str | None = None,
    ) -> RunState: ...


@dataclass(frozen=True, slots=True)
class RunWriteGuard:
    expected_version: int
    claim_id: str | None = None
    fencing_token: int | None = None
```

规则：

- `expected_version` 必填，不再允许无条件覆盖；
- `claim_id` 与 `fencing_token` 必须同时存在或同时为 `None`；
- Local/Durable 两者都使用 `None`；Distributed execution-owned 写必须同时提供；
- `RunRuntime`、`RunDriver.prepare()`、`RunDriver.cancel_open()` 及所有状态写改为 async；
- `Agent.run()` 必须 `await` command accept/pending，首次 I/O 前不执行同步 Store 调用；
- InMemory Adapter 直接实现 async 方法；
- SQLite 允许使用 Durable 专属的异步边界，但 checkpoint capture 必须先在 Loop Task 完成；
- PostgreSQL 和 Redis 必须使用原生异步 Client/Pool，禁止 `to_thread`、`run_sync` 或
  executor 包装同步驱动；
- 不为调用方便重新增加同步 QueryLoop、同步 Agent 或同步 Store facade。

持有 I/O 资源的 Durable Profile 使用 async `open/build_agent/close`；Distributed Profile 使用
async `open/close` 和 claim-only 内部 hydrate。两者均使用 async context manager。Local
`AgentBuilder.build()` 仍是无 I/O 的同步组装。不得在同步 Profile constructor 中隐式连接数据库
或启动 Worker。

同一原则适用于执行路径上的 `ArtifactStore` 以及 Distributed 的 Plan、Task、Team、Queue、
Lease、Replay Port：in-memory 实现可以立即返回，但 I/O 实现必须原生 async。SQLite/
Filesystem Level 2 Adapter 可以使用 Durable 专属的受控 offload；该边界不得被 PostgreSQL、
Redis 或共享 Blob Adapter 复用。

### 3.3 Adapter 不持有 Runtime 对象

Phase 5 的 `bind_checkpoint_source()` 使 SQLite Adapter 弱持有运行时对象。Phase 6
移除该反向绑定。Kernel 在事件循环中捕获不可变 `SessionCheckpoint`，再显式交给 Store。

```python
class DurableStateStore(RunStore, Protocol):
    async def initialize_session(self, session: SessionState) -> None: ...
    async def load_checkpoint(self, session_id: str) -> SessionCheckpoint | None: ...
    async def accept_command(...) -> DurableCommandReceipt: ...
    async def load_pending_turn(...) -> AcceptedTurnInput | None: ...

    async def commit_waiting(
        self,
        *,
        checkpoint: SessionCheckpoint,
        run_id: str,
        turn_id: str,
        reason: WaitReason,
        guard: RunWriteGuard,
    ) -> RunCheckpoint: ...

    async def commit_terminal(
        self,
        *,
        checkpoint: SessionCheckpoint,
        run_id: str,
        turn_id: str,
        status: Literal["completed", "failed", "cancelled"],
        guard: RunWriteGuard,
    ) -> RunCheckpoint: ...
```

`commit_waiting()` 和 `commit_terminal()` 是 Session/Message/Context/Checkpoint/Run
状态的单事务边界。Store 不调用 `capture()`，不引用 QueryLoop、MessageRuntime 或
ContextRuntime。

`RunDriver` 是 execution start/waiting/terminal 的唯一提交 Owner。外部 CANCELLED 是唯一例外，
只由下方矩阵中的 `RunCommandService` transaction 提交。RunDriver 通过所有 Profile 都提供的
`RunCommitRuntime` 调用上述原子方法。`Agent.run()` 返回 terminal/waiting outcome 时，
权威提交已经完成；Worker 只在此后 ACK，不再次提交 Run 状态。Terminal Event 只能在
权威提交成功后发布。

### 3.4 状态写 Owner

| 写入 | 唯一 Owner | Fence 规则 |
|---|---|---|
| accepted input preparation、running checkpoint、WAITING、execution terminal | Worker 内的 RunDriver/RunCommitRuntime | 必须提供当前 claim ID、fence、expected version |
| 外部 cancel | RunCommandService transaction | 调用方不提供 fence；事务锁行、轮转 fence、清 Claim，并原子终结未提交 input/cursor/checkpoint |
| resume/wakeup/retry/HITL/side-effect resolution 接受 | RunCommandService transaction | 只接受 WAITING；锁行并记录当前 fence/version |
| expired claim recovery | Reconciler transaction | 轮转 fence、释放 Claim、恢复 input、写 recover Outbox；不提交 WAITING/terminal |
| submission | RunSubmissionService transaction | 无活动 Claim；锁 Session 并创建 QUEUED Run |

Store 层用事务和约束执行该矩阵。Channel、Transport、Worker supervisor、Outbox Relay 和
Event subscriber 都不是 Run 状态写 Owner。

### 3.5 Tenant Scope

```python
@dataclass(frozen=True, slots=True)
class RequestScope:
    tenant_id: str
    principal_id: str
```

`RequestScope` 由已通过鉴权的 Channel/CLI host 注入，禁止从用户 payload、Tool arguments
或 A2A message metadata 直接信任。所有 Submission、Command、Query、Stream、Artifact
和 A2A mapping Service 都显式接收 scope。

PostgreSQL 主键/唯一键固定 tenant-scoped，例如 `(tenant_id, session_id)`、
`(tenant_id, submission_id)` 和 `(tenant_id, command_id)`。Worker 不信任 Redis payload 中的
tenant 字段；它使用 outbox ID 从 PostgreSQL 加载权威 scope，并构造绑定该 tenant 的 Store；
Kernel 的 RunState 保持 Session-scoped，
不读取鉴权对象。跨 tenant 的 Session/Run/Artifact/cursor 一律返回稳定 not-found，不能
通过错误差异泄露存在性。

### 3.6 Distributed Shared Contract v1

Wave 2 冻结以下共享类型。后续 PostgreSQL、Redis、Worker、Channel 和 Transport 只能实现或
映射这些类型，不得在 Adapter 内复制同义 DTO：

- `AcceptedStartInput`、`AcceptedTurnExecution`、`AcceptedTurnInput`、
  `RunWriteGuard`、`DurableRunCommand`、`DurableCommandReceipt` 和 `RunState` 继续由
  `agentos.runtime` 唯一拥有；`agentos.distributed` 只引用，不重新定义或包装；
- `RunReadModel(tenant_id, session_id, run_id, status, wait_reason, aggregate_version, result)`
  是 PostgreSQL 查询边界的独立 immutable DTO；status/wait reason/result 分别直接引用 canonical
  `RunStatus`、`WaitReason` 和 `AgentResult` 并执行精确类型校验，COMPLETED 必须有 result，
  其他状态不得携带 result；
- `ArtifactRecord`、`ArtifactPage` 和 `ArtifactNotFoundError` 继续由
  `agentos.artifacts` 唯一拥有；
- `QueueDelivery(delivery_id, outbox_id, delivery_count)` 只承载 Redis delivery identity、
  稳定 outbox identity 和投递次数，不携带 tenant、session、run 或业务 payload；
- `RunDeliveryTarget(scope, outbox_id, session_id, run)` 是 PostgreSQL 根据 outbox ID
  解析出的权威执行目标；
- `ClaimedExecution(target, claim, execution)` 同时绑定 PostgreSQL 权威 delivery target、Claim、
  canonical `AcceptedTurnExecution` 及 execution guard 的 version/claim ID/fencing token；
  `execution.preparation` 必须是下文冻结的封闭 preparation，不能使用字符串 mode 或裸 cursor；
- `SessionLease(scope, session_id, owner_id, lease_id, expires_at)` 只表达 Redis 排他调度，
  不能替代 PostgreSQL fence；
- `OutboxRecord(scope, outbox_id, topic, payload, created_at, publish_attempts,
  last_publish_attempt_at, published_at)` 保存 PostgreSQL 真值和冻结 JSON payload；
- `OutboxClaim(record, owner_id, claim_id, expires_at)` 是 Relay 的短期领取结果；
- `RunEventEnvelope(tenant_id, session_id, run_id, turn_id, execution_attempt, event_sequence,
  event, occurred_at)` 是 Replay、SSE、WebSocket 和 A2A 共用的 typed event 信封；`event` 必须是
  Worker 从 canonical `TurnStreamEvent` 投影出的 allowlisted `LiveRunEvent`，`event_kind` 由具体
  projection 类型派生，调用方不能独立填写；
- `ReplayItem(cursor, event)`、`ReplayBatch(items, next_cursor)` 和
  `StreamGap(tenant_id, session_id, run_id, requested_cursor, oldest_available_cursor, reason)`
  定义 Replay + Tail 与显式 Gap；Replay identity 只包含 tenant/session/run，不保存发起
  principal；Gap reason 只允许 `trimmed` 或 `unavailable`；
- 非空 `ReplayBatch.next_cursor` 必须等于最后一个 item cursor；`StreamGap.requested_cursor` 必须
  等于当前 subscription 请求 cursor；
- `ArtifactContent(record, data)` 是 Artifact read 的 immutable application result；
- `WorkerState(worker_id, status, accepting_claims, active_claim_count, last_heartbeat_at,
  drain_started_at)` 是 Readiness 使用的不可变快照；status 只允许 `created`、`running`、
  `draining`、`closed`。

共享类型校验规则固定为：

- identifier 必须是精确 `str`、1..255 个 Unicode 字符、无首尾空白、无任何空白或控制字符；
- Artifact handle 额外遵守 canonical `art_` + UUID4；
- aggregate version、attempt count、active count 和 event sequence 是拒绝 `bool` 的非负整数；
- delivery count、fencing token 和 execution attempt 是拒绝 `bool` 的正整数；
- datetime 必须 timezone-aware，进入 DTO 时统一规范为 UTC；
- Outbox attempt/published 时间不得早于 created time；零次 attempt 不得有 attempt/published
  时间，非零 attempt 必须有 `last_publish_attempt_at`，`published_at` 不得早于最后一次 attempt；
- tuple 和 JSON payload 在构造边界复制并冻结；所有 DTO 使用 `frozen=True, slots=True`。

State/Application Port 按接口隔离原则冻结为 `RunSubmissionPort`、`RunCommandPort`、
`RunQueryPort`；一次 `submit` 或 `submit_command` 必须由 Port 在单个 PostgreSQL 事务中完成，
Service 不能跨多个 Port 拼接 Run、AcceptedInput 和 Outbox。Worker/Relay Port 冻结为
`ExecutionClaimPort`、`OutboxPort`、`QueuePort`、`LeasePort` 和 `EventReplayPort`；Artifact
Application Port 冻结为 `DistributedArtifactPort`。所有 I/O 方法必须是 coroutine 或返回
`AsyncIterator`，不得存在同步 shadow method。

Scope 规则分为两步：

1. Application、tenant state、claim、lease、replay 和 artifact 操作显式接收
   `RequestScope`；
2. Queue 全部操作和 Outbox Relay claim 是受信任内部 delivery bootstrap，不接收 request
   scope。Queue topic/partition 是部署级全局地址，只保存全局唯一 opaque `outbox_id`；
   `ExecutionClaimPort.resolve_delivery(outbox_id)` 必须从 PostgreSQL 返回权威
   `RunDeliveryTarget.scope`，后续所有操作显式使用该 scope。

因此 Redis tenant 字段即使存在也没有权限语义。Outbox claim 返回的 `OutboxRecord.scope`
同样来自 PostgreSQL，mark/release 必须提交完整 claim，不能由调用方另传 tenant；
`QueuePort.publish(record=claim.record)` 接收该权威 record，并且只向 record 指定的部署级全局
topic/partition 写入稳定 `outbox_id`，不写 scope 或业务 payload。确定性 outbox ID 的生成必须把
tenant 纳入 identity，最终 ID 在 PostgreSQL 中全局唯一，允许无 scope bootstrap 查询。

Side Effect DTO 和 `SideEffectStore` 必须由 Task 6 在 canonical capability/runtime leaf 中一起
冻结。Task 5 不允许用 `dict`、`object`、字符串状态或位于 `distributed` 的临时类型占位；
Task 6 完成后 PostgreSQL Adapter 直接实现该 canonical Port。

## 4. 首次 Run 与 Command Contract

### 4.1 RunSubmission

分布式入口不能直接调用 `agent.run(UserTurnInput)`，否则 Channel 节点会成为隐式 Worker。
首次执行使用独立的幂等提交类型：

```python
@dataclass(frozen=True, slots=True)
class RunSubmission:
    session_id: str
    submission_id: str
    content: str
    artifact_handles: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RunSubmissionReceipt:
    session_id: str
    run_id: str
    submission_id: str
    aggregate_version: int
    duplicate: bool
```

PostgreSQL 在一个事务中：

1. 按 `(tenant_id, submission_id)` 验重并校验 canonical input digest；
2. 锁定 Session，并校验不存在其他非终态 Run；
3. 创建 Run 并转换为 `QUEUED`；
4. 保存不可变的 pending start input；
5. 写入确定性 Wakeup Outbox；
6. 提交后返回 receipt。

canonical input 使用版本化 JSON，摘要覆盖 tenant、session、正文和有序
`artifact_handles`；提交事务必须验证所有附件属于同一 tenant/session。相同 scope 下同 ID
同内容返回 `duplicate=True`；同 ID 不同内容抛出 `RunSubmissionConflictError`。用户正文
只在 Worker 开始 Turn 后追加为 StoredMessage；pending start input 是 Command 数据，
不是第二份 Conversation Read Model。

所有 Adapter 必须调用 shared contract 的 `canonical_submission_digest(scope, submission)`：v1
使用 UTF-8 JSON、排序 object key、无多余空白，字段固定为 `version=1`、`tenant_id`、
`session_id`、`content` 和有序 `artifact_handles`；principal 与 submission ID 不进入摘要。

### 4.2 AcceptedTurnInput

Worker 从 PostgreSQL 加载已接受的内部输入：

```python
AcceptedTurnInput = AcceptedStartInput | AcceptedContinuationInput


@dataclass(frozen=True, slots=True)
class AcceptedStartInput:
    run_id: str
    submission_id: str
    input: UserTurnInput
    turn_id: str
    user_message_id: str


@dataclass(frozen=True, slots=True)
class AcceptedTurnExecution:
    input: AcceptedTurnInput
    guard: RunWriteGuard
    preparation: AcceptedTurnPreparation


@dataclass(frozen=True, slots=True)
class ApplyAcceptedInput:
    pass


@dataclass(frozen=True, slots=True)
class RestoreAcceptedTurn:
    cursor: RunExecutionCursor


AcceptedTurnPreparation = (
    ApplyAcceptedInput | RestoreAcceptedTurn | SideEffectResume
)
```

`AcceptedStartInput` 进入标准 User Turn 准备路径；`AcceptedContinuationInput` 同样增加
acceptance 时确定性分配的 `turn_id`，并进入标准 Continuation Turn 准备路径。Phase 5
`AcceptedContinuationInput.aggregate_version` 在 breaking migration 中移除，唯一版本依据是
`AcceptedTurnExecution.guard.expected_version`。内部类型不作为外部 Transport wire type
导出。

PostgreSQL Claim 根据权威 Run、accepted input、当前 execution cursor 和 resolution ledger
生成唯一 preparation：

- 当前 accepted turn 没有 cursor 时返回 `ApplyAcceptedInput`；RunDriver 只在权威 Run 为
  QUEUED 时执行 `QUEUED -> RUNNING`，Run 已为 RUNNING 时不重复 start transition；
- 当前 cursor 属于该 accepted turn 时返回 `RestoreAcceptedTurn(cursor)`；恢复不得重复追加
  StoredMessage、递增 turn number 或发布首次 Turn/UserMessage Event，但必须从已持久化
  ArtifactRef 和 continuation data 重建当前 Turn 必要的临时投影；
- 首次执行 `resolve_side_effect` 创建新的 continuation execution turn，并通过
  `SideEffectResume` 携带旧 source `pending_tools` cursor、current ledger record 和 typed
  resolution；它不等同于 `RestoreAcceptedTurn`，resolution payload 不投影给 Provider；新的
  continuation cursor 提交后发生崩溃时，后续 claim 使用 `RestoreAcceptedTurn` 恢复该新 Turn；
- cursor 属于其他 turn、cursor/checkpoint/version 不一致或 resolution source 无法验证时
  fail closed。

RunDriver 在任何 Run/Turn 写入前拒绝缺失 `SideEffectResumeValidator` 的
`SideEffectResume`。Validator 是独立的内部协作 Port，不属于 `SideEffectStore`，因为
Ledger CRUD 不能证明 source cursor、current attempt、resolution 和当前 fence 的组合一致性。

accepted turn 使用以下持久状态机：

```text
ACCEPTED
  -> CLAIMED(claim_id, fencing_token)
  -> COMMITTED(checkpoint_id, terminal_or_waiting)

CLAIMED --claim expired/reconciled--> ACCEPTED

ACCEPTED/CLAIMED --external cancel transaction--> COMMITTED(cancelled checkpoint)
```

`load` 不改变状态；Worker 必须通过原子 `claim_pending_turn()` 取得
`(ExecutionClaim, AcceptedTurnExecution)`。该事务同时激活 claim、轮转 fence 并执行
ACCEPTED -> CLAIMED，不存在“已有 active claim 但 input 尚未 claimed”的窗口。input 在
WAITING/terminal checkpoint 成功前一直可恢复，不存在
不可逆的“读取即消费”。首次输入在 acceptance 时确定性分配 `turn_id` 和
`user_message_id`；QueryLoop 使用这些 ID 幂等准备 User Turn。Continuation 不追加 User
StoredMessage。

如果进程在 `QUEUED -> RUNNING`、用户消息内存追加或首次 Provider 调用附近崩溃，过期
Claim Reconciler 轮转 fence、让 Run 保持 RUNNING、将 CLAIMED 重新变为 ACCEPTED，并保留
相同 ID。下一 Worker 根据权威 cursor 取得 `ApplyAcceptedInput` 或
`RestoreAcceptedTurn` 后接管。尚未提交的内存状态被丢弃；已由 WAITING/terminal 原子提交的
input 已是 COMMITTED，重投只 ACK。不得在独立
事务中提前标记 input consumed。

### 4.3 Running Execution Checkpoint

Phase 6 增加受 Fence 保护的 Running Execution Checkpoint。它不是 Provider transcript，
只保存 AgentOS 已拥有的 StoredMessage/ActiveWindow/Context 恢复状态和有界执行游标：

```python
ExecutionCheckpointStage = Literal[
    "before_provider",
    "pending_tools",
    "after_tools",
]


@dataclass(frozen=True, slots=True)
class PendingToolInvocation:
    invocation_id: str
    provider_tool_call_id: str
    tool_name: str
    invocation_ref: ProtectedPayloadRef


@dataclass(frozen=True, slots=True)
class RunExecutionCursor:
    turn_id: str
    stage: ExecutionCheckpointStage
    provider_call_index: int
    assistant_message_id: str | None
    pending_tools: tuple[PendingToolInvocation, ...] = ()
```

`invocation_id` 由 SDK 使用 tenant/run/turn/provider-call-index/tool-index 确定性分配，独立于
Provider 返回的 tool call ID。Cursor 同时保留原始 Provider tool call ID，以便恢复严格的
tool-use/tool-result pairing。`ProtectedPayloadRef` 通过注入的 PayloadProtector 加密保存规范化
arguments；默认 Trace、Event、错误和 Read Model 不暴露该内容。

持久化 StoredMessage 中的 ToolCall arguments 同样使用受保护 payload 表示；Hydration 在
授权 scope 内解密为内存 ProviderInput，数据库 JSON、Checkpoint metadata 和日志不保存
明文 secret。Distributed Profile 未配置 PayloadProtector 时拒绝持久执行 Tool，而不是降级
为明文。

RunDriver 的安全提交点固定：

1. 应用 accepted input 后，在首次 Provider 前提交 `before_provider`；
2. Provider 完整响应已追加 assistant StoredMessage 后、任何 Tool handler 开始前，提交
   `pending_tools` 和全部稳定 invocation mapping；
3. Tool batch 已通过 Ledger 得到确定结果并追加全部 Tool Result 后，提交 `after_tools`；
4. 无 Tool 的最终 Provider response 直接进入 terminal commit。

Running Execution Checkpoint 在一个 fenced PostgreSQL 事务中写入 Session/Message/Context、
cursor 和新的 aggregate version，但 Run status 保持 RUNNING。每次成功提交后 RunDriver 使用
返回的新 `RunWriteGuard.expected_version` 继续执行。

崩溃恢复规则：

- `before_provider`：允许重新调用 Provider，尚无 Tool 被调用；
- `pending_tools`：不得重新调用 Provider；按 cursor 的 stable invocation 恢复 Tool batch；
- Ledger `COMPLETED(outcome_kind=provider_result)`：从 `result_ref` 重建原 provider tool result；
- Ledger RESERVED：尚未开始 handler，可执行；
- Ledger STARTED 且 pure/idempotent：按相同 invocation/operation ID 重试；
- Ledger STARTED 且其他 policy：下一 Worker 的 RunDriver 用最新 execution checkpoint 原子
  提交 `side_effect_reconciliation` WAITING；
- `after_tools`：保持已持久化 tool pair，进入下一 Provider call。

Reconciler 只轮转 fence、释放旧 Claim、恢复 AcceptedInput 并发布 recover Outbox，不捕获
Runtime checkpoint，也不直接提交 WAITING/FAILED。所有 live execution checkpoint、WAITING
和 terminal 仍由下一 Worker 内的 RunDriver 提交。

`WaitRequest` 是特殊控制结果，不是 Provider Tool Result。可能返回它的 Tool 必须显式声明
`wait_capable=True`，同时使用 `pure` SideEffectPolicy 和 `EXCLUSIVE` ToolConcurrencyPolicy。
包含 wait-capable Tool 的 Provider batch 必须只有该一个调用；Runtime 在任何 handler 开始前
拒绝混合 batch，避免已经发生的 peer side effect 被等待投影丢弃。

handler 返回 `WaitRequest` 时，RunDriver 的 fenced `commit_waiting()` 在同一事务中：

1. 将该 invocation 记录为 `COMPLETED(outcome_kind=wait_control, wait_reason_digest=...)`；该
   outcome 不得出现在仍为 RUNNING 的 execution cursor 中；
2. 从 ActiveWindow 移除当前 assistant tool-use，不追加伪造 Tool Result，但保留 StoredMessage
   作为审计事实；
3. 清除当前 running execution cursor，提交 Session/Message/Context checkpoint；
4. 将 accepted input 置为 COMMITTED、Run 置为 WAITING，并写入确定性 Outbox。

崩溃发生在该事务前时，状态仍是 `pending_tools` 加 RESERVED/STARTED；pure policy 使用相同
invocation ID 重试 handler。事务提交后不得再次调用该 handler。后续 Durable Command 创建新的
continuation Turn，通过标准 `continuation-data` 投影恢复，不恢复旧 tool batch。由此任何 Provider
请求都不会看到未配对的 tool-use，也不会把内部等待控制结果伪造成用户输入或 Tool Result。

### 4.4 Durable Command

Phase 5 的 `resume`、`wakeup`、`retry` 和 `hitl_answer` 保持不变。Phase 6 增加只允许处理
`side_effect_reconciliation` WaitReason 的 `resolve_side_effect`，并对 `cancel` 做 breaking
safe-stop override：非终态不再等于无条件可取消，未解决或正在执行的副作用必须先完成治理。
分布式
`accept_command()` 在 PostgreSQL 事务中完成：

```text
lookup command_id
-> exact duplicate: prior receipt
-> conflicting duplicate: reject
-> lock Session + Run
-> validate state / wait kind / due time
-> cancelling: lock current Ledger rows
-> STARTED/AMBIGUOUS/COMPENSATING exists: reject without rotating fence or changing Run
-> resolve RESERVED as cancelled_before_start; preserve known terminal Ledger rows
-> insert immutable Command
-> WAITING -> QUEUED or nonterminal -> CANCELLED
-> aggregate_version += 1
-> cancelling nonterminal: fencing_token += 1 and clear active claim
-> commit current ACCEPTED/CLAIMED input, clear running cursor, write CANCELLED terminal record
-> insert deterministic Outbox
-> COMMIT
```

Command API 返回的是“已持久接受”，不是“已执行完成”。HTTP Command Response 与 SSE/
WebSocket 结果观察必须分离。

`accept_command()` 始终返回 `DurableCommandReceipt`。内部 Accepted Input 只由
claim-aware `claim_pending_turn()` 返回。Ingress 不持有 Worker fence：Resume/Wakeup 等
事务锁定 WAITING Run 并记录当前 fence。

Cancel 只在 Side Effect 安全停止点提交。cancel transaction 在轮转 fence 前锁定当前 Run 的
Ledger rows：RESERVED 原子变为 `RESOLVED(cancelled_before_start)`；COMPLETED、COMPENSATED 和
RESOLVED 保留；存在 STARTED、AMBIGUOUS 或 COMPENSATING 时抛出
`SideEffectInFlightError`，事务不写 Command、不改变 Run/input/cursor，也不轮转 fence。正在执行的
Worker 因此仍可完成 Ledger 写；若 Worker 崩溃，则普通 claim recovery 把 operation 带入既定
retry/reconciliation 路径。AMBIGUOUS 可能长期保持 WAITING；调用方必须先提交受权 resolution。
resolution 后 Run 仍为非终态时，再按原 command idempotency 规则重试 cancel；若 resolution 已将
Run 置为 FAILED，则以该终态结束。该 409 行为是 Phase 6 breaking contract，不提供 Phase 5
“任意非终态 cancel”兼容 fallback。

通过安全检查后，Cancel QUEUED/RUNNING 在同一事务中轮转 fence、清除
Claim，将当前 ACCEPTED/CLAIMED input 置为 COMMITTED，清除 running cursor，以最新已持久
SessionCheckpoint 写入 CANCELLED terminal record，再将 Run 转为 CANCELLED 并写 Outbox。
WAITING cancel 复用已提交 checkpoint 且不存在未提交 input。Cancel commit 后旧 Worker 的
checkpoint、terminal、heartbeat、release 和 ledger complete 全部失败；重复 delivery 只 ACK。

## 5. PostgreSQL Truth Contract

PostgreSQL 是以下数据的唯一持久真值：

- Session 和 active-run constraint；
- Run、aggregate version、WaitReason 和最终结果；
- RunSubmission、Durable Command 和 pending accepted turn；
- SessionCheckpoint、StoredMessage、ActiveWindow 和 Context 恢复状态；
- Running Execution Checkpoint 和 RunExecutionCursor；
- Execution Claim、单调 fencing token 和数据库时间的 expiry；
- Outbox 和 relay 状态；
- Side Effect Ledger；
- Plan/Task/Team 的持久业务状态；
- A2A task/run mapping 和 push delivery 状态。

数据库必须包含：

- `(tenant_id, session_id) WHERE status IN (...)` 的 partial unique active-run index；
- Command/Submission 的 tenant-scoped unique id；
- Session 当前 fencing token 和 active claim identity；
- Checkpoint/Command 记录上的 fencing token；
- Outbox 的确定性 ID、topic、payload、created/delivered attempt metadata；
- Side Effect Ledger 的 operation ID、invocation/result ref、digest、status 和
  compensation metadata。

数据库事务使用数据库时间判断 claim 到期。禁止把 `time.monotonic()`、节点本地时间戳或
Redis payload 中的时间当作跨节点权威时钟。

Phase 6 将 `aggregate_version` 明确为每次权威 Run 聚合写的 CAS 版本，不再只统计 status
transition。Command、start、Running Execution Checkpoint、WAITING、terminal 和外部 cancel
成功时各递增一次；Claim heartbeat 和纯 delivery metadata 不递增。Store 返回新 version，
RunDriver 用它构造下一次 immutable guard。

Side Effect Ledger 是受同一 claim/fence 保护的独立 operation 聚合。reserve/start/complete/
resolution/compensation 等 Ledger-only transaction 必须比较当前 guard，但不递增 Run
`aggregate_version`，因此同一 Tool batch 的独立 invocation 可以并行完成。只有同时写入 Run、
SessionCheckpoint 或 accepted input 的 WAITING/terminal transaction 才推进 version 并返回新 guard。

## 6. Lease、Claim 与 Fencing

```python
@dataclass(frozen=True, slots=True)
class ExecutionClaim:
    tenant_id: str
    session_id: str
    run_id: str
    owner_id: str
    claim_id: str
    fencing_token: int
    expires_at: datetime
```

Claim 流程：

1. Worker 从 Redis 收到 delivery，暂不 ACK；
2. Worker 获取短期 Redis Session Lease；
3. PostgreSQL `claim_pending_turn()` 锁定 Session/Run/input，使用数据库时间校验状态，
   递增 fencing token，并原子返回 `ExecutionClaim + AcceptedTurnExecution`；
4. 只有 PostgreSQL 激活成功后才 Hydrate 和执行 Provider Loop；
5. Heartbeat 续期 Redis Lease 后，再使用当前 token 续期 PostgreSQL Claim；任一步失败
   都停止新 Provider/Tool 工作并取消当前 AgentStream；
6. WAITING/终态提交在同一 PostgreSQL 事务中校验 token、写 Checkpoint、转换状态并清除
   active claim；
7. PostgreSQL 提交成功后才 ACK Redis delivery。

以下 PostgreSQL 写必须在自身事务内比较当前 fencing token：

- Claim activate/heartbeat/release/reclaim；
- `QUEUED -> RUNNING` 及所有后续 Run transition；
- Session、Message、ActiveRef、Context 和 Checkpoint 写；
- pending accepted turn claim/commit；
- RUNNING cancellation；
- Side Effect Ledger reserve/complete；
- 过期 Claim 的恢复写。

Redis 前后两次 `ensure_owned()` 不能替代数据库事务内 fencing。旧 Worker 的 token 在新
Claim 激活后，对 checkpoint、terminal、heartbeat、release 和 side-effect complete
全部失效。

过期 Claim 不在进程启动时直接标记 Run FAILED。Reconciler 必须先由 PostgreSQL
`recover_expired_claim()` 轮转 fencing token、使 CLAIMED input 回到 ACCEPTED，再根据
Side Effect Ledger 的固定恢复矩阵发布 recover delivery。Run 保持 RUNNING，由下一 claim
根据权威 cursor 生成 typed preparation 后接管；需要 reconciliation 时也由该 Worker 的
RunDriver 从最新 execution checkpoint 原子提交 WAITING。不得伪造一条普通
`RUNNING -> QUEUED` 领域转换。

本 Contract 不再定义独立 `generation`。`fencing_token` 就是 Session/Run 的单调执行代次，
事件中的 `execution_attempt` 等于该 token。唯一 live event key 是
`(tenant_id, run_id, fencing_token, event_sequence)`；takeover 后旧 attempt 的事件和状态写
不能冒充新 attempt。

## 7. Queue、Outbox 与 ACK

Redis Queue/Wakeup 采用 at-least-once。PostgreSQL Outbox 是从真值状态到 Redis 的唯一
发布 Owner。

```text
PostgreSQL state/command transaction
-> insert Outbox(outbox_id)
-> COMMIT
-> Outbox Relay XADD * outbox_id=<stable-id>
-> mark relay attempt/delivered
```

规则：

- Redis Stream entry ID 由 Redis 生成并只作为 delivery/replay cursor；`outbox_id` 是稳定的
  业务去重键，禁止每次重发生成随机 envelope ID；
- XADD 成功但 mark-delivered 前崩溃可以重复投递；Consumer 必须去重；
- Worker 在 PostgreSQL terminal/waiting commit 成功前不得 ACK；
- 重投时若 PostgreSQL 已证明对应 accepted turn 完成，Worker 直接 ACK，不再次执行；
- Redis 故障不回滚已经提交的 Command；Relay 恢复后继续发布；
- PostgreSQL 故障时 Worker 不 ACK Redis，并 fail closed；
- Dead Letter 只是交付状态，不得单独把 Run 标记终态；
- Stream trimming 不得删除仍处于 Pending 的 delivery；
- Pub/Sub 不用于 durable continuation；
- 读取即删除、无 ACK 的 consume API 不进入新路径。

Outbox Relay 使用 PostgreSQL `claim_batch()`/`mark_published()`/`release_claim()` Port。
参考 Adapter 通过 `FOR UPDATE SKIP LOCKED` 和短期 relay claim 支持多个 Relay；乱序 claim
不影响 Redis，因为 Stream ID 不使用 outbox UUID。重复 XADD 产生多个 entry 时，Consumer
按 payload 中的 `outbox_id` 去重并分别安全 ACK。

## 8. Worker Contract

`DistributedWorker` 是唯一分布式执行 Host，但不是第二个执行引擎：

```text
receive delivery
-> load PostgreSQL truth
-> exact duplicate/terminal: ACK
-> acquire Redis lease
-> PostgreSQL claim_pending_turn -> Claim + AcceptedTurnExecution
-> hydrate Agent from latest checkpoint
-> Agent.run(internal execution, stream=True)
-> RunDriver commits WAITING/terminal with fence
-> ACK delivery
```

Worker 是该 `AgentStream` 的唯一消费者。它在 `async with stream` 中发布 Event Sink；SSE、
WebSocket 和 A2A observer 只订阅 Redis replay，不消费 Worker stream。Heartbeat/claim 丢失
或 drain timeout 时，Worker 调用 `await stream.aclose()` 并等待全部 cleanup；关闭完成前不
释放 Agent/Store，也不得启动新的 Provider/Tool。`Agent.run()` 返回或 stream terminal event
表示 RunDriver 已完成权威提交，Worker 不执行第二次 terminal transition。

Worker 必须提供 `start()`、`drain(timeout)` 和 `close()` 的异步生命周期。Drain 顺序固定：

1. 标记 `DRAINING`；
2. 停止新的 receive/claim；
3. 取消阻塞读取；
4. 继续为活动 Claim heartbeat；
5. 等待活动执行提交；
6. 超时后关闭活动 AgentStream，等待 cleanup，停止续租并让 Claim 自然过期。

禁止批量释放其他 Worker 的 Lease，禁止在进程退出时无条件失败所有 RUNNING Run。

## 9. Side Effect Policy

本节的稳定 identity、attempt、handler error、result ref、WAITING 原子载荷、resolution resume
和 compensation handler 细节由
`2026-07-20-agentos-phase6-task6-side-effect-contract-addendum.md` 精化。Task 6 实现必须同时
满足两份合同，不得在代码中自行补充未冻结语义。

`ToolConcurrencyPolicy` 只决定同一批次是否可并行；它不能表达重试安全性。
`RegisteredTool` 必须增加独立的 `SideEffectPolicy`：

```text
pure
idempotent
deduplicated
compensatable
non_retryable
```

工具 handler 接收统一的 `ToolInvocation`，不再只接收裸 arguments：

```python
@dataclass(frozen=True, slots=True)
class ToolInvocationContext:
    invocation_id: str
    operation_id: str
    tenant_id: str
    session_id: str
    run_id: str
    turn_id: str
    tool_call_id: str
    attempt: int


@dataclass(frozen=True, slots=True)
class ToolInvocation:
    arguments: FrozenJsonObject
    context: ToolInvocationContext
```

`operation_id` 由 `tenant_id + run_id + turn_id + invocation_id` 确定性派生，重试时不变；
原始 `tool_call_id` 只用于 Provider tool pair。迁移发生在 breaking release，不保留两套
handler signature。`attempt` 是同一 operation 的 handler
调用次数，不等于 fencing token；当前 `RunWriteGuard` 由 Executor/Ledger Runtime 持有，
不暴露给业务 handler。

Side Effect Ledger 使用以下状态：

```text
RESERVED -> STARTED -> COMPLETED
    |          |
    |          +-> AMBIGUOUS -> COMPENSATING -> COMPENSATED
    |                       \-> RESOLVED
    \-> RESOLVED(cancelled_before_start)
```

Port 至少提供 fenced `reserve/get/mark_started/complete/mark_ambiguous/
begin_compensation/complete_compensation/resolve`。Ledger 保存 canonical invocation digest、
安全有界的 `invocation_ref`、可重载 `result_ref`、result digest、policy、claim/fence 和
`outcome_kind`/compensation metadata。小结果可以 inline；超过 Tool Result Budget 的结果必须使用同
tenant/session ArtifactRef。只保存 digest 不能满足结果复用。

所有 Tool invocation 在调用 handler 前先 reserve，并在实际调用前 mark_started。相同
operation 已 `COMPLETED(provider_result)` 时从 `result_ref` 重建同一 Tool Result，不再次调用
handler；`COMPLETED(wait_control)` 只由同事务 WAITING checkpoint 消费，不映射为 Provider
Tool Result。

| Policy | RESERVED 恢复 | STARTED/claim lost | COMPLETED |
|---|---|---|---|
| `pure` | 执行 | 自动重试 | 复用 result_ref |
| `idempotent` | 执行 | 使用相同 operation ID 自动重试 | 复用 result_ref |
| `deduplicated` | 执行 | AMBIGUOUS，进入 reconciliation WAITING | 复用 result_ref |
| `compensatable` | 执行 | AMBIGUOUS，进入 reconciliation WAITING | 复用 result_ref |
| `non_retryable` | 执行 | AMBIGUOUS，进入 reconciliation WAITING | 复用 result_ref |

`compensatable` Tool 必须同时声明满足 `idempotent` 契约的 compensation handler；无法提供
幂等补偿的 Tool 不得声明 compensatable，只能使用 non_retryable。Runtime 不自动猜测补偿时机；
应用通过 `resolve_side_effect` Command 选择 `accept_result`、`retry_proven_safe`、
`compensate` 或 `fail`。只有 compensatable 支持 `compensate`；补偿使用稳定的
`operation_id + ":compensation"`，并把该 ID 传给 compensation handler。COMPENSATING 时
claim 丢失必须使用同一 ID 重试；COMPENSATED 时直接继续 terminal commit，不再次调用。
未解决的 AMBIGUOUS operation 阻止该 Turn 继续调用 Provider。

`side_effect_reconciliation` 是类型化 WaitReason。`resolve_side_effect` 必须引用当前
operation ID，Store 校验 policy、Ledger 状态和 Run wait reason 后才执行确定性迁移；错误
决议 fail closed。`deduplicated` 不宣传 exactly-once：外部效果已发生但 Ledger 未 complete
时必须等待应用 reconciliation。Provider Retry Policy 不得决定 Tool Retry。

Resolution Command 先按普通 Command 原子执行 `WAITING -> QUEUED`、创建 accepted
continuation 和 Outbox；下一 Worker 的 RunDriver 按以下矩阵处理并提交结果：

| Resolution | 前置条件 | Ledger 后置 | Run/AcceptedInput 后置 | 后续 |
|---|---|---|---|---|
| `accept_result` | AMBIGUOUS；提供并校验同 scope 的 result_ref/digest | `RESOLVED(accepted)` + result_ref | input 保持 CLAIMED，直到后续 WAITING/terminal commit 才 COMMITTED | 恢复原 tool result pair，继续 Provider |
| `retry_proven_safe` | AMBIGUOUS；提供审计 attestation；非 pure/idempotent | `RESOLVED(retry_safe)` 后创建同 invocation 的新 RESERVED attempt | input 保持 CLAIMED 直到 outcome commit | 重试原 pending tool，不重新调用 Provider |
| `compensate` | AMBIGUOUS + compensatable + compensation handler | fenced completion transaction 执行 `COMPENSATING -> COMPENSATED` | input 保持 CLAIMED；后续 FAILED terminal transaction 才置 COMMITTED | RunDriver 观察 COMPENSATED 后只提交 FAILED，不自动重做原 Tool |
| `fail` | 任意 AMBIGUOUS | `RESOLVED(failed)` | FAILED checkpoint、Run FAILED、input COMMITTED | 不调用 Tool/Provider |

每种决议的 Ledger 更新与 execution checkpoint/terminal 必须在各自 fenced transaction 中完成；
Ledger-only transaction 校验 guard 但保持其 `expected_version` 不变，只有 Run 聚合 transaction
推进 aggregate version 并写对应 terminal/wakeup Outbox。`accept_result`/`retry_proven_safe`
后仍可能继续多个 execution checkpoint，accepted input 在这些 running checkpoint 中保持 CLAIMED，
最终 outcome 由 RunDriver 提交。补偿完成与 FAILED terminal 是两个明确的 fenced transaction：
前者只持久化 COMPENSATED 并保留当前 guard version，后者提交 FAILED checkpoint、input COMMITTED、
递增 aggregate version 与 terminal Outbox。

补偿故障窗口固定：调用前保持 COMPENSATING 并可执行；效果发生后、COMPENSATED 落库前使用
稳定 key 幂等重试；COMPENSATED 后、FAILED terminal commit 前只重试 terminal commit。

Built-in Context/Skill 只读工具可声明 `pure`；所有外部 Tool 必须显式声明策略。

## 10. Distributed Artifact Contract

Distributed Profile 必须注入跨 Worker 可访问的共享 ArtifactStore，禁止默认使用节点本地
路径：

```text
PostgreSQL Artifact metadata + tenant/session scope
                    |
                    v
Async BlobStore (S3-compatible or deployment-provided shared backend)
```

Phase 6 把 `ArtifactStore` I/O 方法和 `ArtifactRuntime` 中涉及 I/O 的方法升级为唯一 async
契约。Artifact metadata、blob key、size/digest 和 lifecycle 在 PostgreSQL；bytes 只进入
BlobStore，不进入 Message、Checkpoint、Redis 或 PostgreSQL JSON。

上传使用 durable staging metadata -> blob conditional put -> metadata activation transaction。
`staging` metadata 和已完成但尚未 activation 的 blob 都是可恢复状态；同一 tenant 下相同
`upload_id` 和相同 canonical metadata/digest 必须复用原 artifact ID 并幂等收敛，不同内容必须在
blob I/O 前冲突失败。失败或 cancellation 保留 staging/blob 供同一 upload 重试，不能执行
best-effort 删除制造新的崩溃窗口。`staging` 对 read/list 不可见，只有完成 size/digest 校验并
原子切换为 active 后才能读取。

在 Artifact upload 尚无可续租 lease/ownership contract 前，不执行基于 age 的 stale staging
cleanup，避免 cleanup 与慢上传竞态；该能力延期到独立 upload lease Spec。删除使用 metadata
tombstone/transaction 与幂等 blob cleanup，不能让可见 metadata 指向已知删除内容。跨
tenant/session 的 get/read/list/cursor 一律 not-found。Worker 在执行 Provider
前异步解析 active mount 并把 bytes 放入仅当前 Turn 的内存 projection cache；同步
ProviderRequest build 不发起网络/磁盘 I/O，Turn 终态清除 cache 但不删除 Artifact。

参考实现至少提供 PostgreSQL metadata + S3-compatible BlobStore Adapter；部署方可以注入
满足同一 Contract 的共享后端。Readiness 必须验证 metadata、blob put/read/delete probe 和
跨节点可见性。Worker A 上传、Worker B hydrate 后 `load_attachment` 并投影是发布门禁。

## 11. Event 与 Stream Replay

PostgreSQL 保存终态和必要审计事实；Redis 保存短期 live event replay。每个事件包含：

```text
tenant_id / session_id / run_id / turn_id
execution_attempt / event_sequence / event_kind
```

规则：

- Worker Event Sink 发布类型化 QueryLoop Event，不保存 ContextSnapshot 或敏感 payload；
- 安全投影保留用户可见 content/final/status/plan 信息，但删除原始 Prompt、thinking、Tool Result、
  WaitReason detail、异常对象/文本、内部 cancellation detail；Tool 事件只保留 name、call ID 和状态；
- Redis replay 使用稳定 cursor、Replay + Tail 和显式 Gap；
- 裁剪、Redis 丢失或 cursor 过旧时返回 `StreamGap`，不得伪造连续历史；
- Terminal truth 始终可从 PostgreSQL 查询；
- `RunEventStream.subscribe()` 在返回 iterator、Channel 发送 SSE headers 前，必须 await
  `RunQueryPort` 完成同 tenant/session/run 的 PostgreSQL existence/authorization preflight；
- `EventReplayPort.follow()` 返回显式支持 async `aclose()` 的 `EventSubscription`；Application
  validation wrapper 在正常结束、校验失败、消费者取消/关闭三条路径都必须关闭底层 tail；
- SSE/WebSocket disconnect 不取消 Run，除非客户端另行提交 Cancel Command；
- 多个 observer 不分享消费型 ACK，不影响 Worker delivery。

## 12. Transport 与 Channel 边界

### 12.1 Transport

`agentos.transports` 只拥有：

- immutable wire type；
- request decode/validation；
- domain/application DTO mapping；
- response/event encode；
- SSE/WebSocket/A2A frame serialization。

Transport 禁止导入：

```text
RunStore / TaskStore / SessionProvider
PostgreSQL / Redis / SQLite client
Agent / QueryLoop / Worker / Daemon
concrete RuntimeProfile
```

Transport 不执行网络 I/O。HTTP/A2A Client 属于 Adapter，连接生命周期属于 Channel。

### 12.2 Channel

Channel 可以拥有连接、鉴权、限流、路由、heartbeat 和 disconnect 生命周期。HTTP/SSE/
Artifact Channel 只调用：

- `RunSubmissionService`；
- `RunCommandService`；
- `RunQueryService`；
- `RunEventStream`；
- `ArtifactService`。

A2A Channel 还可以调用 Wave 4 增补冻结的 `A2ATaskService`、`A2ATaskCatalogService`、
`A2APushService` 和 immutable `A2AAgentCardProvider`，用于持久 task/run binding、tenant-scoped
catalog、push config 与静态 card projection；它们不拥有 Run 状态写权限。Channel 不得直接调用其
Port 或 PostgreSQL Adapter。该列表是 A2A Channel 的完整 Application/Provider 白名单。

Channel 不创建或更新 Run 状态，不持有 Retry 真值，不调用 Tool。

### 12.3 HTTP/SSE

- HTTP 提交首次 Run 或 Durable Command，返回持久 receipt；
- HTTP Query 返回 PostgreSQL Read Model；
- SSE 只观察 Event Stream；
- SSE resume cursor、heartbeat、terminal 和 gap 使用纯 mapping；
- ASGI app 只组合 route、auth、service 和 encoder。

HTTP v1 wire contract：

| Operation | Idempotency | Service | Success | Domain errors |
|---|---|---|---|---|
| `POST /v1/sessions/{sid}/runs` | required `Idempotency-Key` -> submission_id | `runs.submit` | `202` receipt | `400` validation, `404` scoped artifact, `409` active/conflict, `503` backend |
| `POST /v1/sessions/{sid}/runs/{rid}/commands` | required `Idempotency-Key` -> command_id | `commands.submit` | `202` receipt | `400` validation, `404` scoped run, `409` state/conflict/not-due/side-effect-in-flight, `503` backend |
| `GET /v1/sessions/{sid}/runs/{rid}` | none | `runs.get` | `200` read model | `404` scoped run, `503` backend |
| `GET /v1/sessions/{sid}/runs/{rid}/events` | `Last-Event-ID` cursor | `events.subscribe` | `200 text/event-stream` | `404` scoped run, `503` backend |
| `POST /v1/sessions/{sid}/artifacts` | required `Idempotency-Key` | `artifacts.upload` | `201` metadata | `400/413/415` validation/policy, `409` conflict, `503` backend |
| `GET /v1/sessions/{sid}/artifacts` | cursor | `artifacts.list` | `200` page | `400` cursor, `503` backend |
| `GET /v1/sessions/{sid}/artifacts/{aid}` | none | `artifacts.read` | `200` typed bytes | `404` scoped artifact, `503` backend |
| `DELETE /v1/sessions/{sid}/artifacts/{aid}` | required `Idempotency-Key` | `artifacts.delete` | `204` | `404` scoped artifact, `409` conflict, `503` backend |

Tenant 不出现在 URL/body，由 auth 注入 RequestScope。Transport error body 固定
`{code, message, request_id}`，不得包含 Adapter exception。SSE cursor 绑定
tenant/session/run；过旧 cursor 发送一个 `stream_gap` frame 后关闭，客户端再查询 Run Read
Model，不用 HTTP 连接状态推断执行结果。

Artifact upload 使用有界 `multipart/form-data` 单文件 part；Transport 只解析 filename、
media type 和 bytes 并执行请求大小门禁，ArtifactService 执行 tenant/session policy 和存储。
read 响应只返回受控 media type/filename/content，不返回 blob key、signed credential 或本地路径。

### 12.4 WebSocket

WebSocket 在 6C 实现，不与 6B 顺带混合。Wire protocol 至少包含：

- `submit_run`、`submit_command`；
- `subscribe_run`、`unsubscribe_run`；
- `receipt`、`event`、`stream_gap`、`error`；
- client request ID 与 server event cursor。

连接只保存 subscription，不保存 Run 真值。连接重建后按 cursor 重新订阅。
每个连接和 subscription 使用有界发送缓冲；慢消费者超过上限时发送可用的最后 cursor，
以稳定 `slow_consumer` code 关闭连接。禁止无界积压或反向阻塞 Worker Event Sink。重连使用
cursor replay，gap 行为与 SSE 相同。

### 12.5 A2A

`transports.a2a` 只拥有 message/card/operation/push wire type、serialization、版本协商和
映射。A2A endpoint 调用 Application Service；A2A task/run mapping 和 push delivery
真值在 PostgreSQL Adapter；push worker 属于 Worker 层；auth/trust/egress 属于 Policy。

| A2A operation | AgentOS mapping | Idempotency / stream |
|---|---|---|
| `SendMessage`（无 task） | `RunSubmissionService.submit` | messageId -> submission_id；返回 A2A Task read model |
| `SendMessage`（WAITING human_input） | `RunCommandService.submit(hitl_answer)` | messageId -> command_id；文本/parts 映射为非空 answer payload |
| `SendMessage`（WAITING remote_result/resource_availability） | `RunCommandService.submit(wakeup)` | messageId -> command_id；A2A result metadata 进入有界 payload |
| `SendMessage`（WAITING timer/retry_backoff） | 拒绝 | 返回稳定 not-due/state mapping，不隐式提前唤醒 |
| `SendMessage`（WAITING side_effect_reconciliation） | 拒绝 | 普通 A2A Message 无权构造 resolution；使用受权 `resolve_side_effect` API |
| `SendStreamingMessage` | 与 `SendMessage` 相同后订阅 `RunEventStream` | snapshot-first；scoped cursor 只通过协商扩展恢复 |
| `GetTask` / `ListTasks` | `RunQueryService` / `A2ATaskCatalogService` | tenant-scoped binding 与 keyset page |
| `CancelTask` | `RunCommandService.submit(cancel)` | 派生 operation identity -> command_id |
| `SubscribeToTask` | `RunEventStream.subscribe` | snapshot-first replay/gap 与 SSE 相同 |
| `Create/Get/List/DeleteTaskPushNotificationConfig` | `A2APushService` | PostgreSQL truth + Outbox，不进入 Transport |
| `GetExtendedAgentCard` | immutable `A2AAgentCardProvider` | 不读取 Task/Store metadata |

Run 状态映射固定：CREATED/QUEUED -> `submitted`，RUNNING -> `working`，WAITING human
input -> `input-required`，其他 WAITING -> `working`，COMPLETED -> `completed`，FAILED ->
`failed`，CANCELLED -> `canceled`。未知/跨 tenant task 返回 A2A not-found；领域 conflict、
not-due、auth 和 stream gap 使用版本化 A2A error mapping，不暴露内部异常。

### 12.6 CLI

CLI 是 Application Service Adapter，不是数据库脚本集合：

| CLI command | 调用边界 |
|---|---|
| `agent-os run submit` | `RunSubmissionService.submit` |
| `agent-os run command` | `RunCommandService.submit` |
| `agent-os run get` | `RunQueryService.get` |
| `agent-os run watch` | `RunEventStream.subscribe` |
| `agent-os worker start` | `DistributedWorker` lifecycle |
| `agent-os serve` | `DistributedRuntimeProfile` + Channel composition |
| `agent-os artifact upload/list/read/delete` | `ArtifactService` |
| `agent-os migrate` | `DistributedMigrationService` |

CLI scope 来自部署凭证/显式 tenant option 的已验证映射。Parser 不创建 Client、不执行 SQL，
Migration Service 才拥有 migration lock、version 和 transaction。

## 13. Team 与 Planner

- Planner/Task/Team 领域类型保留各自 Owner；具体 PostgreSQL/Redis Adapter 迁入
  `agentos.distributed`；
- Team message 的 durable delivery 使用稳定 source message ID：目标 Run 正处于匹配
  WAITING 时提交 `wakeup`；不存在可恢复 Run 时通过 `RunSubmissionService` 创建新 Run；
  其他非终态状态拒绝，不增加含混的通用 team command；
- Team Worker 不直接重放 `LocalContinuationInput`，不创建另一套 retry store；
- Task/Plan 终态与 result Outbox 同事务，Outbox 是唯一通知 Owner；
- Plan release、Team claim 和 Task completion 都必须校验单调 fencing token；
- Team UI stream 使用同一 Redis replay contract，不维护仅存在于进程内的 stream list。

## 14. Package Boundary 与 Breaking Cutover

目标主结构：

```text
agentos/
  runtime/                         # async Kernel Port 与唯一 QueryLoop
  durable/                         # SQLite/Filesystem Level 2 Adapter
  distributed/
    models.py
    protocols.py
    services.py
    profile.py
    postgres/
      state.py
      claims.py
      outbox.py
      side_effects.py
      artifacts.py
      team.py
    redis/
      leases.py
      queue.py
      replay.py
    blobs/
      protocol.py
      s3.py
    worker/
      runner.py
      relay.py
      supervisor.py
      team.py
  transports/
    http/
    sse/
    websocket/
    a2a/
  adapters/a2a/
  policies/
  channels/
    asgi_app.py
    sse_endpoint.py
    artifact_endpoint.py
    websocket_endpoint.py
    a2a_endpoint.py
  multi/
    team_types.py
    team_ports.py
    team_runtime.py
    team_tools.py
  deployment_types.py
  deployment_reports.py
  deployment_validation.py
  deployment_profiles.py
```

最终删除：

- `channels/a2a.py`；
- `channels/a2a_operations.py`；
- `channels/a2a_conformance.py`；
- `channels/asgi.py`；
- `channels/durable_session.py`；
- `deployment.py`；
- `multi/team.py`；
- 新 Profile 路径中的 `SessionSnapshot`/`PostgresSessionSnapshotPersistence` 组合；
- `runtime/profile_distributed.py` 中旧线程 Daemon/Profile 实现。

`channels/__init__.py`、`multi/__init__.py` 和根 `agentos/__init__.py` 同步收窄，只导出批准的
高层入口。旧模块不保留长期 re-export。版本、inventory、stability manifest、CHANGELOG 和
迁移说明在同一 Cutover 更新。

迁移说明必须单列 cancel safe-stop override：旧调用方需要处理 `side_effect_in_flight` 409，展示或
执行 Side Effect resolution；resolution 后 Run 仍为非终态时使用原 command idempotency 规则重试，
已经 FAILED 时不再提交 cancel。

本 Contract 取代 `2026-07-11-agentos-oversized-module-decomposition-plan.md` 中要求长期
保留兼容 facade/re-export 的条款；其 leaf-first、共享文件单 Owner 和 no-growth 顺序继续有效。

## 15. Optional Dependency

- Base：不安装 SQLite async、PostgreSQL、Redis 或 Server Client；
- `agentos[durable]`：安装 Durable SQLite 的异步依赖；
- `agentos[distributed]`：安装原生异步 PostgreSQL pool 和 Redis Client；
- `agentos[distributed-artifacts]`：安装 S3-compatible async Blob Client；
- `agentos[security]`：提供参考 PayloadProtector；生产部署可注入 KMS-backed 实现；
- `agentos[server]`：安装 ASGI server；
- 组合安装由部署方选择，不在 import 时创建连接。

Local/Durable import 测试必须验证未加载 `psycopg`、`psycopg_pool` 或 `redis`。Distributed
Adapter 中禁止使用同步 connection/client。

## 16. 错误契约

至少提供稳定、无 secret 的领域错误：

- `RunNotFoundError`；
- `RunSubmissionConflictError`；
- `ActiveRunConflictError`；
- `CommandConflictError` / `CommandStateError`；
- `ClaimConflictError` / `ClaimExpiredError` / `StaleFenceError`；
- `CheckpointConflictError`；
- `DeliveryUnavailableError`；
- `StreamGapError` 或类型化 `StreamGap`；
- `SideEffectAmbiguousError`；
- `SideEffectInFlightError`；
- `DistributedStoreClosedError`；
- `DistributedBackendUnavailableError`。

错误文本不得包含 DSN、密码、Token、SQL、Artifact 路径、Tool secret arguments 或原始
Prompt。Transport 使用稳定 error code 映射，不把 Adapter exception 原样返回网络。
Distributed Store/Service 使用 `agentos.distributed.errors` 中无参数构造、固定 code/message 的
领域错误；不得直接重新导出允许携带任意内部文本的 runtime/Adapter exception。已有 canonical
domain owner 提供同样固定安全文本的 not-found 类型（例如 `ArtifactNotFoundError`）保持例外，
由 Transport 按类型映射稳定 code。

## 17. 故障注入门禁

必须覆盖：

- Command/Submission commit 前崩溃、commit 后响应前崩溃；
- accepted turn load/claim、`QUEUED -> RUNNING`、用户消息准备和首次 Provider 调用边界崩溃；
- Provider response 后到 `pending_tools`、checkpoint 后到首个 Tool、部分 Tool 完成、全部结果后到
  `after_tools`、`after_tools` 后到下一 Provider 的五个 Running Cursor 崩溃窗口；
- WaitRequest 的 atomic WAITING commit 前后崩溃，以及 ActiveWindow 无 dangling tool-use；
- Outbox XADD 前崩溃、XADD 后 mark-delivered 前崩溃；
- 多 Relay 乱序 claim、重复 XADD 和 Consumer outbox_id 去重；
- 两个 Worker 并发 Claim；
- Lease 续租丢失、takeover 后旧 token 的全部写失败；
- Tool 调用前崩溃、效果发生后结果保存前崩溃、结果保存后 ACK 前崩溃；
- Redis 不可用时 Command/Outbox 可恢复；
- PostgreSQL 不可用时 Worker 不 ACK；
- Pending reclaim、duplicate delivery 和 Dead Letter 前真值校验；
- Drain 期间停止新 Claim，活动任务完成或自然过期；
- Stream trim/gap/reconnect；
- 数据库时间与应用节点时钟漂移；
- cancellation 与 terminal commit 竞争；
- RUNNING cancel 后旧 fence 的 checkpoint/terminal/ledger complete 全失败；
- Cancel 在 Tool 调用前、STARTED 到 complete 之间、COMPLETED 后三个窗口的安全停止点矩阵；
- 同 Session 并发首次提交只产生一个非终态 Run；
- 进程重启后 accepted start/continuation 恢复；
- 五类 Side Effect 在 RESERVED、STARTED、COMPLETED 崩溃点的固定恢复矩阵；
- 补偿调用前、外部补偿效果后到 COMPENSATED 前、COMPENSATED 后到 FAILED terminal 前的
  三个崩溃窗口；
- 跨 tenant Session/Run/Artifact/cursor 隔离；
- Worker A 上传、Worker B hydrate/read/mount shared Artifact；
- Legacy Snapshot 零写入；
- Local/Durable import boundary；
- OCR 在源码、测试和路线图中的零新增。

真实 PostgreSQL/Redis 集成测试是 Phase 6 发布门禁，Fake Connection 不能替代。

## 18. Readiness 与可观测性

Readiness 至少报告：

- PostgreSQL connectivity、migration version 和 transaction probe；
- Redis connectivity、consumer group、replay capacity；
- shared Artifact metadata/blob probe 和跨 Worker 可见性；
- Outbox lag/oldest pending age；
- Queue lag/pending/reclaim/dead-letter count；
- Worker state、active claims、heartbeat age、drain state；
- stale claim recovery outcome；
- stream gap/trim count；
- ambiguous side effect count；
- optional dependency 与 live backend verification evidence。

Trace 关联 `tenant_id -> session_id -> run_id -> turn_id -> command/submission_id ->
claim/fencing_token -> tool_call/operation_id`。默认不得记录 Prompt、Artifact bytes、Tool
arguments 或 ContextSnapshot。

## 19. Public API

推荐的高层入口：

```python
scope = RequestScope(tenant_id="tenant_1", principal_id="user_1")

async with DistributedRuntimeProfile(...) as services:
    receipt = await services.runs.submit(
        scope,
        RunSubmission(
            session_id="session_1",
            submission_id="req_123",
            content="完成这个任务",
        ),
    )
```

Worker、ASGI App 和 CLI 都由 Profile 组合相同 Services。应用不直接构造 PostgreSQL Store、
Redis Queue 或 QueryLoop。底层 Adapter 可以从 canonical leaf module 导入，但不从根包批量
re-export。

## 20. 验收标准

Phase 6 只有同时满足以下条件才完成：

- 唯一 QueryLoop 和唯一 async Run/Durable Port；
- Local、Durable、Distributed Contract Matrix 全绿；
- 首次 Run 与 Continuation 都走持久提交、Queue、Claim、Agent、Checkpoint 路径；
- accepted turn 的 claim/commit 状态机经各崩溃窗口仍不丢失、不重复持久消息；
- PostgreSQL 是唯一状态真值，Redis 删除后仍可从 PostgreSQL 恢复；
- stale fence 不能提交任何状态、checkpoint 或 side-effect ledger；
- cancel safe-stop override 在三个 Tool 窗口均不产生不可治理的 STARTED/AMBIGUOUS terminal；
- RunDriver 是 execution waiting/terminal 的唯一提交 Owner，外部 CANCELLED 只由
  RunCommandService 提交，Worker 只在提交后 ACK；
- ACK 始终发生在 PostgreSQL 提交之后；
- Side Effect Policy 对五类工具均有确定行为；
- HTTP/SSE/WebSocket/A2A 不持有领域真值；
- Team/Planner 不复制 Run/Retry Worker；
- Tenant scope 覆盖所有 ingress/query/stream/artifact 和数据库唯一键；
- 共享 Artifact 可在另一 Worker hydrate、读取和重新投影；
- Legacy Snapshot 和旧大模块从新版本删除；
- Base/Local/Durable 不导入分布式 Client；
- live PostgreSQL/Redis 故障注入通过；
- 全量 pytest、Ruff、compileall、Public API、module size 和 `git diff --check` 全绿；
- 文档、迁移说明、readiness 和 release evidence 与实现一致；
- OCR 未进入任何代码、测试或路线图。
