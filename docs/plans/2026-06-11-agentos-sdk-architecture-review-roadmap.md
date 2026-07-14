# agent-os SDK 架构评审与迭代路线图

> **SUPERSEDED FOR LOOP TOPOLOGY:** 本历史路线图中的旧双 Loop 与 Agent API
> 描述已被 `docs/superpowers/specs/2026-07-12-agentos-single-async-query-loop-design.md`
> 取代；历史正文保留。
>
> Date: 2026-06-11  
> Branch: `review/agentos-sdk-architecture-20260611`  
> Status: draft for review  
> Scope: 生产级 agent SDK 设计模式 review，不直接进入核心重构。

## Scope Contract

本轮目标不是一次性把 agent-os 改成完整平台，而是先建立一套可持续迭代的判断框架：

1. 明确当前 SDK 已经能稳定支持哪些 agent 形态。
2. 明确哪些形态只是有底层 primitives，还缺 production profile 或协议表面。
3. 以未来 agent 形态为目标，逐个 Phase 设定目标结论，再按结论检查代码和文档。
4. 每个 Phase 完成前必须有要求、实现文件、测试或验证、状态清单。
5. 所有实现工作都留在本 review 分支或后续 feature 分支，不能直接污染 `master`。

本计划只允许启动 Phase 0 文档对齐。`RuntimeProfile`、生产级 session provider、A2A 完整协议、team runtime 等都需要单独设计文档和实现计划后再动代码。

## External Baseline

本次 review 参考了 2026-06-11 可访问的公开文档：

- AgentScope 2.0 将 production agent service 定义为多租户、多会话 HTTP 服务，服务层负责 request routing、session state、persistence、scheduling、tool offloading、workspace lifecycle，并通过 Redis storage/message bus 支持多 worker 共享逻辑服务。
- AgentScope 2.0 Agent Team 把 leader/worker 都建模成独立 session，通过 Redis-backed message bus、inbox 和 wakeup dispatcher 分布式协作。worker 不是 leader 内部的嵌套协程，而是可由集群任意节点唤醒运行的 session。
- A2A 最新规范把 Agent Card、well-known URI、registry/direct config、task/message operations、streaming、push notification、auth/security、skills/capabilities 作为互操作核心。A2A 不只是一个 `/a2a/tasks` JSON endpoint。

参考链接：

- https://docs.agentscope.io/v2/deploy/agent-service
- https://docs.agentscope.io/v2/deploy/agent-team
- https://a2a-protocol.org/latest/topics/agent-discovery/
- https://a2a-protocol.org/latest/specification/

## Current Architecture Read

已检查的本仓关键边界：

- Runtime: `src/agentos/runtime/query_loop.py`, `src/agentos/runtime/async_query_loop.py`, `src/agentos/runtime/agent.py`
- Builder: `src/agentos/builder.py`
- Channels: `src/agentos/channels/asgi.py`, `src/agentos/channels/session.py`, `src/agentos/channels/a2a.py`, `src/agentos/channels/a2a_server.py`
- Persistence: `src/agentos/persistence/base.py`, `src/agentos/persistence/serializers.py`, `src/agentos/persistence/sqlite.py`, `src/agentos/persistence/postgres.py`
- Registry: `src/agentos/registry/*`, `src/agentos/multi/registry.py`
- Multi-agent: `src/agentos/multi/coordinator.py`, `src/agentos/multi/task_store.py`, `src/agentos/multi/message_queue.py`, `src/agentos/multi/postgres_tasks.py`, `src/agentos/multi/redis_queue.py`
- Skill docs: `.claude/skills/agent-os/*`
- Design references: `AGENTS.md`, `docs/design/sdk-architecture.md`, `docs/design/llm-context-only-example.md`

上一轮基线测试记录：`uv run pytest -q` 为 `609 passed, 6 skipped`。本 roadmap 是文档新增，后续只需要做 diff hygiene；进入实现 Phase 后再按变更范围跑 targeted tests、full tests、compileall 和 `git diff --check`。

## High-Level Review Conclusion

agent-os 当前的核心优点是 module boundary 已经基本正确：`QueryLoop` 只做 turn 调度，context/message/compression/recall/tool/provider/observability/persistence/multi-agent 都有独立边界。这个方向适合作为底层 SDK。

但以生产级 agent 基石衡量，SDK 仍需要继续把 primitives 收敛成面向部署形态的 profile。Phase 1 已引入 `RuntimeProfile` 边界，Phase 10A 已补上 `DistributedWebRuntimeProfile`，可以把 durable web session、Redis lease、Postgres snapshot 和 ASGI channel 组合成标准多节点 Web 形态；但 A2A operation parity、team worker lifecycle、distributed TeamStore、planner DAG/retry/persistent store、workspace sandbox enforcement 还不能说已经是开箱即用的生产形态。

一句话判断：

```text
agent-os 现在已经是 context-first agent runtime SDK；
下一步要升级成 profile-driven agent application SDK。
```

## Supported Agent Forms Today

### Directly Supported

这些形态可以直接用当前 SDK 构建：

- Terminal/script agent: `AgentBuilder().build()` + `Agent.run()`，适合 CLI、脚本、测试、单进程工具 agent。
- Async terminal/service agent: `AgentBuilder().build_async()` + `Agent.async_run()` 或 `async_stream()`，适合接入 FastAPI/aiohttp/Starlette 的 event loop。
- Tool-calling agent: `ToolRegistry` + `ToolCallRouter` + `RegisteredTool`。
- Long conversation agent: `CompressionRuntime` + `RecallRuntime` + `CompressionIndex`。
- Streaming agent: typed stream events、SSE、JSONL。
- Observable agent: typed events、OTel/Langfuse adapters、event log、snapshots。
- Local multi-agent coordination: `AgentCoordinator` + in-memory registry/task table/inbox。
- Distributed task primitives: Postgres task store、Redis message queue、remote task executor/A2A adapter。

### Partially Supported

这些形态已有核心 primitives，但还不能算生产级直接支持：

- Web distributed agent: Phase 10A 后已有 `DistributedWebRuntimeProfile`，可用 `DurableAgentSessionProvider`、`RedisSessionLeaseStore`、`PostgresSessionSnapshotPersistence` 支持任意 node 接同一 session 后 hydrate/mutate/persist full runtime state；仍需部署侧负责 credentials、migration、TTL/stale lease recovery、auth、workspace enforcement、live backend verification。
- Session-persistent web agent: `SessionSnapshot` 已可序列化 context/message/compression/event，`DurableAgentSessionProvider` 和 `DistributedWebRuntimeProfile` 已把标准 restore/save 生命周期收敛成 SDK 边界；非标准 lifecycle 仍需要自定义 `AgentSessionProvider`。
- Agent registry/discovery agent: 有 `AgentCard`、`PersistentAgentRegistry`、`ServiceResolver`，但 AgentCard 字段不是 A2A 完整模型，发现机制也未覆盖 well-known、signed card、auth、skills/interfaces。
- A2A agent: 当前 `A2AAdapter` 更像内部 task JSON bridge，不是完整 A2A protocol binding。
- Team discussion agent: 当前有 spawn/dispatch 和 Redis/Postgres 边界，但缺 team membership、leader/worker session stream、team tools、wake-up conversation model。
- Planner/intent-router agent: Phase 6C 后已有 `PlannerRuntime`、`PlannerTools`、`PlanState`、`SubAgentTemplate`、`EvidenceHandle`、`plan_to_working_state_summary` primitives 和 intent-router / plan-and-execute 示例；自动 decomposition、DAG scheduling、persistent PlanStore、production retry policy 仍是后续工作。
- Workspace-isolated production agent: 有 policy/security 方向，但 workspace 还不是 channel/profile/session 的硬边界。

### Not Production Supported Yet

这些方向需要显著扩展：

- Full A2A compliant server/client。
- Multi-tenant web service resource model: user/agent/session/credential/workspace/schedule/message bus。
- Distributed wakeup/scheduled/background-tool agent。
- Sandboxed code interpreter。
- Voice/audio realtime agent。
- Graph/DAG workflow engine。

## Sync vs Async Decision

目标结论：

```text
sync loop 和 async loop 都应该保留；
生产 web/distributed 默认选择 async；
terminal/script/testing 默认保留 sync。
```

理由：

- 单 node terminal agent 的主要价值是可调试、可测试、无 event loop 依赖，sync API 更好用。
- Web agent 的主要瓶颈是 provider I/O、tool I/O、MCP/skill source、SSE streaming 和 cancellation，native async 更适合。
- 当前 `AsyncQueryLoop` 已经是 native async provider/tool path，并且保留 sync facade。这个方向是对的。
- 需要补的是 profile 层的默认选择，而不是删除其中一个 loop。

后续判断标准：

- `AgentBuilder().build()` 仍构建 sync-first agent。
- `AgentBuilder().build_async()` 仍构建 async-first agent。
- Web runtime profile 默认使用 async loop，且 session hydrate/save 不阻塞 event loop。
- 如果某些 adapter 只有 sync 实现，必须明确 executor fallback 和 cancellation 行为。

## Proposed Runtime Shape

目标结论：

```text
agent-os 应新增 RuntimeProfile 层，
把 local terminal agent 与 web distributed agent 的差异留在 profile，
不让 QueryLoop 感知本地/分布式分支。
```

建议 profile 责任：

- session identity and hydration
- workspace boundary
- memory backend selection
- event store selection
- registry/discovery strategy
- execution backend/sandbox policy
- channel capabilities
- concurrency, locking, lease, cancellation
- snapshot save/restore policy

建议初始类型：

- `LocalRuntimeProfile`: 单进程、local workspace、in-memory/SQLite persistence、sync loop default。
- `WebRuntimeProfile`: ASGI/SSE、durable session provider、async loop default、distributed lock、event replay buffer、auth/rate-limit hooks。
- `DistributedAgentProfile`: registry/discovery、Postgres task store、Redis message bus、remote executor/A2A client/server。
- `DistributedTeamRuntimeProfile`: team runtime、message queue、worker session provider、retry/cancellation store、UI stream、worker runner/daemon preset。

## Phase Roadmap

### Phase 0: Capability Truth Alignment

目标结论：

```text
开发指导 skill 必须真实区分 supported directly、supported with glue、future extension；
否则 SDK 使用者会误把 primitives 当 production-ready agent form。
```

检查对象：

- `.claude/skills/agent-os/modules/agent-forms.md`
- `.claude/skills/agent-os/modules/architecture.md`
- `.claude/skills/agent-os/modules/persistence.md`
- `.claude/skills/agent-os/modules/multi-agent.md`

优化方向：

- 把 web distributed agent 标为 primitives-ready，注明标准路径使用 `DistributedWebRuntimeProfile`，生产部署仍需要 credentials、migration、TTL/recovery、auth、workspace 等 app glue。
- 把 A2A agent 标为 partially supported，注明当前是 minimal task bridge，不是 complete A2A。
- 把 team discussion agent 标为 future/partial，注明缺 team membership 和 wakeup session model。
- 给终端 agent、async agent、long conversation、tool-calling、streaming agent 保留 directly supported。

验收：

- 文档与代码实际能力一致。
- 不夸大生产能力。
- `git diff --check` 通过。

### Phase 1: RuntimeProfile Design

目标结论：

```text
SDK 的部署形态必须通过 RuntimeProfile 表达；
QueryLoop 只消费 collaborators，不知道 local/web/distributed。
```

检查对象：

- `src/agentos/builder.py`
- `src/agentos/runtime/agent.py`
- `src/agentos/channels/*`
- `src/agentos/persistence/*`

优化方向：

- 先写 design spec，定义 `RuntimeProfile` protocol 和 Local/Web 两个最小实现边界。
- 不急着把所有 builder 参数收进 profile；先保证 profile 能声明 loop type、session provider、persistence、workspace、event store。
- 明确 backwards compatibility: 现有 `AgentBuilder` API 不破坏。

验收：

- 有 design/spec 和 implementation plan。
- targeted tests 覆盖 local profile 与 web profile collaborator assembly。
- full tests、compileall、diff check 通过。

### Phase 2: Production Web Session Hydration

目标结论：

```text
web 形态的 session 是 runtime state 的 truth boundary；
任意 node 接到请求都必须能 acquire lock、load snapshot、run turn、save snapshot、release lock。
```

检查对象：

- `src/agentos/channels/session.py`
- `src/agentos/channels/asgi.py`
- `src/agentos/persistence/base.py`
- `src/agentos/persistence/serializers.py`
- `src/agentos/builder.py`

优化方向：

- 新增 production `DurableAgentSessionProvider` 或等价 profile-level provider。
- 支持 load/save `SessionSnapshot`。
- 定义 session locking/lease protocol，防止同一 session 同时被两个 node 跑 turn。
- SSE mid-turn resume 与 event buffer 保持 channel concern，snapshot 是 session concern。
- 如果 turn 失败，明确保存策略：保存 user message 与 failure event，或 rollback 到 turn 前 snapshot，二者选一并测试。

验收：

- 两个 provider 实例模拟不同 node，同一 session 可以连续接力。
- 并发同 session 请求被锁或排队，不产生双写。
- snapshot 包含 messages、context、compression index、session state、event records。

Phase 2 artifacts:

- `docs/superpowers/specs/2026-06-11-production-web-session-hydration-design.md`
- `docs/superpowers/plans/2026-06-11-production-web-session-hydration-implementation-plan.md`

Phase 2A starts with `SessionPersistence` + lease protocol + in-memory lease tests.
Redis/Postgres adapters are later implementation slices behind the same lifecycle boundary.

### Phase 3: Workspace and Permission Boundary

目标结论：

```text
workspace 不是工具实现细节，而是 agent session 的执行边界；
web/distributed profile 必须显式选择 per-agent、per-user 或 per-session workspace 策略。
```

检查对象：

- `src/agentos/policies/security.py`
- `src/agentos/capabilities/executor.py`
- `src/agentos/channels/*`
- future `workspace/` module if introduced

优化方向：

- 定义 `WorkspaceProvider`/`WorkspaceHandle`，先不实现复杂 sandbox。
- 让 tools/executors 获取 workspace boundary，而不是隐式使用进程 cwd。
- 对 web profile 默认选择 per-session 或 per-user strategy，terminal profile 默认 local cwd。
- 把 permission context 与 workspace strategy 绑定，给 subagent 权限降级留接口。

验收：

- 文件/命令类工具通过 workspace handle 执行。
- policy tests 覆盖 subagent 权限不超过 parent。
- terminal 默认行为不破坏。

### Phase 4: A2A and Registry Upgrade

目标结论：

```text
A2A 不是内部 task bridge；
agent-os 需要区分 internal multi-agent dispatch 与 external interoperable A2A protocol。
```

检查对象：

- `src/agentos/multi/types.py`
- `src/agentos/channels/a2a.py`
- `src/agentos/channels/a2a_server.py`
- `src/agentos/registry/*`

优化方向：

- 新增 A2A-compatible AgentCard model 或 adapter layer。
- 支持 well-known card publication。
- 支持 registry/direct config resolver。
- 支持 skills/interfaces/security/capabilities 的协议映射。
- 当前 `/a2a/tasks` 可保留为 internal bridge，但文档中不能称为 full A2A。

验收：

- AgentCard serialization golden tests。
- well-known route returns protocol card。
- resolver 能从 static/direct/well-known 获取 card。
- A2A docs 明确 internal vs external protocol。

### Phase 5: Team Discussion and Distributed Wakeup

目标结论：

```text
team 型 multi-agent 不应是 leader 内部的嵌套函数调用；
leader 与 worker 都应是独立 session，通过 inbox/message bus/wakeup 交换上下文片段。
```

检查对象：

- `src/agentos/multi/coordinator.py`
- `src/agentos/multi/message_queue.py`
- `src/agentos/multi/redis_queue.py`
- `src/agentos/runtime/*`
- `src/agentos/channels/*`

优化方向：

- 引入 team record/member record。
- 定义 team tools: `team_create`, `agent_create`, `team_say`, `team_delete`。
- worker 作为独立 session，拥有自己的 context/message runtime 和 event stream。
- 通过 message bus 把 team message 注入 recipient session 的下一轮 context。
- continuation/wakeup 与 background-tool completion 复用同一机制。

验收：

- leader 创建 team 与 worker。
- worker 并发运行，结果通过 team message 回 leader。
- 不共享 active messages，不直接写 parent working state。
- Redis-backed queue 合同测试覆盖跨 worker wakeup。

### Phase 6: Planner, Intent Router, Subagent Templates

目标结论：

```text
plan-and-execute、intent-router、subagent delegation 是应用模式；
SDK 应提供可组合模板和状态边界，而不是硬编码唯一 planner。
```

检查对象：

- `src/agentos/multi/*`
- `src/agentos/context/*`
- `src/agentos/capabilities/*`
- `.claude/skills/agent-os/flow/*`

优化方向：

- Phase 6A 已定义 `SubAgentTemplate`: role, system/context seed, tool policy, workspace policy。
- Phase 6A 已定义 planner state projection: plan、steps、assignments、evidence handles。
- Phase 6C 已定义 `plan_to_working_state_summary`，working state 只接收摘要投影，`PlanStore` 仍是 truth source。
- 主 agent 可做 intent recognition 和 plan generation，subagent 执行 task，结果以 artifacts/evidence handles 回收。
- 工作空间层从“当前 agent 执行目录”扩展为“plan/session/team 的资源边界”。

验收：

- 一个 intent-router 示例不需要自定义底层 runtime。
- 一个 plan-and-execute 示例能分配给 subagent 并回收 artifact。
- docs 明确 planner 是 pattern，不是默认 query loop，且 working state 不是 plan truth source。

### Phase 7: Production Hardening Matrix

目标结论：

```text
生产级 SDK 不只看功能路径，还要看失败路径、可观测性、幂等、预算、安全和升级兼容。
```

检查对象：

- `docs/todo-production-hardening.md`
- `src/agentos/observability/*`
- `src/agentos/runtime/retry.py`
- `src/agentos/policies/*`
- `tests/*`

优化方向：

