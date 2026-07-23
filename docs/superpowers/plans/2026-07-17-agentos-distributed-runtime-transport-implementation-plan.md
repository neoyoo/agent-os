# AgentOS Phase 6 Distributed Runtime / Transport 实施计划

> 状态：Wave 0-5D 与 Wave 6 Task 7-9 已完成；Phase 6 已通过最终双层独立 Review
>
> 日期：2026-07-17
>
> 对应 Contract：
> `docs/superpowers/specs/2026-07-17-agentos-phase6-distributed-runtime-transport-contract.md`
>
> Wave 4 Transport Addendum：
> `docs/superpowers/specs/2026-07-21-agentos-phase6-wave4-transport-contract-addendum.md`
>
> Wave 5 WebSocket / CLI / Team Addendum：
> `docs/superpowers/specs/2026-07-21-agentos-phase6-wave5-websocket-cli-team-contract-addendum.md`
>
> 基线提交：`35b3090 feat: complete phase5 durable runtime profile`

## 0. 执行状态（2026-07-22）

- Wave 0：Contract、breaking map 和 red gates 已完成；
- Wave 1：唯一 Async Kernel 与 Durable 回归已完成；
- Wave 2：Distributed Shared Contract 已冻结并完成；
- Wave 3：PostgreSQL、Redis、Worker/Relay 与 Distributed Profile 已完成；
- Wave 4：HTTP/SSE/Artifact、A2A wire、A2A durable push 和 Channel Integration 已完成；
- Wave 5A：Shared Run Stream、WebSocket wire/channel/ASGI 与 terminal publication recovery 已完成；
- Wave 5B：Migration authority、PostgreSQL migration adapter 与 CLI Application Commands 已完成；
- Wave 5C：Team types/ports/runtime/tools/identity/in-memory adapter 与 delivery/event typed contract
  已完成；
- Wave 5D：Team PostgreSQL/Redis delivery、Worker、internal start、claim-scoped Team hydration 与
  trusted wakeup provenance 已完成并通过独立 Spec/Quality/Security Review；OCR 仍明确排除；
- Wave 6 Task 7：Deployment canonical leaf、消费者迁移与旧模块物理删除已完成；
- Wave 6 Task 8.5：Workspace 模块职责拆分已完成；
- Wave 6 Task 8：live backend failure injection、backend I/O deadline、heartbeat fail-closed、
  Relay batch deadline 与 shutdown cleanup 已完成；
- Wave 6 Task 9：canonical import、public facade 收窄、legacy 物理删除、`0.3.0a1`
  version/migration/release evidence 与治理清单切换已完成，并通过最终双层独立 Review。

Wave 4 收口证据：

- 全量测试：`3722 passed, 24 skipped`；
- Architecture：`144 passed`；
- Ruff、`compileall`、`git diff --check` 全部通过；
- Spec Compliance Review：`P0=0, P1=0, P2=0`；
- Code Quality/Security Review：`P0=0, P1=0`；剩余 P2 为 ASGI 限流与 SSE 连接并发准入
  尚未冻结具体合同，登记到 Wave 6D 发布门禁。

Wave 5A 收口证据：

- 全量测试：`3850 passed, 22 skipped`；
- Architecture：`144 passed`；Ruff、`compileall`、`git diff --check` 全部通过；
- `AgentStream`、`QueryLoop`、`RunDriver` 分别为 246、499、490 行，未上调硬阈值；
- Contract Compliance Review：`P0=0, P1=0`；Code Quality Review：`P0=0, P1=0`；
- 非阻断 P2：WebSocket 输入适配防御增强、committed outcome 长 Run 查询优化、gap 双 API
  收敛，保留为后续质量优化，不改变 Wave 5A 协议正确性与持久真相。

Wave 5B 收口证据：

- CLI + Distributed：`470 passed, 9 skipped`；Architecture：`162 passed`；
- 全量测试：`4029 passed, 21 skipped`；
- Ruff、`compileall`、`git diff --check` 全部通过；
- PostgreSQL 17 实库验证覆盖 v1 -> v2、v2 -> v1 -> v2、复合外键隔离、合法
  delivery/submission/outbox、数据态 downgrade 和 active member 软删除后重绑；
- Migration 独立 Review：`P0=0, P1=0, P2=0`；最终 Contract Review：
  `P0=0, P1=0, P2=0`；Code Quality/Security Review：`P0=0, P1=0`；
- 非阻断 P2：CLI `--content-file`、`--stdin` 与 `--payload-file` 的 hard max 尚未冻结，留待
  后续安全预算合同定义；本轮不引入任意限制值；
- `cli/commands/run.py` 297 行、`cli/application.py` 251 行、
  `distributed/postgres/migrations.py` 133 行；`distributed/profile.py` 与
  `distributed/postgres/state.py` 已完成职责审查，仍分别保持资源组合/生命周期与 Store facade
  单一职责，不做机械拆分；
- CLI conflict 分类覆盖 side-effect-in-flight 与 Artifact 幂等冲突；基础 import 不加载
  `psycopg`、`redis`、`uvicorn`，Distributed 路径不存在同步 I/O wrapper。

Wave 5C 收口证据：

- Team Domain：`76 passed`；Multi：`335 passed`；Schema/Migration：`31 passed`；
- 全量测试：`4109 passed, 23 skipped`；Architecture：`164 passed`；
- Team message 使用 `4096 UTF-8 bytes` hard max，Team Tool Result 使用 `64 KiB` hard max，
  message page hard max 为 `10`，member capability raw item hard max 为 `32` 且拒绝重复项；
- `TeamAccessContext` 贯通 member-originated Runtime、Port、Tool 与 InMemory Adapter，owner identity
  只从可信 tenant/team/member/session binding 派生；
- Team DTO、recipient snapshot、capability tuple 与 Workspace metadata 均在构造边界防御复制并冻结；
  capability 只接受有界 `tuple/list`，Workspace metadata 只接受精确 `str -> str` 且支持
  `deepcopy/dataclasses.asdict`；
- Provider projection 不暴露 target Session、delivery/claim/fence、operation digest、workspace path 或
  metadata；
- Spec Compliance Review：`P0=0, P1=0, P2=0`；Code Quality/Security Review：
  `P0=0, P1=0`，两项 P2（capability raw count、Workspace metadata 传递不可变性）已按 TDD 修复；
- Ruff、`compileall`、module-size baseline 与 `git diff --check` 全部通过。

Wave 5D 实现证据：

- PostgreSQL 保存 Team/Member/Message/Delivery/Event 真值，Redis 只保存幂等 typed replay；
- internal-start 与 wakeup 都由当前 TeamDelivery claim authority 授权，`team_delivery_id` 持久关联
  submission/command/accepted input，普通 command 不能伪造 Team authority；
- takeover 在 binding 与 active-run 重算前按 delivery 恢复首次 accepted input，覆盖 Run terminal、
  binding 删除及 receipt-result commit crash window；
- trusted wakeup 在 PostgreSQL command 事务内重新验证 message correlation 与锁定后的 wait kind/handle；
- Team delivery heartbeat 失权后等待已开始的 Service 调用完成但禁止 result/ACK；binding 在线性化
  提交前撤销时以专用 typed error 收敛为 durable reject，其他 stale authority 保持 fail closed；
- Team Redis replay 使用 PostgreSQL `event_sequence` 稳定 cursor、`1000` 条 SDK hard max、bounded Lua
  scan，覆盖乱序补投、trim 后旧事件不复活、oversized canonical window 有界收敛与 malformed stream
  失效重建；
- Team message 分页使用同 Team 事务串行化下分配的 PostgreSQL `message_sequence`，不再以节点时间或
  可回拨 wall clock 作为 keyset 真值；Team event 使用结构化 `delivery_id`、一 delivery 一 event
  唯一约束和 tenant/delivery/team 复合外键，不扫描 JSON payload；
