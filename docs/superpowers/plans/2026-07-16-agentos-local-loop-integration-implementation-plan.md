# AgentOS Phase 4 Local Loop Integration Implementation Plan

**Spec:** `docs/superpowers/specs/2026-07-16-agentos-phase4-local-loop-contract.md`
**Branch:** `feature/agentos-sdk-phase4-local-loop`
**Method:** Spec-first、TDD、主线单点集成、无旧 API 兼容层。

## 1. 完成标准

只有同时满足以下条件才可报告 Phase 4 完成：

1. M3 Contract Matrix 全部有代码和测试证据；
2. `agentos.attachments` 与其运行时引用零命中；
3. Root API 精确为五个名称，Inventory 由脚本生成；
4. 两份 Level 1 example 只使用 Builder/Public API；
5. target tests、全量 pytest、compileall、ruff、diff-check 全绿；
6. Spec Review 和 Code Quality Review 分别通过；
7. 工作区中的用户修改 `AGENTS.md`、工程规范和 `uv.lock` 未被覆盖或提交。

## 2. Owner 与并行规则

主会话单点拥有共享核心：

- Contract/Plan 文档；
- Builder、Session 绑定和 Projection Registry；
- ProviderRequestBuilder、TurnLifecycle、QueryLoop 集成；
- Artifact 原子切换和旧包最终删除；
- Root API、Inventory、最终集成与提交。

可并行支线只修改分配文件：

- Artifact Tool serializer/schema/router contract；
- 纯 Run 状态值类型和 InMemory Runtime；
- packaging dependency/import 门禁；
- Builder API 冻结后的 Level 1 examples；
- 最终只读 Spec Review 和 Code Quality Review。

任何支线不得修改 Builder、ProviderRequestBuilder、QueryLoop、Root API Inventory 或
其他支线文件。共享冲突由主会话处理。

## 3. 任务

### P4-0 Contract、Characterization 与 Drift Gate

**Owner:** 主会话
**Files:** 两份 Phase 4 文档、只新增 contract tests。
**Red evidence:** 旧 Builder、Attachment、Root API 和 Continuation 断言必须按新
Contract 失败，不能用当前旧测试全绿冒充完成。

- [x] 冻结七项 Scope Contract、非目标和 API 精确形状。
- [x] 记录 Scheduler re-baseline、Artifact 扩展修改面和 Phase 5 边界。
- [x] 建立 Phase 4 contract matrix 测试入口。
- [x] 运行旧基线并保存失败原因，不改生产代码使 Red 变绿。

验证：

```powershell
python -m pytest tests/architecture/test_root_facade_contract.py tests/artifacts tests/runtime -q
```

### P4-1 ContextProjectionRegistry

**Owner:** 主会话
**Create:** `src/agentos/context/projection_registry.py` 及独立测试。
**Modify:** `context/__init__.py`、ProviderRequestBuilder contract tests。
**Do not modify:** `context/registry.py` Renderer/Slot Owner 逻辑。

- [x] 先写空 Registry、稳定 provider 顺序、duplicate slot fail-closed 的 Red 测试。
- [x] 实现只聚合无参 `projections()` provider 的最小 Registry。
- [x] 每次调用重新读取 provider，不缓存 Projection。
- [x] Builder 后续只注册 provider，不识别 Artifact/Skill/Plan/Memory 类型。

验证：

```powershell
python -m pytest tests/context/test_projection_registry.py tests/context -q
```

### P4-2 Artifact Tools

**Owner:** 支线 A
**Modify:** `artifacts/tools.py`、Artifact tool tests、Router 的专属 Artifact adapter
文件；必要时新增小型 adapter，不扩张 Router 主文件职责。

- [x] 先写 canonical `list_attachments` JSON Red 测试。
- [x] 冻结两个 Tool schema、`EXCLUSIVE` policy 和唯一 handler owner。
- [x] 从 `context_protocol.py` 删除旧 schema/handler 的最终操作由主线执行。
- [x] Tool Result 不泄漏 bytes/path/base64。

验证：

```powershell
python -m pytest tests/artifacts/test_tools.py tests/capabilities -q
```

### P4-3 Artifact 输入与消息真值原子切换