- 建 production readiness matrix: auth、rate limit、budget、timeout、retry、idempotency、snapshot migration、event replay、PII redaction、secret handling。
- 对每种 agent form 标注 readiness level。
- 引入 migration/versioning checklist，避免 snapshot/card/task schema 破坏升级。
- Phase 7A 已引入 `agentos.readiness`，覆盖 terminal、async web host、web distributed session、A2A discovery、team discussion、planner/intent-router 六个关键形态。

验收：

- 关键 agent form 有 dimension-level readiness checklist。
- Web distributed agent 明确保留 deployment policy gap，不能把 adapter 存在等同于完整生产就绪。
- A2A、team、planner 不能绕过矩阵宣称 full production ready。
- docs 与 tests 能证明“不只是 happy path”。

Phase 7B artifacts:

- `docs/superpowers/specs/2026-06-12-production-readiness-guidance-design.md`
- `docs/superpowers/plans/2026-06-12-production-readiness-guidance-implementation-plan.md`
- `docs/production-readiness.md`
- `tests/docs/test_production_readiness_docs.py`

Phase 7B conclusion:

```text
The readiness matrix now feeds a public production checklist and SDK skill
guidance, so production-bound specs must carry form id, dimension levels, and
required_app_glue instead of relying on vague readiness labels.
```

### Phase 8A: Distributed Session Adapters

Target conclusion:

```text
Web distributed session support becomes production-credible only when the SDK
ships concrete distributed lease and snapshot adapters behind the same
DurableAgentSessionProvider lifecycle.
```

Artifacts:

- `docs/superpowers/specs/2026-06-12-distributed-session-adapters-design.md`
- `docs/superpowers/plans/2026-06-12-distributed-session-adapters-implementation-plan.md`
- `RedisSessionLeaseStore`
- `PostgresSessionSnapshotPersistence`
- `docs/migrations/2026-06-12-postgres-session-snapshots.sql`
- `tests/channels/test_redis_session_lease_store.py`
- `tests/persistence/test_postgres_session_snapshot_persistence.py`

Conclusion:

```text
The SDK now has concrete Redis lease and Postgres full-snapshot adapters for
the durable web session lifecycle. The web distributed form remains
primitives-ready because production deployments still own credentials,
migration rollout, TTL tuning, stale lease recovery, workspace enforcement, and
failure policy.
```

### Phase 9A: Team Tools

Target conclusion:

```text
Team discussion agents need first-class LLM-callable tools over TeamRuntime;
team records/messages alone are not enough for an SDK-level agent pattern.
```

Artifacts:

- `docs/superpowers/specs/2026-06-12-team-tools-design.md`
- `docs/superpowers/plans/2026-06-12-team-tools-implementation-plan.md`
- `TeamTools`
- `tests/multi/test_team_tools.py`

Conclusion:

```text
Team discussion now has SDK-level tools for team creation, member creation,
team messages, message reads, and team deletion. It remains primitives-ready
because worker session lifecycle, UI stream protocol, and production permission
policy are still app/profile-owned.
```

### Phase 12A: Distributed TeamStore

Target conclusion:

```text
Team agents are persistent leader/worker session boundaries; a distributed
TeamStore is the minimum SDK-owned state primitive for multi-node teams.
```

Artifacts:

- `docs/superpowers/specs/2026-06-12-distributed-team-store-design.md`
- `docs/superpowers/plans/2026-06-12-distributed-team-store-implementation-plan.md`
- `PostgresTeamStore`
- `docs/migrations/2026-06-12-postgres-team-store.sql`
- `tests/multi/test_postgres_team_store.py`

Conclusion:

```text
Team discussion now has a Postgres-backed TeamStore for shared team records,
members, and messages across nodes. It remains primitives-ready because worker
execution lifecycle, automatic worker scheduling/retry, UI stream protocol, and
production permission downgrade policy are still app/profile-owned.
```

### Phase 13A: Team Worker Session Lifecycle

Target conclusion:

```text
Team workers are independent sessions, not only member records. agent-os needs
an explicit worker session lifecycle boundary before team discussion can become
a production-grade distributed agent pattern.
```

Artifacts:

- `docs/superpowers/specs/2026-06-12-team-worker-session-lifecycle-design.md`
- `docs/superpowers/plans/2026-06-12-team-worker-session-lifecycle-implementation-plan.md`
- `TeamWorkerSessionRequest`
- `TeamWorkerSession`
- `TeamWorkerSessionProvider`
- `InMemoryTeamWorkerSessionProvider`
- `tests/multi/test_team_runtime.py`
- `tests/multi/test_team_tools.py`

Conclusion:

```text
Team discussion now has an SDK boundary for registering and closing independent
worker sessions when agents are added to or removed through a team lifecycle.
It remains primitives-ready because automatic worker wakeup/execution,
retry/backoff policy, cancellation scheduling, UI stream protocol, and
production permission downgrade policy are still app/profile-owned.
Later phases add worker execution, retry/backoff, and cancellation intent
boundaries.
```

### Phase 14A: Team Worker Runner

Target conclusion:

```text
A team worker session is only useful if a distributed runner can turn team
message wakeups into worker continuation turns. The SDK should provide this
runner boundary without coupling QueryLoop to team semantics.
```

Artifacts:

- `docs/superpowers/specs/2026-06-12-team-worker-runner-design.md`
- `docs/superpowers/plans/2026-06-12-team-worker-runner-implementation-plan.md`
- `TeamWorkerAgentProvider`
- `TeamWorkerRunResult`
- `TeamWorkerRunError`
- `TeamWorkerRunner`
- `tests/multi/test_team_worker_runner.py`

Conclusion:

```text
Team discussion now has a batch runner that consumes team-message deliveries,
resolves worker agents, runs worker continuation turns, acks successful
deliveries, and records failures without coupling QueryLoop to team semantics.
At Phase 14A it remained primitives-ready because continuous worker hosting,
retry/backoff policy, cancellation scheduling, UI stream protocol, and
production permission downgrade policy were still app/profile-owned. Phase 15A
adds the worker hosting loop, Phase 16A adds retry/backoff primitives, and
Phase 17A adds cancellation intent primitives.
```

### Phase 15A: Team Worker Daemon

Target conclusion:

```text
Team worker execution should be hostable as a long-running service loop. The
SDK should provide a daemon boundary around TeamWorkerRunner without moving
team semantics into QueryLoop or prematurely baking retry/backoff and
cancellation policy into the runner. Phase 16A later adds the retry/backoff
boundary while cancellation remains separate.
```

Artifacts:

- `docs/superpowers/specs/2026-06-12-team-worker-daemon-design.md`
- `docs/superpowers/plans/2026-06-12-team-worker-daemon-implementation-plan.md`
- `TeamWorkerDaemon`
- `TeamWorkerDaemonState`
- `TeamWorkerDaemonStatus`
- `tests/multi/test_team_worker_daemon.py`

Conclusion:

```text
Team discussion now has a service-hostable daemon that repeatedly invokes
TeamWorkerRunner, exposes start/stop/join lifecycle, and records latest
runner results/errors. At Phase 15A it remained primitives-ready because
retry/backoff policy, cancellation scheduling, UI stream protocol, and
production permission downgrade policy were still app/profile-owned. Phase 16A
adds retry/backoff primitives and Phase 17A adds cancellation intent primitives.
```

### Phase 16A: Team Worker Retry Boundary

Target conclusion:

```text
Daemonized team workers need retry/backoff state so failed worker continuations
do not hot-loop. The SDK should provide retry policy and store boundaries
without forcing queue transports to become schedulers or mixing cancellation
and permission policy into the runner.
```

Artifacts:

- `docs/superpowers/specs/2026-06-12-team-worker-retry-boundary-design.md`
- `docs/superpowers/plans/2026-06-12-team-worker-retry-boundary-implementation-plan.md`
- `TeamWorkerRetryPolicy`
- `TeamWorkerRetryRecord`
- `TeamWorkerRetryStore`
- `InMemoryTeamWorkerRetryStore`
- retry projection fields on `TeamWorkerRunResult`

Conclusion:

```text
Team discussion now has SDK retry/backoff primitives for failed worker
continuation deliveries. TeamWorkerRunner can schedule retries, skip attempts
until due, retry stored deliveries, clear retry state on success, and mark
exhausted attempts. At Phase 16A it remains primitives-ready because persistent
retry storage, cancellation scheduling, UI stream protocol, and production
permission downgrade policy are still app/profile-owned.
Phase 16B adds persistent retry storage and Phase 17A adds cancellation intent
primitives.
```

### Phase 16B: Team Worker Persistent Retry Store

Target conclusion:

```text
Distributed team retry/backoff state must survive worker restarts and be
inspectable across nodes. The SDK should provide a Postgres retry store behind
the TeamWorkerRetryStore protocol while leaving cancellation and permission
policy as separate phases.
```

Artifacts:

- `docs/superpowers/specs/2026-06-12-team-worker-persistent-retry-store-design.md`
- `docs/superpowers/plans/2026-06-12-team-worker-persistent-retry-store-implementation-plan.md`
- `PostgresTeamWorkerRetryStore`
- `docs/migrations/2026-06-12-postgres-team-worker-retries.sql`
- `tests/multi/test_postgres_team_worker_retry_store.py`

Conclusion:

```text
Team discussion now has a Postgres-backed TeamWorkerRetryStore for durable
retry/backoff records, including delivery payload restoration for due retries.
It remains primitives-ready because cancellation intent, UI stream protocol,
and production permission downgrade policy are still app/profile-owned.
```

### Phase 17A: Team Worker Cancellation Boundary

Target conclusion:

```text
Team worker services need cancel intent before a queued or retry-delayed
continuation starts. The SDK should provide cancellation store boundaries that
TeamWorkerRunner and TeamWorkerDaemon can observe without coupling QueryLoop to
team semantics.
```

Artifacts:

- `docs/superpowers/specs/2026-06-12-team-worker-cancellation-boundary-design.md`
- `docs/superpowers/plans/2026-06-12-team-worker-cancellation-boundary-implementation-plan.md`
- `TeamWorkerCancellationRecord`
- `TeamWorkerCancellationStore`
- `InMemoryTeamWorkerCancellationStore`
- cancellation projection fields on `TeamWorkerRunResult`

Conclusion:

```text
Team discussion now has cancellation intent primitives for queued and
retry-delayed worker continuations. TeamWorkerRunner can skip cancelled
deliveries before run_continuation(), ack exact delivery cancels, keep
worker-scope cancellations active, clear retry records on cancel, and expose
cancellation state through TeamWorkerDaemon. It remains primitives-ready because
persistent cancellation storage, UI stream protocol, and production permission
downgrade policy are still app/profile-owned. Phase 18A adds the SDK
workspace/capability downgrade boundary.
```

### Phase 18A: Team Worker Permission Boundary

Target conclusion:

```text
Team workers are not full-power copies of the leader. The SDK should enforce a
permission downgrade boundary when worker sessions are created, at minimum
blocking workers from receiving a workspace broader than the team workspace or
one whose local root escapes the team root.
```

Artifacts:

- `docs/superpowers/specs/2026-06-15-team-worker-permission-boundary-design.md`
- `docs/superpowers/plans/2026-06-15-team-worker-permission-boundary-implementation-plan.md`
- `TeamWorkerPermissionPolicy`
- `TeamWorkerPermissionError`
- workspace/capability downgrade enforcement in `InMemoryTeamWorkerSessionProvider`

Conclusion:

```text
Team discussion now has SDK workspace/capability downgrade checks at worker
session creation. TeamWorkerPermissionPolicy rejects broader workspace scopes,
local roots outside the team workspace, and capabilities outside a configured
allow-list. At Phase 18A it remained primitives-ready because persistent
cancellation storage, production UI stream protocol, and tool sandbox
enforcement were still app/profile-owned. Phase 19A adds persistent
cancellation storage.
```

### Phase 19A: Team Worker Persistent Cancellation Store

Target conclusion:

```text
Team worker cancellation intent cannot be process-local in a production
cluster. The SDK should provide a durable TeamWorkerCancellationStore adapter
so any worker node can observe, acknowledge, and clear cancellation requests
before executing queued or retry-delayed continuations.
```

Artifacts:

- `docs/superpowers/specs/2026-06-15-team-worker-persistent-cancellation-store-design.md`
- `docs/superpowers/plans/2026-06-15-team-worker-persistent-cancellation-store-implementation-plan.md`
- `PostgresTeamWorkerCancellationStore`
- `docs/migrations/2026-06-15-postgres-team-worker-cancellations.sql`
- `tests/multi/test_postgres_team_worker_cancellation_store.py`

Conclusion:

```text
Team discussion now has a Postgres-backed TeamWorkerCancellationStore for
durable cancellation intent across worker restarts and nodes. At Phase 19A it
remained
primitives-ready because production UI stream protocol and tool sandbox
enforcement are still app/profile-owned.
```

### Phase 20A: Team UI Stream Protocol

Target conclusion:

```text
Team discussion cannot expose backend state and daemon internals as its UI
contract. The SDK should provide a stable, serializable, cursor/replay-capable
team UI event protocol so web frontends can observe team lifecycle, messages,
worker results, retries, and cancellations without coupling to TeamRuntime or
TeamWorkerRunner internals.
```

Artifacts:

- `docs/superpowers/specs/2026-06-15-team-ui-stream-protocol-design.md`
- `docs/superpowers/plans/2026-06-15-team-ui-stream-protocol-implementation-plan.md`
- `TeamUiEvent`
- `TeamUiEventKind`
- `TeamUiStreamStore`
- `InMemoryTeamUiStreamStore`
- `team_ui_event_to_dict`
- `team_ui_event_from_dict`
- TeamRuntime UI event publication for create/member/message/delete lifecycle
- TeamWorkerRunner UI event publication for completed, failed, retry-skipped,
  and cancelled worker continuation results
- `tests/multi/test_team_ui_stream.py`

Conclusion:

```text
Team discussion now has an SDK UI projection protocol with replay cursors,
bounded in-memory storage, serializer round trips, lifecycle/message events,
and worker result/retry/cancel events. At Phase 20A it remained primitives-ready because
network/SSE exposure, distributed UI stream storage, and tool sandbox
enforcement are still app/profile-owned.
```

### Phase 20B: Team UI Network Replay And Distributed Store

Target conclusion:

```text
Team UI event streams cannot stay process-local if team discussion agents are
used through web frontends or multi-node workers. The SDK should provide a
network-readable replay endpoint and a distributed stream store while keeping
team UI projection outside QueryLoop.
```

Artifacts:

- `docs/superpowers/specs/2026-06-15-team-ui-network-stream-design.md`
- `docs/superpowers/plans/2026-06-15-team-ui-network-stream-implementation-plan.md`
- `GET /v1/teams/{team_id}/ui-events`
- `PostgresTeamUiStreamStore`
- `docs/migrations/2026-06-15-postgres-team-ui-events.sql`
- `tests/multi/test_postgres_team_ui_stream_store.py`
- ASGI endpoint replay tests

Conclusion:

```text
Team discussion now has network JSON cursor replay for UI events and a
Postgres-backed TeamUiStreamStore for cross-node UI history. It remains
primitives-ready because at Phase 20B live SSE/follow subscription and tool sandbox
enforcement are still app/profile-owned.
```

### Phase 20C: Team UI Live Follow Endpoint

Target conclusion:

```text
Team UI replay alone still forces web frontends to poll. The SDK should provide
a live SSE/follow endpoint over the existing TeamUiStreamStore cursor model,
without coupling QueryLoop to team or UI semantics.
```

Artifacts:

- `docs/superpowers/specs/2026-06-15-team-ui-live-follow-design.md`
- `docs/superpowers/plans/2026-06-15-team-ui-live-follow-implementation-plan.md`
- `GET /v1/teams/{team_id}/ui-events/stream`
- SSE event ids using `TeamUiEvent.event_id`
- Last-Event-ID resume support
- idle-timeout, poll-interval, and heartbeat behavior in `AsgiAgentApp`
- ASGI stream replay/follow tests

Conclusion:

```text
Team discussion now has JSON replay and SSE follow endpoints for team UI
events, backed by the same TeamUiStreamStore cursor semantics. It remains
primitives-ready because tool sandbox enforcement is still app/profile-owned.
```

### Phase 10A: Distributed Web Runtime Profile

Target conclusion:

```text
Distributed web agents need an SDK profile preset that assembles durable
session, distributed lease, snapshot persistence, workspace, and channel policy;
raw adapters alone still leave production users wiring too much by hand.
```

Artifacts:

- `docs/superpowers/specs/2026-06-12-distributed-web-profile-design.md`
- `docs/superpowers/plans/2026-06-12-distributed-web-profile-implementation-plan.md`
- `DistributedWebRuntimeProfile`
- readiness recommendation for `web-distributed-session`
- SDK skill guidance for multi-node web specs

Conclusion:

```text
Standard multi-node web agents now have an SDK preset that assembles the durable
session provider, distributed lease store, snapshot persistence, workspace
metadata, and ASGI channel. The form remains primitives-ready until deployment
credentials, migrations, TTL/stale lease recovery, auth, workspace enforcement,
and live backend checks are supplied.
```

### Phase 11A: A2A Operation Boundary

Target conclusion:

```text
A2A Agent Card and discovery are only the interoperability entry point.
agent-os needs a protocol operation boundary that separates external A2A
message/task semantics from the internal AgentCoordinator task bridge.
```

Artifacts:

- `docs/superpowers/specs/2026-06-12-a2a-operation-boundary-design.md`
- `docs/superpowers/plans/2026-06-12-a2a-operation-boundary-implementation-plan.md`
- `A2AMessage`, `A2ATask`, `A2AOperationRequest`, `A2AOperationResponse`
- `A2AOperationServer`, `A2AOperationClient`, `AgentA2AOperationRunner`
- ASGI `/a2a/message:send`

Conclusion:

```text
agent-os now has a minimal external A2A message/send operation boundary while
keeping `/a2a/tasks` as an internal JSON bridge. The form remains
primitives-ready until task lifecycle lookup/cancel/resubscribe, streaming,
push notification, auth enforcement, signed-card trust, and external conformance
tests land.
```

### Phase 11B: A2A Task Lifecycle

Target conclusion:

```text
message/send starts an A2A interaction, but production interoperability also
needs task lookup and cancel operations. agent-os should project internal
TaskStore state into A2A task lifecycle semantics through a protocol boundary,
not by exposing the internal /a2a/tasks bridge as full A2A.
```