- version 2 down migration 在 guard 前以 `ACCESS EXCLUSIVE` 锁定 submission/command/accepted input，
  再于任何 destructive statement 前拒绝仍有关联 `team_delivery_id` 的输入真值；两份 migration
  SHA-256 均为 `822EF95CB9CCD45EFBCBA0CEFE8C1482FF5662BEDF532BEAC5B58FC28582B304`；
- Wave 5D Team 定向：`191 passed`；Distributed：`432 passed, 10 skipped`；真实 Redis Lua：
  `1 passed`；Runtime + Distributed + Multi：`1314 passed, 10 skipped`；
- 全量测试：`4219 passed, 22 skipped`；Architecture：`164 passed`；
- 真实 PostgreSQL 17 验证通过 v1 -> v2、Team-bound down fail-closed、清理后 v2 -> v1 -> v2、
  message identity/index 与 event composite FK/unique catalog 检查；Python async PostgreSQL live tests
  因本机未安装 `psycopg/psycopg_pool` 未执行；
- Ruff、`compileall`、module-size baseline、no-sync/OCR 静态检查与 `git diff --check` 全部通过；
- `distributed/worker/team.py` 427 行仍只拥有单条 delivery claim/heartbeat/route/result/ACK 状态机；
  `distributed/postgres/_commands.py` 398 行仍只拥有 Durable Command 事务，trusted wakeup 沿用该
  authority；`multi/team_tools.py` 423 行仍只拥有 Team Tool surface，schema/projection 已在独立 leaf；
  均低于 500 行强制拆分线，不做机械拆分；
- Spec Compliance Review：`P0=0, P1=0, P2=0`；Code Quality/Security Review：
  `P0=0, P1=0, P2=0`；Wave 5D 已关闭，后续按 Wave 6 Task 7、Task 8.5、Task 8、Task 9 顺序收口。

## 1. 完成标准

Phase 6 的目标不是“存在 PostgreSQL/Redis 类”，而是完成以下可验证闭环：

```text
Run/Command 持久提交
-> Outbox
-> Redis at-least-once delivery
-> PostgreSQL fenced claim
-> 同一 Agent/QueryLoop 执行
-> atomic checkpoint/terminal
-> Redis ACK
-> HTTP/SSE/WebSocket/A2A 观察
```

全部子阶段、breaking cutover、live backend failure injection 和发布证据通过后，Phase 6
才算完成。

## 2. Mandatory Context Bootstrap

每个主线任务和 subagent 开始前必须完整阅读：

1. 根 `AGENTS.md`；
2. `docs/governance/agentos-engineering-standard.md`；
3. `docs/superpowers/specs/2026-07-10-agentos-context-protocol-v1-design.md`；
4. `docs/superpowers/specs/2026-07-10-agentos-next-generation-sdk-architecture-design.md`；
5. `docs/superpowers/specs/2026-07-12-agentos-single-async-query-loop-design.md`；
6. `docs/superpowers/specs/2026-07-17-agentos-phase5-durable-profile-contract.md`；
7. 本 Phase 6 Contract 和实施计划；
8. Task 6 读取
   `docs/superpowers/specs/2026-07-20-agentos-phase6-task6-side-effect-contract-addendum.md`；
9. 当前任务直接触碰的类型、调用链和目标测试。

Subagent prompt 必须包含允许文件、禁止文件、前置提交、红测试、验证命令和提交边界。

## 3. Scope Contract

### 3.1 目标

- 完成 6A、6B、6C、6D；
- 删除旧分布式真值路径和超大混合模块；
- 不创建同步/异步双 API；
- 不维护长期兼容 facade；
- 不改变 Context Protocol v1、Provider Input 双平面或 Artifact Mount 语义。

### 3.2 非目标

- OCR、自动附件摘要、Embedding、Vector Retrieval；
- Provider transcript 恢复；
- 全局 exactly-once；
- Leader Election、跨 Region 多主；
- 与 Phase 6 无关的代码清理或格式化。

### 3.3 共享文件 Owner

以下文件只由主会话 Architecture/Integration Owner 修改：

- `src/agentos/runtime/run_runtime.py`；
- `src/agentos/runtime/durable_runtime.py`；
- `src/agentos/runtime/run_driver.py`；
- `src/agentos/runtime/query_loop.py`；
- `src/agentos/runtime/agent.py`；
- `src/agentos/runtime/profile_contracts.py`；
- `src/agentos/artifacts/store.py`、`src/agentos/artifacts/runtime.py`；
- `src/agentos/distributed/models.py`；
- `src/agentos/distributed/protocols.py`；
- `src/agentos/distributed/services.py`；
- `src/agentos/distributed/profile.py`；
- 根/`channels`/`multi` public facade；
- `pyproject.toml`、Public API inventory/stability、CHANGELOG；
- 最终 legacy 文件删除和 migration map。

### 3.4 Subagent 允许范围

- PostgreSQL/Artifact Owner：`src/agentos/distributed/postgres/**`、
  `src/agentos/distributed/blobs/**`、对应 migration 和测试；
- Redis Owner：`src/agentos/distributed/redis/**`、对应测试；
- Worker/Fault Owner：`src/agentos/distributed/worker/**`、故障注入测试；
- HTTP/SSE Owner：`src/agentos/transports/http/**`、`transports/sse/**` 和独占测试；
- A2A Owner：`src/agentos/transports/a2a/**`、`src/agentos/adapters/a2a/**`、
  `src/agentos/policies/a2a_*.py`、`src/agentos/testing/a2a_conformance/**` 和独占测试；
- WebSocket Owner：`src/agentos/transports/websocket/**` 和独占测试；
- Team Domain Owner：`src/agentos/multi/team_*.py`、`tests/multi/test_team*.py` 和新的
  `tests/multi/team/**`；
- CLI Owner：`src/agentos/cli/**` 和独占测试；
- Quality/Release Owner：deployment leaf、readiness/release 独占测试。

Subagent 禁止修改共享 facade、Builder、inventory、Contract、Plan 或其他 Owner 文件。

### 3.5 行为约束

- 每个行为先写失败测试，确认失败原因，再最小实现；
- 不双写 `SessionSnapshot` 与 `SessionCheckpoint`；
- 不以线程池包装 PostgreSQL/Redis；
- 不在 Transport 中导入 Runtime/Store/Worker；
- 不为旧 API 添加 shim、fallback、deprecated wrapper；
- 不用 Redis 作为 Run/Session/Retry 真值；
- 不在没有 Side Effect Policy 时自动重试外部 Tool。
- Ingress 不伪造 Worker fence；RUNNING cancel 必须原子轮转 fence；
- Worker 不在 Agent 返回后第二次提交 terminal；
- 所有 Service/Store/Artifact/cursor 操作必须绑定 RequestScope。

### 3.6 验证

- 目标测试 -> 模块测试 -> Contract Matrix -> 全量测试；
- Spec Compliance Review 与 Code Quality Review 分开；
- live PostgreSQL/Redis 故障测试是发布门禁；
- 每个 Wave 运行 module size、import boundary 和 `git diff --check`。

### 3.7 回滚

- 一个提交只引入一个可验证行为；
- 不提交红测试状态；
- Adapter 并行分支只包含独占文件，主线按固定顺序 cherry-pick/merge；
- 不通过恢复旧双写路径回滚；回滚到上一个绿色提交。

## 4. 当前基线与目标偏移自检

当前基线：

- Phase 5：`2834 passed, 12 skipped`；
- Architecture：`72 passed`；
- 当前 `RunStore`/`DurableStateStore` 是同步 Port；
- QueryLoop 事件循环内直接调用同步 SQLite；
- `channels.durable_session` 的 Legacy Snapshot 与 Phase 5 Checkpoint 重叠；
- 当前 Distributed Profile 主要是旧 Team/Daemon readiness 组合，不是 M5 Runtime；
- PostgreSQL/Redis/Team/A2A 代码已有部分语义证据，但同步且混合 Owner。

