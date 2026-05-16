# TODO: 分布式 Multi-Agent 消息互通

## 问题定义

当前 multi-agent coordination 已有协议边界和 adapter：`TaskStore` / `AgentMessageQueue` 是分布式任务与消息边界，`TaskTable` / `AgentInbox` 是 in-memory adapter，`PostgresTaskStore` / `RedisAgentMessageQueue` 是生产 adapter。Redis retry/reclaim、outbox reconciler、跨节点 continuation trigger 和可选 live Postgres/Redis integration harness 已补齐；剩余缺口集中在 worker ownership、heartbeat、session affinity 与真实多节点调度策略。

因此，这个问题不是“multi-agent 没实现”，而是：

```text
分布式多智能体交互时，任务状态、消息队列、结果通知、取消/超时、late result 等事实如何跨节点互通并保持一致。
```

源码来源：[`src/agentos/multi/coordinator.py`](../src/agentos/multi/coordinator.py)，[`src/agentos/multi/task_store.py`](../src/agentos/multi/task_store.py)，[`src/agentos/multi/message_queue.py`](../src/agentos/multi/message_queue.py)，[`src/agentos/multi/tasks.py`](../src/agentos/multi/tasks.py)，[`src/agentos/multi/inbox.py`](../src/agentos/multi/inbox.py)，[`src/agentos/multi/postgres_tasks.py`](../src/agentos/multi/postgres_tasks.py)，[`src/agentos/multi/redis_queue.py`](../src/agentos/multi/redis_queue.py)，[`src/agentos/multi/reconciler.py`](../src/agentos/multi/reconciler.py)，[`src/agentos/multi/redis_continuation.py`](../src/agentos/multi/redis_continuation.py)，[`src/agentos/multi/remote.py`](../src/agentos/multi/remote.py)，[`src/agentos/channels/a2a.py`](../src/agentos/channels/a2a.py)，[`tests/integration/test_live_backends.py`](../tests/integration/test_live_backends.py)

## 当前已实现

| 能力 | 当前实现 | 证据 |
|---|---|---|
| 本地任务状态机 | `TaskTable` 作为 `TaskStore` in-memory adapter，支持 claim/lease、cancel intent、late result、consume results | [`src/agentos/multi/tasks.py`](../src/agentos/multi/tasks.py), [`src/agentos/multi/task_store.py`](../src/agentos/multi/task_store.py), [`tests/multi/test_task_store_contract.py`](../tests/multi/test_task_store_contract.py) |
| 本地点对点 inbox | `AgentInbox` 作为 `AgentMessageQueue` in-memory adapter，用本地 `Queue` + `Event` 实现 send/collect/wait/backpressure/ack | [`src/agentos/multi/inbox.py`](../src/agentos/multi/inbox.py), [`src/agentos/multi/message_queue.py`](../src/agentos/multi/message_queue.py), [`tests/multi/test_message_queue_contract.py`](../tests/multi/test_message_queue_contract.py) |
| Postgres task store | `PostgresTaskStore` 保存 `TaskRecord` payload、claim metadata、terminal result 和 outbox marker | [`src/agentos/multi/postgres_tasks.py`](../src/agentos/multi/postgres_tasks.py), [`docs/migrations/2026-05-16-postgres-multi-agent-tasks.sql`](migrations/2026-05-16-postgres-multi-agent-tasks.sql), [`tests/multi/test_postgres_task_store.py`](../tests/multi/test_postgres_task_store.py) |
| Redis Streams queue | `RedisAgentMessageQueue` 用 Redis Streams 提供 per-agent inbox delivery/ack | [`src/agentos/multi/redis_queue.py`](../src/agentos/multi/redis_queue.py), [`tests/multi/test_redis_message_queue.py`](../tests/multi/test_redis_message_queue.py) |
| Redis pending/retry | `RedisAgentMessageQueue.reclaim_pending()` 使用 XPENDING/XCLAIM reclaim idle messages，超过重试次数写 dead-letter stream | [`src/agentos/multi/redis_queue.py`](../src/agentos/multi/redis_queue.py), [`tests/multi/test_redis_pending_retry.py`](../tests/multi/test_redis_pending_retry.py) |
| outbox reconciler | `OutboxReconciler` 扫描 outbox 并补发 terminal task result envelope | [`src/agentos/multi/reconciler.py`](../src/agentos/multi/reconciler.py), [`tests/multi/test_outbox_reconciler.py`](../tests/multi/test_outbox_reconciler.py) |
| cross-node continuation | `RedisContinuationTrigger` 发布 Redis Pub/Sub notice，并支持 TaskStore polling fallback | [`src/agentos/multi/redis_continuation.py`](../src/agentos/multi/redis_continuation.py), [`tests/multi/test_redis_continuation.py`](../tests/multi/test_redis_continuation.py) |
| 本地 spawn | `SpawnExecutor` 用 `ThreadPoolExecutor` 执行 ephemeral subagent | [`src/agentos/multi/spawn.py`](../src/agentos/multi/spawn.py), [`tests/multi/test_spawn_executor.py`](../tests/multi/test_spawn_executor.py) |
| remote dispatch | endpoint-backed agent 通过 `RemoteTaskExecutor` + `A2AAdapter` 调用远端 `/a2a/tasks` | [`src/agentos/multi/remote.py`](../src/agentos/multi/remote.py), [`src/agentos/channels/a2a.py`](../src/agentos/channels/a2a.py), [`tests/multi/test_remote_dispatch.py`](../tests/multi/test_remote_dispatch.py) |
| agent registry 持久化 | 已有 `PersistentAgentRegistry` 和 `PostgresAgentRegistryStore` | [`src/agentos/registry/persistent.py`](../src/agentos/registry/persistent.py), [`src/agentos/registry/postgres.py`](../src/agentos/registry/postgres.py), [`tests/registry/test_remote_registry.py`](../tests/registry/test_remote_registry.py) |