Artifacts:

- `docs/superpowers/specs/2026-06-12-a2a-task-lifecycle-design.md`
- `docs/superpowers/plans/2026-06-12-a2a-task-lifecycle-implementation-plan.md`
- `TaskStoreA2ATaskLifecycleRunner`
- `a2a_task_from_task_record`
- `a2a_state_from_task_status`
- ASGI `/a2a/tasks/{id}` and `/a2a/tasks/{id}:cancel`

Conclusion:

```text
agent-os now projects internal TaskStore records into A2A task lifecycle
operations for lookup and cancel. The form remains primitives-ready until task
resubscribe, streaming task updates, push notification config, auth enforcement,
signed-card trust, and external conformance tests land.
```

### Phase 39: Planner Step Recovery Boundary

Target conclusion:

```text
Planner should move from "can express plan/dependencies" to "has an auditable
recovery boundary after failure." The SDK owns step failure recording,
retry eligibility, attempt/backoff metadata, and tool entrypoints; automatic
decomposition, production DAG scheduling, worker dispatch loops, and complex
compensation remain app/profile-owned.
```

Artifacts:

- `docs/superpowers/specs/2026-06-15-planner-step-recovery-design.md`
- `docs/superpowers/plans/2026-06-15-planner-step-recovery-implementation-plan.md`
- `PlanRetryPolicy`
- `PlanStep.attempts`, `last_failed_at`, `next_retry_at`, `retry_status`,
  and `retry_exhausted_at`
- `PlannerRuntime.fail_step`
- `PlannerRuntime.retryable_steps`
- `PlannerRuntime.retry_step`
- Planner tools: `plan_fail_step`, `plan_retryable_steps`, `plan_retry_step`

Conclusion:

```text
Planner/intent-router agents now have SDK-level failure and retry state
boundaries that survive PlanStore serialization and can be driven through
LLM-callable planner tools. The form remains primitives-ready because the
production DAG scheduler, worker dispatch loop, automatic decomposition policy,
and compensation semantics are still application/profile responsibilities.
```

### Phase 52: Planner Worker Dispatch Boundary

Target conclusion:

```text
Planner should not become a full production DAG scheduler, but the SDK should
own a narrow worker-dispatch boundary. It can submit dependency-ready steps
through the existing coordinator assignment path and return an auditable report,
while loop scheduling, worker scaling, decomposition, and compensation remain
app/profile-owned.
```

Artifacts:

- `docs/superpowers/specs/2026-06-15-planner-worker-dispatch-boundary-design.md`
- `docs/superpowers/plans/2026-06-15-planner-worker-dispatch-boundary-implementation-plan.md`
- `PlanDispatchReport`
- `PlanDispatchSkip`
- `PlannerRuntime.dispatch_ready_steps(...)`
- Planner tool: `plan_dispatch_ready_steps`

Conclusion:

```text
Planner/intent-router agents now have a bounded ready-step dispatch boundary
that reuses AgentCoordinator assignments and reports assigned/skipped steps
without creating a scheduler loop. Assignments now also carry dispatch outbox
evidence (`pending` / `submitted` / `failed`) so scheduler ticks can recover a
saved assignment with the original `task_id` after a process restart. The form
remains primitives-ready because automatic LLM decomposition, production DAG
scheduling loops, worker lifecycle management, and complex compensation
semantics are still application/profile work.
```

### Phase 40: A2A JWKS Trust Boundary

Target conclusion:

```text
A2A signed-card trust cannot depend only on locally embedded HMAC secrets. The
SDK should provide a narrowly scoped JWKS key-discovery boundary that composes
with A2ACardResolver and HmacA2ACardVerifier; OIDC issuer validation, CA trust,
tenant RBAC, and full identity governance remain deployment-owned.
```

Artifacts:

- `docs/superpowers/specs/2026-06-15-a2a-jwks-trust-boundary-design.md`
- `docs/superpowers/plans/2026-06-15-a2a-jwks-trust-boundary-implementation-plan.md`
- `JwksA2ACardTrustStore`
- A2A card tests for trusted JWKS verification, HTTPS-only URLs, cache behavior,
  unsupported key rejection, key-id allow-lists, and resolver integration

Conclusion:

```text
A2A discovery now has a configured JWKS key-discovery trust boundary for
HS256/oct signed Agent Cards. This reduces the trust rollout gap while keeping
OIDC issuer validation, CA trust rollout, tenant RBAC mapping, DNS/egress
controls, and full A2A conformance as future deployment/profile work.
```

### Phase 41: A2A Protocol Version Boundary

Target conclusion:

```text
A2A interoperability cannot rely only on payload shape. The SDK should own a
narrow protocol-version boundary that sends A2A-Version on outbound operation
requests, validates inbound requested versions, and returns the A2A
VersionNotSupportedError mapping when a peer asks for an unsupported version.
Full payload parity, extension negotiation, and external conformance remain
later phases.
```

Artifacts:

- `docs/superpowers/specs/2026-06-15-a2a-protocol-version-boundary-design.md`
- `docs/superpowers/plans/2026-06-15-a2a-protocol-version-boundary-implementation-plan.md`
- `A2AProtocolVersionPolicy`
- default outbound `A2A-Version: 1.0` on `A2AOperationClient`
- inbound supported-version validation on `A2AOperationServer`
- `VersionNotSupportedError` JSON-RPC mapping with error code `-32009`

Conclusion:

```text
A2A operation calls now have SDK-level protocol version negotiation primitives.
Clients send A2A-Version by default, servers can configure supported versions,
and unsupported versions produce a structured A2A version-not-supported error.
The A2A form remains primitives-ready because complete payload parity,
extension negotiation, and external conformance tests are still roadmap work.
```

### Phase 42: A2A Text Part Payload Boundary

Target conclusion:

```text
After sending A2A-Version: 1.0, the SDK should stop emitting the legacy `kind`
discriminator for text parts in new operation payloads. The next safe
conformance slice is text-part payload parity, while file/data parts,
artifacts, streaming event wrappers, and full conformance remain later phases.
```

Artifacts:

- `docs/superpowers/specs/2026-06-15-a2a-text-part-payload-boundary-design.md`
- `docs/superpowers/plans/2026-06-15-a2a-text-part-payload-boundary-implementation-plan.md`
- `a2a_message_part_to_dict` emits `{"text": "..."}`
- `a2a_message_part_from_dict` accepts both A2A 1.0 and legacy text shapes

Conclusion:

```text
A2A operation text parts now use the A2A 1.0 wrapper-object shape while keeping
legacy `kind: text` inbound compatibility. The A2A form remains
primitives-ready because file/data part parity, artifact/event payload parity,
extension negotiation, and external conformance tests are still roadmap work.
```

### Phase 43: A2A File/Data Part Payload Boundary

Target conclusion:

```text
A2A 1.0 interoperability cannot stop at text-only messages. The SDK should
represent file bytes, file URLs, and structured data as first-class A2A message
parts and emit the A2A 1.0 wrapper-object payload shape, while keeping full
artifact/event parity and external conformance testing for later phases.
```

Artifacts:

- `docs/superpowers/specs/2026-06-15-a2a-file-data-part-payload-boundary-design.md`
- `docs/superpowers/plans/2026-06-15-a2a-file-data-part-payload-boundary-implementation-plan.md`
- `A2AMessagePart.from_file_bytes`
- `A2AMessagePart.from_file_url`
- `A2AMessagePart.from_data`
- A2A 1.0 `raw`, `url`, and `data` message part serializer/parser tests
- legacy inbound `kind: file` and `kind: data` compatibility tests

Conclusion:

```text
A2A operation message parts now cover text, file bytes, file URLs, and
structured data using A2A 1.0 wrapper-object payloads, with legacy inbound
compatibility for text/file/data payloads. The A2A form remains
primitives-ready because artifact/event payload parity, extension negotiation,
and external conformance tests are still roadmap work.
```

### Phase 44: A2A Artifact/Event Payload Boundary

Target conclusion:

```text
A2A 1.0 interoperability cannot stop at message parts. The SDK should project
task artifacts and task stream/push events into A2A 1.0 wrapper-object payloads
so peers can consume final outputs and progress updates without knowing
agent-os internal task records.
```

Artifacts:

- `docs/superpowers/specs/2026-06-15-a2a-artifact-event-payload-boundary-design.md`
- `docs/superpowers/plans/2026-06-15-a2a-artifact-event-payload-boundary-implementation-plan.md`
- `A2AArtifact`
- `a2a_artifact_to_dict`
- `a2a_artifact_from_dict`
- `A2ATaskArtifactUpdateEvent`
- `a2a_task_artifact_update_event_to_dict`
- `a2a_task_artifact_update_event_from_dict`
- `statusUpdate` wrapper serialization for task subscription and push payloads

Conclusion:

```text
A2A task artifacts now use protocol artifact objects with `artifactId` and part
wrappers, and task status/artifact events now have SDK-level `statusUpdate` and
`artifactUpdate` wrapper serializers. The A2A form remains primitives-ready
because extension negotiation, external conformance tests, and deployment trust
governance remain roadmap work.
```

### Phase 45: A2A Extension Negotiation Boundary

Target conclusion:

```text
A2A payload parity moves the main interoperability risk from JSON shape to
optional semantics. The SDK must own a narrow extension negotiation boundary so
peers can declare, require, reject, or degrade optional capabilities without
putting hidden assumptions into QueryLoop, team runtime, or application code.
```

Artifacts:

- `docs/superpowers/specs/2026-06-15-a2a-extension-negotiation-boundary-design.md`
- `docs/superpowers/plans/2026-06-15-a2a-extension-negotiation-boundary-implementation-plan.md`
- `A2AExtensionNegotiationPolicy`
- `A2AExtensionNegotiationError`
- `A2AExtensionNegotiationResult`
- outbound `A2A-Extensions` header generation on `A2AOperationClient`
- inbound required-extension validation on `A2AOperationServer`

Conclusion:

```text
A2A operation calls now have SDK-level extension negotiation primitives.
Clients can advertise configured supported extensions declared by peer Agent
Cards, servers can require local Agent Card extensions through A2A-Extensions,
and missing required support returns the A2A -32008 error while optional
unsupported extensions degrade by default. The A2A form remains
primitives-ready because external conformance tests and deployment trust
governance remain roadmap work.
```

### Phase 46: A2A Conformance Harness

Target conclusion:

```text
A2A cannot rely only on unit tests to prove it "looks like the spec". The SDK
needs a stable self-conformance harness that composes Agent Card, JSON-RPC
envelope, protocol version, part wrapper, artifact/event wrapper, and extension
negotiation checks into repeatable reports. Official external suite integration
remains future work.
```

Artifacts:

- `docs/superpowers/specs/2026-06-15-a2a-conformance-harness-design.md`
- `docs/superpowers/plans/2026-06-15-a2a-conformance-harness-implementation-plan.md`
- `A2AConformanceCheck`
- `A2AConformanceFinding`
- `A2AConformanceReport`
- `A2AConformanceHarness`
- `tests/channels/test_a2a_conformance.py`

Conclusion:

```text
A2A discovery/operation support now has a structured SDK self-conformance
report covering Agent Card required fields and extensions, A2A 1.0 message part
wrappers, artifact/status/artifact-event wrapper shapes, JSON-RPC operation
envelopes, protocol version policy, extension negotiation, and the required
extension -32008 error mapping. The A2A form remains primitives-ready because
external conformance suite integration and deployment trust governance remain
roadmap work.
```

### Phase 53: A2A External Conformance Adapter Boundary

Target conclusion:

```text
SDK self-conformance is not enough for production A2A interoperability. The SDK
should not own running official or vendor-specific conformance suites, but it
should own a stable adapter that imports external suite or CI results into the
same A2AConformanceReport evidence model used by agent-os readiness.
```

Artifacts:

- `docs/superpowers/specs/2026-06-15-a2a-external-conformance-adapter-boundary-design.md`
- `docs/superpowers/plans/2026-06-15-a2a-external-conformance-adapter-boundary-implementation-plan.md`
- `A2AExternalConformanceImportError`
- `A2AExternalConformanceReportImporter`
- `A2AConformanceReport` suite metadata fields: `source`, `version`, `run_id`,
  `target`, and `metadata`

Conclusion:

```text
A2A discovery/operation support can now import external conformance result JSON
from CI or deployment-owned suite runs into the SDK conformance report model.
The A2A form remains primitives-ready because SDK-owned external suite
execution, certification policy, and deployment trust governance remain
profile/deployment work.
```

### Phase 54: Distributed Team Profile Preset

Target conclusion:

```text
Distributed team agents should not require every application to hand-wire team
stores, queues, worker session providers, retry/cancellation stores, UI streams,
and worker daemon policies from primitives. The SDK should provide a
production-oriented profile preset that assembles these boundaries and exposes
readiness metadata, while deployment still owns credentials, migrations,
process supervision, worker scaling, and OS/container sandboxing.
```

Artifacts:

- `docs/superpowers/specs/2026-06-15-distributed-team-profile-preset-design.md`
- `docs/superpowers/plans/2026-06-15-distributed-team-profile-preset-implementation-plan.md`
- `DistributedTeamRuntimeProfile`
- runtime profile tests for team runtime/runner/daemon assembly and one worker
  message batch

Conclusion:

```text
Team discussion agents now have a standard SDK profile preset that assembles
TeamRuntime, TeamWorkerRunner, TeamWorkerDaemon, worker session provider,
message queue, retry/cancellation stores, and UI stream into one coherent
boundary with readiness metadata. The form remains primitives-ready because
credential wiring, migration execution, process supervision, worker scaling,
live backend verification, and OS/container sandboxing remain deployment work.
```

### Phase 47: A2A OIDC Claims Auth Boundary

Target conclusion:

```text
A2A peer trust cannot stay at static bearer-token comparisons once agents are
registered across services or tenants. The SDK should provide a narrow JWT/OIDC
claims verification boundary for inbound A2A calls: verify signature, issuer,
audience, time validity, and optional peer allow-list before an operation runs.
Full OIDC discovery metadata and RS256/JWKS public-key rollout, CA policy, DNS
pinning, egress proxying, and tenant RBAC remain deployment-owned.
```

Artifacts:

- `docs/superpowers/specs/2026-06-15-a2a-oidc-claims-auth-boundary-design.md`
- `docs/superpowers/plans/2026-06-15-a2a-oidc-claims-auth-boundary-implementation-plan.md`
- `A2AJwtClaims`
- `A2AJwtVerifier`
- `HmacA2AJwtVerifier`
- `OidcClaimsA2AInboundAuthPolicy`
- inbound operation auth tests for valid claims, issuer/audience/time failures,
  and peer allow-list rejection

Conclusion:

```text
A2A inbound operation auth now has a JWT/OIDC claims boundary for HS256 compact
JWTs, issuer/audience validation, exp/nbf time checks with leeway, and optional
peer-id allow-lists before operation execution. The A2A form remains
primitives-ready because RS256/JWKS JWT verification, CA trust rollout,
DNS/egress controls, credential rotation, and tenant RBAC mapping remain
deployment/profile work.
```

### Phase 48: A2A OIDC Discovery Metadata Boundary

Target conclusion:

```text
A2A JWT claims validation should not require operators to hand-copy issuer
metadata forever. The SDK should own a narrow OIDC discovery metadata boundary
that fetches /.well-known/openid-configuration, requires exact issuer match,
requires an HTTPS jwks_uri, caches metadata, and exposes the JWKS URI to later
JWT verifier phases. RS256/JWKS JWT signature verification, CA policy, DNS
pinning, egress proxying, credential rotation, and tenant RBAC remain
deployment/profile work.
```

Artifacts:

- `docs/superpowers/specs/2026-06-15-a2a-oidc-discovery-boundary-design.md`
- `docs/superpowers/plans/2026-06-15-a2a-oidc-discovery-boundary-implementation-plan.md`
- `A2AOidcDiscoveryError`
- `OidcDiscoveryMetadata`
- `OidcDiscoveryMetadataProvider`
- `tests/channels/test_a2a_oidc_discovery.py`

Conclusion:

```text
A2A auth rollout now has an SDK-owned OIDC discovery metadata provider that
fetches the issuer's OpenID configuration, validates exact issuer match,
requires HTTPS jwks_uri values, caches metadata with TTL, and exposes the JWKS
URI for future public-key verifier phases. The A2A form remains
primitives-ready because RS256/JWKS JWT verification, CA trust rollout,
DNS/egress controls, credential rotation, tenant RBAC mapping, and external
conformance suite integration remain roadmap/deployment work.
```

### Phase 49: A2A RS256 JWKS JWT Verifier Boundary

Target conclusion:

```text
A2A OIDC peer auth should be able to verify public-key JWT signatures without
turning the runtime loop into an identity provider. The SDK should provide a
narrow RS256/JWKS JWT verifier that fetches trusted JWKS metadata, selects an
explicit kid, enforces alg == RS256, verifies the RSA SHA-256 signature, and
returns the existing A2AJwtClaims projection. OIDC discovery metadata can feed
the JWKS URL, while CA policy, DNS pinning, egress proxying, key rotation
governance, and tenant RBAC remain deployment/profile responsibilities.
```

Artifacts:

- `docs/superpowers/specs/2026-06-15-a2a-rs256-jwks-jwt-verifier-design.md`
- `docs/superpowers/plans/2026-06-15-a2a-rs256-jwks-jwt-verifier-implementation-plan.md`
- `JwksA2AJwtVerifier`
- `pyproject.toml` optional `security` extra
- `tests/channels/test_a2a_jwks_jwt_verifier.py`

Conclusion:

```text
A2A inbound OIDC auth now has an SDK-owned RS256/JWKS JWT verifier that uses
configured HTTPS JWKS URLs or OIDC discovery metadata, enforces explicit RS256
and kid selection, ignores unsupported JWKS keys, caches public keys with TTL,
and returns the shared A2AJwtClaims projection for OidcClaimsA2AInboundAuthPolicy.
The A2A form remains primitives-ready because CA trust rollout, DNS/egress
controls, credential rotation, tenant RBAC mapping, supervised webhook workers,
and external conformance suite integration remain roadmap/deployment work.
```

### Phase 50: A2A Egress URL Policy Boundary

