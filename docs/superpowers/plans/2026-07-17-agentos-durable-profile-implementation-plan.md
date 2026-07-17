# AgentOS Phase 5 Durable Profile 实施计划

> **For development agents:** 严格按 `AGENTS.md`、工程规范、Phase 5 Contract 和
> 本计划执行。每个行为先观察 Red，再做最小实现；共享 Kernel 文件只由主会话修改。

**Goal:** 完成 M4 Durable Agent，使 WAITING Run 在进程对象销毁后可由 SQLite/
Filesystem 恢复，并通过幂等 Command 使用同一 `run_id` 继续执行。

**Architecture:** Runtime domain 定义 Command、Checkpoint 和 Store Port；SQLite
Adapter 原子维护 Run/Command/Checkpoint；Profile 水合标准 Context/Message/Run
组件；QueryLoop 只接受 Store 已批准的 `AcceptedContinuationInput`。Artifact、Plan、
Memory/Skill Adapter 使用独立文件 Owner 并行实现。

**Tech Stack:** Python 3.11+、stdlib `sqlite3`/`pathlib`/`json`、pytest、ruff。

## 1. Bootstrap 与已知边界

- 当前分支：`feature/agentos-sdk-phase5-durable-profile`；
- Phase 4 基线：`10c9746`；
- 用户未提交修改：`AGENTS.md`、
  `docs/governance/agentos-engineering-standard.md`，禁止暂存、覆盖或回滚；
- `ai-knowledge/wiki` 在当前仓库不存在，无法读取；以根 `AGENTS.md`、工程规范、
  总架构、Context Protocol、单一异步 Loop Spec 和本计划为权威；
- OCR 永久排除；
- Legacy `persistence/base.py::SessionSnapshot`、`persistence/sqlite.py` 和
  `persistence/filesystem.py` 不接入 Phase 5，不双写；
- Redis/PostgreSQL、Worker、Transport、Claim/Lease 留在 Phase 6。

## 2. Scope Contract

| 项目 | 合同 |
|---|---|
| Phase/Spec | Phase 5 / M4；Phase 5 Durable Profile Contract |
| Acceptance | SQLite/Filesystem persistence、restart、command idempotency、wait/resume/cancel/timer、Artifact reload、Plan/Memory/Skill composition |
| File Ownership | 主会话：`runtime/`、`builder.py`、`_builder_*`、profile、shared tests/docs；支线：互斥的 Artifact、Plan、Memory/Skill adapter 文件 |
| Forbidden | `AGENTS.md`、工程规范、Provider Adapter、Context Protocol schema/slot order、QueryLoop Provider/Tool control flow、Redis/Postgres 实现 |
| Dependency | Kernel -> typed Ports；Adapter -> domain；禁止 Kernel -> sqlite/filesystem/distributed |
| Completion | 同 run_id restart continuation 和全部 Phase 5 P0 fault tests |
| Deferral | Distributed delivery/lease/fencing -> Phase 6；summary/vector retrieval -> 独立未来 Spec；OCR 永久不做 |
| Verification | targeted pytest、full pytest、compileall、ruff、module-size、public API、diff、drift/import scans |

## 3. 文件规模审查

实施前结论：

- `runtime/query_loop.py` 为 490 行，接近 500 门禁；本阶段不得向其中加入 Durable
  command、SQLite 或 hydration 控制流；
- `runtime/agent.py` 145 行，可只增加公共输入分派；
- `runtime/run_runtime.py` 162 行，可增加 version-aware Port，但 Command/Checkpoint
  必须放入独立模块；
- `builder.py` 接近 300 行职责门禁；Durable 组装提取到 `_builder_durable.py`，
  Builder 只保留薄入口；
- 新增 Adapter 单文件目标小于 300 行；接近门禁时按 schema/serialization/store
  职责拆分，不创建通用 `utils.py`。

## 4. Wave 0：规范与 Red Contract

### Task 0：冻结文档

文件：

- `docs/superpowers/specs/2026-07-17-agentos-phase5-durable-profile-contract.md`
- `docs/superpowers/plans/2026-07-17-agentos-durable-profile-implementation-plan.md`
- `docs/superpowers/specs/2026-07-10-agentos-next-generation-sdk-architecture-design.md`

