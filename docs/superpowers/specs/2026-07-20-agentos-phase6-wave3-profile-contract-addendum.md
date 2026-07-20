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

## 6. Optional Dependencies

- `agentos[distributed]`：`psycopg[binary]`、`psycopg-pool`、`redis`；
- `agentos[distributed-artifacts]`：`aioboto3`；
- Base、Local 和 Durable import 不得加载上述客户端。

旧 `postgres`/`redis` extras 的 breaking removal 留到 Phase 6 Task 9，不在 Wave 3 顺手清理。

## 7. Wave 3 Verification

至少覆盖 constructor 零 I/O、open failure rollback、close 幂等、Worker 显式 start、两 Queue 独立、
每 claim 新 Agent、tenant-aware payload context、checkpoint restore、Artifact scope/只读/cache、真实
PostgreSQL/Redis submit-to-ACK、跨 Worker Artifact 和 full restart recovery。Spec Compliance 与
Code Quality Review 的 P0/P1 清零后，Wave 3 才可关闭。