Target conclusion:

```text
A2A production deployments should not rely on ad hoc HTTPS checks for outbound
metadata, discovery, key, and peer-operation calls. The SDK should provide one
small, injectable egress URL policy boundary that can be reused by Agent Card
resolution, OIDC discovery, JWKS trust stores, JWT verifiers, operation clients,
and the internal task bridge. DNS pinning, enterprise egress proxies, CA rollout,
credential rotation, and tenant RBAC remain deployment/profile responsibilities.
```

Artifacts:

- `docs/superpowers/specs/2026-06-15-a2a-egress-url-policy-boundary-design.md`
- `docs/superpowers/plans/2026-06-15-a2a-egress-url-policy-boundary-implementation-plan.md`
- `A2AEgressPolicyError`
- `A2AEgressUrlPolicy`
- `PublicHttpsA2AEgressUrlPolicy`
- `HostAllowListA2AEgressUrlPolicy`
- `egress_url_policy` injection for `A2ACardResolver`,
  `OidcDiscoveryMetadataProvider`, `JwksA2ACardTrustStore`,
  `JwksA2AJwtVerifier`, `A2AOperationClient`, and `A2AAdapter`
- `tests/channels/test_a2a_egress_url_policy.py`

Conclusion:

```text
A2A outbound discovery, metadata, JWKS, peer operation, and internal task bridge
calls now have a reusable SDK egress URL policy boundary for public HTTPS and
host/domain allow-list enforcement before transport execution. The A2A form
remains primitives-ready because DNS pinning, enterprise egress proxying, CA
trust rollout, credential rotation, tenant RBAC mapping, supervised webhook
workers, and external conformance suite integration remain deployment/profile
work.
```

### Phase 51: A2A Push Worker Health Boundary

Target conclusion:

```text
A2A push notification delivery should be observable by a deployment supervisor,
not merely runnable as a background loop. The SDK should expose a narrow health
projection over A2APushNotificationDaemonState so deployments can classify a
worker as healthy, degraded, unhealthy, stopped, stale, or unstarted without
coupling QueryLoop to webhook delivery.
```

Artifacts:

- `docs/superpowers/specs/2026-06-15-a2a-push-worker-health-boundary-design.md`
- `docs/superpowers/plans/2026-06-15-a2a-push-worker-health-boundary-implementation-plan.md`
- `A2APushNotificationHealthStatus`
- `A2APushNotificationHealthReport`
- `A2APushNotificationHealthPolicy`
- `A2APushNotificationDaemon.health(...)`
- daemon health tests in `tests/channels/test_a2a_push_notification_daemon.py`

Conclusion:

```text
A2A push notification workers now expose a supervisor-friendly health projection
that classifies daemon state from polling recency and recent errors. The A2A
form remains primitives-ready because deployment-owned process supervision,
restart/alerting policy, health endpoint wiring, DNS/egress governance, CA
rollout, tenant RBAC mapping, and external conformance suite integration remain
outside the SDK core.
```

### Phase 52: A2A Push Worker Deployment Health Profile

Target conclusion:

```text
A2A push notification workers should not only expose an internal health
projection. The SDK should provide a narrow deployment-facing profile that turns
daemon health into JSON-safe health/readiness checks for ASGI hosts and service
supervisors. The SDK should not own process supervision, restart policy,
alerting, credentials, DNS pinning, enterprise egress proxying, CA rollout,
tenant RBAC, or external conformance execution.
```

Artifacts:

- `docs/superpowers/specs/2026-06-15-a2a-push-worker-deployment-health-profile-design.md`
- `docs/superpowers/plans/2026-06-15-a2a-push-worker-deployment-health-profile-implementation-plan.md`
- `A2APushNotificationDeploymentProfile`
- `A2APushNotificationDeploymentProfile.health_payload(...)`
- `A2APushNotificationDeploymentProfile.health_check(...)`
- `A2APushNotificationDeploymentProfile.readiness_check(...)`
- `A2APushNotificationDeploymentProfile.readiness_metadata()`
- ASGI `/v1/ready` wiring test in `tests/channels/test_asgi_app.py`

Conclusion:

```text
A2A push notification workers now have a deployment-facing profile that
serializes daemon health into stable probe payloads and can be plugged directly
into AsgiAgentApp(readiness_checks=...). The A2A form remains primitives-ready
because process supervision, restart/alerting policy, credentials, DNS/egress
governance, CA rollout, tenant RBAC mapping, and external conformance suite
execution remain deployment/profile work.
```

### Phase 53: A2A Tenant RBAC Inbound Auth Boundary

Target conclusion:

```text
A2A enterprise deployments need tenant-aware authorization after peer identity
is authenticated. The SDK should provide a narrow claims-backed tenant RBAC
policy mapping verified JWT/OIDC claims to tenant, role, and scope attributes,
then authorizing operations and resources through declarative rules. The SDK
should not own tenant directory administration, role assignment lifecycle, IdP
administration, credential rotation, or organization sync.
```

Artifacts:

- `docs/superpowers/specs/2026-06-15-a2a-tenant-rbac-inbound-auth-boundary-design.md`
- `docs/superpowers/plans/2026-06-15-a2a-tenant-rbac-inbound-auth-boundary-implementation-plan.md`
- `A2ATenantRbacRule`
- `ClaimsTenantRbacA2AInboundAuthPolicy`
- tenant/role/scope claim extraction with custom claim-name support
- wildcard operation and resource matching
- operation/resource auth tests in `tests/channels/test_a2a_operations.py`

Conclusion:

```text
A2A inbound peer auth now has a claims-backed tenant RBAC policy boundary that
can authorize message, task, and push-config operations by verified tenant,
role, scope, and resource claims. The A2A form remains primitives-ready because
tenant directories, role assignment lifecycle, IdP administration, credential
rotation, CA rollout, DNS/egress governance, supervised webhook workers, and
external conformance suite execution remain deployment/profile work.
```

### Phase 54: A2A Bearer Credential Rotation Boundary

Target conclusion:

```text
A2A production credential rotation should not require applications to manually
wire old and new bearer tokens across every auth provider and inbound policy.
The SDK should provide a narrow bearer credential rotation boundary: outbound
calls use the current active token, inbound authorization accepts active
overlap tokens, expired/not-yet-valid/revoked tokens are rejected, and secrets
are redacted from diagnostics. Secret generation, KMS or secret-manager
distribution, approval workflow, audit policy, CA trust, and DNS/egress
governance remain deployment-owned.
```

Artifacts:

- `docs/superpowers/specs/2026-06-15-a2a-bearer-credential-rotation-boundary-design.md`
- `docs/superpowers/plans/2026-06-15-a2a-bearer-credential-rotation-boundary-implementation-plan.md`
- `A2ABearerCredential`
- `A2ACredentialRotationError`
- `RotatingBearerA2ACredentialStore`
- `RotatingBearerA2AAuthProvider`
- `RotatingBearerA2AInboundAuthPolicy`
- outbound current credential tests in `tests/channels/test_a2a_operations.py`
- inbound overlap/rejection and operation/resource authorization tests in `tests/channels/test_a2a_operations.py`

Conclusion:

```text
A2A bearer peer auth now has an SDK-owned credential rotation boundary that
selects the current outbound bearer token and accepts overlapping active
inbound tokens while keeping expired, future, revoked, malformed, unknown, or
disallowed peer tokens behind a generic unauthorized error. The A2A form remains
primitives-ready because credential issuance, secret distribution,
KMS/secret-manager governance, CA rollout, DNS/egress governance, tenant
directory lifecycle, supervised webhook workers, and external conformance suite
execution remain deployment/profile work.
```

### Phase 55: A2A Per-Peer Rate Limit Boundary

Target conclusion:

```text
Public A2A services cannot rely only on global ASGI rate limiting. The SDK
should provide an operation-layer rate-limit boundary that runs after peer
authorization and before runner/store work, keyed by authenticated peer,
operation, task, and resource context. Distributed/global quota storage,
gateway enforcement, billing tiers, and commercial entitlement policy remain
deployment-owned.
```

### Phase 88: Sandbox / Workspace Backend Adapter Boundary

Target conclusion:

```text
Future production agents need a pluggable workspace execution backend for
local tools, generated subagent work, and Docker/E2B/enterprise sandbox
adapters. AgentOS should own the stable request/result/policy/protocol shape
and a local reference adapter, while real isolation kernels, container images,
network policy, resource enforcement, image patching, and live backend
verification remain deployment-owned.
```

Artifacts:

- `docs/superpowers/specs/2026-06-16-sandbox-workspace-backend-boundary-design.md`
- `docs/superpowers/plans/2026-06-16-sandbox-workspace-backend-boundary-implementation-plan.md`
- `WorkspaceExecutionRequest`
- `WorkspaceExecutionResult`
- `WorkspaceExecutionPolicy`
- `WorkspaceExecutionBackend`
- `SandboxBackend`
- `LocalWorkspaceExecutionBackend`
- argv-only local execution contract
- JSON-safe execution evidence fields: `backend`, `workspace_id`,
  `workspace_scope`, `command`, `capability`, `cwd`, `exit_code`,
  `started_at`, `finished_at`, `timed_out`, stdout/stderr byte counts,
  metadata, and `env_keys`
- public API exports from `agentos.workspace` and top-level `agentos`
- readiness, production docs, objective coverage audit, roadmap, and agent-os
  skill guidance updates

Conclusion:

```text
`LocalWorkspaceExecutionBackend` now provides the SDK's local reference
`WorkspaceExecutionBackend`. It runs argv tuples with no shell parsing, checks
cwd containment against the `WorkspaceHandle` root, applies capability
allow-list policy, captures stdout/stderr for the caller, and emits JSON-safe
execution evidence while redacting environment values down to `env_keys`. It is
not a production isolation boundary. Docker/E2B/enterprise runner adapters,
container or microVM isolation, filesystem mount policy, network egress policy,
CPU and memory limits, secret injection, sandbox image/runtime patching,
production audit storage, and live sandbox backend verification remain
deployment-owned.
```

Artifacts:

- `docs/superpowers/specs/2026-06-16-a2a-per-peer-rate-limit-boundary-design.md`
- `docs/superpowers/plans/2026-06-16-a2a-per-peer-rate-limit-boundary-implementation-plan.md`
- `A2APeerIdResolver`
- `A2AOperationRateLimitPolicy`
- `A2ARateLimitError`
- `PeerKeyA2AOperationRateLimitPolicy`
- `A2AOperationServer(rate_limit_policy=...)`
- A2A operation tests for per-peer `message/send` throttling and operation/task/resource limiter keys

Conclusion:

```text
A2A operation calls now have an SDK-owned local per-peer rate-limit boundary.
After version/extension negotiation and inbound auth succeed,
A2AOperationServer can ask an injected policy to throttle operation calls by
peer, operation, task, and resource context before runner or persistence work
starts. The A2A form remains primitives-ready because distributed/global quota
storage, gateway enforcement, billing tiers, commercial entitlement policy,
credential issuance, CA rollout, DNS/egress governance, tenant directory
lifecycle, supervised webhook workers, and external conformance suite execution
remain deployment/profile work.
```

### Phase 56: A2A Task Resubscribe Operation Boundary

Target conclusion:

```text
Task subscription is a protocol operation, not just an ASGI route. Public A2A
services should expose task resubscribe through the same operation boundary used
for message, task, push config, auth, extension negotiation, protocol version
checks, and per-peer rate limiting. The SDK should own a narrow
`tasks/resubscribe` operation primitive that returns the next task subscription
event for a task cursor. Long-lived SSE connection management, backpressure,
fan-out, and gateway quota policy remain transport/deployment-owned.
```

Artifacts:

- `docs/superpowers/specs/2026-06-16-a2a-task-resubscribe-boundary-design.md`
- `docs/superpowers/plans/2026-06-16-a2a-task-resubscribe-boundary-implementation-plan.md`
- `A2AOperationRequest.task_resubscribe(...)`
- `A2AOperationResponse(task_event=...)`
- `A2AOperationServer.handle_task_resubscribe(...)`
- JSON-RPC `tasks/resubscribe` dispatch in `A2AOperationServer.handle_operation(...)`
- ASGI `POST /a2a/tasks/{id}:subscribe` reuse of the task subscribe operation boundary before SSE starts
- operation and ASGI tests for auth/rate-limit boundary reuse

Conclusion:

```text
A2A task subscription now has an SDK-owned operation boundary. JSON-RPC
`tasks/resubscribe` and the ASGI subscribe route both reuse A2AOperationServer
version checks, extension negotiation, inbound auth, resource authorization,
and per-peer rate limiting before task lifecycle reads or SSE response start.
The A2A form remains primitives-ready because long-lived stream fan-out,
backpressure, gateway/global quota stores, billing policy, credential issuance,
CA rollout, DNS/egress governance, tenant directory lifecycle, supervised
webhook workers, and external conformance suite execution remain
deployment/profile work.
```

### Phase 57: A2A Message Stream Operation Boundary

Target conclusion:

```text
A2A streaming should not be an ad hoc transport feature. The SDK should expose
a narrow `message/stream` operation boundary that starts work through the same
protocol version, extension negotiation, inbound peer authorization, resource
authorization, trace propagation, and per-peer rate-limit controls as
`message/send`, then returns an initial stream projection suitable for ASGI SSE
transport. Long-running stream fan-out, backpressure, durable stream cursor
storage, and gateway/global quota enforcement remain deployment-owned.
```

Artifacts:

- `docs/superpowers/specs/2026-06-16-a2a-message-stream-boundary-design.md`
- `docs/superpowers/plans/2026-06-16-a2a-message-stream-boundary-implementation-plan.md`
- `A2AOperationRequest.message_stream(...)`
- JSON-RPC `message/stream` dispatch in `A2AOperationServer.handle_operation(...)`
- `A2AOperationServer.handle_message_stream(...)`
- ASGI `POST /a2a/message:stream` route with initial `event: task` SSE payload
- ASGI message-stream follow-up through `tasks/resubscribe` when task lifecycle is configured
- operation and ASGI tests for auth/rate-limit boundary reuse and SSE initiation

Conclusion:

```text
A2A message streaming now has an SDK-owned operation boundary. JSON-RPC
`message/stream` reuses A2AOperationServer protocol version checks, extension
negotiation, inbound auth, trace propagation, and per-peer rate limiting before
runner work starts. The ASGI `/a2a/message:stream` route returns JSON operation
errors before SSE starts, emits an initial task event after successful
operation handling, and follows task updates through `tasks/resubscribe` when a
task lifecycle runner is available. The A2A form remains primitives-ready
because long-lived stream fan-out, backpressure, durable cursor storage,
gateway/global quota stores, billing policy, credential issuance, CA rollout,
DNS/egress governance, tenant directory lifecycle, supervised webhook workers,
and external conformance suite execution remain deployment/profile work.
```

### Phase 58: A2A Message Stream Client Boundary

Target conclusion:

```text
Outbound A2A clients should be able to initiate `message/stream` through the
same Agent Card URL, auth provider, protocol version header, extension
negotiation, and egress URL policy as `message/send`. The SDK should own the
initial operation request and response boundary, while long-lived SSE
consumption, retry, backpressure, durable cursor handling, fan-out, and gateway
quota remain transport/deployment-owned.
```

Artifacts:

- `docs/superpowers/specs/2026-06-16-a2a-message-stream-client-boundary-design.md`
- `docs/superpowers/plans/2026-06-16-a2a-message-stream-client-boundary-implementation-plan.md`
- `A2AOperationClient.stream_message(...)`
- operation client tests for `/message:stream`, protocol version headers,
  trace headers, extension negotiation, required-extension pre-network
  rejection, and egress URL policy enforcement
- readiness, production docs, and agent-os skill guidance updates

Conclusion:

```text
A2A message streaming now has a symmetric outbound client initiation boundary.
`A2AOperationClient.stream_message(...)` builds `message/stream` JSON-RPC
requests, posts to `/message:stream`, applies egress URL policy before
transport, sends the default or caller-overridden A2A protocol version header,
injects configured auth provider headers, negotiates A2A extensions, and parses
the initial operation response. The A2A form remains primitives-ready because
SSE client consumption, reconnect/backpressure policy, durable stream cursors,
fan-out, gateway/global quota stores, billing policy, credential issuance, CA
rollout, DNS/egress governance, tenant directory lifecycle, supervised webhook
workers, and external conformance suite execution remain deployment/profile
work.
```

### Phase 59: A2A Message Stream Client Events Boundary

Target conclusion:

```text
A2A SDK clients should consume peer `text/event-stream` responses as typed
protocol events through the same Agent Card URL, auth provider, protocol
version header, extension negotiation, and egress URL policy used by
`message/stream` initiation. The SDK should own a narrow SSE parser and typed
client consumption boundary; reconnect loops, durable cursors, fan-out,
backpressure, gateway quota, billing, and credential issuance remain
deployment/transport-owned.
```

Artifacts:

- `docs/superpowers/specs/2026-06-16-a2a-message-stream-client-events-design.md`
- `docs/superpowers/plans/2026-06-16-a2a-message-stream-client-events-implementation-plan.md`
- `A2AMessageStreamEvent`
- `parse_a2a_sse_events(...)`
- `a2a_message_stream_event_from_dict(...)`
- `A2AOperationClient.stream_message_events(...)`
- `A2ATransport.post_sse(...)` and `UrllibA2ATransport.post_sse(...)`
- parser/client tests for typed task, message, status update, artifact update,
  comment skipping, multi-line data, auth/version/extension headers,
  required-extension pre-network rejection, and egress URL policy enforcement
- readiness, production docs, and agent-os skill guidance updates

Conclusion:

```text
A2A outbound message streaming now has a typed client-side SSE event
consumption boundary. `A2AOperationClient.stream_message_events(...)` posts
`message/stream` with the same protocol version, extension negotiation, auth
provider, trace propagation, and egress URL policy as `stream_message(...)`,
then parses peer `text/event-stream` chunks into `A2AMessageStreamEvent`
values through `parse_a2a_sse_events(...)`. The A2A form remains
primitives-ready because reconnect/backpressure policy, durable stream cursor
storage, fan-out, gateway/global quota stores, billing policy, credential
issuance, CA rollout, DNS/egress governance, tenant directory lifecycle,
supervised webhook workers, and external conformance suite execution remain
deployment/profile work.
```