实施前必须再次确认：

- 当前分支为 `feature/agentos-sdk-phase6-distributed-runtime`；
- 用户保留的 `AGENTS.md`、工程规范和 inventory generator 工作区状态未被暂存或覆盖；
- OCR 仍为零新增；
- 不把旧 `SessionSnapshot` 作为捷径；
- 不把 Transport 拆分误当成 Distributed Runtime 已完成；
- 不因时间紧张跳过 live backend failure injection。

## 5. 文件规模门禁

Phase 6 必须处理或删除的超大模块：

| 文件 | 当前行数 | 处理 |
|---|---:|---|
| `channels/a2a_operations.py` | 4644 | leaf 迁移后删除 |
| `channels/a2a.py` | 2592 | wire/policy/adapter 迁移后删除 |
| `channels/asgi.py` | 2188 | mapping/channel runtime 迁移后删除 |
| `multi/team.py` | 1985 | types/ports/runtime/tools 迁移后删除 |
| `channels/a2a_conformance.py` | 1834 | testing package 迁移后删除 |
| `deployment.py` | 1460 | leaf 迁移后删除 |
| `multi/postgres_team.py` | 1042 | 分布式 Adapter 迁移后删除 |
| `multi/postgres_tasks.py` | 1008 | 分布式 Adapter 迁移后删除 |
| `multi/postgres_plan.py` | 734 | 分布式 Adapter 迁移后删除 |
| `persistence/postgres.py` | 730 | Legacy Snapshot 路径删除后收口 |
| `channels/durable_session.py` | 639 | 新 Profile 切换后删除 |
| `multi/redis_queue.py` | 529 | async Redis Adapter 稳定后删除 |

新文件目标：

- 300 行触发职责审查；
- 500 行默认拆分；
- 800 行禁止增长；
- 不用 `noqa`、Ruff suppress 或基线例外掩盖职责增长。

## 6. Wave 0：Contract 与 Red Gates（主线串行）

### Task 0：冻结 Phase 6 文档与 breaking map

修改：

- 本 Contract/Plan；
- `2026-07-11-agentos-oversized-module-decomposition-plan.md` 顶部标记被 Phase 6
  Contract 取代的兼容条款；
- 新增 Phase 6 public API removal/addition map；
- 在 breaking map 中单列 cancel safe-stop override 与 `side_effect_in_flight` 迁移路径。

检查：

- 所有上位规范链接存在；
- 旧 facade 删除清单完整；
- WebSocket 明确属于 6C；
- OCR 仍排除。

提交：`docs: freeze phase6 distributed runtime contract`

### Task 1：先写 async/fencing/submission Red Contract

新增测试：

- `tests/runtime/test_async_run_store_contract.py`；
- `tests/runtime/test_async_run_driver_contract.py`；
- `tests/runtime/test_accepted_start_input.py`；
- `tests/runtime/test_run_commit_owner.py`；
- `tests/runtime/test_running_execution_checkpoint.py`；
- `tests/distributed/test_run_submission_contract.py`；
- `tests/distributed/test_accepted_turn_recovery.py`；
- `tests/distributed/test_tenant_scope.py`；
- `tests/distributed/test_claim_models.py`；
- `tests/distributed/test_no_sync_bridge.py`；
- `tests/architecture/test_distributed_import_boundaries.py`；
- `tests/architecture/test_distributed_legacy_snapshot_exclusion.py`。

红测试必须分别证明：

- Store 方法可 await，挂起时 event loop 仍前进；
- `RunDriver.prepare/cancel_open` await Store；
- submission exact duplicate/conflict；
- accepted start 与 continuation 语义不混淆；
- stale fence 被类型化拒绝；
- ingress command 不要求调用方 fence，但 RUNNING cancel 轮转 fence；
- QUEUED/RUNNING cancel 在同一事务提交当前 AcceptedInput、清 cursor、写 CANCELLED checkpoint，
  旧 Worker 的 terminal/ledger complete 均失败；
- cancel 遇到 STARTED/AMBIGUOUS/COMPENSATING 时返回 side_effect_in_flight 且事务零写入；
  RESERVED 原子标记 cancelled_before_start，已知 terminal Ledger row 保留；
- AMBIGUOUS WAITING 不再保证直接 cancel；调用方先 resolution，Run 仍非终态才按幂等规则重试，
  resolution 已产生 FAILED 时不再 cancel；
- accepted input 在 claim/load/start 崩溃后仍可恢复，deterministic message 不重复；
- `AcceptedTurnExecution.preparation` 只允许 `ApplyAcceptedInput`、
  `RestoreAcceptedTurn(cursor)` 或 `SideEffectResume`，不保留字符串 mode 或裸 cursor；
  RunDriver 根据权威 QUEUED/RUNNING 状态决定是否执行 start transition；
- RunDriver 唯一提交 execution terminal；外部 CANCELLED 只由 RunCommandService 提交，
  Worker 只 ACK；
- Provider/tool 边界 execution cursor 不依赖模型重现 tool call ID；
- tenant-scoped idempotency 和跨 tenant not-found；
- Distributed 源码不使用 `to_thread`/`run_sync` 包装数据库/Redis；
- Local/Durable import 不加载分布式 Client；
- 新 Profile 不导入 Legacy Snapshot。

Task 1 的测试与最小 Contract type 一起进入绿色提交，不单独提交 failing branch。

## 7. Wave 1：唯一 Async Kernel 与 Durable 回归（主线串行）

### Task 2：RunStore/RunRuntime async 化

修改：

- `runtime/run_runtime.py`；
- `runtime/run_driver.py`；
- `runtime/query_loop.py`；
- `runtime/agent.py`；
- stream cleanup callback；
- Local InMemory adapter 和相关测试。

顺序：

1. `RunStore` 方法改为 async，并引入必填 `RunWriteGuard`；
2. `RunRuntime` 全部状态操作 async；
3. `RunDriver.prepare/events/cancel_open` await；
4. 引入 `AcceptedTurnExecution(input + RunWriteGuard)` 和 `RunCommitRuntime`；
5. `QueryLoop.execute()` 在 reservation 内 await prepare；
6. Agent durable command accept/pending await；
7. waiting/terminal 只由 RunDriver 经 RunCommitRuntime 提交；
8. cancellation/stream close 保持单次 terminal。

验证：

```powershell
python -m pytest tests/runtime/test_async_run_store_contract.py tests/runtime/test_run_runtime.py tests/runtime/test_query_loop*.py tests/runtime/test_agent*.py -q
python -m pytest tests/runtime -q
```

提交：`refactor: make run state ports natively async`

### Task 3：显式 Checkpoint handoff 与 Running Execution Cursor

修改：

- `runtime/durable_runtime.py`；
- `runtime/checkpoint.py`；
- 新的 execution cursor leaf type；
- `runtime/turn_lifecycle.py`；
- Durable waiting/terminal 调用链；
- 删除 `bind_checkpoint_source()`。

测试：

- Store 不持有 RuntimeCheckpointSource；
- capture 在 QueryLoop task 内发生；
- `before_provider/pending_tools/after_tools` 三个安全提交点；
- SDK stable invocation ID 与原 Provider tool-call ID mapping；
- PayloadProtector 对持久 Tool arguments 加密、授权 hydrate 和错误/trace 零明文；
- pending_tools checkpoint 成功前绝不启动 Tool handler；
- `RestoreAcceptedTurn(cursor)` 从 cursor 继续，不重新调用已经持久化响应对应的 Provider，
  不重复追加 StoredMessage/Turn 首次事件，并重建 Artifact/continuation 临时投影；
- WaitRequest 的 WAITING commit 原子移除 ActiveWindow tool-use、清除 running cursor 且不伪造
  Tool Result；
