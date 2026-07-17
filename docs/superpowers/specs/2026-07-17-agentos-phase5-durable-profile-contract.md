# AgentOS Phase 5 Durable Profile Contract

> 状态：已确认方向，进入实现
>
> 日期：2026-07-17
>
> 目标里程碑：M4 Durable Agent
>
> 上位规范：`2026-07-10-agentos-next-generation-sdk-architecture-design.md`、
> `2026-07-10-agentos-context-protocol-v1-design.md`、
> `2026-07-12-agentos-single-async-query-loop-design.md`

## 1. 目的

Phase 5 在 M3 Local Agent 的单一异步 Kernel 上增加单机持久化能力，使一个进入
WAITING 的 Run 可以在原进程对象全部销毁后，通过 SQLite 和文件系统恢复并使用
同一个 `run_id` 创建 Continuation Turn。

本阶段不复制或派生第二个 QueryLoop。Durable Profile 只提供权威 Store、命令接受、
checkpoint、水合和本地适配器，执行仍经过同一个 `Agent -> QueryLoop -> RunDriver`。

## 2. 范围

本阶段必须完成：

- SQLite 保存 Session、Run、StoredMessage、ActiveWindow、Working State、Plan、
  Memory、Skill Activation、Command 和 Checkpoint；
- 文件系统保存 Artifact bytes，SQLite 保存 Artifact metadata；
- `wait -> 销毁进程对象 -> hydrate -> 同 run_id continuation -> artifact reload`；
- 持久化 Resume、Wakeup、Retry、HITL Answer 和 Cancel Command；
- `command_id` 幂等、终态拒绝和 Timer 到期校验；
- WAITING 状态与 Checkpoint 的单事务提交；
- 遗留 RUNNING Run 的 fail-closed 恢复；
- Skill、Plan、Memory、HITL 和 Timer Wakeup 的可组合接入；
- `agentos[durable]` optional extra；
- 基础安装和 Durable 路径均不导入 Redis/PostgreSQL client。

本阶段明确不包含：

- OCR、图纸字段提取或文档识别；
- 自动附件摘要、Embedding、向量或混合检索；
- PostgreSQL、Redis、Queue、Worker、Claim、Lease、Fencing 或分布式交付；
- Provider Transcript、Provider Conversation ID 或 Provider File ID 恢复；
- 自动重放遗留 RUNNING Run 的 Provider 或 Tool 副作用；
- 跨 Session、Workspace 或 Tenant Artifact 共享。

OCR 永久不属于 AgentOS SDK 路线图。应用可以通过独立 Ingestion Extension 把识别
结果作为普通 Tool Result、Memory 或 Artifact metadata 接入。

## 3. 架构边界

```text
DurableRuntimeProfile
  -> hydrate SessionState / MessageRuntime / ContextRuntime
  -> compose SQLite Run/Command/Checkpoint Store
  -> compose SQLite + Filesystem ArtifactStore
  -> build standard Agent

DurableRunCommand
  -> DurableCommandRuntime
  -> atomic accept in authoritative Store
  -> AcceptedContinuationInput
  -> Agent / QueryLoop / RunDriver
```

依赖规则：

- Kernel 只依赖 `RunStore`、`DurableCommandStore`、`CheckpointStore` 等类型化 Port；
- `QueryLoop` 不导入 `sqlite3`，不读取数据库，不实现 command idempotency；
- Durable Adapter 可以依赖 Kernel domain type，Kernel 不导入 Durable Adapter；
- ContextSnapshot、ProviderRequest、ContextMount 和 Provider transcript 都不是恢复真值；
- Plan、Memory、Skill 各自保留原 Store/Runtime Owner，Durable Profile 只组合实现；
- Legacy `SessionSnapshot`、`SQLitePersistence` 和 `FileSystemPersistence` 不参与新路径，
  不做双写。

## 4. Durable Command Contract

### 4.1 类型

```python
DurableRunCommandKind = Literal[
    "resume",
    "wakeup",
    "retry",
    "hitl_answer",
    "cancel",
]


@dataclass(frozen=True, slots=True, init=False)
class DurableRunCommand:
    run_id: str
    command_id: str
    kind: DurableRunCommandKind
    payload: FrozenJsonObject


@dataclass(frozen=True, slots=True)
class AcceptedContinuationInput:
    run_id: str
    command_id: str
    kind: DurableContinuationKind
    payload: FrozenJsonObject
    aggregate_version: int


@dataclass(frozen=True, slots=True)
class DurableCommandReceipt:
    run_id: str
    command_id: str
    kind: DurableRunCommandKind
    aggregate_version: int
    duplicate: bool
```