### Phase 60: A2A Message Stream Conformance Boundary

Target conclusion:

```text
A2A self-conformance should track the SDK protocol surface that deployments are
told to rely on. After `message/stream` server, outbound client initiation, and
typed SSE client event primitives exist, the SDK-owned self-conformance harness
should check those primitives too. External conformance suite execution,
certification, stream reconnect, backpressure, durable cursors, fan-out, gateway
quota, billing, and credential issuance remain deployment/profile work.
```

Artifacts:

- `docs/superpowers/specs/2026-06-16-a2a-message-stream-conformance-boundary-design.md`
- `docs/superpowers/plans/2026-06-16-a2a-message-stream-conformance-boundary-implementation-plan.md`
- `message-stream-jsonrpc-envelope` self-conformance check
- `message-stream-event-shape` self-conformance check
- `A2AConformanceHarness.run(stream_operation_request_payload=...)`
- `A2AConformanceHarness.run(stream_event_payload=...)`
- conformance tests for passing stream checks, non-stream operation failures,
  and malformed stream event failures
- readiness, production docs, and agent-os skill guidance updates

Conclusion:

```text
A2A self-conformance now includes the stream protocol surface added in recent
phases. `A2AConformanceHarness` verifies a `message/stream` JSON-RPC request
envelope and parses a representative SSE payload into a typed message stream
event, while preserving external conformance suite execution and long-running
stream lifecycle policy as deployment/profile responsibilities.
```

### Phase 61: A2A Task Resubscribe Client Boundary

Target conclusion:

```text
A2A outbound clients should support one-shot `tasks/resubscribe` cursor
requests through the same Agent Card URL, auth provider, protocol version
header, extension negotiation, trace propagation, and egress URL policy used by
`message/send` and `message/stream`. The SDK owns the single request/response
boundary only; automatic reconnect loops, durable cursor storage, fan-out,
backpressure, gateway quota, billing, and credential issuance remain
deployment/transport-owned.
```

Artifacts:

- `docs/superpowers/specs/2026-06-16-a2a-task-resubscribe-client-boundary-design.md`
- `docs/superpowers/plans/2026-06-16-a2a-task-resubscribe-client-boundary-implementation-plan.md`
- `A2AOperationClient.task_resubscribe(...)`
- operation client tests for `/tasks/{task_id}:subscribe`, JSON-RPC
  `tasks/resubscribe` payloads, trace/protocol headers, required-extension
  pre-network rejection, and egress URL policy enforcement
- readiness, production docs, and agent-os skill guidance updates

Conclusion:

```text
A2A outbound clients now have a one-shot task resubscribe boundary.
`A2AOperationClient.task_resubscribe(...)` builds `tasks/resubscribe`
JSON-RPC requests, posts to `/tasks/{task_id}:subscribe`, applies egress URL
policy before transport, sends the default or caller-overridden A2A protocol
version header, injects configured auth/trace headers, negotiates A2A
extensions, and parses the typed task subscription response. The A2A form
remains primitives-ready because automatic reconnect loops, durable cursor
storage, fan-out, backpressure, gateway/global quota stores, billing policy,
credential issuance, CA rollout, DNS/egress governance, tenant directory
lifecycle, supervised webhook workers, and external conformance suite
execution remain deployment/profile work.
```

### Phase 62: A2A Task Resubscribe Conformance Boundary

Target conclusion:

```text
A2A self-conformance should track the SDK protocol surface that deployments are
told to rely on. After `tasks/resubscribe` exists on the server and outbound
client boundary, the SDK-owned self-conformance harness should verify the
JSON-RPC request envelope and the `statusUpdate` task subscription event shape.
External conformance suite execution, automatic reconnect loops, durable cursor
storage, fan-out, backpressure, gateway quota, billing, and credential issuance
remain deployment/profile work.
```

Artifacts:

- `docs/superpowers/specs/2026-06-16-a2a-task-resubscribe-conformance-boundary-design.md`
- `docs/superpowers/plans/2026-06-16-a2a-task-resubscribe-conformance-boundary-implementation-plan.md`
- `task-resubscribe-jsonrpc-envelope` self-conformance check
- `task-resubscribe-event-shape` self-conformance check
- `A2AConformanceHarness.run(task_resubscribe_request_payload=...)`
- `A2AConformanceHarness.run(task_resubscribe_event_payload=...)`
- conformance tests for passing default checks, non-resubscribe request
  failures, and malformed event failures
- readiness, production docs, and agent-os skill guidance updates

Conclusion:

```text
A2A self-conformance now includes the task resubscribe protocol surface.
`A2AConformanceHarness` verifies a `tasks/resubscribe` JSON-RPC request
envelope and parses a representative `statusUpdate` task subscription event,
while preserving external conformance suite execution and stream lifecycle
policy as deployment/profile responsibilities.
```

### Phase 63: A2A Stream Lifecycle Profile Boundary

Target conclusion:

```text
A2A streaming/task-resubscribe/push are composed from narrow SDK operation
boundaries, but production deployments need an explicit stream lifecycle
contract. The SDK should expose a JSON-safe deployment profile naming required
lifecycle components and deployment-owned responsibilities for reconnect
policy, durable cursor storage, fan-out, backpressure, quota, billing,
credential issuance, supervision, egress governance, CA rollout, tenant
directory lifecycle, and external conformance execution. It must not implement
those loops or stores in `QueryLoop`, `AsyncQueryLoop`, or the A2A client.
```

Artifacts:

- `docs/superpowers/specs/2026-06-16-a2a-stream-lifecycle-profile-boundary-design.md`
- `docs/superpowers/plans/2026-06-16-a2a-stream-lifecycle-profile-boundary-implementation-plan.md`
- `A2AStreamLifecycleDeploymentProfile`
- JSON-safe stream lifecycle readiness metadata and ASGI-compatible
  `readiness_check()`
- public API exports from `agentos.channels` and top-level `agentos`
- readiness, production docs, and agent-os skill guidance updates

Conclusion:

```text
A2A stream lifecycle readiness is now explicit without moving deployment-owned
stream machinery into SDK core. `A2AStreamLifecycleDeploymentProfile` reports
configured and missing lifecycle components, identifies SDK-owned operation and
parser primitives, and keeps automatic reconnect loops, durable cursor storage,
fan-out, backpressure, gateway quota, billing, credential issuance, process
supervision, DNS pinning, enterprise egress proxy, CA rollout, tenant directory
lifecycle, and external conformance execution as deployment responsibilities.
```

### Phase 64: Planner Orchestration Profile Boundary

Target conclusion:

```text
Planner and intent-router agents should be production-plannable without turning
`PlannerRuntime` or `QueryLoop` into a long-running workflow engine. The SDK
should expose a deployment-facing profile that names SDK-owned planner
primitives and the deployment-owned orchestration components required for
production plan-and-execute systems: LLM decomposition policy, DAG scheduler,
worker dispatch loop, compensation policy, plan store, worker supervision, and
operational governance.
```

Artifacts:

- `docs/superpowers/specs/2026-06-16-planner-orchestration-profile-boundary-design.md`
- `docs/superpowers/plans/2026-06-16-planner-orchestration-profile-boundary-implementation-plan.md`
- `PlannerOrchestrationDeploymentProfile`
- JSON-safe planner orchestration readiness metadata and ASGI-compatible
  `readiness_check()`
- public API exports from `agentos.multi` and top-level `agentos`
- readiness, production docs, and agent-os skill guidance updates

Conclusion:

```text
Planner production orchestration readiness is now explicit without adding a
scheduler loop to SDK core. `PlannerOrchestrationDeploymentProfile` reports
configured and missing orchestration components, identifies SDK-owned planner
state/tool/dispatch/retry primitives, and keeps automatic LLM decomposition
policy, production DAG scheduler, worker dispatch loop, compensation
orchestration, worker process lifecycle, live backend verification, migration
execution, credentials and secret distribution, and OS/container sandboxing as
deployment responsibilities.
```

### Phase 65: Workspace Execution Isolation Profile Boundary

Target conclusion:

```text
Workspace-aware agents need a production-facing isolation contract that does not
confuse SDK pre-execution checks with real process/container sandboxing. The SDK
should expose a deployment profile that states configured workspace isolation
components, SDK-owned protections, and deployment-owned protections for
terminal, web, team, and planner/subagent execution shapes.
```

Artifacts:

- `docs/superpowers/specs/2026-06-16-workspace-execution-isolation-profile-boundary-design.md`
- `docs/superpowers/plans/2026-06-16-workspace-execution-isolation-profile-boundary-implementation-plan.md`
- `WorkspaceExecutionIsolationProfile`
- JSON-safe workspace execution isolation readiness metadata and
  ASGI-compatible `readiness_check()`
- public API export from top-level `agentos`
- readiness, production docs, architecture skill, agent-forms skill, and
  multi-agent skill guidance updates

Conclusion:

```text
Workspace execution isolation readiness is now explicit without pretending the
SDK provides a secure process or container sandbox. `WorkspaceExecutionIsolationProfile`
reports configured and missing isolation components, identifies SDK-owned
workspace handles, policies, scope narrowing, path escape pre-check, and tool
capability pre-check, and keeps OS/container sandboxing, process isolation,
filesystem mount policy, network egress policy, CPU and memory limits, secret
redaction, audit logging backend, sandbox image/runtime patching, and live
sandbox backend verification as deployment responsibilities.
```

### Phase 66: Distributed Web Session Operations Profile Boundary

Target conclusion:

```text
Distributed web agents already have durable session hydration primitives, but
production readiness needs a separate operations contract. The SDK should expose
a deployment profile that states configured distributed-session operations
components, SDK-owned hydrate/save/lease primitives, and deployment-owned
responsibilities for credentials, migrations, TTL/stale recovery, auth/tenant
integration, workspace policy, live backend verification, rollout, and alerting.
```

Artifacts:

- `docs/superpowers/specs/2026-06-16-distributed-web-session-operations-profile-boundary-design.md`
- `docs/superpowers/plans/2026-06-16-distributed-web-session-operations-profile-boundary-implementation-plan.md`
- `DistributedWebSessionOperationsProfile`
- JSON-safe distributed web session operations readiness metadata and
  ASGI-compatible `readiness_check()`
- public API exports from `agentos.runtime` and top-level `agentos`
- readiness, production docs, architecture skill, agent-forms skill, and
  persistence skill guidance updates

Conclusion:

```text
Distributed web session operations readiness is now explicit without adding a
stale-lease sweeper, migration runner, credential manager, or recovery loop to
SDK core. `DistributedWebSessionOperationsProfile` reports configured and
missing operations components, identifies SDK-owned distributed web session
primitives, and keeps Redis/Postgres credentials, migration execution, lease TTL
tuning, stale lease recovery policy, auth and tenant integration, workspace
policy configuration, live backend verification, rollout/rollback, and alerting
as deployment responsibilities.
```

### Phase 67: AgentOS Objective Coverage Audit

Target conclusion:

```text
AgentOS now needs a stable objective coverage ledger more than another isolated
primitive. The SDK already has terminal, web, distributed web session, A2A,
team, planner, workspace, and skill guidance evidence, but the original goal
must remain auditable across future phases. A coverage audit should map each
objective area to current evidence, remaining production blockers, and next
priority phases without declaring the long-running goal complete.
```

Artifacts:

- `docs/superpowers/specs/2026-06-16-agentos-objective-coverage-audit-design.md`
- `docs/superpowers/plans/2026-06-16-agentos-objective-coverage-audit-implementation-plan.md`
- `docs/agentos-objective-coverage-audit.md`
- production readiness link to the objective coverage audit
- docs test coverage for original objective areas, evidence pointers,
  blockers, and this roadmap entry

Conclusion:

```text
Overall completion estimate: 96%. Terminal/script and single-node async web
agents are direct. Distributed web hydration, A2A discovery/operations, team
discussion, planner/intent routing, workspace expansion, and SDK skill guidance
are primitives-ready with explicit deployment-owned blockers. The long-running
goal remains active because external conformance execution, automatic LLM
decomposition policy governance, LLM prompt/model/approval/evaluation policy,
production DAG scheduler, worker process lifecycle execution, OS/container sandboxing,
credential issuance and secret distribution, and live backend verification still
require further SDK or deployment-profile work.
```

### Phase 68: A2A External Conformance Execution Profile

Target conclusion:

```text
A2A interoperability cannot rely only on SDK self-conformance, but the SDK
should not embed or claim an external certification runner. AgentOS should
expose a deployment-facing `A2AExternalConformanceExecutionProfile` that consumes
imported external conformance reports, checks required A2A check ids and
deployment components, and emits JSON-safe readiness metadata without making a
certification claim.
```

Artifacts:

- `docs/superpowers/specs/2026-06-16-a2a-external-conformance-execution-profile-design.md`
- `docs/superpowers/plans/2026-06-16-a2a-external-conformance-execution-profile-implementation-plan.md`
- `A2AExternalConformanceExecutionProfile`
- required external check ids: `agent-card`, `message-send`, `message-stream`,
  `tasks-resubscribe`, `push-notification-config`
- required deployment components: `external_suite_runner`, `target_endpoint`,
  `credential_policy`, `network_egress_policy`, `version_matrix`,
  `ci_artifact_retention`, `failure_alerting`
- JSON-safe readiness metadata and ASGI-compatible `readiness_check()`
- public API exports from `agentos.channels` and top-level `agentos`
- readiness, production docs, objective coverage audit, and agent-os skill
  guidance updates

Conclusion:

```text
A2A external conformance readiness now has an SDK-owned evidence profile without
turning AgentOS into an external suite runner or certification authority.
`A2AExternalConformanceExecutionProfile` gates imported reports against required
checks and configured deployment components, preserves non-required vendor
checks as report metadata, and keeps suite execution automation, target
environment setup, credentials/secrets, network egress and CA policy, version
matrix governance, CI artifact retention, release gating, failure alerting, and
certification attestation deployment-owned.
```

### Phase 69: Planner Scheduler Tick Boundary

Target conclusion:

```text
Planner and intent-router agents need a production-shaped scheduling primitive,
but AgentOS should not turn `PlannerRuntime`, `QueryLoop`, or `AsyncQueryLoop`
into a long-running workflow engine. The SDK should expose one bounded
`scheduler_tick` operation that deployment-owned daemons, cron jobs, or
scheduler agents can call to reset due retries and dispatch dependency-ready
steps through the existing coordinator boundary.
```

Artifacts:

- `docs/superpowers/specs/2026-06-16-planner-scheduler-tick-boundary-design.md`
- `docs/superpowers/plans/2026-06-16-planner-scheduler-tick-boundary-implementation-plan.md`
- `PlanSchedulerRetryReset`
- `PlanSchedulerTickReport`
- `PlannerRuntime.scheduler_tick(...)`
- `PlannerTools` tool: `plan_scheduler_tick`
- public API exports from `agentos.multi` and top-level `agentos`
- readiness, production docs, objective coverage audit, and agent-os skill
  guidance updates

Conclusion:

```text
Planner scheduler work now has an SDK-owned one-shot boundary without adding a
long-running scheduler loop to SDK core. `PlannerRuntime.scheduler_tick(...)`
resets due retryable steps before bounded ready-step dispatch and returns a
`PlanSchedulerTickReport` that combines retry reset metadata with the normal
`PlanDispatchReport`. Deployment code still owns polling, distributed locking,
worker dispatch loop supervision, worker process lifecycle, automatic LLM
decomposition policy, compensation orchestration, credentials, migrations, live
backend verification, and OS/container sandboxing.
```

### Phase 70: Worker Process Lifecycle Profile Boundary

Target conclusion:

```text
Team and planner workers need a production-facing worker process lifecycle
contract, but AgentOS should not become a process supervisor. The SDK should
expose a JSON-safe readiness profile that names configured lifecycle components
and deployment-owned gaps while keeping actual supervisor/systemd/Kubernetes,
job runner, restart, drain, scaling, credential, migration, and live backend
operations outside SDK core.
```

Artifacts:

- `docs/superpowers/specs/2026-06-16-worker-process-lifecycle-profile-boundary-design.md`
- `docs/superpowers/plans/2026-06-16-worker-process-lifecycle-profile-boundary-implementation-plan.md`
- `WorkerProcessLifecycleDeploymentProfile`
- required deployment components: `process_supervisor`, `restart_policy`,
  `graceful_shutdown`, `health_probe`, `readiness_probe`, `scaling_policy`,
  `credential_policy`, `migration_policy`, `alerting`,
  `live_backend_verification`
- JSON-safe readiness metadata and ASGI-compatible `readiness_check()`
- public API exports from `agentos.runtime` and top-level `agentos`
- readiness, production docs, objective coverage audit, and agent-os skill
  guidance updates

Conclusion:

```text
Worker lifecycle readiness now has an SDK-owned profile without turning
AgentOS into a process manager. `WorkerProcessLifecycleDeploymentProfile`
reports configured and missing lifecycle components, names SDK-owned worker
primitives such as `TeamWorkerRunner`, `TeamWorkerDaemon`,
`PlannerRuntime.scheduler_tick`, and `PlanSchedulerTickReport`, and keeps the
process supervisor or job runner, restart policy, graceful shutdown and
draining, horizontal scaling policy, credentials, migrations, live backend
verification, endpoint wiring, alert routing/runbooks, and OS/container
sandboxing deployment-owned.
```

### Phase 71: Planner Decomposition Policy Boundary

Target conclusion:

```text
Planner and intent-router agents need an SDK-owned gate for LLM-produced plan
decompositions, but AgentOS should not own prompt policy, model routing,
autonomous planning loops, or human approvals. The SDK should validate
structured `PlanDecomposition` proposals before they become persisted
`PlanState` records and expose readiness metadata for the deployment policies
that govern automatic LLM decomposition.
```

Artifacts:

- `docs/superpowers/specs/2026-06-16-planner-decomposition-policy-boundary-design.md`
- `docs/superpowers/plans/2026-06-16-planner-decomposition-policy-boundary-implementation-plan.md`
- `PlanDecompositionValidationReport`
- `PlannerRuntime.validate_decomposition(...)`
- `PlannerDecompositionPolicyDeploymentProfile`
- required deployment components: `prompt_policy`, `output_schema`,
  `validation_gate`, `template_mapping_policy`, `approval_policy`,
  `model_routing_policy`, `evaluation_policy`, `trace_logging`, and
  `rollback_policy`