- WaitRequest commit 前崩溃从同一 pending invocation 恢复，commit 后 continuation 不恢复旧 batch；
- commit waiting/terminal 接收 immutable checkpoint；
- capture 失败不开始数据库事务；
- transaction 失败不返回 terminal/waiting outcome。
- terminal event 只在 atomic commit 成功后发布；
- Worker/Channel 无第二个 commit owner。

提交：`refactor: pass immutable checkpoints to durable stores`

### Task 4：SQLite Durable async Adapter 与 Profile 生命周期

修改：

- `durable/sqlite_store.py` 及 leaf；
- `durable/profile.py`；
- `_builder_durable.py`；
- `pyproject.toml` 的 durable extra；
- Plan/Memory/Skill/Artifact 中本次执行路径涉及的 SQLite I/O。

规则：

- 使用 Durable 专属异步 DB 边界；
- 不在事件循环执行同步 SQLite/Filesystem I/O；
- `ArtifactStore` 与 ArtifactRuntime I/O 方法升级为唯一 async 契约；
- ProviderRequest build 只读当前 Turn projection cache，不发起 Artifact I/O；
- I/O Profile 使用 async open/build/close 生命周期；
- 保持 Phase 5 单 Session 单 active Run 和 atomic checkpoint 语义；
- 不引入 PostgreSQL/Redis。

验证：

```powershell
python -m pytest tests/durable tests/runtime/test_durable* tests/artifacts/test_sqlite_filesystem_artifact_store.py tests/planning/test_sqlite_plan_store.py tests/memory/test_sqlite_memory_store.py -q
python -m pytest tests/architecture/test_distributed_import_boundaries.py -q
```

提交：`refactor: move durable profile behind async io ports`

## 8. Wave 2：Distributed Shared Contract（主线串行）

### Task 5：models/protocols/services

新增：

- `distributed/models.py` 及职责 leaf：RequestScope、Submission/Receipt、RunReadModel、canonical
  submission digest、accepted execution 引用、Claim、Delivery、Outbox、安全 LiveEvent projection、
  StreamGap、WorkerState；
- `distributed/protocols.py`：State/Claim/Outbox/Queue/Lease/Replay/Artifact Port；
- `distributed/services.py`：RunSubmission/Command/Query/Event/Artifact Application Service；
- `distributed/errors.py`。

要求：

- 只依赖 runtime/domain type；
- 不导入 concrete backend；
- 所有 I/O Port 原生 async；
- DTO 完全 immutable；
- Service 全部显式接收 RequestScope，数据库键固定 tenant-scoped；
- identifier、UTC datetime、version/fence 严格校验；
- Service 不执行 QueryLoop。
- Accepted execution、Run/Command、Artifact、RunStatus、WaitReason 和 AgentResult 直接引用
  canonical runtime/domain type，不在 distributed 复制定义；
- Replay 使用从 `TurnStreamEvent` allowlist 投影的 typed `RunEventEnvelope`，删除 Prompt、thinking、
  Tool Result 和异常 payload，`event_kind` 从 projection 派生；
- `RunQueryPort` 返回 PostgreSQL `RunReadModel`；Event subscribe 在打开 stream 前异步 preflight；
- Replay tail 使用显式 async-close subscription，Application wrapper 负责透传关闭；
- Queue delivery 不携带或信任 tenant payload，publish 接收 PostgreSQL 权威 `OutboxRecord`；
- Side Effect DTO/Port 与 ToolInvocation 在 Task 6 的 canonical capability/runtime leaf 一起
  冻结，Task 5 不使用 `dict`/`object` 临时占位。

验证：

```powershell
python -m pytest tests/distributed tests/architecture/test_distributed_import_boundaries.py -q
python -m ruff check src/agentos/distributed tests/distributed
```

提交：`feat: define distributed runtime contracts`

### Task 6：Side Effect Contract 与 ToolInvocation

前置条件：先冻结并完整遵守
`2026-07-20-agentos-phase6-task6-side-effect-contract-addendum.md`。该补充合同解决稳定 identity、
多 attempt、handler error、result ref、WAITING 原子载荷、reconciliation resume 和
compensation handler 输入；实现层不得重新解释这些语义。

主线修改：

- `capabilities/tools.py`；
- `capabilities/executor.py`；
- `capabilities/backend.py`；
- QueryLoop tool execution context；
- built-in tool registration；
- 全仓 tool handler callsite。

测试：

- operation ID 跨 retry 稳定；
- 五类 policy 的允许/拒绝矩阵；
- Ledger `RESERVED/STARTED/COMPLETED/AMBIGUOUS/COMPENSATING/COMPENSATED/RESOLVED`
  状态与 result_ref 复用；
- 五类 policy 在 RESERVED/STARTED/COMPLETED 的固定恢复矩阵；
- `resolve_side_effect` 只处理 reconciliation WaitReason；
- 四种 resolution 的 Ledger/Run/AcceptedInput/Outbox 后置矩阵；
- Ledger-only transaction 校验 guard 但不递增 Run aggregate version；compensation completion
  保持 version，随后 FAILED terminal 只递增一次；
- cancel 的 Side Effect 安全停止点矩阵，以及拒绝时 fence/Run/input/cursor 全部不变；
- compensatable 必须声明 compensation handler；
- wait-capable 只能组合 `pure + EXCLUSIVE`，且所在 Provider batch 必须是单一调用；混合 batch
  在任何 handler 开始前 fail closed；
- WAITING commit 原子记录 `COMPLETED(wait_control)`；commit 前使用同一 invocation 恢复，
  commit 后不得再次调用 handler 或恢复旧 batch；
- 补偿调用前崩溃：Ledger 保持 `COMPENSATING`，恢复后使用同一 compensation operation ID，
  handler 调用一次、外部补偿生效一次；
- 外部补偿生效后、`COMPENSATED` 落库前崩溃：恢复时允许用同一 ID 重试 handler，
  外部系统幂等去重，补偿效果总计一次，随后 Ledger 进入 `COMPENSATED`；
- `COMPENSATED` 落库后、FAILED terminal commit 前崩溃：恢复只重试 terminal commit，
  compensation handler 调用次数不再增加，Run 最终为 FAILED，terminal Outbox 只有一份且
  delivery 只在提交后 ACK；
- 未声明外部 Tool fail closed；
- concurrency policy 与 side-effect policy 独立；
- handler 只使用统一 `ToolInvocation` signature。

提交：`feat: make tool side effects explicit`

Wave 2 完成后冻结 Shared Contract。后续 subagent 不得自行扩展 Protocol。

## 9. Wave 3：PostgreSQL / Redis / Worker 并行实现

本 Wave 使用独立 worktree。主会话只答疑、Review 和集成，不实现第四个大支线。

### Workstream A：PostgreSQL Truth / Shared Artifact（PostgreSQL/Artifact Owner）

允许文件：

- `distributed/postgres/state.py`；
- `distributed/postgres/claims.py`；
- `distributed/postgres/outbox.py`；
- `distributed/postgres/side_effects.py`；
- `distributed/postgres/artifacts.py`；
- `distributed/postgres/schema.py`；
- `distributed/blobs/protocol.py`、`distributed/blobs/s3.py`；
- Phase 6 migrations；
- `tests/distributed/postgres/**`。

红测试：

- active Run partial unique constraint；
- submission/command duplicate and conflict；
- tenant-scoped unique key、跨 tenant not-found；
- accepted -> claimed -> committed/expired -> accepted；
- expired RUNNING 保持 RUNNING，并按权威 cursor 生成 typed preparation，不新增
  `RUNNING -> QUEUED` 领域转换；