**Owner:** 主会话
**Modify:** `runtime/run.py`、`messages/runtime.py`、`messages/store.py`、
`runtime/turn_lifecycle.py`、对应 tests。

- [x] `UserTurnInput.attachments` Red 测试改为 `artifact_handles`。
- [x] Message append 支持不可变 `ArtifactRef`，并覆盖 Store/Read Model round-trip。
- [x] Turn 准备按当前 Session 解析 handle、写 refs、创建 user upload Mount。
- [x] 不把占位文本或 bytes 写进业务 user message。
- [x] 无 ArtifactRuntime 时传 handles 必须 fail-closed。

验证：

```powershell
python -m pytest tests/messages tests/artifacts/test_message_refs.py tests/runtime/test_turn_lifecycle.py -q
```

### P4-4 Builder、Session、Request 与 Mount 接线

**Owner:** 主会话
**Modify:** `builder.py`、`_builder_*.py`、`provider_request_builder.py`、
`runtime/agent.py`、`runtime/session.py`、对应 tests。

- [x] `build(session_id=...)` 和默认稳定 Session ID Red 测试。
- [x] Builder 创建共享 SessionState、ContextRuntime、ArtifactRuntime、Run Runtime。
- [x] `Agent.artifacts` 替换 `Agent.attachments`。
- [x] ProviderRequestBuilder 依赖 Projection Registry 和 Mount provider。
- [x] 固化 ContextSnapshot -> transcript -> continuation -> mounts 顺序。
- [x] user upload 与 tool result mount 每 attempt 重投影。
- [x] complete/fail/cancel/wait 统一清 Mount，不删 Artifact。

验证：

```powershell
python -m pytest tests/runtime/test_agent_builder.py tests/runtime/test_provider_request_builder.py tests/artifacts -q
```

### P4-5 Run/Continuation Kernel

**Owner:** 支线 B 实现纯类型与 Store；主会话负责接线。
**Create:** 小型 `runtime/run_state.py`、`runtime/run_runtime.py`、
`runtime/continuation.py`，不得把职责堆进 `query_loop.py`。
**Modify:** Notice providers、ProviderInputItem contract 和对应 adapters/tests。

- [x] 先写状态转换、所有非终态取消、终态拒绝和 WAITING cycle Red 测试。
- [x] 实现 typed `ContinuationNotice`，迁移自由字符串 notice store。
- [x] 实现独立 `continuation_data` ProviderInputItem 和 XML escape 测试。
- [x] Continuation data 不进入 System、ContextSnapshot、StoredMessage 或 Frontend。
- [x] Agent/TurnLifecycle 接线 InMemory Run 真值。
- [x] continuation 重新读取权威状态并重新构建 ProviderRequest。
- [x] 不实现 Durable command/checkpoint/restart。

验证：

```powershell
python -m pytest tests/runtime/test_run_state.py tests/runtime/test_continuation_projection.py tests/multi -q
```

### P4-6 Profile 拆分与 Session 转发

**Owner:** 主会话
**Precondition:** `runtime/profile.py` 超过 500 行，修改前先按现有职责拆分。

- [x] Characterization tests 先冻结 profile 行为。
- [x] 提取 Local/Web/Distributed profile 实现到独立模块，门面只保留稳定导出。
- [x] `LocalRuntimeProfile.build_agent(session_id)` 转发唯一 Session ID。
- [x] 拆分不改变 Durable/Distributed 行为。

验证：

```powershell
python -m pytest tests/runtime/test_runtime_profile.py tests/architecture/test_module_size_policy.py -q
```

### P4-7 删除旧 Attachment 域

**Owner:** 主会话执行源代码删除；支线可迁移纯测试。
**Delete:** `src/agentos/attachments/`、旧专属 tests。
**Modify:** 旧 import call sites、`context_protocol.py`、Provider adapter tests。

- [x] 所有生产输入、路由、投影和清理已使用 Artifact 后再删除旧包。
- [x] 不保留 re-export、deprecation proxy 或双写桥。
- [x] 历史文档允许保留文字，但当前源代码/测试禁止导入旧包。

验证：

```powershell
rg -n "agentos\.attachments|AttachmentRuntime|AttachmentLifecycle|attachments=" src tests
python -m pytest tests/artifacts tests/providers tests/runtime -q
```

预期：`rg` 对当前源码和测试零命中。

