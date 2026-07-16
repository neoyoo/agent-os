# AgentOS Skill / Plan / Memory Projection Implementation Plan

> 状态：待用户批准；依赖 Phase 3 Contract Addendum

**Goal:** 把 Skill、Plan 和 Episodic/Semantic Memory 作为可组合 Extension 接入既有
Context Projection Port，并把 Planner 领域从 4859 行 `multi/planner.py` 迁入
`agentos.planning`。

**Architecture:** Extension 只提交类型化 SystemSection/ContextSlot Projection；Context
Renderer 不理解 Extension 业务。PlanStore、Skill Source、MemoryStore 保持真值；Projection
每次重新读取。Kernel/QueryLoop 不导入任何 Extension。

## Scope Contract

- **Phase / Specs:** Phase 3C；两份 2026-07-10 Spec、Phase 3 Contract Addendum、总体计划。
- **Acceptance:** Skill metadata/trust、Trusted Instruction、active-plan、memory-context、
  Planner package migration、Port/Adapter 方向和大文件拆分。
- **Allowed:** `capabilities/skills.py` 及新 skill modules、`memory/**`、`recall/**`、
  新 `planning/**`、新 `multi/planning_dispatch.py`、Task 3 明列的 `persistence/**` 与
  compression consumers、三个 context projection tests，以及下文列出的直接消费者。
- **Forbidden:** Provider Adapter、Artifact、QueryLoop、ProviderRequestBuilder、Builder、
  Context Renderer/Snapshot/Registry Core、非 Planner Root/Public API、module-size baseline 写入、
  Distributed/Transport 新语义。Task 3 对既有 Session Store/Recall Adapter 的机械归位、
  Memory/Recall/Persistence module export/stability 和 `builder.py` RecallRuntime 构造签名迁移，
  以及 Task 9 对 Planner Root/module export、Examples、API Inventory 的原子迁移是仅有例外；
  不得借归位改变 Redis/PostgreSQL 语义。
- **Dependencies:** Skill/Memory/Planning 可依赖 Context value types 和自身 Port；`planning`
  禁止导入 `multi`；`multi` 可实现 Planning dispatch Port；`recall` 禁止导入 `memory`，
  `memory` 只拥有 Episodic/Semantic 领域。
- **Completion:** 三类 Projection 可独立构造并通过协议测试；旧 `multi/planner.py` 删除；
  压缩片段 Recall 与 Session Store 职责从 `memory/` 原子迁出。
- **Deferral:** Builder/Registry 聚合、Run-Plan 持久绑定、Memory query 接线到 Phase 4；
  SQLite/Filesystem 与 immutable dispatch snapshot/template-version restart recovery 到
  Phase 5；PostgreSQL/Redis/分布式 Worker 归位到 Phase 6。
- **Verification:** 目标/模块/全量测试、compileall、ruff、import/drift/module-size/diff。

## Readiness Gate

- [ ] Phase 3 Contract Addendum 已批准；
- [ ] 当前相关基线 `240 passed`；
- [ ] `ContextProjectionProvider` 和 `TrustedSkillInstructionProvider` 签名未漂移；
- [ ] `ai-knowledge/wiki` 缺失已记录；历史 Memory/Planner Spec 只作实现参考。

## Target Package Responsibilities

```text
capabilities/
  skill_types.py       metadata、trust decision、load result
  skill_trust.py       SkillVerificationSubject / SkillTrustPolicy Port
  skill_sources.py     builtin/filesystem/chained I/O
  skill_runtime.py     activation、trusted items、available projection
  skill_projection.py  fixed XML projection
  skills.py            tool registration facade，<300 行

memory/
  records.py           episodic/semantic values、MemorySelectionContext
  access.py            MemoryAccessPolicy
  memory_store.py      MemoryStore Port / Level 1 in-memory
  in_memory.py         Level 1 episodic/semantic adapter
  runtime.py           selection、permission/expiry、projection
  projection.py        fixed XML projection

recall/
  types.py             SegmentRecallDocument、RecallCandidate、CompressedSegmentPackage
  index.py             RecallIndex Port
  store.py             SegmentHotStore / SegmentDurableStore narrow Ports
  segment_repository.py  compression segment 持久化、索引和原文读取
  in_memory_index.py   Level 1 RecallIndex adapter
  embeddings.py        recall embedding value/helpers
  qdrant_index.py      optional RecallIndex adapter
  runtime.py           recall_context command owner

persistence/
  session_store.py       Hot/Durable Session Store Ports + HotSessionState
  in_memory_session.py   existing in-memory session adapters
  redis_session.py       existing Redis hot-session adapter
  session_serializers.py existing session/segment codecs

planning/
  models.py            Plan/Step/Evidence value types
  errors.py            stable plan errors
  store.py             Plan Store/Claim Store Ports
  in_memory.py         local adapters
  decomposition.py     proposal、validation、governance
  dispatch.py          PlanStepDispatcher Port
  runtime.py           Plan mutation/state truth coordination
  scheduling.py        bounded scheduler/tick
  daemons.py           daemon lifecycle
  profiles.py          deployment/readiness declarations
  projection.py        AuthorizedPlanSource、active-plan XML
  tools.py             planner tool registration
  serializers.py       domain codec

multi/
  planning_dispatch.py PlanStepDispatcher -> AgentCoordinator adapter
```