- running execution checkpoint 每次递增 aggregate version 并返回新 guard；
- checkpoint/terminal atomic rollback；
- two-worker claim；
- stale fence 对所有写失败；
- cancel 与 old worker completion 竞争；
- QUEUED/RUNNING cancel 原子提交 AcceptedInput、清 running cursor 并写 CANCELLED terminal record；
- cancel 锁 Ledger 后执行 RESERVED cancellation、STARTED/AMBIGUOUS/COMPENSATING conflict 和
  terminal row preservation；
- DB time expiry；
- outbox 与 state 同事务；
- side-effect 全状态、result_ref 和 compensation；
- protected invocation ref、stable invocation ID 和 running cursor recovery；
- PostgreSQL Artifact metadata + shared Blob 跨 Worker reload；
- Artifact upload 先持久化 staging，再 conditional put blob 和 activation；同一 `upload_id`
  同内容幂等恢复，冲突在 blob I/O 前失败，staging 对 read/list 不可见；失败/cancellation 保留
  staging/blob，不在缺少 upload lease 时执行 age-based stale cleanup；
- connection cancellation/close。

提交：`feat: add fenced postgres runtime truth store`

### Workstream B：Redis Delivery（Redis Owner）

允许文件：

- `distributed/redis/leases.py`；
- `distributed/redis/queue.py`；
- `distributed/redis/replay.py`；
- `tests/distributed/redis/**`。

红测试：

- acquire/renew/release exact owner lease；
- consumer group ACK/reclaim；
- Redis 自动 Stream ID + stable outbox_id payload duplicate；
- 多 Relay 乱序 XADD 和 Consumer outbox_id 去重；
- pending-safe trimming；
- wakeup redelivery；
- replay + tail、old cursor gap；
- close/drain 取消阻塞读取；
- Redis outage 的类型化错误。

提交：`feat: add redis delivery and replay adapters`

### Workstream C：Worker/Relay（Worker Owner）

允许文件：

- `distributed/worker/runner.py`；
- `distributed/worker/relay.py`；
- `distributed/worker/supervisor.py`；
- `tests/distributed/worker/**`。

使用 Fake Port 先验证：

- receive -> claim AcceptedTurnExecution -> hydrate -> Agent stream -> RunDriver commit -> ACK
  顺序；
- terminal duplicate 直接 ACK；
- claim loss 取消 AgentStream；
- Worker 使用 `stream=True` 并作为唯一消费者；
- waiting/terminal 前不 ACK；
- Agent terminal 后不再次提交；
- Relay crash window；
- drain 顺序和 timeout natural expiry；
- PostgreSQL/Redis failure fail closed；
- side-effect ambiguous recovery。

提交：`feat: add distributed worker lifecycle`

### Wave 3 集成顺序

1. PostgreSQL；
2. Redis；
3. Worker/Relay；
4. 主线新增 `distributed/profile.py` 组合；
5. contract tests；
6. live PostgreSQL/Redis happy path；
7. Spec Compliance Review；
8. Code Quality Review。

共享 facade 只在三支线全部绿色后由主线修改。

主线 `distributed/profile.py` 组合必须遵守
`2026-07-20-agentos-phase6-wave3-profile-contract-addendum.md`：只公开 claim-only hydration，
Worker/Relay 使用独立 Queue，BlobStore 为 borrowed resource，构造阶段零 I/O，Worker 显式启动，
claim Artifact view 只读且绑定 tenant/session。当前静态 `context_projections` 在 Distributed
Profile 中 fail closed，直到独立的 claim-scoped projection factory 合同获批。

## 10. Wave 4：6B Transport Leaf 并行

### Workstream D：HTTP/SSE

前置条件：完整遵守 Wave 4 Transport Addendum，先冻结 JSON/header/multipart、scoped cursor、
golden frame 和稳定错误映射。

目标文件：

- `transports/http/request_types.py`；
- `transports/http/response_types.py`；
- `transports/http/request_decoder.py`；
- `transports/http/response_encoder.py`；
- `transports/sse/frames.py`；
- `transports/sse/cursors.py`；
- `transports/sse/codec.py`；
- `tests/transports/http/**`、`tests/transports/sse/**`。

覆盖 malformed body、size limit、header、error redaction、receipt、heartbeat、resume、
terminal、gap、Contract HTTP status/error matrix、tenant-scoped cursor 和 golden frame。
同时覆盖 Artifact multipart upload/list/read/delete、size/media policy 和路径/credential
不泄漏。禁止导入 Store/Agent/Worker。

提交：`refactor: extract http and sse wire protocols`

### Workstream E：A2A Wire/Mapping

前置条件：完整遵守 Wave 4 Transport Addendum，只实现 A2A 1.0 canonical wire；不迁移 legacy
payload fallback。

目标文件：

- `transports/a2a/message_types.py`；
- `transports/a2a/card_types.py`；
- `transports/a2a/operation_types.py`；
- `transports/a2a/push_types.py`；
- `transports/a2a/serialization.py`；
- `transports/a2a/protocol.py`；
- `transports/a2a/mapping.py`；
- `transports/a2a/sse.py`；
- `tests/transports/a2a/**`。

覆盖 JSON round-trip、保留字段、版本/extension negotiation、domain mapping、RunStatus 到
A2A state、全部 11 个官方 PascalCase operation matrix 和 A2A golden payload。TaskStore、push
Store、HTTP Client 和 Worker 不进入 Transport；禁止 legacy `message/send`、`message/stream`、
`tasks/get`、`tasks/cancel` 或 `tasks/resubscribe` alias。

提交：`refactor: extract a2a wire protocols`

### Workstream F：A2A Policy/Adapter/Conformance（A2A Owner）

在 Workstream E 稳定后启动：

- `policies/a2a_auth.py`、`a2a_trust.py`、`a2a_egress.py`；
- `adapters/a2a/client.py`；
- `distributed/postgres/a2a.py`（task binding 与 push config/operation 唯一 truth owner）；
- `distributed/postgres/a2a_catalog.py`（tenant-scoped ListTasks join 与 keyset pagination）；
- `distributed/postgres/a2a_delivery.py`（status transaction fanout、顺序、重试和 delivery 终态唯一 truth）；
- `distributed/worker/a2a_push.py`（复用已有 Outbox/Queue/ACK 生命周期）；
- `testing/a2a_conformance/**`；
- 独占测试。

禁止新增第二个 push PostgreSQL Store、push retry Daemon 或同步 urllib client。Push delivery
必须复用 Phase 6 Outbox/Fencing/ACK 语义。实现前按 Addendum 冻结以下红测矩阵：

- Create 与当前 Run 状态事务对账，覆盖 terminal-before-create 与 transition-after-create；
- queued/running/human-wait/other-wait/三个 terminal 的完整状态映射；
- 同 task/config 按 status sequence 串行，重试不得让终态越过前驱；
- Delete-before-attempt、attempt-before-Delete、claim 后暂停、send gate、webhook 内同步 Delete 与
  成功后零 POST 的线性化；
- send gate 默认 2 秒/hard max 5 秒，drain backpressure/timeout/cancel 后 transaction 与行锁释放；
- 2xx/非 2xx/网络失败/取消/事务失败/崩溃窗口的 ACK 顺序；
- attempt lease fencing/takeover、8 次失败预算、指数退避、abandoned 终态与 typed failure category；
- direct StreamResponse body、`application/a2a+json`、token/auth header 的 CRLF/control/size/scheme
  门禁、跨 origin redirect strip；
- secret 解密失败 fail closed 与 historical key rotation invariant；
- PostgreSQL 为真值、Redis 只负责 pending/reclaim/ACK 的架构门禁。

提交：`refactor: isolate a2a policy adapters and conformance`

### Wave 4 Channel Integration（主线串行）

主线新增：

- `channels/asgi_app.py`；
- `channels/asgi_router.py`；
- `channels/service_wiring.py`；
- `channels/run_endpoint.py`；
- `channels/sse_endpoint.py`；
- `channels/artifact_endpoint.py`；
- `channels/a2a_endpoint.py`。