`cancel` 不产生 `AcceptedContinuationInput`。它在 Store 事务内把非终态 Run 转为
`CANCELLED` 并返回 receipt。其他命令成功时先完成 `WAITING -> QUEUED`，再把内部
`AcceptedContinuationInput` 交给 Agent。

### 4.2 Agent API

配置 Durable Command Runtime 的 Agent 接受 `DurableRunCommand`。未配置时确定性抛出
`DurableCommandUnsupportedError`。

- 首次接受 continuation command：执行同一个 `run_id` 并返回 `RunOutcome`；
- 首次接受 cancel command：不进入 QueryLoop，返回 `DurableCommandReceipt`；
- 完全相同的重复 `command_id`：通常不再次执行，返回 `duplicate=True` 的 receipt；唯一
  例外是重启水合后该 command 仍是当前 Run 的权威 QUEUED pending continuation，此时
  Agent 可以恢复该 execution slice，但不能再次应用 command 或增加 aggregate version；
- 同一 `command_id` 对应不同 run/kind/payload：抛出 `CommandConflictError`；
- Durable command 支持 stream；command 在原子更新 `WAITING -> QUEUED` 时已经完成
  对聚合根的应用，因此 stream 的提前关闭不允许重复接受同一 command。
- Agent 必须在接受 Durable command 前取得对应 QueryLoop 的 execution reservation；
  Agent 与 QueryLoop 在同一 asyncio Task 内复用同一个 reservation。receipt、拒绝和异常
  路径释放 reservation，成功执行路径把它原子转换为 AgentStream lease。Profile close 与
  command accept 竞态只能由一方成功，不允许先落库 command 再因 Store 已关闭而失败。

普通 `str`、`UserTurnInput` 和 `LocalContinuationInput` 的 Local 行为不变。

### 4.3 状态与命令矩阵

| Command | 合法 WaitReason | 额外条件 |
|---|---|---|
| `hitl_answer` | `human_input` | payload 必须是非空 JSON object |
| `resume` | `human_input`、`remote_result`、`resource_availability` | payload 可为空 |
| `wakeup` | `timer`、`remote_result`、`resource_availability` | 存在 `not_before` 时必须到期 |
| `retry` | `retry_backoff` | 必须到期 |
| `cancel` | 任意非终态 | 不创建 Continuation Turn |

Resume/Wakeup/Retry/HITL Answer 只允许从 WAITING 接受。COMPLETED、FAILED 和
CANCELLED 一律拒绝。QUEUED/RUNNING 上的首次 continuation command 也拒绝；只有
已经存在且内容一致的 command ID 返回重复 receipt。

### 4.4 幂等边界

SQLite 事务按以下顺序接受 command：

```text
BEGIN IMMEDIATE
  -> lookup command_id
  -> exact duplicate: return prior receipt
  -> conflicting duplicate: reject
  -> load Run + aggregate_version
  -> validate state / wait kind / due time
  -> insert immutable Command record
  -> WAITING -> QUEUED or nonterminal -> CANCELLED
  -> aggregate_version += 1
COMMIT
```

幂等只保证命令对 Run 聚合状态应用一次，不宣传外部 Tool 副作用 exactly-once。

崩溃可能发生在 command 已原子完成 `WAITING -> QUEUED`、但 QueryLoop 尚未执行之间。
Profile 水合后再次提交完全相同的 command 时，Store 仍返回 duplicate receipt；Agent 只有
在该记录与当前 QUEUED aggregate version 完全匹配时，才读取已有
`AcceptedContinuationInput` 并恢复执行。该路径是 pending execution ownership recovery，
不是第二次接受或应用 command。非 QUEUED、非当前 version 或非 exact duplicate 一律不
进入恢复执行。

## 5. WaitReason 与 Timer

`WaitReason` 增加：

```python
not_before: datetime | None = None
```

规则：