Planner 迁移允许修改的现有直接消费者固定为：

```text
src/agentos/testing/contracts/plan_store.py
src/agentos/testing/contracts/plan_claim_store.py
src/agentos/multi/postgres_plan.py
src/agentos/multi/serializers.py
src/agentos/multi/__init__.py
src/agentos/__init__.py
src/agentos/examples/planner_patterns.py
src/agentos/examples/production_reference_web_agent.py
tests/multi/test_planner_claimed_scheduler_daemon.py
tests/multi/test_planner_projection.py
tests/multi/test_planner_runtime.py
tests/multi/test_planner_tools.py
tests/multi/test_planner_scheduler_governance_profile.py
tests/multi/test_plan_claim_store_contract.py
tests/multi/test_plan_store_contract.py
tests/multi/test_planner_scheduler_daemon.py
tests/multi/test_postgres_plan_claim_store.py
tests/multi/test_postgres_plan_store.py
tests/integration/test_distributed_planner_worker_flow.py
tests/architecture/test_public_api.py
tests/architecture/test_public_api_inventory.py
tests/architecture/test_extension_boundaries.py
docs/api-stability.md
docs/production-readiness.md
docs/public-api-inventory.json
docs/public-api-stability.json
```

Memory/Recall 原子迁移允许修改的现有消费者固定为：

```text
src/agentos/memory/__init__.py
src/agentos/memory/embeddings.py
src/agentos/memory/in_memory.py
src/agentos/memory/qdrant_index.py
src/agentos/memory/recall_index.py
src/agentos/memory/redis_store.py
src/agentos/memory/runtime.py
src/agentos/memory/serializers.py
src/agentos/memory/store.py
src/agentos/memory/types.py
src/agentos/recall/__init__.py
src/agentos/recall/runtime.py
src/agentos/persistence/__init__.py
src/agentos/persistence/postgres.py
src/agentos/compression/compressor.py
src/agentos/compression/llm_compressor.py
src/agentos/compression/runtime.py
src/agentos/builder.py
tests/memory/**
tests/compression/test_memory_sink.py
tests/compression/test_package_compressor.py
tests/recall/test_query_recall.py
tests/recall/test_runtime.py
tests/capabilities/test_tools.py
tests/observability/test_event_log.py
tests/runtime/test_query_loop.py
tests/runtime/test_session_recovery.py
tests/architecture/test_phase7_memory_boundaries.py
tests/architecture/test_public_api.py
tests/architecture/test_public_api_inventory.py
docs/README-OUTLINE.md
docs/readme-online.md
docs/api-stability.md
docs/public-api-inventory.json
docs/public-api-stability.json
```

目标：生产模块默认 `<500`；facade `<300`。测试按领域拆分，不保留 4000 行单测试文件。

---

### Task 0: Characterization 基线

**Files:** 新增 architecture tests；拆分前只读现有生产代码。

- [ ] 固化 Planner Store/Runtime/Tools/Scheduler/Claim/Daemon 当前行为，记录 `240 passed`。
- [ ] 记录但不提交当前架构缺口：`agentos.planning` 不存在、`multi/planner.py` 超 800、
  `skills.py` 超 500、旧 `MemoryRuntime` 混入 segment repository 职责。
- [ ] Task 0 只提交当前可通过的 characterization；每个架构 Red 必须在对应实现 Task 内
  写入并转 Green 后再提交。QueryLoop 不得导入 Extension 的现有边界测试保持 Green。
- [ ] 运行：

```powershell
python -m pytest tests/capabilities tests/memory tests/multi -q
python -m pytest tests/architecture/test_extension_boundaries.py -q
```

- [ ] 提交：`test: characterize extension domain boundaries`。

### Task 1: Skill 领域拆分与 Trust Contract

