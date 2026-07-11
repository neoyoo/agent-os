# AgentOS Engineering Governance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立一套会在每次 AgentOS 开发任务中进入可信上下文的工程规范，并让根项目指令和总体实施计划共同强制执行。

**Architecture:** 使用“两层内容、一条执行链”：根 `AGENTS.md` 保存始终加载的精简契约，`docs/governance/agentos-engineering-standard.md` 保存完整权威规范；每个任务通过 Context Bootstrap 绑定当前 Spec、Plan、模块边界和验收项。未来由 AgentOS 构建的开发 Agent 将精简契约投影到 SystemEnvelope 的 Workspace Contract，而不是写入 Memory、ContextSnapshot 或 StoredMessage。

**Tech Stack:** Markdown、Git、PowerShell、现有 AgentOS Context Protocol v1 与 Superpowers 计划流程。

---

### Task 1: 创建权威工程标准

**Files:**
- Create: `docs/governance/agentos-engineering-standard.md`

- [x] **Step 1: 建立规范身份和适用范围**

写入版本、authority、适用对象、与 `AGENTS.md`/Spec/Plan 的优先级，以及“规范属于可信指令，不属于会话数据”的规则。

- [x] **Step 2: 定义强制 Context Bootstrap**

固定每个开发任务开始前必须读取：

```text
AGENTS.md
docs/governance/agentos-engineering-standard.md
当前总架构 Spec
当前子系统 Spec
当前 Implementation Plan
相关源码和测试
```

并固定 Scope Contract 的七项内容：phase/spec、验收项、文件边界、依赖边界、完成项、延期项、验证命令。

- [x] **Step 3: 定义架构和抽象门禁**

写入 Context-First、Kernel/Extension/Adapter 依赖方向、QueryLoop/Router/Executor/Store 等职责，以及引入 Protocol、Registry、Scheduler、Strategy、Factory、Adapter 的充分条件和禁止模式崇拜规则。

- [x] **Step 4: 定义规模和复杂度门禁**

固定：

```text
300 行：职责审查
500 行：默认必须拆分或登记例外
800 行：项目代码禁止，生成代码/协议数据/兼容矩阵例外
```

同时规定“职责优先于行数”、修改现有超大文件的拆分要求、函数/类复杂度信号和例外审批格式。

- [x] **Step 5: 定义 API、状态、并发、安全和测试规则**

覆盖 Public API、错误语义、取消、WAITING 恢复、Tool 并发、持久化、敏感数据、TDD、Contract/Golden/Fault 测试、双层 Review、Subagent/Worktree 和 Definition of Done。

- [x] **Step 6: 验证规范不存在占位符和内部冲突**

Run:

```powershell
$patterns=@(('TO'+'DO'),('T'+'BD'),'待定','稍后补充')
Select-String -Path docs/governance/agentos-engineering-standard.md -Pattern $patterns
```

Expected: 无输出。

### Task 2: 强化根项目上下文契约

**Files:**
- Modify: `AGENTS.md`

- [x] **Step 1: 增加 Mandatory Context Bootstrap**

在 Required Design References 后增加强制读取顺序，并要求架构级任务在任何代码编辑前输出 Scope Contract。

- [x] **Step 2: 增加精简 Engineering Contract**

把不可压缩的核心规则直接写入 `AGENTS.md`：上下文是真值投影、依赖方向、职责隔离、文件规模门禁、Spec/TDD/Review/验证纪律。

- [x] **Step 3: 增加未来开发 Agent 的 Workspace Contract 映射**

明确工程契约必须进入 SystemEnvelope 的 `Workspace Contract`，不得进入 ContextSnapshot、Memory、StoredMessage 或 Compressed History。

- [x] **Step 4: 验证强制引用和关键门禁存在**

Run:

```powershell
Select-String -Path AGENTS.md -Pattern 'agentos-engineering-standard.md','Mandatory Context Bootstrap','500','Workspace Contract'
```

Expected: 四类关键内容均有匹配。

### Task 3: 同步 SDK 总体实施计划

**Files:**
- Modify: `docs/superpowers/plans/2026-07-10-agentos-context-first-sdk-master-implementation-plan.md`

- [x] **Step 1: 将工程标准加入 Definition of Ready**

要求每个工作包声明 Context Bootstrap、Scope Contract、文件规模审查结果和例外记录。

- [x] **Step 2: 强化质量门禁**

增加模块规模扫描、触碰超大文件规则、架构依赖审查和完整验证矩阵。

- [x] **Step 3: 同步已确认的新架构语义**

把以下内容加入阶段覆盖矩阵：

```text
WAITING -> 退出当前 Loop -> Wakeup -> QUEUED -> Continuation Turn
Level 1 ToolCallScheduler -> 默认并发 8 -> exclusive barrier -> 有序写回
```

其中 WAITING 恢复进入 Runtime/Phase 4 与 Durable/Distributed 后端，Tool Scheduler 进入 Phase 4。

- [x] **Step 4: 验证主计划包含新增约束**

Run:

```powershell
Select-String -Path docs/superpowers/plans/2026-07-10-agentos-context-first-sdk-master-implementation-plan.md -Pattern 'Context Bootstrap','ToolCallScheduler','WAITING','500 行'
```

Expected: 四类新增约束均有匹配。

### Task 4: 完成治理文档验证和提交

**Files:**
- Verify: `AGENTS.md`
- Verify: `docs/governance/agentos-engineering-standard.md`
- Verify: `docs/superpowers/plans/2026-07-10-agentos-context-first-sdk-master-implementation-plan.md`
- Verify: `docs/superpowers/plans/2026-07-11-agentos-engineering-governance-implementation-plan.md`

- [x] **Step 1: 检查占位符和 Markdown 差异**

Run:

```powershell
$patterns=@(('TO'+'DO'),('T'+'BD'),'待定','稍后补充')
Select-String -Path AGENTS.md,docs/governance/agentos-engineering-standard.md,docs/superpowers/plans/2026-07-10-agentos-context-first-sdk-master-implementation-plan.md -Pattern $patterns
git diff --check
```

Expected: 占位符无输出，`git diff --check` exit code 0。

- [x] **Step 2: 确认没有修改 SDK 实现代码或用户文件**

Run:

```powershell
git status --short
```

Expected: 本任务只出现上述四个治理文件；用户已有的 `docs/design/llm-context-only-example.md` 保持独立 modified，不能暂存。

- [x] **Step 3: 提交治理变更**

```powershell
git add -- AGENTS.md docs/governance/agentos-engineering-standard.md docs/superpowers/plans/2026-07-10-agentos-context-first-sdk-master-implementation-plan.md docs/superpowers/plans/2026-07-11-agentos-engineering-governance-implementation-plan.md
git diff --cached --check
git commit -m "docs: enforce AgentOS engineering governance"
```

Expected: 提交只包含治理规范、根上下文契约和计划同步。