## 当前缺口

| 缺口 | 当前事实 | 影响 |
|---|---|---|
| live Postgres/Redis integration | 已有 `tests/integration/`、pytest mark 和 `docker-compose.test.yml`，默认跳过 | CI 默认不启动真实服务；需要 `AGENTOS_RUN_INTEGRATION=1` 显式运行 |
| worker ownership 与 registry 结合 | task claim 已有 `worker_id` / `lease_expires_at` / `attempt`，但尚未和 registry heartbeat/session affinity 完整联动 | 健康检查失败释放 lease 或 affinity 路由还未自动化 |

源码来源：[`src/agentos/multi/tasks.py`](../src/agentos/multi/tasks.py)，[`src/agentos/multi/inbox.py`](../src/agentos/multi/inbox.py)，[`src/agentos/multi/coordinator.py`](../src/agentos/multi/coordinator.py)

## 分布式一致性原则

- `TaskStore` 是分布式任务和结果的 truth source；当前 `TaskTable` 只是 `TaskStore` 的 in-memory adapter。
- `AgentMessageQueue` 是 delivery / notification 层，不是结果真值源。Redis Streams 可以承载 `task_request`、`task_cancel`、`result_ready` 等 envelope，但 terminal result 必须以 `TaskStore` 为准。
- Postgres 与 Redis Streams 之间存在双写一致性问题。设计必须包含 outbox 或 reconciler：当任务状态已写入 Postgres 但 result-ready 通知发送失败时，后台流程能根据 outbox 或未通知终态任务重新补发通知。
- 任务状态更新必须幂等，并通过 version / updated_at / compare-and-set 条件保护，防止重复完成、取消覆盖完成、late result 覆盖终态等问题。

源码来源：[`src/agentos/multi/tasks.py`](../src/agentos/multi/tasks.py)，[`src/agentos/multi/inbox.py`](../src/agentos/multi/inbox.py)，[`src/agentos/registry/persistent.py`](../src/agentos/registry/persistent.py)

## TODO

### P0: 固化分布式边界接口

- [x] 定义 `TaskStore` Protocol，覆盖 `create`、`get`、`claim_queued`、`mark_running`、`mark_completed`、`mark_failed`、`request_cancel`、`ack_cancelled`、`mark_timed_out`、`store_late_result`、`consume_results_for_agent`。
- [x] 定义 `AgentMessageQueue` Protocol，覆盖 `create_inbox`、`remove_inbox`、`send`、`collect`、`wait`、`ack` 或等价确认语义。
- [x] 保留现有 `TaskTable` 和 `AgentInbox` 作为 in-memory adapter，避免破坏当前本地单进程测试。
- [x] 明确 `TaskStore` 是结果真值源；`TaskTable` 是 in-memory adapter；`AgentInbox` / `AgentMessageQueue` 是执行消息和 result-ready 通知通道，不能把二者职责混在一个类里。
- [x] 在 task record schema 中预留 `worker_id`、`lease_expires_at`、`attempt`、`updated_at`、`version` 或等价 CAS 字段。