验收：OCR 永久排除；Command、Timer、Checkpoint 和 recovery policy 无未决语义。

### Task 1：先写 P0 Red tests

创建：

- `tests/durable/test_command_contract.py`
- `tests/durable/test_sqlite_run_store.py`
- `tests/durable/test_checkpoint_atomicity.py`
- `tests/durable/test_restart_resume.py`
- `tests/durable/test_timer_and_cancel.py`
- `tests/durable/test_checkpoint_safety.py`
- `tests/architecture/test_durable_import_boundaries.py`

Red 断言：

1. `WaitReason(timer/retry_backoff)` 缺少 `not_before` 被拒绝；
2. exact duplicate 返回 duplicate receipt，provider 调用次数不增加；
3. conflicting duplicate、terminal/non-WAITING resume 被拒绝；
4. checkpoint 注入失败后 Run 仍 RUNNING 且无 checkpoint；
5. wait 后销毁 Agent/Profile 对象，重建后同 run_id resume；
6. RUNNING restart 只标 FAILED，不调用 Provider/Tool；
7. checkpoint 中不存在 ProviderRequest、ContextMount、temporary ref、Base64/blob；
8. Durable import graph 不加载 Redis/PostgreSQL module。

运行并记录预期 ImportError/AttributeError/行为失败：

```powershell
python -m pytest tests/durable tests/architecture/test_durable_import_boundaries.py -q
```

### Task 2：Domain types 和 Port

创建/修改：

- `src/agentos/runtime/durable_commands.py`
- `src/agentos/runtime/checkpoint.py`
- `src/agentos/runtime/durable_runtime.py`
- `src/agentos/runtime/run.py`
- `src/agentos/runtime/run_state.py`
- `src/agentos/runtime/run_runtime.py`
- `src/agentos/_waiting.py`
- `src/agentos/runtime/errors.py`

实现：

- frozen command/receipt/accepted continuation/checkpoint value types；
- WaitReason UTC `not_before` validation；
- Run aggregate version；
- Store Port 和 command acceptance policy；
- 无 SQLite import 的 DurableCommandRuntime/CheckpointSource 边界。

目标测试：

```powershell
python -m pytest tests/durable/test_command_contract.py tests/runtime/test_run_state.py tests/runtime/test_run_contracts.py -q
```

提交边界：`feat: define durable command and checkpoint contracts`。

## 5. Wave 1：主线 SQLite Run/Checkpoint

### Task 3：SQLite schema 与 serialization

创建：

- `src/agentos/durable/__init__.py`
- `src/agentos/durable/schema.py`
- `src/agentos/durable/serialization.py`
- `src/agentos/durable/sqlite_store.py`

表至少包含：

- `durable_sessions`
- `durable_runs`
- `durable_commands`
- `durable_checkpoints`
- `durable_messages`
- `durable_active_refs`
- `durable_context_states`

规则：

- schema version 显式；
- foreign key 开启；
- transaction 由 Store Owner 控制；
- JSON `ensure_ascii=False, sort_keys=True, separators=(",", ":")`；
- datetime 统一 UTC；
- 反序列化严格验证 domain type；
- 数据损坏映射为 `CheckpointCorruptedError`，不静默创建空状态。

### Task 4：Atomic wait/terminal checkpoint

实现：

- SQLite RunStore create/get/versioned transition；
- atomic `commit_waiting(snapshot, reason, expected_version)`；
- terminal checkpoint；
- command acceptance/dedup/cancel/due validation；
- abandoned RUNNING fail-closed recovery；
- hydrate latest Session state。

故障注入：transaction 中每个持久步骤可通过测试 hook 抛错，逐项验证 rollback。

目标测试：

```powershell
python -m pytest tests/durable/test_sqlite_run_store.py tests/durable/test_checkpoint_atomicity.py tests/durable/test_timer_and_cancel.py -q
```

提交边界：`feat: add atomic SQLite durable state store`。

## 6. Wave 1 并行支线

共享 Domain/Port 提交合入后才启动。每个 subagent 只能修改声明文件，不使用
`git add .`，必须回报测试和文件规模。

### Workstream A：SQLite + Filesystem Artifact

允许文件：

