# AgentOS Phase 6 Wave 3 Distributed Profile Contract Addendum

> 状态：已确认，作为 Phase 6 Wave 3 组合层的强制补充合同
>
> 日期：2026-07-20
>
> 上位规范：`2026-07-17-agentos-phase6-distributed-runtime-transport-contract.md`

## 1. 目的与取代关系

本文冻结 Distributed Runtime Profile 在实现前仍未明确的公开 API、资源所有权、claim 水合和
Artifact 适配语义。本文不改变 PostgreSQL 真值、Redis 交付、唯一异步 QueryLoop、Worker 只
ACK 或 OCR 不进入 SDK 的既有边界。

上位规范中“Distributed Profile 使用 `build_agent()`”的泛化表述由本文取代。Distributed
execution 只能由 PostgreSQL claim 产生的 `AcceptedTurnExecution` 驱动，应用不得绕过 Worker
直接创建可写分布式 Agent。

## 2. Public API

唯一高层入口是 `agentos.distributed.DistributedRuntimeProfile`。它提供：

- `open() -> Self`、`close() -> None` 和 async context manager；
- `name == "distributed"` 与只读 `is_open`；
- 只读 `runs`、`commands`、`queries`、`events`、`artifacts` Application Service；
- 只读 `worker` 和 `relay` Host；
- 不提供公开 `build_agent()`。

`async with profile as services` 返回 Profile 自身。构造函数只校验并保存配置，不连接数据库、
不创建客户端、不初始化 schema，也不启动 Worker。`open()` 负责建立 Profile 拥有的资源并完成
组合；Worker 只能由调用方显式执行 `await services.worker.start()`。Relay 当前不创建后台任务，
由部署 Host 调用 `relay_once()`。

## 3. Resource Ownership

一个 Profile 独占：

- 一个 `PostgresPool`，供 State、Claim、Outbox、SideEffect、ResumeValidator 和 Artifact metadata
  adapter 共享；
- 四个互不共享生命周期的 Redis adapter/client owner：Worker Queue、Relay Queue、Lease、Replay；
- 一个 `DistributedWorker` 和一个 `OutboxRelay`。

WorkerRunner 与 DistributedWorker 使用同一个 Worker Queue。Relay 使用另一个 Queue；两者禁止
复用，因为 `DistributedWorker.close()` 会关闭它持有的 Queue。Relay close 只停止新 batch 并等待
当前 batch，Profile 负责关闭 Relay Queue。

注入的 `BlobStore` 和 `AgentBuilder` 是 borrowed resource。Profile 不关闭、替换或跨部署复用其
运行时状态；BlobStore 的外层生命周期由部署方管理。Profile close 顺序固定为 Relay、Worker、
Replay、Lease、Relay Queue、Artifact adapter、PostgreSQL Pool。每项关闭都必须尝试，保留首个
错误；close 幂等，closed 后不允许 reopen。

## 4. Claim-only Agent Hydration

每个 claim 创建新的 Agent 和 QueryLoop，不按 Session 缓存。内部 AgentFactory 必须：

1. 从 `claimed.target.scope/session_id` 创建 `PostgresStateStore.bind(scope)`；
2. 只从该绑定 view 加载 checkpoint；
3. 使用 `PayloadProtectionContext(scope.tenant_id, session_id)`；
4. checkpoint 存在时复用 canonical hydration，不存在时创建空 Session/Message/Context runtime；
5. RunRuntime 和 checkpoint store 使用同一个 bound state；
6. 注入 PostgreSQL SideEffectStore 与独立 SideEffectResumeValidator；
7. 注入 claim-scoped 只读 Artifact view；
8. 返回不带 `DurableCommandRuntime` 的标准 Agent。

Distributed hydration 不调用 Durable Profile 的 `recover_abandoned_runs()` 或
`initialize_session()`。reclaim、accepted input 和 Session 建立均由 PostgreSQL submission/claim
owner 负责。

Builder 必须拒绝预绑定 context、message、compression runtime、tool router，以及当前形态的
静态 `context_projections`。后者没有 claim-scoped factory，直接复用可能跨 tenant 泄漏；开放前
必须另行冻结按 claim 创建 projection provider 的协议。

## 5. Claim-scoped Read-only Artifact View

只读 view 固定绑定 `RequestScope + session_id + DistributedArtifactPort`，并向 ArtifactRuntime 提供
session-scoped ArtifactStore 形状：

- `get/read/list` 不信任调用参数，始终向后端注入绑定 scope；
- session 不匹配统一返回 `ArtifactNotFoundError`，不得泄漏其他 Session 是否存在；
- `get()` 通过后端 `read()` 获得 metadata+bytes，并在当前 claim view 内缓存；后续 `read()` 复用
  bytes，避免同一次 mount 重复下载；
- `put/delete/delete_session` 固定 fail closed，不调用后端；
- cache 生命周期不得超过当前 claim Agent，Turn 终态仍由 ArtifactRuntime 清理 projection cache。

上传和删除只能经 tenant-scoped `ArtifactService` 执行。Worker A 上传后，Worker B 的新 claim
必须能通过共享 metadata/blob backend 重新 list、load 和投影同一 Artifact。