**Files:** `capabilities/skill_types.py`、`skill_trust.py`、`skill_sources.py`、`skills.py`、
相关 tests。

- [ ] Red 覆盖 metadata 不含 content/path、trust/verified 组合、filesystem 默认 untrusted、
  duplicate、resource traversal、异步 source lifecycle，以及
  `SkillTrustPolicy.verify(metadata, SkillVerificationSubject)`。
- [ ] 最小值：

```python
SkillTrust = Literal["trusted", "untrusted"]

@dataclass(frozen=True, slots=True)
class SkillMetadata:
    name: str
    description: str
    loadable: bool
    trust: SkillTrust
```

- [ ] Source 负责 I/O；Definition/Metadata/Trust 不和 Tool 注册混在同一模块。
- [ ] `SkillVerificationSubject` 包含 source_id、skill_name、source_revision、content_digest；
  `SkillTrustDecision` 只包含 verified、policy_id 和完整 subject。Skill 名称、revision、正文
  digest 任一变化或 Policy 撤销后旧 decision 失效，Decision 不得跨 Skill 复用。
- [ ] `skills.py` 收缩为 facade/tool registration，目标 `<300`。
- [ ] 提交：`refactor: split skill domain and sources`。

### Task 2: SkillRuntime、Trusted Instructions 与 Projection

**Files:** `skill_runtime.py`、`skill_projection.py`、`skills.py`、
`tests/context/test_skill_projection.py`、skill runtime/tool contract tests。

- [ ] Red：available-skills 只有 metadata；trusted+verified 加载后激活；Tool Result 是有界
  确认；untrusted body 仅 Tool Result；untrusted 无法进入 SystemEnvelope；停用后 items 为空；
  builtin 未验证拒绝；跨 Session 激活不可见；来源 revision 改变后激活失效。
- [ ] 激活键固定为 `(session_id, skill_name)`；`load/disable/items/projections` 显式接收
  `session_id`，Session 结束清除激活，不读取隐式进程全局 Session。
- [ ] `SkillRuntime.projections(session_id)` 返回最多一个 owner=`SkillRuntime` 的
  available-skills；`SkillRuntime.items(session_id)` 返回该 Session 的可信指令。
- [ ] 新增 `BoundSkillInstructionProvider(runtime, session_id).items()` 和
  `BoundSkillProjectionProvider(runtime, session_id).projections()`；二者构造时冻结 Session，
  以无参方法满足 Phase 2 既有 Port，不修改 Kernel 签名。
- [ ] `BoundSkillTools(runtime, session_id).registered_tools()` 在注册时捕获 Session，生成仍只接收
  `arguments` 的 load/disable handlers；禁止把 session_id 暴露为模型参数或使用全局当前 Session。
- [ ] 完整正文不进入 ContextSnapshot、StoredMessage 或默认 Trace。
- [ ] 提交：`feat: project trusted and available skills`。

### Task 3: 分离 Segment Repository 与 Memory 领域

**Files:** 上述固定 Memory/Recall cutover 清单；新增 `recall/{types,index,store,
segment_repository,in_memory_index,embeddings,qdrant_index}.py`、
`persistence/{session_store,in_memory_session,redis_session,session_serializers}.py`、
`memory/{records,access,memory_store,in_memory}.py`，并重建 `memory/runtime.py`。

- [ ] 把现有 `MemoryRuntime` 的 compressed segment 持久化、索引和原文读取职责迁入
  `recall/segment_repository.py::SegmentRepository`；把对应 types/index/adapters 迁入 `recall/`，
  把 Hot/Durable Session Store types/adapters 迁入 `persistence/`。迁移后 `recall` 源码对
  `agentos.memory` 零 import。
- [ ] `RecallRuntime` 继续唯一拥有 `recall_context` 参数校验、事件、消息注入和命令编排，
  通过注入的 `SegmentRepository` 完成 handle/query 数据访问。
- [ ] 同一原子提交迁移全部 `RecallRuntime(...)` 构造消费者和公开签名测试；不保留
  `memory_runtime` 参数 alias。`builder.py` 只允许做该构造签名迁移，不接线新 Memory Projection。
- [ ] 删除旧 `memory` recall/session exports 和源文件；全部 compression、persistence、tests、
  optional adapter imports 改到新 Owner，不保留 re-export、wrapper 或重复类型；同步更新
  `docs/README-OUTLINE.md`、`docs/readme-online.md`、`docs/api-stability.md`、Public API
  Inventory/Stability 和对应 architecture tests，不得把 module export 失败留到 Phase 4。