- `src/agentos/artifacts/sqlite_filesystem.py`
- `src/agentos/artifacts/__init__.py`
- `tests/artifacts/test_sqlite_filesystem_store.py`

禁止：runtime、builder、context、provider、shared durable schema。

验收：现有 ArtifactStore contract、restart read、session scope、missing blob、put
rollback、safe delete、无路径泄漏。

命令：

```powershell
python -m pytest tests/artifacts/test_store_contract.py tests/artifacts/test_sqlite_filesystem_store.py -q
```

### Workstream B：SQLite PlanStore

允许文件：

- `src/agentos/planning/sqlite.py`
- `src/agentos/planning/__init__.py`
- `tests/planning/test_sqlite_store.py`

禁止：PlannerRuntime、claims、postgres、runtime、builder。

验收：create/save/get/list、revision/CAS、restart、deterministic serializer、无 Claim
或 Lease 语义。

命令：

```powershell
python -m pytest tests/planning/test_sqlite_store.py tests/planning/test_serializers.py -q
```

### Workstream C：SQLite Memory 与 Skill Activation

允许文件：

- `src/agentos/memory/sqlite.py`
- `src/agentos/memory/__init__.py`
- `src/agentos/capabilities/skill_activation.py`
- `src/agentos/capabilities/skill_runtime.py`
- `tests/memory/test_sqlite_store.py`
- `tests/context/test_durable_skill_activation.py`

禁止：QueryLoop、Agent、Builder、Context Renderer、distributed adapters。

验收：Memory session scope/restart/deterministic search；Skill activation 只保存验证
引用，重启重新加载和复验，revision/digest/policy 变化 fail closed。

命令：

```powershell
python -m pytest tests/memory/test_sqlite_store.py tests/context/test_durable_skill_activation.py tests/context/test_skill_projection.py -q
```

## 7. Wave 2：Durable Profile 集成

### Task 5：Hydration 与 Builder 组装

创建/修改：

- `src/agentos/_builder_durable.py`
- `src/agentos/builder.py`
- `src/agentos/durable/profile.py`
- `src/agentos/runtime/profile.py`
- `src/agentos/runtime/profile_contracts.py`
- `src/agentos/runtime/__init__.py`
- `src/agentos/durable/__init__.py`

实现：

- `DurableRuntimeProfile` context manager/close；
- 同 Session 存活 QueryLoop 的弱所有权复用，不强缓存 Agent facade；
- checkpoint source 存活期间拒绝覆盖，并由 QueryLoop/AgentStream 生命周期保持；
- 活跃 execution 时 `close()` fail-fast 且不关闭 Store，Stream 关闭后可重试；
- QueryLoop 在 Run prepare 前原子 reservation execution lease，消除 Profile close 与
  新 Run 提交之间的 prepare 窗口；
- Profile 对全部存活 QueryLoop 使用两阶段 close reservation，任一忙碌时整体回滚；
- 空 Session 初始化和 checkpoint hydration；
- durable Run/Artifact Store 装配；
- AgentBuilder 复用同一 provider/tool/context assembly；
- Local build path 不变；
- profile close 后确定性错误。

### Task 6：同 run_id continuation 接线

修改：

- `src/agentos/runtime/agent.py`
- `src/agentos/runtime/run_driver.py`
- `src/agentos/runtime/turn_lifecycle.py`
- `src/agentos/runtime/continuation.py`

尽量不修改 `query_loop.py`。若类型检查必须修改，只允许把已批准的
`AcceptedContinuationInput` 加入输入校验，不增加 Durable control flow。

实现路径：

```text
Agent receives DurableRunCommand
  -> DurableCommandRuntime.accept
  -> duplicate/cancel: receipt
  -> duplicate + authoritative QUEUED 待执行 continuation: recover existing input
  -> accepted continuation
  -> RunDriver reuses queued run_id
  -> new continuation Turn
  -> command payload temporary context-data projection
  -> normal Provider/Tool loop
```

目标测试：

```powershell
python -m pytest tests/durable/test_restart_resume.py tests/runtime/test_waiting_query_loop.py tests/runtime/test_agent_api.py -q
```

提交边界：`feat: integrate durable profile restart continuation`。

### Task 7：Plan/Memory/Skill/HITL/Artifact composition E2E