删除必须与引用写入共享 `Session -> Artifact` 锁序。未提交的 AcceptedInput、任一可恢复
Checkpoint 中 StoredMessage 的 `artifact_refs`，以及 Side Effect Ledger 的
`ArtifactToolResultRef` 均构成 durable pin；存在任一 pin 时删除 fail closed。Submission 在校验
Artifact 时持有共享行锁，Ledger 在写入 Artifact result ref 前必须在同一 fenced 事务中重新确认
Artifact 仍为 active，禁止形成指向已删除 bytes 的持久引用。

Checkpoint 写入必须在 INSERT 前收集全部 StoredMessage 的 `artifact_refs`，按唯一 `artifact_id`
锁定 active metadata。`resolve_side_effect` 命令若携带 `ArtifactToolResultRef`，校验阶段同样必须持有
共享行锁；该命令处于 AcceptedInput 的 `accepted` 或 `claimed` 状态时，其 result ref 也是 durable
pin。命令恢复完成并提交 checkpoint 后，引用是否继续存活只由 checkpoint/ledger 的持久状态决定。

## 6. Oversized Tool Result

Tool handler 的原始结果必须先由 claim-scoped result-ref projector 处理，再应用 Provider message
预算。预算内结果保存 `InlineToolResultRef`；超限结果以确定性 `upload_id` 写入当前 tenant/session
的共享 ArtifactStore，Ledger 保存 `ArtifactToolResultRef` 和有界 preview。StoredMessage 与 Provider
只接收 preview，不写入原始大结果。恢复时直接从 Ledger 重建相同 preview，不重新执行已完成的
外部效果。Local Profile 不配置该 projector，继续使用 inline ledger 与既有 message budget。

`ArtifactToolResultRef` 是 preview 上限的 canonical owner：preview 最多包含 4,096 个 Unicode 字符。
该固定反序列化边界不得由 Tool 配置或环境变量放宽；构造、外部 resolution payload、Ledger codec
和 replay 必须共享同一值对象校验。可配置的 `ToolResultBudget` 只决定正常 Tool 执行何时转存以及
生成何种 preview，不替代这一持久化安全上限。

外部 `SideEffectResolution(ACCEPT_RESULT)` 的 inline evidence 同样最多包含 4,096 个 Unicode 字符，
更大的 reconciliation 结果必须先写入 ArtifactStore 并提交有界 preview。该限制仅属于外部
reconciliation 输入，不取代正常 Tool handler 路径的 `ToolResultBudget`。

## 7. Recovery and Process Liveness

Session 恢复必须按数据库生成的单调 `checkpoint_sequence DESC` 选择最新快照，不得使用墙钟时间
或随机 ID 推断顺序。

同一 Run 的写路径统一采用 `Session -> Run` 锁序；Claim 在无锁定位候选 identity 后必须先锁
Session，再带锁重读 Run/Outbox。命令提交不得使用联合 `FOR UPDATE OF run, session` 依赖数据库
自行决定顺序。Side Effect 路径在完成 fence 校验后才允许锁 Ledger 行。

首次 schema 初始化必须由固定 PostgreSQL transaction advisory lock 串行化，锁必须先于版本读取、
版本写入和其他 DDL。checkpoint latest 查询必须由
`(tenant_id, session_id, checkpoint_sequence DESC) WHERE snapshot_json IS NOT NULL` partial index 支撑。

Relay 批量 claim 后，只有成功 `mark_published()` 的前缀视为完成。publish、mark 或任务取消中断
批次时，必须逐一尝试释放当前 claim 和未处理后缀；单个 release 失败不得跳过其余 claim，并且
对外仍传播原始批次异常。进程硬退出或 PostgreSQL 不可用时继续由 claim TTL 回收。

Worker 被长任务占满并发槽时，receive loop 仍必须按既有 claim heartbeat interval 刷新 readiness
heartbeat；容量恢复后必须重新检查 drain/failure 状态，禁止在失去 readiness 后领取新 delivery。

## 8. Optional Dependencies

- `agentos[distributed]`：`psycopg[binary]`、`psycopg-pool`、`redis`；
- `agentos[distributed-artifacts]`：`aioboto3`；
- Base、Local 和 Durable import 不得加载上述客户端。

旧 `postgres`/`redis` extras 的 breaking removal 留到 Phase 6 Task 9，不在 Wave 3 顺手清理。

## 9. Wave 3 Verification

至少覆盖 constructor 零 I/O、open failure rollback、close 幂等、Worker 显式 start、两 Queue 独立、
每 claim 新 Agent、tenant-aware payload context、checkpoint 单调恢复、Artifact scope/只读/cache、
checkpoint/resolve command Artifact durable pin、首次并发 schema 初始化、Relay 批次中断释放、
满载 Worker readiness heartbeat、真实 PostgreSQL/Redis submit-to-ACK、跨 Worker Artifact 和 full
restart recovery。Spec Compliance 与
Code Quality Review 的 P0/P1 清零后，Wave 3 才可关闭。

## 10. 后续非阻塞债务

- 大型 Tool Result 已上传但 Ledger completion 因 fence/后端失败而未提交时，可能留下无 durable pin 的
  active Artifact。不得在失败路径直接删除，因为同一确定性 upload 可能已被并发成功提交者引用；后续
  应以明确的 orphan 判定和回收协议处理。
- Worker 执行异常与 Lease release 异常同时发生时，当前 release 异常可能覆盖原始执行异常。后续应冻结
  primary/cleanup failure 的传播与观测合同，再调整 Runner。

以上为 P2，不改变 Wave 3 以 P0/P1 清零为关闭条件。