- JSON-safe validation and readiness metadata
- public API exports from `agentos.multi` and top-level `agentos`
- readiness, production docs, objective coverage audit, and agent-os skill
  guidance updates

Conclusion:

```text
Planner decomposition now has an SDK-owned validation/profile boundary without
turning AgentOS into an automatic LLM planner. `PlannerRuntime.validate_decomposition(...)`
returns `PlanDecompositionValidationReport` without mutating `PlanStore`, so
LLM or intent-router proposals can be checked for objective, step, template,
dependency, duplicate-id, and cycle errors before plan creation.
`PlannerDecompositionPolicyDeploymentProfile` reports configured and missing
automatic-decomposition policy components while keeping prompt/model/approval/
evaluation policy, retrieval/tool grounding, rollout/rollback, and live backend
verification deployment-owned.
```

### Phase 72: Planner Scheduler Daemon Boundary

Target conclusion:

```text
Planner and intent-router agents need a production-shaped scheduler polling
loop, but AgentOS should not become a workflow engine. The SDK should provide a
small `PlannerSchedulerDaemon` that repeatedly invokes
`PlannerRuntime.scheduler_tick(...)` for explicitly supplied plan ids, records
daemon state, and leaves plan discovery, distributed scheduler locks, process
supervision, credentials, migrations, and compensation orchestration
deployment-owned.
```

### Phase 89: Nacos Registry Adapter Boundary

Target conclusion:

```text
AgentOS needs a production registry/discovery adapter boundary for AgentCard,
A2A endpoint, capabilities, version, worker service metadata, and health
metadata. Nacos may be used for registration and discovery metadata, but it
must not become task truth, plan truth, session snapshot storage, message
queue, or worker runtime state.
```

Artifacts:

- `docs/superpowers/specs/2026-06-16-nacos-registry-adapter-boundary-design.md`
- `docs/superpowers/plans/2026-06-16-nacos-registry-adapter-boundary-implementation-plan.md`
- `NacosRegistryClient`
- `NacosRegistryConfig`
- `NacosRegistryEvidence`
- `NacosRegistryError`
- `NacosAgentRegistryAdapter`
- `NacosAgentCardResolver`
- `agent_card_to_nacos_metadata(...)`
- `nacos_instance_to_agent_card(...)`
- public API exports from `agentos.registry` and top-level `agentos`
- readiness, production docs, objective coverage audit, roadmap, and agent-os
  skill guidance updates

Conclusion:

```text
`NacosAgentRegistryAdapter` now projects AgentCard values into discovery-only
Nacos service metadata with endpoint, capabilities, version, worker service,
and health fields. `NacosAgentCardResolver` resolves and discovers healthy
Nacos instances with capability filtering, and `NacosRegistryEvidence` records
JSON-safe registration, deregistration, and discovery evidence without
credentials or environment values. Nacos remains a discovery plane only: it is
not task truth, not plan truth, not session snapshot storage, not message
queue, and not worker runtime state. Concrete Nacos client construction,
credentials, TLS/auth, tenant namespace policy, service naming, live backend
verification, alerting, and runbooks remain deployment-owned.
```

Artifacts:

- `docs/superpowers/specs/2026-06-16-planner-scheduler-daemon-boundary-design.md`
- `docs/superpowers/plans/2026-06-16-planner-scheduler-daemon-boundary-implementation-plan.md`
- `PlannerSchedulerDaemonStatus`
- `PlannerSchedulerDaemonError`
- `PlannerSchedulerDaemonState`
- `PlannerSchedulerDaemon`
- public API exports from `agentos.multi` and top-level `agentos`
- readiness, production docs, objective coverage audit, and agent-os skill
  guidance updates

Conclusion:

```text
Planner scheduling now has an SDK-owned polling shell without turning AgentOS
into a distributed DAG scheduler. `PlannerSchedulerDaemon` repeatedly calls the
existing one-shot `PlannerRuntime.scheduler_tick(...)` for explicitly supplied
plan ids, captures per-plan errors without aborting the whole iteration, and
exposes `run_once`, `start`, `stop`, `join`, `is_running`, and immutable
`PlannerSchedulerDaemonState` snapshots. Production deployments still own plan
discovery, tenant filtering, distributed scheduler locks, leader election,
process supervision, credentials, migrations, compensation orchestration, and
live backend verification.
```

### Phase 73: Planner Schedulable Plan Selection Boundary

Target conclusion:

```text
Planner scheduler daemons need a narrow SDK primitive for selecting which plans
are worth ticking from PlanStore, but AgentOS must not own tenant authorization,
distributed locks, leader election, global fairness, or process supervision.
The SDK should expose deterministic schedulable-plan summaries that filter
plans by owner, status, ready pending steps, and due retryable failed steps,
returning JSON-safe records that deployment-owned schedulers can claim, lock,
or supervise outside the SDK.
```

Artifacts:

- `docs/superpowers/specs/2026-06-16-planner-schedulable-plan-selection-boundary-design.md`
- `docs/superpowers/plans/2026-06-16-planner-schedulable-plan-selection-boundary-implementation-plan.md`
- `PlannerSchedulablePlanReason`
- `PlannerSchedulablePlan`
- `PlannerRuntime.schedulable_plans(...)`
- `PlannerTools` tool: `plan_schedulable_plans`
- public API exports from `agentos.multi` and top-level `agentos`
- readiness, production docs, objective coverage audit, and agent-os skill
  guidance updates

Conclusion:

```text
Planner scheduling now has SDK-owned schedulable plan selection without turning
AgentOS into a distributed scheduler. `PlannerRuntime.schedulable_plans(...)`
filters owner-scoped `PlanStore` rows by status and includes only plans with
dependency-ready pending steps or due retryable failed steps, returning
JSON-safe `PlannerSchedulablePlan` summaries. `plan_schedulable_plans` exposes
the same query through `PlannerTools` while preserving owner scoping. Production
deployments still own plan discovery sources, distributed claim stores,
scheduler locks, leader election, fairness, process supervision, credentials,
migrations, compensation orchestration, and live backend verification.
```

### Phase 74: Planner Plan Claim Lease Boundary

Target conclusion:

```text
Multi-node planner schedulers need an SDK boundary for plan claim/lease
coordination so multiple scheduler workers do not tick the same plan
concurrently. AgentOS should provide a small, testable PlanClaimStore boundary
with local in-memory semantics and JSON-safe claim records/results, but
deployment code still owns production distributed claim stores/adapters,
distributed locks, leader election, tenant authorization, global fairness, process
supervision, live backend verification, migrations, credentials, and
compensation orchestration.
```

Artifacts:

- `docs/superpowers/specs/2026-06-16-planner-plan-claim-lease-boundary-design.md`
- `docs/superpowers/plans/2026-06-16-planner-plan-claim-lease-boundary-implementation-plan.md`
- `PlanClaimStore`
- `InMemoryPlanClaimStore`
- `PlanClaimRecord`
- `PlanClaimResult`
- `PlanClaimStatus`
- `PlannerRuntime.claim_schedulable_plans(...)`
- `PlannerTools` tool: `plan_claim_schedulable_plans`
- public API exports from `agentos.multi` and top-level `agentos`
- readiness, production docs, objective coverage audit, and agent-os skill
  guidance updates

Conclusion:

```text
Planner scheduling now has SDK-owned schedulable plan selection plus a local
plan claim/lease primitive. `PlannerRuntime.claim_schedulable_plans(...)`
composes owner/status/ready-or-retryable plan selection with the injected
`PlanClaimStore`, returning JSON-safe `PlanClaimResult` values for claimed or
busy plans. `InMemoryPlanClaimStore` supports local tests, single-process
schedulers, lease renewal by the owning worker, release, and expired-lease
takeover. The form remains primitives-ready because production deployments
still own plan discovery, production distributed claim stores/adapters,
distributed scheduler locks, leader election, tenant authorization, global
fairness, process supervision, worker dispatch lifecycle, compensation
orchestration, credentials, migrations, and live backend verification.
```

### Phase 75: Postgres Plan Claim Store Boundary

Target conclusion:

```text
Multi-node planner schedulers need a durable shared claim store after the local
PlanClaimStore boundary. AgentOS should provide a PostgresPlanClaimStore adapter
and migration so scheduler workers can coordinate claim/lease state across
nodes, while deployment code still owns distributed scheduler locks, leader
election, stale lease recovery policy, tenant authorization, fairness,
credentials, migration execution, process supervision, compensation
orchestration, and live backend verification.
```

Artifacts:

- `docs/superpowers/specs/2026-06-16-postgres-plan-claim-store-boundary-design.md`
- `docs/superpowers/plans/2026-06-16-postgres-plan-claim-store-boundary-implementation-plan.md`
- `docs/migrations/2026-06-16-postgres-plan-claims.sql`
- `PostgresPlanClaimStore`
- public API exports from `agentos.multi` and top-level `agentos`
- readiness, production docs, objective coverage audit, and agent-os skill
  guidance updates

Conclusion:

```text
Planner scheduling now has local and Postgres-backed SDK claim/lease stores.
`PostgresPlanClaimStore` implements the existing `PlanClaimStore` protocol with
parameterized SQL, atomic claim/renew/takeover semantics, owner-preserving
JSON-safe `PlanClaimRecord` values, and release-by-owner-worker behavior.
`docs/migrations/2026-06-16-postgres-plan-claims.sql` defines the durable claim
table and lookup indexes. The form remains primitives-ready because production
deployments still own plan discovery, distributed scheduler locks, leader
election, stale lease recovery policy, tenant authorization, global fairness,
process supervision, worker dispatch lifecycle, compensation orchestration,
credentials, migration execution, and live backend verification.
```

### Phase 76: Planner Claimed Scheduler Tick Boundary

Target conclusion:

```text
Distributed planner workers need a narrow SDK composition boundary that claims
schedulable plans before running scheduler ticks. AgentOS should expose a
claim-before-tick scheduler boundary that selects owner/status-filtered plans,
claims each plan through PlanClaimStore, ticks only plans claimed by the current
worker, and optionally releases the worker's claims after each tick. Deployment
still owns plan discovery sources, leader election, global fairness, distributed
lock tuning, stale-lease sweepers, tenant authorization, process supervision,
autoscaling, compensation orchestration, credentials, migrations, and live
backend verification.
```

Artifacts:

- `docs/superpowers/specs/2026-06-16-planner-claimed-scheduler-tick-boundary-design.md`
- `docs/superpowers/plans/2026-06-16-planner-claimed-scheduler-tick-boundary-implementation-plan.md`
- `PlanClaimedSchedulerTickReport`
- `PlanClaimedSchedulerTickSkip`
- `PlannerRuntime.claimed_scheduler_tick(...)`
- `PlannerTools` tool: `plan_claimed_scheduler_tick`
- public API exports from `agentos.multi` and top-level `agentos`
- readiness, production docs, objective coverage audit, and agent-os skill
  guidance updates

Conclusion:

```text
Planner scheduler workers now have an SDK-owned claim-before-tick scheduler
boundary without turning AgentOS into a full scheduler platform.
`PlannerRuntime.claimed_scheduler_tick(...)` composes schedulable plan
selection, `PlanClaimStore` claims, and one-shot `scheduler_tick(...)` calls,
returning `PlanClaimedSchedulerTickReport` with claimed/busy results, tick
reports, skipped plans, and optional released plan ids. `plan_claimed_scheduler_tick`
exposes the same behavior through `PlannerTools` while preserving owner scoping.
Production deployments still own plan discovery, distributed scheduler locks,
leader election, stale lease recovery policy, tenant authorization, global
fairness, process supervision, worker lifecycle execution, compensation
orchestration, credentials, migration execution, and live backend verification.
```

### Phase 77: Planner Worker Dispatch Supervision Profile

Target conclusion:

```text
Distributed planner workers need a stable supervision projection for
claim-before-tick dispatch batches, but AgentOS should not become a process
supervisor or job runner. The SDK should expose a JSON-safe
PlannerWorkerDispatchSupervisionProfile that consumes
PlanClaimedSchedulerTickReport history, summarizes recent dispatch health,
and reports deployment-owned lifecycle, lock, stale-lease, compensation,
credential, migration, and live-backend gaps.
```

Artifacts:

- `docs/superpowers/specs/2026-06-16-planner-worker-dispatch-supervision-profile-design.md`
- `docs/superpowers/plans/2026-06-16-planner-worker-dispatch-supervision-profile-implementation-plan.md`
- `PlannerWorkerDispatchSupervisionProfile`
- required deployment components: `claimed_scheduler_tick_loop`,
  `worker_process_lifecycle`, `plan_claim_store`, `scheduler_lock_policy`,
  `stale_lease_recovery`, `compensation_policy`, `metrics_alerting`, and
  `live_backend_verification`
- health/readiness dispatch supervision payloads over
  `PlanClaimedSchedulerTickReport` history
- public API exports from `agentos.multi` and top-level `agentos`
- readiness, production docs, objective coverage audit, and agent-os skill
  guidance updates

Conclusion:

```text
Planner worker dispatch supervision now has an SDK-owned profile without
turning AgentOS into a worker process manager. `PlannerWorkerDispatchSupervisionProfile`
summarizes recent `PlanClaimedSchedulerTickReport` history into claim, busy,
tick-failed, release, and consecutive-failure health metadata, and exposes
`claimed_scheduler_tick_loop` plus `metrics_alerting` readiness components for
deployment probes. Production deployments still own real worker loop execution,
plan discovery, distributed scheduler locks, leader election, stale-lease
sweepers, tenant authorization, global fairness, process supervision,
compensation orchestration, credentials, migration execution, and live backend
verification.
```

### Phase 78: Planner Stale Claim Sweep Boundary

Target conclusion:

```text
Distributed planner workers need a standard recovery boundary for expired plan
claims, but AgentOS should not own cron scheduling, leader election, or global
fairness policy. The SDK should expose a race-safe, JSON-safe stale-claim sweep
operation and readiness profile so deployments can detect and release expired
planner leases with auditable evidence.
```

Artifacts:

- `docs/superpowers/specs/2026-06-16-planner-stale-claim-sweep-boundary-design.md`
- `docs/superpowers/plans/2026-06-16-planner-stale-claim-sweep-boundary-implementation-plan.md`
- `PlanClaimSweepSkip`
- `PlanClaimSweepReport`
- `PlanClaimSweepStore`
- `PlannerRuntime.sweep_expired_claims(...)`
- `InMemoryPlanClaimStore.expired_claims(...)`
- `InMemoryPlanClaimStore.release_expired_claim(...)`
- `PostgresPlanClaimStore.expired_claims(...)`
- `PostgresPlanClaimStore.release_expired_claim(...)`
- `PlannerStaleClaimSweepProfile`
- required deployment components: `stale_claim_sweep_schedule`,
  `plan_claim_store`, `scheduler_lock_policy`, `sweep_safety_window`,
  `metrics_alerting`, and `live_backend_verification`
- public API exports from `agentos.multi` and top-level `agentos`
- readiness, production docs, objective coverage audit, and agent-os skill
  guidance updates

Conclusion:

```text
Planner stale claim recovery now has an SDK-owned sweep/report boundary without
turning AgentOS into a cron runner or distributed scheduler. `PlannerRuntime.sweep_expired_claims(...)`
uses `PlanClaimSweepStore` to list expired claims, supports dry-run reports, and
releases only exact inspected `PlanClaimRecord` values by matching plan id,
owner, worker, generation, and expiry. `PlannerStaleClaimSweepProfile`
summarizes recent sweep reports into readiness metadata with
`stale_claim_sweep_schedule` and `sweep_safety_window` deployment components.
Production deployments still own invocation schedule, distributed locks, leader
election, tenant filtering, fairness, alert routing, compensation
orchestration, credentials, migration execution, process supervision, and live
backend verification.
```

### Phase 79: A2A External Conformance Execution Record Boundary

Target conclusion:

```text
External A2A interoperability needs repeatable execution evidence, but AgentOS
should not embed an official suite runner, CI system, certification authority,
credential issuer, or network trust controller. The SDK should expose a
JSON-safe execution record and gate report boundary that lets deployments attach
already-executed external suite metadata to imported A2AConformanceReport
results, then evaluate release readiness without making a certification claim.
```

Artifacts:

- `docs/superpowers/specs/2026-06-16-a2a-external-conformance-execution-record-boundary-design.md`
- `docs/superpowers/plans/2026-06-16-a2a-external-conformance-execution-record-boundary-implementation-plan.md`
- `A2AExternalConformanceExecutionRecord`
- `A2AExternalConformanceGateReport`
- execution metadata fields: suite, target, command, exit code, started/ended
  timestamps, environment, artifact URI, stdout/stderr summaries, report, and
  metadata
- gate metadata fields: execution success, report presence, required checks,
  missing checks, failed required checks, configured components, missing
  components, and `no_certification_claim`
- public API exports from `agentos.channels` and top-level `agentos`
- readiness, production docs, objective coverage audit, and agent-os skill
  guidance updates

Conclusion:

```text
A2A external conformance now has an SDK-owned execution evidence and gate
boundary without turning AgentOS into an external suite runner or certification
authority. `A2AExternalConformanceExecutionRecord` captures already-executed
suite command, target, exit code, timing, artifact, and imported report
metadata. `A2AExternalConformanceGateReport` evaluates execution success,
required-check presence, failed required checks, configured components, and
missing components into JSON-safe release/readiness metadata with
`no_certification_claim`. Production deployments still own suite selection and
invocation, CI orchestration, target environment provisioning, credentials,
network egress, DNS pinning, CA rollout, artifact storage, release policy, and
certification attestation.
```

### Phase 80: Planner LLM Decomposition Gate Boundary

Target conclusion:

```text
Leader agents and intent routers need a safe SDK entrance for raw LLM-produced
plan drafts, but AgentOS should not own prompt policy, model routing, approval
workflow, or automatic plan creation. The SDK should expose a JSON-safe
decomposition proposal gate that parses and normalizes a raw proposal, applies
deployment-supplied policy controls, reuses structured decomposition
validation, and returns an auditable accept/reject report without mutating
PlanStore.
```