创建：

- `tests/durable/test_profile_composition.py`
- `tests/durable/test_artifact_restart.py`
- `tests/examples/test_durable_agent.py`
- `src/agentos/examples/durable_agent.py`

验收：

- human wait + hitl answer；
- timer wait + due wakeup；
- persisted plan and memory projection after restart；
- trusted Skill activation reverified；
- old Artifact handle reloads bytes and projects canonical content part；
- no Provider transcript dependency。
- Agent/Stream 释放后 Profile 不保留无界 Session cache；
- 未消费 Stream 阻止 Profile close，关闭后 Profile 可正常关闭。

## 8. Package 与 Public API

修改：

- `pyproject.toml`
- `docs/api-stability.md`
- `docs/public-api-stability.json`
- `docs/public-api-inventory.json`
- `CHANGELOG.md`

新增稳定模块级 API：

- `agentos.runtime.DurableRunCommand`
- `agentos.runtime.DurableCommandReceipt`
- `agentos.durable.DurableRuntimeProfile`
- `agentos.durable.SQLiteDurableStore`
- `agentos.artifacts.SqliteFilesystemArtifactStore`
- `agentos.planning.SQLitePlanStore`
- `agentos.memory.SQLiteMemoryStore`

Root facade 不聚合 Adapter。

## 8.1 Final Review Remediation

最终双层 Review 发现以下阻断项，必须继续按 TDD 收口，不能作为后续阶段延期：

1. Durable command 必须在 `accept()` 落库前取得 QueryLoop execution reservation；
   `ExecutionLease` 只允许同一 asyncio Task 内部幂等重入，使用线程 Event/Barrier 覆盖
   Profile close 竞态，不使用 `sleep`。
2. `SqliteFilesystemArtifactStore` 增加幂等 close/closed 语义；Profile 在 close reservation
   保护期间关闭它，旧 Agent 的 upload/read/list 必须抛 `DurableStoreClosedError`。
3. duplicate command row 先严格验证全部字段和 payload JSON；损坏统一映射为
   `CheckpointCorruptedError`，只有完整合法的不同记录才是 `CommandConflictError`。
4. `SQLiteDurableStore` 稳定 public methods 及 `SQLiteMemoryStore.put/get/search` 补齐
   简洁中文 docstring，并刷新模块规模基线。
5. `agentos.artifacts` 与 `agentos.capabilities` 对稳定 Durable Adapter 使用 lazy facade
   export；仅导入 Kernel 不得传递加载 SQLite/Filesystem Adapter。
6. Artifact 写盘 `OSError` 映射为无路径领域错误；delete/delete_session 使用 staging，
   SQLite 提交失败恢复全部 blob，重启按 metadata 真值收敛遗留 staging。
7. Contract 明确 authoritative QUEUED 待执行 continuation 是 exact duplicate receipt 的
   唯一 execution recovery 例外，command 本身仍只应用一次。
8. 清除已被上位架构取代的历史 OCR tool 路线描述；OCR 仍永久不属于 SDK。
9. Artifact reconciliation 在扫描 staging 前取得 SQLite `BEGIN IMMEDIATE`，与跨 Store
   put/delete 共用同一写保留，消除旧 metadata 快照恢复 orphan 的竞态。
10. metadata 删除提交后，blob cleanup 失败按已提交删除返回成功并保留 staging，由重启
    reconciliation 收敛；合法 final 不被旧 staging 覆盖，非普通文件目标 fail closed。
11. metadata、正式 blob 或同 ID staging 任一存在都视为 ID 碰撞；reconciliation 清理前
    禁止复用 Artifact ID，避免旧 bytes 与新 metadata 错误绑定。

目标测试：

```powershell
python -m pytest tests/durable/test_profile_composition.py tests/durable/test_command_store.py tests/artifacts/test_sqlite_filesystem_store.py tests/memory/test_sqlite_store.py -q
```

## 9. Verification Gates

每个工作包：

```powershell
python -m pytest <target-tests> -q
python -m compileall -q src tests
python -m ruff check src tests
git diff --check
```

Phase 集成：