源码来源：[`src/agentos/multi/tasks.py`](../src/agentos/multi/tasks.py)，[`src/agentos/multi/inbox.py`](../src/agentos/multi/inbox.py)，[`docs/superpowers/specs/2026-05-05-phase-8-multi-agent-coordination-design.md`](superpowers/specs/2026-05-05-phase-8-multi-agent-coordination-design.md)

### P1: Postgres-backed TaskStore

- [x] 新增 `PostgresTaskStore`，用 SQL 表保存 `TaskRecord`、terminal `TaskResult`、`late_result`、`consumed_at`、`worker_id`、`lease_expires_at`、`attempt`、`updated_at`、`version`。
- [x] 用 compare-and-set 风格 SQL transition 保护状态转换，例如只允许 `queued -> running`、`running -> completed`。
- [x] 增加 `claim_queued(worker_id, capabilities, limit)`，让 worker 原子领取任务，避免重复执行。Postgres 实现应使用 `FOR UPDATE SKIP LOCKED`、`UPDATE ... WHERE ... RETURNING` 或等价原子 claim 机制。
- [x] 增加 lease/deadline 字段，worker crash 后可以重新 claim 或标记 timeout。
- [x] 增加 outbox 表或 task notification marker，记录 terminal result 已写入但 result-ready 通知尚未成功发送的任务。
- [x] 增加 reconciler，周期性扫描 outbox 或未通知终态任务并补发 `result_ready`。
- [x] 新增 migration，包含 up/down。
- [x] 添加并发 transition 测试，验证重复 claim、重复 terminal write、cancel/complete 竞态、late result 都是幂等或被拒绝。

源码来源：[`src/agentos/persistence/postgres.py`](../src/agentos/persistence/postgres.py)，[`src/agentos/registry/postgres.py`](../src/agentos/registry/postgres.py)，[`docs/migrations/2026-05-07-postgres-agent-registry.sql`](migrations/2026-05-07-postgres-agent-registry.sql)，[`docs/migrations/2026-05-07-postgres-memory-backends.sql`](migrations/2026-05-07-postgres-memory-backends.sql)

### P2: Redis Streams-backed AgentMessageQueue

- [x] 新增 `RedisAgentMessageQueue`，用 Redis Streams 表达 per-agent inbox。
- [x] envelope 写入 stream，consumer group 按 agent/worker 消费。
- [x] 支持 pending/ack/retry，避免 worker crash 后 envelope 永久丢失。
- [x] 明确 Redis Streams 只承载 delivery/notification；所有 terminal result、cancel intent、lease 和 consumed 状态以 `TaskStore` 为准。
- [x] 保留 backpressure 行为，超过容量或积压阈值时 emit `AgentInboxBackpressureEvent`。
- [x] 明确 result-ready 通知和 task_request envelope 的 stream key 命名。
- [x] 添加 Redis fake 或 contract tests，覆盖 send/collect/wait/backpressure/retry。

源码来源：[`src/agentos/memory/redis_store.py`](../src/agentos/memory/redis_store.py)，[`src/agentos/multi/inbox.py`](../src/agentos/multi/inbox.py)，[`src/agentos/events/types.py`](../src/agentos/events/types.py)，[`tests/memory/test_production_adapters.py`](../tests/memory/test_production_adapters.py)，[`tests/multi/test_inbox.py`](../tests/multi/test_inbox.py)

### P3: Coordinator 适配分布式任务和消息边界

- [x] 让 `AgentCoordinator` 依赖 `TaskStore` 和 `AgentMessageQueue` Protocol，而不是直接依赖本地 `TaskTable`/`AgentInbox` 具体类。
- [x] `dispatch` 对 endpoint-backed agent 继续支持 A2A remote task；对 queue-backed worker 走 distributed message queue。
- [x] `collect_results(parent_agent_id)` 从 distributed task store 读取 terminal results，并消费 result-ready notification。
- [x] `cancel(task_id)` 区分 queued 和 running：queued task 可以直接进入 `cancelled`；running task 写入 `cancel_requested_at` / cancel intent，并由 worker ack、lease timeout 或 late result 规则收敛到终态。
- [x] 对本地 agent 保留 `agent.interrupt()`；对远程/分布式任务发送 cancel envelope 或写 cancel intent，不能假设远端会立即停止。
- [x] continuation trigger 需要支持跨节点唤醒，不能只依赖本地 `AgentInbox` event。