HTTP/SSE/Artifact Channel 只调用 `RunSubmissionService`、`RunCommandService`、
`RunQueryService`、`RunEventStream` 和 `ArtifactService` 五个 Application boundary。A2A
Channel 可以额外调用增补冻结的 `A2ATaskService`、`A2ATaskCatalogService`、`A2APushService` 和
immutable `A2AAgentCardProvider`，但不得直连其 Port/Adapter。主线先增加 scope-producing
authenticator 和 task/push shared contract，再完成
HTTP/SSE/Artifact，最后集成 A2A；每次集成后运行 architecture import gate。

`channels/a2a_endpoint.py` 必须对 11 个官方 operation 逐项增加 success/error contract tests，并覆盖：
version/extension negotiation、鉴权先于 JSON-RPC dispatch、Send inline config identity、Task binding、
ListTasks、push CRUD、snapshot-first stream、terminal preflight、disconnect/aclose 与错误脱敏。Channel
不得保留旧 `A2AMessagePart`、legacy method alias、私有错误码或第二套 wire DTO。

提交：`refactor: compose channels over distributed services`

## 11. Wave 5：6C WebSocket / CLI / Team

Wave 5 完整遵守对应 Addendum，按 5A Shared Run Stream/WebSocket、5B Migration/CLI、
5C Team Domain、5D Team Distributed Delivery、5E breaking cutover 顺序执行。共享 Contract、
Migration authority 和 internal Team start 由主线 Owner 修改；支线不得自行扩展。

### Workstream G：WebSocket

新增：

- `transports/run_stream.py`，并原子迁移 SSE/A2A cursor/event projection；
- `transports/websocket/frames.py`；
- `transports/websocket/serialization.py`；
- `channels/websocket_buffer.py`；
- `channels/websocket_session.py`；
- `channels/asgi_websocket.py`；
- `tests/transports/websocket/**`。

主线随后新增 `channels/websocket_endpoint.py` 并薄接入 `asgi_app.py`。冻结 `/v1/ws`、
`agentos.run.v1`、request ID 幂等身份、逐资源 auth、multi-run subscription、cursor resume、
gap、disconnect 不取消 Run、显式 cancel 和双重 bytes/count backpressure。slow consumer 只使用
已经成功发送的 cursor，发送稳定 error 后以 `4408` 关闭并 exactly-once 释放 subscription。

Shared Run Stream hard max 的主线集成必须同时完成：Provider UTF-8 delta 预分片；超限 final result
省略但保留 COMPLETED/read model/`turn_completed`；超限 pre-terminal event 通过非 Public 的
AgentStream failure control 返回 RunDriver 权威 FAILED，不使用跨 observability/sync-work generator
的 `athrow`；observability wrapper 确定性关闭内层 generator。另以 Event/Barrier 覆盖 terminal
commit 清 claim 后 heartbeat 失败的竞态：PostgreSQL 已终态时完成 terminal append 后 ACK，仍为
非终态的 stale fence 关闭 stream 且 no-ACK。

terminal publication recovery 由主线补齐：新增 internal `CommittedExecutionOutcome` 与按 execution
`outbox_id` 的 PostgreSQL 查询；Worker 使用保留 `event_sequence=2^53-1` 从 committed checkpoint
稳定重建 terminal envelope；Redis 使用单个 Lua 原子 ensure，按稳定 event identity 去重且不维护
无界旁路索引。初次 resolve 已终态、claim-none 后复核已终态、heartbeat 复核同 execution 已终态三条
路径统一进入 recovery。superseded delivery 只 ACK，不得 claim 后续 input；外部 cancel 的新 fence
先关闭旧 stream，再按新 committed outcome 发布。任一查询、校验、stream cleanup 或 Redis ensure
失败均 no-ACK，recovery 不 hydrate Agent、不调用 Provider/Tool。
current outcome 的 recovery 必须先 acquire/ensure Session Lease；heartbeat failure 一律先取消并回收
execution，再在 Lease 仍归当前 Worker 时补发 canonical terminal，不再等待失权 generator 继续产出。

提交：`feat: add websocket command and event channel`

### Workstream H：Team 拆分

新增：

- `multi/team_types.py`；
- `multi/team_ports.py`；
- `multi/team_runtime.py`；
- `multi/team_tools.py`；
- `multi/team_identity.py`；
- `multi/team_in_memory.py`；
- 对应测试。

Team Domain Owner 先冻结上述 type/port。随后 PostgreSQL Owner 在其独占目录实现
`distributed/postgres/team.py`，Worker Owner 在其独占目录实现
`distributed/worker/team.py`，Redis Owner 实现 typed Team event replay；三者不得反向修改
`multi/team_*`，也不得互相修改对方目录。

规则：

- Team delivery 使用稳定 source ID：匹配 WAITING Run 时生成 wakeup，否则通过 Submission
  创建新 Run；其他非终态拒绝；
- 新 Run 使用 internal continuation start，不写入伪 user StoredMessage；
- WAITING 只匹配 `remote_result/resource_availability` 且 correlation 等于 wait handle；
- Wave 5 TeamMessage 禁止跨 Session Artifact handles；
- 不直接运行 LocalContinuation；
- 不保留独立 retry/daemon；
- PostgreSQL 保存 Team truth；Redis 只 delivery/replay；
- 单调 fencing token 保护 claim/release/result。

提交：`refactor: run team continuations through distributed runtime`

在 PostgreSQL/Worker 支线前，主线必须先完成 `distributed/internal_models.py`、
`internal_errors.py`、`internal_protocols.py`、`internal_services.py`、accepted internal-start
Kernel/schema/restore 合同、active-run-by-session query、Team identity helper 和 claim-scoped
`TeamAccessContext`，并通过以下红测：

- apply/restore 从同一 canonical payload 重建字节等价的 ephemeral projection，只有 apply 发布首次
  Turn/stream event，二者都不产生 StoredMessage 或 `UserMessageAppendedEvent`；
- expired、released、stale-fence InternalSubmissionAuthority 在提交事务内 fail closed 且 Run 不存在；
- internal-start apply/restore/wakeup 每次 Run Worker claim 都从 PostgreSQL active binding 重建
  `TeamAccessContext`，禁止 payload/Redis/delivery claim 提供 owner；
- Team message/delivery/submission/command/outbox 固定 identity 测试向量跨 Owner 完全一致；
- sender 与 direct/broadcast 任一语义变化产生 identity conflict；broadcast retry 遇 membership 变化仍
  复用首次 recipient snapshot，不增补 delivery；
- 两个并发相同 operation 的 `team_say` 在唯一键竞争后返回同一 duplicate，不泄漏数据库异常；
- active Run 阻止 binding/team 删除，pending delivery 遇 binding 撤销稳定 REJECTED 并 ACK；
- claim 自然过期但尚未 takeover 时 result commit 零写入且不 ACK；
- 第二次 route evaluation 再次冲突时 release claim、保持可 takeover 且不 ACK。
- trusted wakeup 必须持久化 `team_delivery_id` provenance，普通同形 wakeup 不获得 TeamTools；
- receipt-result crash takeover 必须先按 delivery 恢复原 input kind/run/version，再检查 binding 或
  active Run；
- wakeup 提交事务内必须复核 PostgreSQL TeamMessage correlation 与锁定后的 wait kind/handle。

### Workstream I：Migration Authority（主线串行）

新增：

- `distributed/migrations/models.py`；
- `distributed/migrations/protocols.py`；
- `distributed/migrations/service.py`；
- `distributed/postgres/migrations.py`；
- `distributed/errors.py`：migration-required/legacy/checksum 稳定错误；
- `distributed/profile.py`：open 只做 compatibility check；
- `distributed/postgres/state.py`、`distributed/postgres/schema.py`：删除自动 DDL authority；
- `docs/migrations/2026-07-20-postgres-distributed-runtime.sql` 及 packaged 镜像：删除 legacy marker；
- `docs/migrations/2026-07-21-postgres-team-delivery.sql` 及 packaged 镜像；
- 对应 migration contract/adapter/profile no-DDL tests。

