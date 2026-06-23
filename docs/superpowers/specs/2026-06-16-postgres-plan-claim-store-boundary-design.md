# Postgres Plan Claim Store 边界设计

## 目标结论

AgentOS 应该提供一个由 Postgres 支撑的持久化 `PlanClaimStore` 适配器，让多
节点 scheduler worker 可以通过真实共享状态边界协调 plan lease。SDK 负责适配器
契约、JSON 安全记录、原子的 claim/renew/release 语义以及迁移结构。部署层仍然
负责 leader 选举、全局公平性、锁参数调优、过期 lease 策略、凭证、迁移执行、
进程监督、租户授权和真实后端验证。

## 当前缺口

Phase 74 增加了 `PlanClaimStore`、`PlanClaimRecord`、
`InMemoryPlanClaimStore`、`PlannerRuntime.claim_schedulable_plans(...)` 以及
`plan_claim_schedulable_plans` 工具。这已经覆盖本地 scheduler 和单元测试，
但运行在不同节点上的生产级 web 或 planner worker 需要一个共享 lease store。
当前 readiness 矩阵仍然把生产级分布式 claim store 和分布式 scheduler lock 列为
部署层负责的阻塞项。

## SDK 边界

- 在 `src/agentos/multi/postgres_plan.py` 中增加 `PostgresPlanClaimStore`。
- 保持 `PlanStore` 和 `PlanClaimStore` 分离：plan state 仍然是持久化 truth，
  claim row 则是临时 scheduler lease。
- 只使用参数化 SQL。
- 当不存在 row、row 已过期，或相同 owner 和 worker 发起续租时，claim 成功。
- 当另一个 owner 或 worker 持有未过期 lease 时，claim 返回携带现有
  `PlanClaimRecord` 的 `busy`。
- release 只删除匹配 worker 的 row，并接受一个可选 owner guard，供需要跨 owner
  worker id 安全性的 scheduler runtime 使用。
- `get_claim(...)` 返回已持久化的记录，但不判断它是否已经过期。

## 非目标

- 本阶段不提供 Redis adapter。
- 不提供 scheduler leader 选举或全局公平性算法。
- 不提供自动 stale-lease sweeper。
- 不集成应用层租户授权或 RBAC。
- 不提供 migration runner、credential loader 或真实后端健康探测。
- 不修改 `QueryLoop` 或 `AsyncQueryLoop`。

## 验证

- 为 adapter 行为增加 fake-connection 单元测试。
- 为 `agentos_plan_claims` 表和索引增加 migration 测试。
- 增加 public API export 测试。
- 更新 readiness/docs/skill/audit/roadmap，将持久化 Postgres claim adapter
  纳入 SDK evidence，同时仍然把分布式 scheduler lock 和运维控制保留为部署层职责。