- datetime 必须 timezone-aware，并标准化为 UTC；
- `timer` 和 `retry_backoff` 必须提供 `not_before`；
- 其他 wait kind 不允许提供 `not_before`；
- Store 使用 UTC ISO-8601 或等价的确定性表示；
- command runtime 通过注入 clock 校验到期，不直接调用可替换测试之外的 sleep；
- 未到期抛出 `CommandNotDueError`，Run 保持 WAITING，command 不落库。

Timer wakeup 是显式命令，不在 QueryLoop 内保持 sleep 或轮询。进程外调度器属于应用；
Phase 5 可提供列出 due waits 的本地查询，但不引入 Worker/Queue。

## 6. Run Aggregate 与版本

`RunState` 增加单调递增的 `aggregate_version`。每次合法状态转换增加 1。Store 必须
使用版本检查或单事务写入，禁止最后写入覆盖并发命令。

同一 Session 的 Durable Profile 同时最多存在一个非终态 Run。这样 Session-level
Message/Working State 的 checkpoint 具有唯一执行 Owner。分布式并发、多 Run Claim
和 Lease 进入 Phase 6。

恢复策略：

- WAITING：保持 WAITING，等待持久命令；
- QUEUED：保留为可显式恢复的已接受 continuation；
- RUNNING：标记 FAILED，记录稳定 recovery error；
- CREATED：可以取消或由应用重新排队；
- 终态：保持不变；
- 绝不自动重放遗留 RUNNING Run 的 Provider 或 Tool 调用。

## 7. Checkpoint Contract

### 7.1 内容

Checkpoint 是一次恢复提交的 metadata，不是完整 Provider transcript。一次 checkpoint
事务保存：

- Session ID、status、next turn number；
- Run ID、status、WaitReason、aggregate version；
- append-only StoredMessage；
- 非临时 ActiveWindow MessageRef；
- Declared Working State Schema 和 Working State；
- Compressed History 与 Inherited State；
- Plan 由 SQLitePlanStore 独立持久；
- Memory 由 SQLiteMemoryStore 独立持久；
- 当前有效 Skill Activation reference；
- Checkpoint ID、turn ID、创建时间和 schema version。

### 7.2 禁止内容

Checkpoint、SQLite 和默认 Trace 不得保存：

- ProviderRequest、ProviderResponse 或 Provider transcript；
- ContextSnapshot XML 或 SystemEnvelope；
- ContextMount；
- temporary recall ref 或 runtime notice；
- Artifact bytes、Base64、Signed URL、本地绝对路径或 Provider File ID；
- 完整 Skill 正文；
- secret 或未脱敏 Tool arguments。

### 7.3 原子 WAITING

`DurableWaitingRuntime.commit_waiting()` 必须在一个 SQLite 事务中：

1. 校验当前 Run 为 RUNNING 和 expected aggregate version；
2. 写入 Session、Message、ActiveWindow 和 Context 恢复状态；
3. 写入 Checkpoint metadata；
4. 执行 `RUNNING -> WAITING` 并保存完整 WaitReason；
5. 增加 aggregate version；
6. 提交事务。

任一步骤失败必须整体回滚，Run 仍为 RUNNING，不允许返回 WaitingCommit。

COMPLETED、FAILED 和运行时取消同样在状态提交时保存最新恢复状态。普通 RUNNING
中间步骤不提供透明副作用恢复承诺。

## 8. Hydration

Hydration 从最新有效 checkpoint 重建新的进程对象：

```text
SQLite authoritative records
  -> SessionState
  -> MessageStore + ActiveWindow
  -> ContextState + ContextRuntime
  -> RunRuntime bound to durable RunStore
  -> ProviderRequestBuilder on the normal assembly path
```

恢复后每次 Provider attempt 仍由 ProviderRequestBuilder 从这些权威状态重新组装。
旧进程的 Agent、QueryLoop、ProviderRequest、ContextMount 和临时 recall 全部丢弃。

`AcceptedContinuationInput` 的 payload 由 Command Store 读取并临时投影为
`authority=context-data`、`persistence=ephemeral`、`visibility=internal` 的
continuation data。它不追加为用户 StoredMessage。

## 9. Artifact Adapter

`SqliteFilesystemArtifactStore` 实现现有 `ArtifactStore` Protocol：

