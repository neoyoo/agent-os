# Phase 6 Distributed Runtime Breaking Map

Phase 6 是一次 breaking cutover。它把 Local、Durable 和 Distributed Profile 收敛到同一套
原生异步 Kernel Port，不保留同步 Store facade、旧分布式 Snapshot 路径或长期兼容
re-export。实际删除在 Phase 6 Task 9 完成；本文先冻结目标 API 和迁移责任。

上位契约：
`docs/superpowers/specs/2026-07-17-agentos-phase6-distributed-runtime-transport-contract.md`。

## Runtime Contract

| Phase 5 / legacy API | Phase 6 canonical API | 迁移动作 |
|---|---|---|
| 同步 `RunStore.create/get/transition` | 原名 `RunStore` 的 `async def` 方法 | 全部调用方直接 `await`；不增加 `AsyncRunStore` |
| 可选 `expected_version` | 必填 `RunWriteGuard(expected_version, claim_id, fencing_token)` | Local/Durable 的 claim/fence 为 `None`；Distributed 两者必须同时提供 |
| `AcceptedContinuationInput.aggregate_version` | `AcceptedTurnExecution.guard.expected_version` | continuation 增加 acceptance 时确定分配的 `turn_id` |
| 首次输入直接调用 Channel 内 Agent | `RunSubmissionService.submit(RequestScope, RunSubmission)` | ingress 只持久接受并返回 receipt；Worker 执行 Loop |
| `DurableStateStore.bind_checkpoint_source()` | 显式传入 immutable `SessionCheckpoint` | Store 不持有 Runtime 对象，也不调用 `capture()` |
| 裸 `arguments` Tool handler | `ToolInvocation(arguments, context)` | 所有外部 Tool 必须显式声明 `SideEffectPolicy` |
| 节点本地 Distributed Artifact bytes | async shared `BlobStore` + PostgreSQL metadata | Distributed Profile 必须注入跨 Worker 共享后端 |

`Agent.run(input, stream=...)`、单一 `QueryLoop` 和 `Agent -> QueryLoop -> RunDriver` 不变；
不会重新引入同步 Agent、同步 QueryLoop 或第二套执行内核。

## Added Public Entry Points

以下名称是 Phase 6 计划批准的高层入口；最终导出位置由 Task 9 的 inventory 门禁确认：

- `RequestScope`；
- `RunSubmission`、`RunSubmissionReceipt`；
- `RunWriteGuard`、`AcceptedStartInput`、`AcceptedTurnExecution`；
- `ExecutionClaim`、`RunExecutionCursor`、`PendingToolInvocation`；
- `RunSubmissionService`、`RunCommandService`、`RunQueryService`、`RunEventStream`、
  `ArtifactService`；
- `DistributedRuntimeProfile`、`DistributedWorker`；
- `ToolInvocation`、`ToolInvocationContext`、`SideEffectPolicy`；
- HTTP/SSE/A2A 的纯 wire mapping，以及 6C 的 WebSocket wire/channel 入口。

底层 PostgreSQL、Redis、Blob 和 Worker leaf Adapter 可从 canonical leaf module 导入，但不从
根 `agentos` facade 批量 re-export。

## Removed Modules And Facades

Task 9 完成时删除以下 legacy 模块或新路径组合，不保留兼容 wrapper：

- `agentos.channels.a2a`；
- `agentos.channels.a2a_operations`；
- `agentos.channels.a2a_conformance`；
- `agentos.channels.asgi`；
- `agentos.channels.durable_session`；
- `agentos.deployment`；
- `agentos.multi.team`；
- `agentos.runtime.profile_distributed` 中的旧线程 Daemon/Profile；
- Distributed 新路径中的 `SessionSnapshot`、`PostgresSessionSnapshotPersistence`、
  同步 PostgreSQL/Redis wrapper。

替代入口分别位于 `agentos.transports.*`、`agentos.channels.*_endpoint`、
`agentos.distributed.*`、`agentos.multi.team_*` 和 `agentos.deployment_*`。迁移提交必须先切换
仓库调用方和 Public API inventory，再原子删除旧入口；不得在中间提交同时维护双写真值。

## Cancel Safe-Stop Override

Phase 6 的 cancel 不再承诺“任意非终态 Run 都可以立即取消”。调用方必须处理稳定错误码
`side_effect_in_flight`（HTTP `409`）：

1. 当前 Ledger 含 `STARTED`、`AMBIGUOUS` 或 `COMPENSATING` 时，cancel 事务零写入：不写
   Command，不改变 Run/input/cursor，也不轮转 fence；
2. 调用方查询并展示当前 Side Effect，随后通过受权 `resolve_side_effect` 选择
   `accept_result`、`retry_proven_safe`、`compensate` 或 `fail`；
3. resolution 后 Run 仍非终态时，使用原 `command_id` 幂等规则重试 cancel；
4. resolution 已把 Run 转为 `FAILED` 时，以该终态结束，不再提交 cancel；
5. `RESERVED` invocation 可在 cancel 事务中原子转为
   `RESOLVED(cancelled_before_start)`；已知 terminal Ledger row 保留。

该行为没有 Phase 5 fallback。旧客户端若忽略 `409 side_effect_in_flight`，会失去明确的取消
结果，因此必须在升级前完成错误处理和 resolution UX。

## Explicit Deferrals

- WebSocket 属于 6C，不在 6B HTTP/SSE 实现中顺带加入；
- OCR、自动附件摘要、Embedding、Vector Retrieval 永久不属于本阶段；
- Provider transcript 恢复、全局 exactly-once、跨 Region 多主不在 Phase 6。