源码来源：[`src/agentos/multi/coordinator.py`](../src/agentos/multi/coordinator.py)，[`src/agentos/multi/continuation.py`](../src/agentos/multi/continuation.py)，[`src/agentos/multi/remote.py`](../src/agentos/multi/remote.py)，[`src/agentos/channels/a2a.py`](../src/agentos/channels/a2a.py)

### P4: 注册发现和 worker ownership

- [ ] 复用已有 `AgentCard` / registry / resolver，记录 worker endpoint、capabilities、status、heartbeat。
- [ ] 明确定义 worker ownership：`AgentCard` 描述可发现 agent 能力和 endpoint；具体执行所有权绑定到进程实例 / worker instance 的 `worker_id` 和 lease，不只绑定到 `AgentCard.capabilities`。
- [ ] 给任务 claim 增加 `worker_id`、lease 和 heartbeat 检查。
- [ ] 区分三类身份：agent capability identity（`AgentCard.agent_id`）、worker instance identity（实际 claim/lease 持有者）、parent/session identity（用于结果归属和 affinity）。
- [ ] 支持 session affinity：同一 parent/session 优先路由到同一 worker，避免状态和 prompt cache 抖动。
- [ ] 健康检查失败时释放 lease 或标记任务 timeout。

源码来源：[`src/agentos/multi/types.py`](../src/agentos/multi/types.py)，[`src/agentos/multi/registry.py`](../src/agentos/multi/registry.py)，[`src/agentos/registry/resolver.py`](../src/agentos/registry/resolver.py)，[`src/agentos/registry/persistent.py`](../src/agentos/registry/persistent.py)，[`src/agentos/registry/postgres.py`](../src/agentos/registry/postgres.py)，[`tests/registry/test_remote_registry.py`](../tests/registry/test_remote_registry.py)

### P5: 测试和验收

- [x] 保留现有 in-memory multi-agent 测试全部通过。
- [x] 增加 distributed contract tests，同一套测试跑 in-memory、Postgres task store、Redis message queue fake。
- [x] 增加 crash/retry 场景：worker claim 后崩溃、parent 未 collect、result late arrival、cancel 与 complete 竞态。
- [x] 增加 A2A remote dispatch 与 distributed task store 的集成测试。
- [x] 增加 trace context 传播测试，确保 remote task 的 trace headers 继续传递。

源码来源：[`tests/multi`](../tests/multi)，[`tests/registry/test_remote_registry.py`](../tests/registry/test_remote_registry.py)，[`tests/channels/test_a2a_adapter.py`](../tests/channels/test_a2a_adapter.py)，[`tests/multi/test_trace_context_propagation.py`](../tests/multi/test_trace_context_propagation.py)

## 设计参考

- `ai-knowledge/wiki/multi-agent.md`：多 agent 的核心问题是拆分、分发、通信、失败处理和避免重复工作；其中明确指出分布式就绪度取决于 agent-to-agent 协议、注册发现和持久状态。
- `ai-knowledge/wiki/agent-registry-discovery.md`：分布式 agent 需要 AgentCard、endpoint、capabilities、heartbeat、version 和 session affinity。
- `ai-knowledge/wiki/channel-remote.md`：远程 channel 的关键问题是输入输出格式、远程安全模型和会话状态同步。

## 非目标

- [x] 不在这一项里重写 `QueryLoop`。
- [x] 不把 `EventBus` 变成消息队列；`EventBus` 仍然只做观察。
- [x] 不把 runtime metadata 渲染进默认 prompt。
- [x] 不默认引入 Nacos；当前项目已有 Postgres registry 和 A2A 基础，优先补齐 Postgres/Redis 边界。

源码来源：[`src/agentos/events/bus.py`](../src/agentos/events/bus.py)，[`docs/design/sdk-architecture.md`](design/sdk-architecture.md)，[`docs/design/llm-context-only-example.md`](design/llm-context-only-example.md)