- SQLite 保存 ArtifactRecord 和受控相对 blob key；
- 文件系统根目录保存 bytes；
- blob key 只由已验证的 Artifact ID 派生，不使用 filename；
- public API 不返回磁盘路径；
- metadata 存在但 blob 缺失时抛出 `ArtifactContentMissingError`；
- 未知与跨 Session 仍返回同一个 `ArtifactNotFoundError`；
- put 在临时文件写入、原子 rename 和 metadata transaction 之间清理失败残留；
- Session delete 删除 metadata 和对应 blob，不扫描或删除根目录之外的路径。
- delete/delete_session 使用受控同目录 staging：SQLite 删除提交失败时恢复 blob，提交
  成功后再清理 staging；Profile 重启时根据 metadata 真值恢复或完成遗留 staging，不能
  留下可见 metadata 指向已删除内容。
- 文件系统 `OSError` 必须映射为不含路径的稳定 Artifact 领域错误。
- Store 提供幂等 `close()`；关闭与所有文件/metadata 操作使用同一进程内生命周期锁。
  Profile close 成功取得全部 QueryLoop close reservation 后关闭 Artifact Store；关闭后
  旧 Agent 的 upload/read/list/delete 操作统一抛出 `DurableStoreClosedError`，错误文本
  不得包含数据库或 Artifact 根目录路径。

Artifact bytes 不进入主 Durable checkpoint transaction。Checkpoint 只保存
StoredMessage 中已有的 ArtifactRef；真实内容通过 ArtifactStore 按 handle 重读。

## 10. Plan、Memory 与 Skill

### 10.1 Plan

`SQLitePlanStore` 实现 `PlanStore` 和 `CompareAndSavePlanStore`。Plan JSON 使用已有
类型化 serializer，revision 由 Store 单调递增。Claim/Lease API 不在本阶段实现。

### 10.2 Memory

`SQLiteMemoryStore` 实现 Session-scoped `MemoryStore`。保存 Episodic/Semantic
MemoryRecord，使用与 InMemory adapter 一致的确定性本地匹配和排序。Embedding 与
向量数据库不在本阶段。

### 10.3 Skill Activation

SQLite 只保存激活引用和验证主体摘要，不保存完整 Skill 正文。恢复时 SkillRuntime
必须从当前 Source 重新加载，并重新执行 Trust Policy；source revision、content digest
或 policy decision 不一致时删除激活并 fail closed，不能把旧正文直接提升到
SystemEnvelope。

## 11. DurableRuntimeProfile

推荐入口：

```python
from agentos.durable import DurableRuntimeProfile

profile = DurableRuntimeProfile(
    agent_builder=AgentBuilder().provider(provider).tools(tools),
    database_path=".agentos/state.db",
    artifact_root=".agentos/artifacts",
)
agent = profile.build_agent(session_id="session_1")
```

规则：

- Profile 负责 schema 初始化、恢复策略和组件装配，不执行 Provider loop；
- Profile 归属 `agentos.durable` Adapter 命名空间，不由 Kernel `agentos.runtime`
  导入具体 Memory、Planning 或 Skill Adapter；
- 同一 Profile、同一 Session 只允许一个存活的 QueryLoop/checkpoint source；重复
  `build_agent()` 必须复用该 QueryLoop，但不要求复用同一个 Agent facade 对象；
- Profile 对 Session QueryLoop 使用弱所有权。只要 Agent 或 AgentStream 仍存活，
  QueryLoop 和 checkpoint source 就必须保持存活且不能被覆盖；两者均不可达后允许回收，
  下次 `build_agent()` 从最新 checkpoint 重新水合；
- Profile 不提供独立 `release_agent()`。Phase 5 的释放边界是应用释放 Agent，并先耗尽
  或关闭其全部 AgentStream；不得用无界强缓存维持 Session 生命周期；
- AgentBuilder 仍负责 Provider、Tool、Context Projection 和 QueryLoop 组装；
- LocalRuntimeProfile 行为和零外部服务依赖保持不变；
- `database_path` 和 `artifact_root` 不进入 Public Run/Message/Artifact domain object；
- 同一数据库连接采用显式 close/context manager 生命周期；
- `close()` 在任一 QueryLoop 仍有活跃 execution 时必须立即抛出 `AgentBusyError`，
  保持 Profile 和 Store 打开；调用方关闭 AgentStream 后可以重试。空闲时 `close()`
  幂等关闭 Store，之后旧 Agent 的持久操作确定性抛出 `DurableStoreClosedError`；