在 CLI migrate 前冻结显式 catalog、checksum policy 和 plan；PostgreSQL Port 在一次 apply 中拥有
advisory lock、ledger 和全序列 transaction。`DistributedRuntimeProfile.open()` 只执行 read-only
exact target compatibility check，不再自动执行 DDL。`migrate --check` 只验证，普通 `migrate`
应用缺失版本；两者都不按目录 glob 猜测 migration。

提交：`refactor: make migration service the schema authority`

### Workstream J：CLI Application Commands

新增/修改：

- `cli/parser.py`；
- `cli/application.py`；
- `cli/auth.py`；
- `cli/output.py`；
- `cli/commands/init.py`；
- `cli/commands/serve.py`；
- `cli/commands/migrate.py`；
- `cli/commands/run.py`：submit/command/get/watch；
- `cli/commands/artifact.py`：upload/list/read/delete；
- `cli/commands/worker.py`：start，并在进程信号后 drain/close；
- `cli/commands/relay.py`：start；
- `distributed/worker/supervisor.py`：增加可观察 `wait()`，不增加同步 wrapper；
- `cli/main.py` 收窄为约 40 行 dispatch；
- `tests/cli/**`。

CLI 不直接写 SQL 或构建 Redis Client；migrate 调用 Distributed Migration Service。
测试通过 Fake Application Services 断言每个 subcommand 的 RequestScope、DTO、receipt/event
输出、错误 code 和 Ctrl+C/drain 生命周期，不连接真实后端。

CLI 通过唯一 `--factory module:callable`/`AGENTOS_CLI_FACTORY` 注入无 I/O `CliHostFactory`，
再按 command 打开最窄 async host；migrate 不打开 Runtime Profile/Redis/Worker。tenant 只是经
`CliScopeResolver` 鉴权和授权的 route hint。删除旧 `run APP`、`--dsn`、`--dry-run` 和
同步 psycopg。`serve`、`worker start`、`relay start` 进程职责分离；没有 WorkerControlPort，
因此不实现跨进程 `worker drain` 子命令。

提交：`refactor: route cli through application services`

## 12. Wave 6：6D Deployment / Failure / Breaking Cutover

### Task 7：Deployment leaf 与 Readiness

迁移到：

- `deployment_types.py`；
- `deployment_reports.py`；
- `deployment_validation.py`；
- `deployment_profiles.py`；
- `deployment_workers.py`，用于隔离 worker process lifecycle，避免 validation 同时承担
  backend verification 与进程监管并越过 500 行硬门禁。

逐个迁移 `readiness.py`、`release.py`、`state_plane.py` 和 reference example 消费者。
报告 leaf 不反向依赖 runner/profile。Readiness 覆盖 migration、outbox lag、queue pending、
claim/heartbeat、drain、stream gap 和 ambiguous effect。

Wave 6D 必须冻结 ASGI ingress 限流与 SSE 连接并发准入合同：明确由内置 Channel 还是外层
ASGI middleware/API Gateway 承担，定义 tenant/principal 维度、稳定 `429` 映射和分布式部署
状态模型，并在 release readiness 中验证配置与生效证据。

完成证据（2026-07-22）：旧 `deployment.py` 已物理删除，生产与测试消费者全部迁移到
canonical leaf；Public API inventory/stability 与 module-size baseline 已同步。报告模块 439 行，
经职责审查仍只负责 evidence report/import/gate/run-result；validation 模块 305 行，只负责
invocation/runner；worker lifecycle 独立为 230 行模块。Deployment、runtime profile、state
plane、reference example 与 API inventory 联合门禁为 `182 passed`，Ruff、compileall 与
`git diff --check` 通过。

提交：`refactor: split deployment evidence boundaries`

### Task 8：Live Backend Failure Injection

网络半开、heartbeat fail-closed 与 Relay batch deadline 同时遵循
`2026-07-22-agentos-phase6-task8-failure-timeout-addendum.md`，不得由测试侧 `wait_for()` 替代
生产 Adapter 的 I/O deadline。

新增 live tests：

- command commit crash windows；
- accepted turn claim/start/message/provider-call crash windows；
- Provider response 完成后、`pending_tools` commit 前崩溃：不得启动 Tool，恢复时允许重新调用
  Provider；
- `pending_tools` commit 后、首个 Tool 前崩溃：不得重新调用 Provider，恢复相同 invocation；
- batch 部分 Tool 完成后崩溃：COMPLETED 从 result_ref 复用，其余调用按 policy 恢复，最终按
  Provider 原始顺序只追加一组完整 Tool Result；
- 全部 Tool 完成后、`after_tools` commit 前崩溃：从 Ledger 重建全部结果，不重复外部效果；
- `after_tools` commit 后、下一 Provider 前崩溃：直接进入下一 Provider call，不重跑 Tool；
- WaitRequest 的 WAITING commit 前/后崩溃：前者恢复同一 invocation，后者不再调用 handler，
  ActiveWindow 无 dangling tool-use，continuation 不恢复旧 batch；
- outbox relay crash windows；
- stale claim takeover；
- Redis restart、PostgreSQL restart；
- network timeout/cancellation；
- worker process kill；
- pending reclaim；
- side-effect ambiguity；
- compensation/resolution，必须分别注入以下三个窗口并断言调用次数、Ledger/Run 状态、
  terminal Outbox 和 delivery ACK 顺序：
  - compensation handler 调用前崩溃：恢复后同一 operation ID，外部效果一次；
  - 外部效果后、`COMPENSATED` 落库前崩溃：同一 operation ID 幂等重试，外部效果仍为一次；
  - `COMPENSATED` 后、FAILED terminal commit 前崩溃：不再次调用 handler，只重试 terminal
    commit，Outbox 去重且 commit 后 ACK；
- QUEUED cancel 与 RUNNING cancel/old Worker terminal 竞争：AcceptedInput 最终 COMMITTED、cursor
  已清除、CANCELLED checkpoint/Run/terminal Outbox 同事务，旧 fence 写全部失败；
- cancel 三窗口：handler 前成功且调用次数为零；外部效果后到 Ledger complete 前返回
  side_effect_in_flight 且 Worker 仍可完成；COMPLETED 后成功并保留结果，外部效果总计一次；
- drain；
- SSE/WebSocket replay gap；
- cross-tenant isolation；
- Worker A upload / Worker B hydration and artifact reload；
- staging 后、blob put 后到 activation 前两个上传崩溃窗口均使用同一 `upload_id` 恢复；
  慢上传期间不得由 age-based cleanup 删除 staging/blob；
- full restart/hydration/artifact reload。

Task 8 模块职责审查（2026-07-22）：`runtime/side_effect_types.py` 仍只承载 Side Effect
领域值对象与状态不变量；`distributed/profile.py` 仍是资源组合和生命周期 owner；
`distributed/worker/runner.py` 仍只承载单条 Run delivery 状态机；`distributed/services.py`、
`distributed/redis/replay.py`、`distributed/postgres/state.py` 与
`distributed/worker/supervisor.py` 分别保持 Application boundary、Run replay、State facade 与
Worker lifecycle 单一职责。上述文件均低于 500 行强制拆分线，本阶段不做机械拆分。

Task 8 收口证据（2026-07-22）：

- 真实 PostgreSQL、Redis 与 S3-compatible Artifact live suite：
  `99 passed, 1 skipped, 4271 deselected`；唯一 skip 为未安装的可选 OpenTelemetry；
- 本地 Artifact live evidence 使用 Moto S3-compatible HTTP server；CI 继续固定使用
  `localstack/localstack:3.8.1`，两者执行同一 S3 Adapter 合同；