```powershell
python -m pytest -q
python -m compileall -q src tests
python -m ruff check src tests
python -m pytest tests/architecture/test_module_size_baseline.py -q
python scripts/generate_public_api_inventory.py --policy docs/public-api-stability.json --output docs/public-api-inventory.json
python -m pytest tests/architecture/test_public_api.py tests/architecture/test_public_api_inventory.py -q
git diff --check
```

Drift/import checks：

```powershell
rg -n "OCR|ocr" src tests docs/superpowers/specs docs/superpowers/plans
rg -n "ProviderRequest|ContextMount|temporary|base64|provider_file|signed_url" src/agentos/durable tests/durable
rg -n "psycopg|asyncpg|import redis|from redis|persistence.postgres|persistence.redis" src/agentos/durable src/agentos/artifacts/sqlite_filesystem.py src/agentos/planning/sqlite.py src/agentos/memory/sqlite.py
```

OCR 搜索只允许出现在明确的永久排除声明中。

## 10. 双层 Review

### Spec Compliance Review

独立检查：

- 同一 QueryLoop；
- Store command idempotency，不在 Agent/Loop 重复实现；
- WAITING/checkpoint transaction；
- Authority/Persistence/Visibility；
- Provider transcript 和 Artifact bytes 排除；
- Redis/PostgreSQL/Distributed 无泄漏；
- 所有 deferral 已登记。

### Code Quality Review

另一独立 reviewer 检查：

- domain/port/adapter 依赖方向；
- SQLite transaction、connection lifecycle 和 thread safety；
- serialization validation 和 corruption handling；
- filesystem failure cleanup/path safety；
- deterministic tests，不使用 sleep；
- 300/500/800 行职责门禁；
- Public API 类型与中文 docstring。

### Review 结果

- Spec Compliance Review：通过。Command ownership recovery、Kernel/Adapter import
  边界和 OCR 永久排除均无 P0-P3 阻断。
- Code Quality Review：通过。execution reservation、Profile lifecycle、SQLite corruption
  mapping、Artifact staging/reconciliation 与 public docstring 修复后无 P0-P3 阻断。

## 11. 完成报告矩阵

最终报告必须逐项填写：

| 设计要求 | 实现文件 | 测试/命令 | 状态 |
|---|---|---|---|
| Restart + same run_id | `_builder_durable.py`、`durable/profile.py`、`durable/sqlite_store.py`、`runtime/run_driver.py` | `tests/durable/test_restart_resume.py` | 已完成 |
| Command idempotency/cancel/timer | `runtime/durable_commands.py`、`runtime/durable_runtime.py`、`durable/sqlite_commands.py` | command contract/store/profile tests | 已完成 |
| Atomic checkpoint | `runtime/checkpoint.py`、`durable/sqlite_store.py`、`durable/sqlite_checkpoint.py` | `tests/durable/test_checkpoint_atomicity.py` | 已完成 |
| Artifact reload | `artifacts/sqlite_filesystem.py`、`artifacts/sqlite_blobs.py` | Artifact store/restart tests | 已完成 |
| Plan/Memory/Skill/HITL | `planning/sqlite.py`、`memory/sqlite.py`、`capabilities/skill_activation*.py` | profile composition、Plan/Memory/Skill tests | 已完成 |
| Profile/Agent/Stream lifecycle | `durable/profile.py`、`runtime/_execution_lease.py`、`runtime/agent.py` | profile composition/lifecycle tests | 已完成 |
| Optional dependency/import boundary | lazy facades、`capabilities/skill_activation_store.py` | architecture import/API tests | 已完成 |
| Full gates/reviews | Section 9/10 对应实现与测试 | `2834 passed, 12 skipped`；Ruff、compileall、diff、drift scans | 已完成 |

### 文件规模审查

- `runtime/query_loop.py`：498 行；
- `durable/sqlite_store.py`：332 行；
- `artifacts/sqlite_filesystem.py`：383 行；
- `artifacts/sqlite_blobs.py`：228 行；
- `capabilities/skill_activation_store.py`：44 行。

全部低于 500 行门禁。Durable Store、Artifact metadata/blob orchestration 和 Skill
Activation Port/Adapter 已按职责拆分，没有向 QueryLoop 增加 Durable 或 Adapter 控制流。

任一设计项未完成时，只能报告 Phase 5“部分完成”。