- QueryLoop 必须在创建或排队 Run 前原子预留 execution lease，并在 AgentStream 创建时
  把 reservation 转成活跃 lease；因此同一进程中 `close()` 与新 `run()` 竞态只能由一方
  成功，不能在 prepare 窗口提前关闭 Store；
- Profile close 必须先为全部存活 QueryLoop 取得 close reservation，再关闭任何 Store；
  任一 QueryLoop 忙时回滚已取得的全部 close reservation，不能留下部分 Session 被封锁；
- 跨进程 Session 互斥和 generation/lease 进入 Phase 6；
- Profile 不隐式创建后台线程、timer worker 或网络服务。

## 12. Optional Dependency

`pyproject.toml` 增加 `durable` extra。参考实现只使用 Python 标准库 `sqlite3` 和
文件系统，因此该 extra 可以为空；它仍作为稳定安装意图和未来仅限 Durable Adapter
依赖的边界存在。

以下导入在 Durable 路径中禁止：

```text
psycopg
psycopg2
asyncpg
redis
agentos.multi.postgres_*
agentos.persistence.postgres
agentos.persistence.redis_*
```

## 13. 错误契约

至少提供：

- `DurableCommandUnsupportedError`；
- `CommandConflictError`；
- `CommandStateError`；
- `CommandNotDueError`；
- `CheckpointConflictError`；
- `CheckpointCorruptedError`；
- `ArtifactContentMissingError`；
- `DurableStoreClosedError`。

错误文本不得包含 payload、secret、Artifact bytes、绝对路径或 Provider 内容。

重复 `command_id` 的已有记录必须先严格校验 command/session/run/kind/payload/version
字段及 payload JSON。字段缺失、类型非法、未知 kind、非法 version 或 payload JSON 损坏
统一抛出 `CheckpointCorruptedError`；只有完整合法且与当前输入不同的记录才抛出
`CommandConflictError`。

## 14. 测试要求

P0 Contract/Fault tests：

- WAITING 后销毁全部进程对象，重新 build 后用同一 run_id 继续；
- 重复 command_id 只执行一次；
- 冲突 command_id、终态和非 WAITING resume 被拒绝；
- WAITING + checkpoint 任一步失败整体回滚；
- 持久 cancel 在重启后仍为 CANCELLED；
- timer/retry 在 `not_before` 前拒绝，到期后接受；
- Artifact reload 成功，metadata 存在而 blob 缺失时 fail closed；
- ProviderRequest、ContextMount、temporary recall、Base64 和 Artifact bytes 不进入
  checkpoint/SQLite；
- 遗留 RUNNING 恢复为 FAILED，不调用 Provider/Tool；
- Plan CAS、Memory session scope 和 Skill activation re-verification；
- 同 Session 的存活 QueryLoop/checkpoint source 不被覆盖，无 Agent/Stream 引用后不产生
  无界 Profile cache；
- 活跃 AgentStream 阻止 Profile close，关闭 Stream 后 close 成功；
- command accept 进入持久化边界后阻止 Profile close，且测试使用 Event/Barrier 确定性
  覆盖竞态；
- Profile close 后旧 Agent 的 Artifact upload/read/list 均稳定失败；
- 损坏的重复 command row 统一映射为 `CheckpointCorruptedError`；
- 无 Durable extra 的 Local Profile 全量测试继续通过；
- Durable import graph 不包含 Redis/PostgreSQL client。

## 15. 验收标准

Phase 5 只有全部满足时才完成：

- DurableRuntimeProfile 使用同一个 QueryLoop；
- Profile 不无界强缓存 Session Agent，checkpoint source 生命周期与存活 QueryLoop 对齐；
- 活跃执行期间 Profile close fail-fast，且不会提前关闭 Store；
- wait/restart/resume 使用同一个 run_id 和新的 Continuation Turn；
- command 去重、cancel 和 timer 语义通过故障测试；
- WAITING 与 checkpoint 原子；
- ProviderRequest 可以从恢复状态重建且不依赖 Provider transcript；
- Artifact metadata/content 分离并可在重启后重新加载；
- SQLite Plan/Memory/Skill activation adapter 可组合；
- Local Profile 不依赖 Durable、Redis 或 PostgreSQL；
- OCR 不在代码、Profile 或后续 SDK 路线图中；
- targeted、full pytest、compileall、ruff、diff、module-size、public API 和 import
  drift gates 全部通过；
- Spec Compliance Review 与 Code Quality Review 均无阻断项。