- [ ] 将旧 `tests/memory/**` 按职责拆到 `tests/recall/**`、`tests/persistence/**`，只把新的
  Episodic/Semantic contract/runtime tests 保留在 `tests/memory/**`。
- [ ] 定义：

```python
MemoryKind = Literal["episodic", "semantic"]
SemanticCategory = Literal["preference", "reference", "fact", "procedure"]
EpisodicCategory = Literal["interaction", "outcome"]
```

- [ ] MemoryRecord 校验 kind/category、session scope、expiry、content/handle；Artifact 仅引用 handle。
- [ ] 定义 `MemorySelectionContext(session_id, principal_id, permissions, query, now)` 和
  `MemoryAccessPolicy`；MemoryStore Protocol 提供 put/get/search，`search` 至少按 context 的
  Session 隔离候选；Level 1 InMemory 实现确定性排序。
- [ ] 提交 1：`refactor: move recall and session storage out of memory`。
- [ ] 提交 2：`feat: define episodic semantic memory store`。

### Task 4: MemoryRuntime 与 memory-context Projection

**Files:** `memory/runtime.py`、`memory/projection.py`、
`tests/context/test_memory_projection.py`、memory runtime tests。

- [ ] Red：跨 Session、无权限、过期过滤，Top-K、score tie-break、重复 handle、XML Escape、
  `instructional="false"`、score/reason 不可见、无结果不输出 Slot。
- [ ] Runtime 每次接收完整 `MemorySelectionContext` 并重新 search Store，不缓存 XML；Store
  返回后再次校验 record session、`MemoryAccessPolicy` 和显式 `now`。
- [ ] `MemoryRuntime.projections(context)` 返回 owner=`MemoryRuntime`；完整记录裁剪形成
  ProjectionVariant。新增 `BoundMemoryProjectionProvider(runtime, context).projections()`，
  构造时冻结请求上下文，以无参方法满足 Phase 2 既有 `ContextProjectionProvider`。
- [ ] 本阶段不实现 Memory 自动提取或写回模型输出。
- [ ] 提交：`feat: project episodic semantic memory`。

### Task 5: Planner 基础类型与 Store 迁入 planning

**Files:** `planning/{models,errors,store,in_memory}.py`、迁移后的 tests/contracts/imports。

- [ ] 按现有行为迁移 PlanStatus、PlanStep、PlanState、Evidence、Store/CAS/Claim Protocol、
  InMemory Store；不在同提交改变状态语义。
- [ ] `planning` 不导入 `multi`、PostgreSQL、Redis、Workspace backend；需要的 Workspace
  value type可通过稳定 Port/value import。
- [ ] 每个迁移提交先运行对应旧测试，再改 import 后运行同一行为测试。
- [ ] 提交：`refactor: move plan domain and stores to planning`。

### Task 6: Planner Runtime、Decomposition 与 Dispatch Port

**Files:** `planning/{decomposition,dispatch,runtime}.py`、`multi/planning_dispatch.py`、
`tests/multi/test_planning_dispatch.py` 和对应 Planner tests。

- [ ] 提取 proposal validation/governance 为纯领域逻辑；PlannerRuntime 只协调 PlanStore。
- [ ] 定义 `PlanStepDispatcher` Protocol；`multi` 实现 adapter，`planning` 不引用 TaskHandle
  或具体 coordinator。
- [ ] 保持 CAS、claim guarded mutation、dependency validation 和 authorization 现有行为。
- [ ] 提交 1：`refactor: isolate planner decomposition policy`。
- [ ] 提交 2：`refactor: invert planner dispatch dependency`。
- [ ] 提交 3：`refactor: move planner runtime`。

### Task 7: Scheduler、Daemon、Profile 与 Tools 拆分

**Files:** `planning/{scheduling,daemons,profiles,tools}.py`、对应拆分 tests。

- [ ] Scheduler 只做 bounded tick/claim/retry；Daemon 只做 start/stop/drain；Profile 只做声明
  和 readiness；Tools 只做授权、参数解析和 Runtime 调用。
- [ ] 禁止通过继承共享可变控制流；线程均有 stop/join 语义。
- [ ] 把 4062/1273 行测试按 runtime/store/scheduler/daemon/tools 拆分。
- [ ] 提交按模块分开，不做单个 4000 行机械移动提交。

### Task 8: Active Plan Projection

**Files:** `planning/projection.py`、`planning/errors.py`、
`tests/context/test_plan_projection.py`。

- [ ] Red：显式 `plan_id + owner_agent_id` 每次通过 `AuthorizedPlanSource` 读 Store；不存在与
  Owner 不匹配同错；状态映射覆盖全部合法/不一致组合；终态不投影；只输出 goal、handle、
  status、instruction；task/owner/path/claim/lease/retry/evidence URI 不可见。