### P4-8 Root API、基础安装与 Level 1 Examples

**Owner:** 主会话拥有 Root/Inventory；支线 C 可先做门禁和 examples。
**Modify:** Root、API policy/stability/inventory、迁移文档、两个 Level 1 examples。

- [x] Root 精确导出五个名称。
- [x] 领域对象继续通过模块级 API 使用。
- [x] API policy tests 迁移，不继续向超大旧 contract test 堆职责。
- [x] Inventory 使用仓库生成流程生成，不手工伪造。
- [x] 基础依赖/import/FakeProvider smoke gate。
- [x] 两个 examples Builder-first，并提取必要的私有 example helper 避免复制。

验证：

```powershell
python -m pytest tests/architecture/test_root_facade_contract.py tests/examples tests/packaging -q
python scripts/generate_public_api_inventory.py --check
```

若实际生成脚本参数不同，使用仓库现有 `--help` 显示的命令并在完成报告记录。

### P4-9 M3 集成与双层 Review

**Owner:** 主会话集成；两个只读 Reviewer 分别审查 Spec 和代码质量。

- [x] 逐项填写 M3 Contract Matrix evidence。
- [x] 扫描触及文件规模；500 行以上文件必须拆分或按规范登记例外。
- [x] Spec Reviewer 只判断实现是否完整符合 Contract。
- [x] Quality Reviewer 只判断架构边界、正确性、测试质量和代码规模。
- [x] 修复审查发现后重跑全部门禁。

最终验证：

```powershell
python -m pytest -q
python -m compileall -q src tests
python -m ruff check src tests
git diff --check
rg -n "agentos\.attachments|AttachmentRuntime|AttachmentLifecycle|attachments=" src tests
```

## 4. M3 验收证据

| Contract | Evidence | 状态 |
|---|---|---|
| 单一 Loop/Scheduler | `test_query_loop_contract.py`、Stream/Scheduler contract suites | 完成 |
| Session 真值 | `test_agent_builder.py`、`test_runtime_profile.py` | 完成 |
| Artifact 垂直切片 | `test_phase4_artifact_acceptance.py` 完整两轮 E2E | 完成 |
| 每 attempt 重组 | `test_phase4_authority_rebuild.py` retry/continuation 路径测试 | 完成 |
| Mount 生命周期 | `test_phase4_artifact_acceptance.py` complete/fail/cancel/wait 矩阵 | 完成 |
| Continuation 数据 | `test_continuation_projection.py`、`test_phase4_authority_rebuild.py` | 完成 |
| Run 状态机 | `test_run_state.py`、`test_query_loop_contract.py` 并发拒绝清理 | 完成 |
| Root API | `test_root_facade_contract.py` 与生成式 Public API Inventory | 完成 |
| 基础安装 | `tests/packaging/test_level1_install.py` | 完成 |
| Level 1 Examples | `test_level1_example_contract.py`、`test_context_protocol_agent.py` | 完成 |

规模处理证据：`runtime/profile.py` 已拆为 Local/Web/Distributed/Operations 模块；
`observability/instrumented.py` 已拆出 `instrumented_tools.py`；production reference
example 已拆出 fixtures/support；`builder.py` 在新增 Projection API 后通过提取重复配置
校验保持在冻结上限内。模块规模基线由生成脚本维护。

Phase 5 延期边界保持不变：Durable Command、Checkpoint、restart/resume、
Redis/PostgreSQL、Worker、Transport 和 Distributed Wakeup/Claim 均未进入本阶段。

最终双层 Review 结论：Spec Review 无 P0-P3 finding；Code Quality Review 最初发现
WAITING cleanup 和 `Agent.artifacts` 类型两个问题，修复复核后无 P0-P2 finding。
修复后的全量测试结果为 `2694 passed, 11 skipped`。

## 5. 提交边界

建议提交顺序：

1. `docs: freeze phase4 local loop contract`
2. `feat: add context projection registry`
3. `feat: integrate session scoped artifacts`
4. `feat: add local run continuation kernel`
5. `refactor: remove legacy attachment runtime`
6. `refactor: close phase4 public api`
7. `docs: close phase4 m3 evidence`

提交必须只暂存本任务文件。用户已有修改始终排除在提交之外。