Artifacts:

- `docs/superpowers/specs/2026-06-16-planner-llm-decomposition-gate-boundary-design.md`
- `docs/superpowers/plans/2026-06-16-planner-llm-decomposition-gate-boundary-implementation-plan.md`
- `PlanDecompositionGatePolicy`
- `PlanDecompositionGateReport`
- `PlannerRuntime.gate_decomposition_proposal(...)`
- `PlannerTools` tool: `plan_gate_decomposition_proposal`
- public API exports from `agentos.multi` and top-level `agentos`
- readiness, production docs, objective coverage audit, and agent-os skill
  guidance updates

Conclusion:

```text
Planner decomposition now has an SDK-owned raw proposal gate before structured
plan persistence. `PlannerRuntime.gate_decomposition_proposal(...)` parses
JSON-like LLM/main-agent proposals into normalized `PlanDecomposition` values,
applies `PlanDecompositionGatePolicy` controls for max steps, required
templates, allowed templates, and approval-required state, reuses
`PlannerRuntime.validate_decomposition(...)`, and returns a JSON-safe
`PlanDecompositionGateReport` without mutating `PlanStore`.
`plan_gate_decomposition_proposal` exposes the same read-only behavior to
leader agents through `PlannerTools`. Production deployments still own the
actual LLM prompt/model/approval/evaluation policy, rollout/rollback, live
backend verification, plan discovery, distributed scheduler locks, worker
dispatch execution, process supervision, compensation orchestration,
credentials, migration execution, and OS/container sandboxing.
```

### Phase 81: Planner Claimed Scheduler Daemon Boundary

Target conclusion:

```text
Production planner workers should be able to run a local polling loop that
discovers schedulable plans through the existing claim-before-tick boundary,
claims them, ticks only claimed work, and exposes an auditable lifecycle state.
AgentOS should provide this daemon boundary without owning tenant routing,
global fairness, distributed locks, leader election, process supervision, or
compensation orchestration.
```

Artifacts:

- `docs/superpowers/specs/2026-06-16-planner-claimed-scheduler-daemon-boundary-design.md`
- `docs/superpowers/plans/2026-06-16-planner-claimed-scheduler-daemon-boundary-implementation-plan.md`
- `PlannerClaimedSchedulerDaemon`
- `PlannerClaimedSchedulerDaemonState`
- `PlannerClaimedSchedulerDaemonError`
- `PlannerClaimedSchedulerDaemonStatus`
- public API exports from `agentos.multi` and top-level `agentos`
- readiness, production docs, objective coverage audit, and agent-os skill
  guidance updates

Conclusion:

```text
`PlannerClaimedSchedulerDaemon` hosts repeated
`PlannerRuntime.claimed_scheduler_tick(...)` calls with worker id, lease,
owner/status filters, limits, release policy, immutable state snapshots, and
recorded daemon errors. This closes the local claimed scheduler worker polling
boundary while tenant routing, global fairness, distributed scheduler locks,
stale claim sweep scheduling policy, process supervision, worker lifecycle
execution, and compensation orchestration remain deployment-owned.
```

### Phase 82: Planner Scheduler Governance Profile Boundary

Target conclusion:

```text
Production multi-node planner workers need more than a local claimed scheduler
daemon. They need an explicit readiness contract for plan discovery policy,
tenant routing, global fairness, distributed scheduler locks, leader election,
stale lease recovery, worker dispatch supervision, and live backend
verification. AgentOS should expose this as a planner scheduler governance
profile without implementing the deployment's global scheduler, lock service,
leader election, or fairness policy.
```

Artifacts:

- `docs/superpowers/specs/2026-06-16-planner-scheduler-governance-profile-boundary-design.md`
- `docs/superpowers/plans/2026-06-16-planner-scheduler-governance-profile-boundary-implementation-plan.md`
- `PlannerSchedulerGovernanceDeploymentProfile`
- `PLANNER_SCHEDULER_GOVERNANCE_REQUIRED_COMPONENTS`
- public API exports from `agentos.multi` and top-level `agentos`
- readiness, production docs, objective coverage audit, and agent-os skill
  guidance updates

Conclusion:

```text
`PlannerSchedulerGovernanceDeploymentProfile` exposes JSON-safe readiness
metadata for `plan_discovery_policy`, `tenant_routing_policy`,
`global_fairness_policy`, `scheduler_lock_policy`,
`leader_election_policy`, `stale_lease_recovery_policy`,
`worker_dispatch_supervision`, and `live_backend_verification`. This turns the
remaining scheduler-governance gap into an auditable deployment contract while
tenant routing implementation, global fairness queues, distributed scheduler
locks, leader election, stale lease recovery execution, worker dispatch
execution, compensation orchestration, credentials, migrations, alerting, and
live backend verification remain deployment-owned.
```

### Phase 83: A2A External Conformance Invocation Plan Boundary

Target conclusion:

```text
Production A2A deployments need a repeatable preflight plan before invoking an
external conformance suite, but AgentOS should not embed a suite runner, CI
system, credential distributor, network trust controller, or certification
authority. The SDK should expose a JSON-safe invocation plan and gate report for
suite id/version, target endpoint, command, required checks, credential policy,
network egress policy, version matrix, artifact retention, and failure alerting
while keeping real suite execution and certification attestation
deployment-owned.
```

Artifacts:

- `docs/superpowers/specs/2026-06-16-a2a-external-conformance-invocation-plan-boundary-design.md`
- `docs/superpowers/plans/2026-06-16-a2a-external-conformance-invocation-plan-boundary-implementation-plan.md`
- `A2AExternalConformanceInvocationPlan`
- `A2AExternalConformanceInvocationGateReport`
- public API exports from `agentos.channels` and top-level `agentos`
- readiness, production docs, objective coverage audit, and agent-os skill
  guidance updates

Conclusion:

```text
`A2AExternalConformanceInvocationPlan` now captures external suite invocation
planning as SDK-owned, JSON-safe metadata before deployment-owned CI runs a
suite. `A2AExternalConformanceInvocationGateReport` evaluates whether the
preflight plan includes required components and carries `no_certification_claim`
metadata. The plan can project an already executed run into
`A2AExternalConformanceExecutionRecord`, linking preflight evidence to the
existing execution record/gate boundary. External suite selection details,
suite execution, target provisioning, credentials, network egress enforcement,
version matrix execution, artifact upload/retention, failure alerting, and
certification attestation remain deployment-owned.
```

## Review Questions For Later Phases

These questions are not all solved in one phase, but each implementation phase
should answer the relevant subset before changing code:

- Should web session locking be fail-fast, queued, or lease-takeover based?
- Should session snapshot saves be turn-level atomic writes or event-sourced
  appends?
- Should the default workspace isolation level be per-session or per-agent?
- Should the next A2A gap prioritize task resubscribe, streaming, or push
  notification hardening?
- Should team workers inherit the leader workspace, or default to independent
  workspaces with explicit artifact sharing?
- Should planner state live in Working State, a ContextState extension, or an
  independent plan runtime?
- Should distributed background tool completion and team wakeup reuse the same
  continuation primitive?

## Immediate Next Step

Phase 83 is implemented and verified. The next iteration should select the
highest-risk remaining production blocker from the objective coverage audit,
write a target conclusion plus design and implementation plan, then add the
next narrow SDK boundary without moving deployment-owned orchestration concerns
into `QueryLoop` or `AsyncQueryLoop`.

### Phase 84: Planner LLM Governance Profile Boundary

Target conclusion:

```text
Production planner and intent-router deployments need traceable references for
prompt policy, model routing, approval, evaluation, trace logging, rollback,
schema, validation, template mapping, budgets, and live verification before
LLM-generated decompositions become plans. AgentOS should expose those
references as a JSON-safe readiness contract without owning prompt text,
model-router implementation, approval workflow, evaluation execution, rollout,
rollback, or live backend verification.
```

Artifacts:

- `docs/superpowers/specs/2026-06-16-planner-llm-governance-profile-boundary-design.md`
- `docs/superpowers/plans/2026-06-16-planner-llm-governance-profile-boundary-implementation-plan.md`
- `PlannerLlmDecompositionGovernanceProfile`
- `PLANNER_LLM_DECOMPOSITION_GOVERNANCE_REQUIRED_COMPONENTS`
- public API exports from `agentos.multi` and top-level `agentos`
- readiness, production docs, objective coverage audit, and agent-os skill
  guidance updates

Conclusion:

```text
`PlannerLlmDecompositionGovernanceProfile` now exposes governance reference
readiness payloads for `component_refs`, `evidence_refs`, and `budget_policy`
across prompt/model/approval/evaluation/trace/rollback/schema/validation/
template/budget/live-verification controls. This makes automatic LLM
decomposition governance auditable at the SDK boundary while prompt text and
prompt review workflow, model-router implementation, human approval workflow,
evaluation platform execution, rollout and rollback execution, secret
distribution, and live backend verification execution remain deployment-owned.
```

### Phase 85: Skill Release Governance Boundary

Target conclusion:

```text
AgentOS is both a runtime SDK and a developer guidance skill. The repository
skill should not silently drift from an installed user-level skill, but the SDK
should not overwrite user directories, publish marketplace entries, sign
artifacts, or own release approval. AgentOS should expose a JSON-safe release
manifest and drift report boundary for version synchronization while keeping
install/copy/publish/sign approval deployment-owned.
```

Artifacts:

- `docs/superpowers/specs/2026-06-16-skill-release-governance-boundary-design.md`
- `docs/superpowers/plans/2026-06-16-skill-release-governance-boundary-implementation-plan.md`
- `SkillReleaseFile`
- `SkillReleaseManifest`
- `SkillReleaseDriftReport`
- `build_skill_release_manifest(...)`
- `compare_skill_release_manifests(...)`
- public API exports from top-level `agentos`
- production docs, objective coverage audit, roadmap, and agent-os skill
  guidance updates

Conclusion:

```text
Skill release governance now has an SDK-owned manifest/drift boundary.
`build_skill_release_manifest(...)` deterministically enumerates skill files,
hashes them, and returns a JSON-safe `SkillReleaseManifest`.
`compare_skill_release_manifests(...)` compares a repository skill manifest
with an installed user-level skill manifest and returns a
`SkillReleaseDriftReport` covering missing, extra, changed, and
version-mismatched files. This closes version synchronization evidence without
turning AgentOS into an installer, publisher, marketplace manager, signing
authority, or approval workflow. install/copy/publish/sign approval remains deployment-owned.
```

### Phase 86: Production State Plane Boundary

Target conclusion:

```text
Production AgentOS deployments need a clear state-plane split before more
adapters are added. AgentOS should expose a JSON-safe
ProductionStatePlaneDeploymentProfile that names the required production
planes and their responsibilities without implementing Nacos, Redis, Postgres,
Kubernetes, systemd, or sandbox infrastructure in this phase.
```

Artifacts:

- `docs/superpowers/specs/2026-06-16-production-state-plane-boundary-design.md`
- `docs/superpowers/plans/2026-06-16-production-state-plane-boundary-implementation-plan.md`
- `ProductionStatePlaneDeploymentProfile`
- `PRODUCTION_STATE_PLANE_REQUIRED_COMPONENTS`
- state-plane readiness components: `agent_registry`, `message_queue`,
  `task_store`, `plan_store`, `worker_process_supervisor`,
  `session_snapshot_persistence`, `state_plane_boundary_policy`, and
  `live_backend_verification`
- recommended adapter boundaries: `NacosAgentRegistryAdapter`,
  `RedisAgentMessageQueue`, `PostgresTaskStore`, `PostgresPlanStore`,
  `WorkerProcessSupervisor`, and `SessionSnapshotPersistence`
- public API exports from `agentos.runtime` and top-level `agentos`
- readiness, production docs, objective coverage audit, roadmap, and agent-os
  skill guidance updates

Conclusion:

```text
`ProductionStatePlaneDeploymentProfile` now exposes the production_state_plane
readiness boundary that separates discovery metadata, message delivery,
task/plan truth stores, worker process lifecycle evidence, and session runtime
snapshots. The boundary policy records that registry is not task truth and
queue is not final task or plan state. Concrete Nacos registry adapter work,
worker supervisor implementation, secret distribution, tenant directory
integration, autoscaling, migrations, alerting, runbooks, and live backend
verification remain deployment-owned or later adapter phases.
```

### Phase 87: Worker Lifecycle Reference Supervisor

Target conclusion:

```text
Team workers, planner workers, and A2A push workers need one common local
process evidence boundary before production adapters are added. AgentOS should
define WorkerProcessSpec, WorkerProcessState, WorkerProcessSupervisor, and a
LocalSubprocessWorkerSupervisor reference adapter that records JSON-safe
lifecycle evidence while staying out of Kubernetes, systemd, autoscaling,
secret distribution, and restart-policy ownership.
```

Artifacts:

- `docs/superpowers/specs/2026-06-16-worker-lifecycle-reference-supervisor-design.md`
- `docs/superpowers/plans/2026-06-16-worker-lifecycle-reference-supervisor-implementation-plan.md`
- `WorkerProcessSpec`
- `WorkerProcessState`
- `WorkerProcessSupervisor`
- `LocalSubprocessWorkerSupervisor`
- JSON-safe lifecycle evidence fields: `status`, `pid`, `exit_code`,
  `started_at`, `stop_requested_at`, `stopped_at`, `error`, `worker_kind`,
  `command`, and `env_keys`
- worker kinds: `team_worker`, `planner_worker`, and `a2a_push_worker`
- public API exports from `agentos.deployment` and top-level `agentos`
- production docs, objective coverage audit, roadmap, and agent-os skill
  guidance updates

Conclusion:

```text
`LocalSubprocessWorkerSupervisor` now provides an argv-only local reference
adapter for `WorkerProcessSupervisor`. It can start, stop, wait for, inspect,
and emit evidence for local worker subprocesses while redacting environment
values down to `env_keys`. The evidence is suitable for team worker, planner
worker, and A2A push worker state reporting. It is not a Kubernetes, systemd,
autoscaling, restart-policy, graceful-drain, secret-distribution, alerting,
or live-backend-verification layer; those responsibilities remain
deployment-owned.
```

### Phase 90: Agent Service Reference Layer

Target conclusion:

```text
AgentOS needs a lightweight reference service layer that composes AsgiAgentApp,
DistributedWebRuntimeProfile, session snapshot persistence, workspace execution
backend references, auth/rate-limit hooks, and readiness profiles into one
auditable ASGI hosting reference without becoming a platform.
```

Artifacts:

- `docs/superpowers/specs/2026-06-16-agent-service-reference-layer-design.md`
- `docs/superpowers/plans/2026-06-16-agent-service-reference-layer-implementation-plan.md`
- `AgentServiceReference`
- `AgentServiceReferenceProfile`
- `AGENT_SERVICE_REFERENCE_REQUIRED_COMPONENTS`
- `AsgiAgentApp composition`
- `DistributedWebRuntimeProfile injection`
- `auth/rate-limit hook injection`
- `readiness check aggregation`
- JSON-safe readiness evidence

Conclusion:

```text
`AgentServiceReference` now provides a reference service for production web
agents. It builds the existing `AsgiAgentApp` from an injected
`DistributedWebRuntimeProfile`, wires auth/rate-limit hooks, aggregates service,
distributed session, state-plane, and workspace readiness checks, and emits
JSON-safe readiness evidence through `AgentServiceReferenceProfile` and
`AGENT_SERVICE_REFERENCE_REQUIRED_COMPONENTS`. This is a reference service, not
a platform: gateway/TLS/CORS/WAF, tenant directory integration,
Kubernetes/systemd/autoscaling, credentials, migrations, sandbox runtime
patching, alerting, rollout/rollback, and live backend verification remain
deployment-owned.
```

### Phase 91: A2A External Conformance Runner Boundary

Target conclusion:

```text
AgentOS should let deployments execute external A2A conformance suites through
a narrow SDK-owned reference boundary without owning the suite, CI system,
credentials, artifact storage, network trust controls, or certification
attestation. The SDK should provide an argv-only CLI runner that turns an
`A2AExternalConformanceInvocationPlan` into an
`A2AExternalConformanceExecutionRecord`, imports report path or stdout JSON,
captures bounded stdout/stderr summaries, records env_keys without secret
values, and preserves the no-certification-claim boundary.
```

Artifacts:

- `docs/superpowers/specs/2026-06-16-a2a-external-conformance-runner-boundary-design.md`
- `docs/superpowers/plans/2026-06-16-a2a-external-conformance-runner-boundary-implementation-plan.md`
- `A2AExternalConformanceRunner`
- `A2AExternalConformanceCliRunner`
- report path and stdout JSON import
- bounded stdout/stderr summaries
- timeout, nonzero exit, missing/malformed report, and import-error evidence
- `env_keys` evidence with secret value redaction
- public API exports from `agentos.channels` and top-level `agentos`
- production docs, objective coverage audit, roadmap, and agent-os skill
  guidance updates

Conclusion:

```text
`A2AExternalConformanceCliRunner` now provides a reference external
conformance CLI runner over `A2AExternalConformanceInvocationPlan`. It executes
commands as argv-only with no shell parsing, captures process evidence into the
existing `A2AExternalConformanceExecutionRecord`, imports external conformance
reports from a report path or stdout JSON, bounds stdout/stderr summaries,
records timeout and import failures as evidence, and exposes env_keys while
redacting secret values from captured output. This closes the SDK reference
runner boundary while official suite selection/installation, CI matrix
execution, artifact storage, network trust rollout, live backend verification,
release policy, and certification attestation remain deployment-owned.
```

### Phase 92: Planner LLM Governance Execution Boundary

Target conclusion:

```text
LLM-generated planner decompositions can enter production only when the
deployment supplies prompt, model, approval, evaluation, and validation
evidence for that specific proposal. AgentOS should consume those external
references and pass/fail statuses through a JSON-safe SDK gate, block plan
creation when evidence is missing or failed, and avoid owning prompt execution,
model routing, approval workflow, evaluation suites, schema validators, or
certification.
```

Artifacts:

- `docs/superpowers/specs/2026-06-16-planner-llm-governance-execution-boundary-design.md`
- `docs/superpowers/plans/2026-06-16-planner-llm-governance-execution-boundary-implementation-plan.md`
- `PlannerLlmGovernanceEvidenceRecord`
- `PlannerLlmGovernanceEvidenceGateReport`
- `PlannerRuntime.gate_llm_governance_evidence`
- planner LLM governance execution evidence
- per-proposal governance evidence gate
- required evidence names: `prompt_evidence`, `model_evidence`,
  `approval_evidence`, `evaluation_evidence`, and `validation_evidence`
- public API exports from `agentos.multi` and top-level `agentos`
- production docs, objective coverage audit, roadmap, and agent-os skill
  guidance updates

Conclusion:

```text
Planner LLM governance now has a per-proposal evidence gate. Deployments build
`PlannerLlmGovernanceEvidenceRecord` values from their prompt registry, model
router, approval workflow, evaluation suite, and validation system; AgentOS
returns `PlannerLlmGovernanceEvidenceGateReport` through
`PlannerRuntime.gate_llm_governance_evidence` and sets
`block_plan_creation=True` when required evidence is missing or failed. This
closes the SDK consumption boundary while deployment-owned
prompt/model/approval/evaluation/validation execution, artifact retention,
release certification, rollout, rollback, and live backend verification remain
outside the SDK.
```

### Phase 93: Live Backend Verification Evidence Boundary

Target conclusion:

```text
Production AgentOS deployments should not claim that Nacos, Redis, Postgres,
worker supervisors, or session snapshot persistence are production-ready only
because profiles name those backends. AgentOS should consume deployment-owned
live backend verification evidence through JSON-safe records, block readiness
when required backend evidence is missing or failed, and keep real backend
checks, credentials, migrations, CI matrix execution, alerting, runbooks, and
certification deployment-owned.
```

Artifacts:

- `docs/superpowers/specs/2026-06-16-live-backend-verification-evidence-boundary-design.md`
- `docs/superpowers/plans/2026-06-16-live-backend-verification-evidence-boundary-implementation-plan.md`
- `BackendVerificationRecord`
- `DeploymentLiveBackendVerificationGateReport`
- `DeploymentLiveBackendVerificationProfile`
- `LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS`
- `deployment_live_backend_verification`
- `block_production_readiness`
- production docs, objective coverage audit, roadmap, and agent-os skill
  guidance updates

Conclusion:

```text
Live backend verification now has a common SDK evidence boundary.
`BackendVerificationRecord` captures deployment-owned backend check results for
state-plane backends, `DeploymentLiveBackendVerificationGateReport` reports
missing, failed, skipped, and unknown required backend evidence, and
`DeploymentLiveBackendVerificationProfile` exposes the
`deployment_live_backend_verification` readiness payload with
`block_production_readiness`. This closes the SDK evidence consumption surface
while backend check execution, credentials and secret distribution, migration
execution, CI matrix execution, alert routing, runbooks, release approval, and
certification remain deployment-owned.
```

### Phase 94: Live Backend Verification Reference Runner Boundary

Target conclusion:

```text
AgentOS should make live backend verification evidence repeatable without
turning the SDK into a Nacos, Redis, Postgres, supervisor, or session snapshot
client. The SDK should provide an argv-only reference runner that invokes
deployment-owned backend verification scripts, imports a report path or stdout
JSON, records bounded process evidence, exposes only env_keys, redacts secret
values, and returns a readiness-blocking run result when execution or required
backend evidence fails. This is a reference runner, not a live backend client.
```

Artifacts:

- `docs/superpowers/specs/2026-06-16-live-backend-verification-reference-runner-boundary-design.md`
- `docs/superpowers/plans/2026-06-16-live-backend-verification-reference-runner-boundary-implementation-plan.md`
- `BackendVerificationInvocationPlan`
- `BackendVerificationRunner`
- `BackendVerificationCliRunner`
- `BackendVerificationReportImporter`
- `BackendVerificationReportImportError`
- `DeploymentLiveBackendVerificationRunResult`
- report path and stdout JSON import
- bounded stdout/stderr summaries
- timeout, nonzero exit, missing/malformed report, and import-error evidence
- `env_keys` evidence with secret value redaction
- no backend client claim
- public API exports from `agentos.deployment` and top-level `agentos`
- production docs, objective coverage audit, roadmap, and agent-os skill
  guidance updates

Conclusion:

```text
`BackendVerificationCliRunner` now provides a reference live backend
verification runner over `BackendVerificationInvocationPlan`. It executes
commands as argv-only with no shell parsing, captures process evidence into
`DeploymentLiveBackendVerificationRunResult`, imports backend verification
records from a report path or stdout JSON, bounds stdout/stderr summaries,
records timeout and import failures as evidence, exposes env_keys while
redacting secret values from captured output, and feeds the existing live
backend verification readiness gate. This closes the SDK reference runner
boundary while backend check script implementation, real backend clients,
credentials and secret distribution, migrations, CI matrix execution, alert
routing, runbooks, release approval, and certification remain
deployment-owned.
```

### Phase 95: Production Readiness Evidence Bundle Boundary

Target conclusion:

```text
AgentOS releases need one JSON-safe release gate evidence bundle that consumes
existing readiness/profile/backend evidence and reports whether the release can
go live without executing real infrastructure checks.
```

Artifacts:

- `docs/superpowers/specs/2026-06-16-production-readiness-evidence-bundle-boundary-design.md`
- `docs/superpowers/plans/2026-06-16-production-readiness-evidence-bundle-boundary-implementation-plan.md`
- `ProductionReadinessEvidenceBundle`
- `ReadinessEvidenceCheck`
- `ReadinessEvidenceStatus`
- `blocking_checks`
- `missing_required_checks`
- `block_production_readiness`
- `sdk_owned`
- `deployment_owned`
- JSON-safe evidence bundle

Conclusion:

```text
`ProductionReadinessEvidenceBundle` now provides the release gate evidence
bundle over existing readiness/profile/backend evidence. It normalizes
`ReadinessEvidenceCheck` values with `ReadinessEvidenceStatus`, reports
`accepted`, `blocking_checks`, `missing_required_checks`, and
`block_production_readiness`, emits `sdk_owned` and `deployment_owned`
metadata, and returns a JSON-safe evidence bundle. It does not execute real
infrastructure checks; backend check execution, credentials, migrations, CI
matrix execution, rollout, rollback, alerting, runbooks, release approval, and
certification remain deployment-owned.
```

### Phase 96: Release Scope Re-baseline

Target conclusion:

```text
AgentOS should stop treating every platform concern as a first-release SDK
blocker. The first production SDK release should support trusted tools,
internal service orchestration, terminal agent, single-node web agent,
distributed web agent, team/planner/A2A primitive composition, production state
plane, readiness evidence, and audit evidence. Sandbox / Docker / E2B /
microVM / enterprise runner adapter work should be a non-blocking future
adapter and not a release blocker; this release does not promise physical
isolation for untrusted code execution.
```

Artifacts:

- `docs/release-scope.md`
- `docs/superpowers/specs/2026-06-16-release-scope-rebaseline-design.md`
- `docs/superpowers/plans/2026-06-16-release-scope-rebaseline-implementation-plan.md`
- Phase 96: Release Scope Re-baseline
- release scope re-baseline
- first production SDK release
- trusted tools
- internal service orchestration
- terminal agent
- single-node web agent
- distributed web agent
- team/planner/A2A primitive
- production state plane
- readiness evidence
- audit evidence
- Sandbox / Docker / E2B / microVM / enterprise runner adapter
- non-blocking future adapter
- not a release blocker
- does not promise physical isolation for untrusted code execution
- `WorkspaceExecutionBackend`
- `SandboxBackend`
- `LocalWorkspaceExecutionBackend`
- policy/capability/path pre-check
- sandbox posture: `trusted tools only`, `deployment-owned isolation`, or
  `future adapter`

Conclusion:

```text
Phase 96 adds a release scope re-baseline for the first production SDK release.
`docs/release-scope.md`, production readiness, objective coverage audit,
roadmap, and agent-os skill guidance now agree that AgentOS is a company-level
agent SDK and development skill, not a full platform. The SDK release boundary
is trusted tools, internal service orchestration, terminal agent, single-node
web agent, distributed web agent, team/planner/A2A primitive composition,
production state plane, readiness evidence, and audit evidence. Sandbox /
Docker / E2B / microVM / enterprise runner adapter support remains a
non-blocking future adapter and not a release blocker. The SDK keeps
`WorkspaceExecutionBackend`, `SandboxBackend`, `LocalWorkspaceExecutionBackend`,
policy/capability/path pre-check, JSON-safe execution evidence, and audit
evidence, while physical isolation for untrusted code execution stays
deployment-owned.

Overall completion estimate: 96%.
```

### Phase 97: Reference State Plane Stack

Target conclusion:

```text
Reference State Plane Stack should not recreate Nacos, Redis, Postgres,
worker supervisors, or service hosting as a platform. It should provide an
SDK-owned reference state plane composition proving that registry, queue,
task/plan truth stores, worker lifecycle evidence, session snapshot
persistence, AgentServiceReference, DistributedWebRuntimeProfile, live backend
verification, and ProductionReadinessEvidenceBundle can be assembled while
real backend clients, credentials, migrations, CI matrix execution, alert
routing, and runbooks remain deployment-owned.
```

Artifacts:

- `docs/superpowers/specs/2026-06-16-reference-state-plane-stack-design.md`
- `docs/superpowers/plans/2026-06-16-reference-state-plane-stack-implementation-plan.md`
- `ReferenceStatePlaneStack`
- `ReferenceStatePlaneStackProfile`
- `REFERENCE_STATE_PLANE_REQUIRED_COMPONENTS`
- reference state plane
- readiness source aggregation
- component identity evidence
- `NacosAgentRegistryAdapter`
- `RedisAgentMessageQueue`
- `PostgresTaskStore`
- `PostgresPlanStore`
- `WorkerProcessSupervisor`
- `LocalSubprocessWorkerSupervisor`
- `SessionSnapshotPersistence`
- `PostgresSessionSnapshotPersistence`
- `AgentServiceReference`
- `DistributedWebRuntimeProfile`
- `ProductionReadinessEvidenceBundle`
- does not create backend clients

Conclusion:

```text
`ReferenceStatePlaneStack` now provides the SDK reference state plane
composition. It accepts existing registry, queue, task store, plan store,
worker supervisor, session snapshot persistence, runtime profile, service
reference, state-plane profile, live backend verification, and readiness bundle
objects, records component identity evidence, aggregates readiness sources, and
builds a `ProductionReadinessEvidenceBundle`. It does not create backend
clients; credentials, migrations, CI matrix execution, alert routing and
runbooks remain deployment-owned.

Overall completion estimate: 97%.
```

### Phase 98: Live Backend Probe Pack

Target conclusion:

```text
Live Backend Probe Pack should not turn AgentOS into a Nacos, Redis,
Postgres, or worker supervisor client. It should provide an SDK-owned
reference probe pack that declares the standard backend probes, emits argv-only
BackendVerificationInvocationPlan values, offers a runnable stdout JSON
example module, and aggregates DeploymentLiveBackendVerificationRunResult
evidence into ProductionReadinessEvidenceBundle while credentials, migrations,
CI matrix execution, alert routing and runbooks remain deployment-owned.
```

Artifacts:

- `ReferenceLiveBackendProbePack`
- `ReferenceLiveBackendProbeSpec`
- `REFERENCE_LIVE_BACKEND_PROBE_PACK_NAME`
- `agentos.examples.live_backend_probe`
- Nacos probe
- Redis probe
- Postgres task/plan/session probe
- worker supervisor probe
- readiness bundle aggregation
- `BackendVerificationInvocationPlan`
- `DeploymentLiveBackendVerificationRunResult`
- `ProductionReadinessEvidenceBundle`
- does not create backend clients

Conclusion:

```text
`ReferenceLiveBackendProbePack` now provides the SDK-owned live backend probe
pack for state-plane readiness evidence. It declares the standard Nacos,
Redis, Postgres task/plan/session, and worker supervisor probes, generates
argv-only `BackendVerificationInvocationPlan` values, points the default
reference command at `agentos.examples.live_backend_probe`, and aggregates
`DeploymentLiveBackendVerificationRunResult` evidence into
`ProductionReadinessEvidenceBundle` through readiness bundle aggregation. It
does not create backend clients; credentials, migrations, CI matrix execution,
alert routing and runbooks remain deployment-owned.

Overall completion estimate: 98%.
```

### Phase 99: SDK Skill / Spec Generator Finalization

Target conclusion:

```text
Phase 99 should make AgentOS a production agent design constraint generator,
not only a runtime SDK reference. The skill/spec generation flow must require
every production-bound agent spec to explicitly choose agent form, runtime
profile, state plane components, persistence backend, registry backend, queue
backend, worker supervisor, A2A exposure, planner/team mode, production
readiness checklist, and sandbox posture: trusted tools only |
deployment-owned isolation | future adapter, while stating that the SDK does
not create deployment-owned infrastructure.
```

Artifacts:

- `docs/superpowers/specs/2026-06-16-sdk-skill-spec-generator-finalization-design.md`
- `docs/superpowers/plans/2026-06-16-sdk-skill-spec-generator-finalization-implementation-plan.md`
- `production_design_constraints`
- spec generator finalization
- production agent design constraint generator
- must explicitly choose
- agent form
- runtime profile
- state plane components
- persistence backend
- registry backend
- queue backend
- worker supervisor
- A2A exposure
- planner/team mode
- production readiness checklist
- sandbox posture: trusted tools only | deployment-owned isolation | future adapter
- SDK-owned constraint template
- does not create deployment-owned infrastructure

Conclusion:

```text
The agent-os skill now has a Phase 99 spec generator finalization gate.
Production-bound specs must carry the `production_design_constraints`
SDK-owned constraint template and explicitly choose agent form, runtime
profile, state plane components, persistence backend, registry backend, queue
backend, worker supervisor, A2A exposure, planner/team mode, production
readiness checklist, and sandbox posture: trusted tools only |
deployment-owned isolation | future adapter. Implementation guidance blocks
handoff when this block is missing. The SDK records constraints and evidence
expectations but does not create deployment-owned infrastructure.

Overall completion estimate: 99%.
```

### Phase 100: Release Hardening

Target conclusion:

```text
Release Hardening should not continue expanding runtime capability. It should
turn the long-running review branch into a reviewable, publishable, and
maintainable SDK release candidate by requiring public API audit evidence,
stable API and experimental API classification, a migration index, README /
quickstart / examples alignment, CHANGELOG.md, full test suite evidence,
diff/commit hygiene, and runtime boundary scan evidence while CI/CD, signing,
publishing, deployment approval, credentials, migrations execution, rollout,
rollback, and physical isolation remain deployment-owned.
```

Artifacts:

- `docs/superpowers/specs/2026-06-16-release-hardening-design.md`
- `docs/superpowers/plans/2026-06-16-release-hardening-implementation-plan.md`
- `docs/release-hardening.md`
- `docs/api-stability.md`
- `docs/migrations/README.md`
- `CHANGELOG.md`
- Phase 100: Release Hardening
- release hardening gate
- release candidate evidence
- public API audit
- stable API
- experimental API
- API stability classification
- migration index
- README / quickstart / examples alignment
- full test suite evidence
- diff/commit hygiene
- SDK-owned release evidence
- does not run CI/CD, signing, publishing, deployment approval

Conclusion:

```text
Phase 100 defines the release hardening gate for the AgentOS SDK release
candidate. `docs/release-hardening.md`, `docs/api-stability.md`,
`docs/migrations/README.md`, `CHANGELOG.md`, README, quickstart, production
readiness, objective coverage audit, roadmap, and the agent-os skill now align
around release candidate evidence. This is SDK-owned release evidence; CI/CD,
signing, publishing, deployment approval, credentials, migration execution,
rollout, rollback, and physical isolation remain deployment-owned.

Phase 101: Production Reference Example closes the SDK-side release example
gap before the long-running goal enters completion audit.
```

### Phase 101: Production Reference Example

Target conclusion:

```text
Production Reference Example proves teams can copy the first-release shape:
AgentServiceReference + DistributedWebRuntimeProfile + Nacos/Redis/Postgres
state plane + readiness endpoint + backend verification +
ProductionReadinessEvidenceBundle + planner primitive. It remains an SDK
reference example and does not create backend clients or real infrastructure;
those remain deployment-owned real infrastructure.
```

Artifacts:

- `docs/superpowers/specs/2026-06-16-production-reference-example-design.md`
- `docs/superpowers/plans/2026-06-16-production-reference-example-implementation-plan.md`
- `src/agentos/examples/production_reference_web_agent.py`
- `tests/examples/test_production_reference_web_agent.py`
- Phase 101: Production Reference Example
- production reference web agent
- AgentServiceReference
- DistributedWebRuntimeProfile
- Nacos/Redis/Postgres state plane
- readiness endpoint
- backend verification
- ProductionReadinessEvidenceBundle
- ReferenceStatePlaneStack
- ReferenceLiveBackendProbePack
- planner primitive
- does not create backend clients
- deployment-owned real infrastructure

Conclusion:

```text
Phase 101 adds the production reference web agent at
`src/agentos/examples/production_reference_web_agent.py`, covered by
`tests/examples/test_production_reference_web_agent.py`. The example composes
`AgentServiceReference`, `DistributedWebRuntimeProfile`, a
Nacos/Redis/Postgres state plane, a readiness endpoint, backend verification,
`ProductionReadinessEvidenceBundle`, `ReferenceStatePlaneStack`,
`ReferenceLiveBackendProbePack`, and a planner primitive into one copyable SDK
reference.

It does not create backend clients. Nacos, Redis, Postgres, credentials,
migrations, CI/CD, process supervision, live backend probe execution,
gateway/TLS, tenant directory integration, rollout, rollback, alerting,
runbooks, and sandbox isolation are deployment-owned real infrastructure.
By default the example runs in `demo_without_live_backend_evidence` mode and
does not report live backend readiness. It becomes
`production_reference_with_live_backend_evidence` only when deployment-owned
`BackendVerificationRecord` evidence is injected.

SDK-side checklist coverage: 100%.
This is non-certifying SDK evidence, not a production certification.
```