- [ ] `running` 且零 Step 或仅 completed/cancelled 时稳定抛出
  `PlanProjectionError("invalid active plan state")`，不输出半个 Slot。
- [ ] `BoundPlanProjectionProvider(source, plan_id, owner_agent_id).projections()` 构造时冻结
  Plan Scope，内部只调用 AuthorizedPlanSource，以无参方法满足 Phase 2 既有 Port。
- [ ] completed steps 优先形成可裁剪 Variant；goal 和未完成 steps 最后保留。
- [ ] 模型自由文本不能调用 Projection 修改 PlanStore。
- [ ] 提交：`feat: project active plan context`。

### Task 9: 删除 multi/planner.py 与迁移消费者

**Files:** 上述固定消费者清单、Planner Public API 文档/测试、删除 `multi/planner.py`；
不得扩大到其他 `multi/**` 或非 Planner Public API。

- [ ] 使用 `rg` 生成全部 import 清单，逐消费者迁移到 `agentos.planning`。
- [ ] PostgreSQL 实现暂时作为 Planning Store Adapter 保留现有模块位置；Phase 6 再移动
  基础设施包，不在本阶段重写 SQL。
- [ ] `multi.__init__` 同步删除旧 Planner 导出；不保留 re-export、wrapper、subclass 或
  双序列化入口。
- [ ] 同一提交把 Root stable exports 改为从 `agentos.planning` 或现有 PostgreSQL Adapter
  直接导入，更新 Planner examples、integration tests、`agentos.planning` module exports、
  Public API Inventory/Stability；不得把失败留到 Phase 4。
- [ ] 删除旧文件后运行全部 planner/multi tests。
- [ ] 提交：`refactor: remove multi planner monolith`。

### Task 10: Phase 3C 收口

- [ ] 运行：

```powershell
python -m pytest tests/context/test_skill_projection.py tests/context/test_plan_projection.py tests/context/test_memory_projection.py -q
python -m pytest tests/capabilities tests/compression tests/recall tests/persistence tests/memory tests/planning tests/multi -q
python -m pytest -q
python -m compileall -q src tests
python -m ruff check src tests
python -m pytest tests/architecture/test_module_size_baseline.py tests/architecture/test_public_api.py tests/architecture/test_public_api_inventory.py -q
rg -n "agentos\.multi\.planner|from agentos\.multi import .*Planner" src tests
rg -n "agentos\.planning|PlannerRuntime|MemoryRuntime|SkillRuntime" src/agentos/runtime src/agentos/context
rg -n "agentos\.memory" src/agentos/recall src/agentos/compression src/agentos/persistence tests/recall tests/compression tests/persistence docs/README-OUTLINE.md docs/readme-online.md
rg --files src/agentos/memory | rg "[\\/](embeddings|qdrant_index|recall_index|redis_store|serializers|store|types)\.py$"
rg -n "MemoryRuntime.*(HotSessionStore|DurableSessionStore|RecallIndex)|src/agentos/memory/(runtime|store|recall_index|types)\.py" docs/README-OUTLINE.md docs/readme-online.md
git diff --check
```

`docs/governance/agentos-module-size-baseline.json` 由 Integration/Quality Owner 在三路合并后
串行更新；本 Workstream 只运行只读规模门禁。Task 3 的 Memory/Recall/Persistence module
breaking cutover 和 Task 9 的 Planner breaking cutover 必须在各自原子提交同步更新 Public API
Inventory/Stability；除此以外的新增 Extension Root export 延期 Phase 4。

Expected drift：旧 planner import 零命中；Recall/Compression/Persistence 和活动 README 对旧
`agentos.memory` import 零命中；旧 memory recall/session 源文件零命中；Kernel 对
Planning/Memory/Skill Runtime import 零命中；活动 README 对旧 MemoryRuntime recall 职责和
旧源码链接零命中。

- [ ] Spec Review：trust、truth source、Owner、Authority、state mapping、dependency direction。
- [ ] Quality Review：超大文件已删除、模块职责、线程生命周期、类型和 tests。
- [ ] 提交：`docs: close phase3c extension projection`。

## Rollback And Integration

Planner 迁移按类型/Store、dispatch、runtime、scheduler、tools 分提交，任何阶段都必须保持
同一 class identity 和 Green tests。禁止复制一份 `planning` 类型后继续维护
`multi.planner` 真值。Phase 4 集成时只消费三个 Projection Provider，不把 Extension
类型硬编码进 Builder。