- 排除 Task 9 breaking cutover 红门禁后的 non-integration：
  `4265 passed, 4 skipped, 99 deselected`；Architecture：`171 passed`；
- Worker 全量：`108 passed`；heartbeat 与 Relay timeout live 定向：`4 passed`；
- Ruff、compileall、module-size baseline 与 `git diff --check` 全部通过；Transport 反向依赖和
  Distributed 同步 I/O 静态搜索均为零命中；OCR 仅命中本 Contract 的四处明确排除文字；
- Spec Compliance Review：`P0=0, P1=0, P2=0`；原始唯一 P2 为本计划状态滞后，已由本段
  修复；Code Quality/Security Review：`P0=0, P1=0, P2=0`；
- Contract 第 17 节提到 Dead Letter 前真值校验，但当前未定义 QueuePort/DLQ 状态转换；
  Task 8 不新增未冻结的 DLQ surface。现有高 `delivery_count` 重投测试证明 Worker 始终先查
  PostgreSQL 真值；若未来提供 SDK 级 DLQ，必须先单独冻结合同，不计入本 Task 验收。

真实后端命令使用项目已有 integration marker 和 test compose。跳过 live suite 不能生成
release-ready evidence。

提交：`test: add distributed runtime failure injection matrix`

### Task 8.5：Workspace 模块职责拆分

Wave 5C 收口触碰 `src/agentos/workspace.py` 时，该文件为 663 行，已超过 500 行拆分门禁。
本轮只收紧 `WorkspaceHandle.metadata` 的既有不可变领域边界，不扩大为公共模块搬迁；Phase 6
breaking cutover 前必须原子完成以下拆分：

- `agentos/workspace/models.py`：Workspace scope、handle、request/result 和 Port DTO；
- `agentos/workspace/policies.py`：workspace narrowing、execution 与 isolation policy；
- `agentos/workspace/local.py`：Local provider 和 reference execution backend；
- `agentos/workspace/__init__.py`：canonical public exports；同批删除旧 `workspace.py`，不保留
  兼容文件、动态 alias 或双实现。

验收：现有 Workspace/Public API/Planning/Team 测试保持通过，root import identity 不分叉，单个生产
文件低于 500 行，Local backend 不进入 Kernel/Team Domain 依赖图。

提交：`refactor: split workspace domain and local adapters`

### Task 9：Breaking Cutover

主线单一 Cutover Owner 执行：

1. 全仓切换 canonical import；
2. 收窄根/`channels`/`multi` facade；
3. 删除 Contract 第 14 节列出的 legacy 文件；
4. 删除 Legacy Snapshot 新路径和同步 backend wrapper；
5. 更新 public API inventory/stability；
6. 更新 version/CHANGELOG/migration guide；
7. 运行 import identity/removal tests；
8. 全量门禁。

禁止多个 worktree 同时修改 public facade 或执行部分删除。

Task 9 实现与门禁证据（2026-07-22，独立 Review 整改后）：

- 全量测试：`3655 passed, 93 skipped`；Architecture：`176 passed`；
- 真实 PostgreSQL、Redis 与 Moto S3-compatible integration：`89 passed, 1 skipped,
  3658 deselected`；唯一 skip 为未安装的可选 OpenTelemetry；
- Ruff、`compileall` 与 `git diff --check` 全部通过；Transport 反向依赖、Distributed
  同步 I/O、Legacy Snapshot 三项静态搜索均为零命中；OCR 仅命中本 Contract 的四处明确
  排除文字；
- Public API inventory 与 module-size baseline 已重新生成；`readiness.py` 按 release evidence、
  form types、channel forms、orchestration forms 和聚合器拆分为 `401/90/287/260/38` 行，
  公共 API 与类型 identity 保持不变；当前无 800 行以上生产模块；
- Release evidence 生成器复用 canonical live-backend 常量，示例命令、版本与 backend 集合已
  对齐；`release.py` 的 redaction/JSON-safe 职责已拆到 106 行私有叶子，入口模块降至 432 行；
  Local/Durable 仍不加载 PostgreSQL/Redis client；
- 初审发现的 legacy `redis`/`postgres` extras、Planner readiness 旧声明、两份旧 README 草稿、
  Worker close/drain-before-start 后 `wait()` 阻塞及 `release.py` 规模问题均已修复并加入回归
  门禁；最终 Spec Compliance Review 与 Code Quality/Security Review 均为
  `P0=0, P1=0, P2=0`，Task 9 与 Phase 6 正式关闭。

提交：`refactor!: complete phase6 distributed runtime cutover`

## 13. 测试与验证门禁

### 13.1 每个 Task

```powershell
python -m pytest <target-tests> -q
python -m ruff check <changed-src> <changed-tests>
python -m compileall -q <changed-src> <changed-tests>
git diff --check
```

### 13.2 6A

```powershell
python -m pytest tests/runtime tests/durable tests/distributed -q
python -m pytest tests/architecture/test_distributed_import_boundaries.py tests/architecture/test_distributed_legacy_snapshot_exclusion.py -q
```

### 13.3 6B/6C

```powershell
python -m pytest tests/transports tests/channels tests/multi -q
python -m pytest tests/testing/a2a_conformance tests/cli -q
```

### 13.4 6D 与最终门禁

```powershell
python -m pytest -m integration -q
python -m pytest -q
python -m pytest tests/architecture -q
python -m ruff check src tests
python -m compileall -q src tests
git diff --check
```

额外静态检查：

```powershell
rg -n "TaskStore|RunStore|SessionProvider|sqlite|postgres|redis|Worker|Daemon|from agentos.runtime import Agent" src/agentos/transports
rg -n "to_thread|run_sync|ThreadPoolExecutor" src/agentos/distributed
rg -n "SessionSnapshot|PostgresSessionSnapshotPersistence|RedisHotSessionStore" src/agentos/distributed src/agentos/channels
rg -n -i "ocr" src tests docs/superpowers/specs/2026-07-17-agentos-phase6-distributed-runtime-transport-contract.md
```

前两条预期零命中；第三条在 Cutover 后预期零命中；OCR 只允许 Contract 中的明确排除说明。

## 14. 双层 Review

### 14.1 Spec Compliance Review

逐项检查：

- PostgreSQL/Redis authority；
- async Port 唯一性；
- Session active Run 约束；
- submission/command idempotency；
- accepted turn claim/commit recovery；
- claim/fence/ACK 顺序；
- RunDriver execution commit owner 与 RunCommandService external-cancel owner 不重叠；
- checkpoint 原子性；
- side-effect 五类行为；
- Transport 禁止依赖；
- Context/Artifact persistence/visibility；
- tenant scope 与跨 tenant not-found；
- breaking deletion；
- OCR 排除。

### 14.2 Code Quality Review

逐项检查：

- race/cancellation/close；
- transaction isolation 和 SQL index；
- async client/pool 生命周期；
- immutable type/strict validation；
- secret redaction；
- worker drain/backpressure；
- shared Artifact staging/retry/cross-worker visibility，以及无 upload lease 时禁止 age-based
  stale cleanup；
- test determinism；
- 300/500/800 文件规模；
- public API 最小化；
- 没有 shim、fallback 或重复抽象。

两层 Review 必须由不同只读 reviewer 给出明确结论。P0/P1 清零后才能进入下一 Wave。

## 15. 完成报告

最终报告必须包含：

- 6A/6B/6C/6D 完成矩阵；
- 核心 API 和 breaking removal；
- PostgreSQL/Redis truth table；
- Side Effect Policy matrix；
- live failure injection 结果；
- 全量测试、架构测试、Ruff、compileall、diff check 数量；
- module size 前后对比；
- optional dependency/import boundary 结果；
- residual risk 与 deployment-owned responsibility；
- 所有提交 SHA 和远端 PR 状态。
