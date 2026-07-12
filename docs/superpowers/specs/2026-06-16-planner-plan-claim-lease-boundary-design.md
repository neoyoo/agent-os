# Planner Plan Claim Lease 边界设计

> Branch: `review/agentos-sdk-architecture-20260611`

## 目标结论

多节点 planner scheduler 需要一层 SDK 级别的 claim/lease 边界，避免两个
worker 同时 tick 同一个可调度 plan。AgentOS 应该提供 JSON 安全的 claim
记录、一个小型 `PlanClaimStore` 协议、内存实现，以及一个把可调度 plan
选择与 claim 获取组合起来的 `PlannerRuntime` 辅助能力。

这不是一个分布式 scheduler。leader 选举、租户授权、全局公平性、真实
Redis/Postgres 锁、迁移、进程监督、worker 执行以及补偿编排仍由部署层负责。

## 当前缺口

Phase 73 增加了确定性的 `PlannerRuntime.schedulable_plans(...)` 摘要和
`plan_schedulable_plans` 工具。这些摘要可以告诉部署层 scheduler 哪些 plan
存在 ready 或到期重试的工作，但不能阻止多个 scheduler worker 并发选择并
tick 同一个 plan。

## SDK 负责的边界

- `PlanClaimRecord` 记录 `plan_id`、`owner_agent_id`、`worker_id`、
  `claimed_at`、`lease_expires_at` 以及单调递增的 `generation`。
- `PlanClaimResult` 返回携带新 claim 的 `claimed`，或携带现有未过期 claim
  的 `busy`。
- `PlanClaimStore` 定义 `claim_plan`、`release_plan` 和 `get_claim`。
  `release_plan` 可以包含 `owner_agent_id`，这样当 worker id 在不同 owner
  之间复用时，runtime 可以对 release 做保护。
- `InMemoryPlanClaimStore` 支持本地测试和单进程 scheduler。
- `PlannerRuntime.claim_schedulable_plans(...)` 先通过
  `schedulable_plans(...)` 过滤 plan，然后通过注入的 claim store 尝试获取
  claim。
- `PlannerTools` 将 `plan_claim_schedulable_plans` 暴露为按 owner 限定范围的
  JSON 工具。

## 部署层负责的边界

- Redis/Postgres compare-and-set、行锁、advisory lock 或 stream group claim
  语义。
- 租户授权和跨租户过滤。
- 全局公平性、优先级队列和饥饿预防。
- leader 选举和 scheduler worker 成员管理。
- scheduler 进程监督、优雅关闭和真实后端探测。
- claim sweeper 任务、告警和迁移发布。

## 验收标准

- 内存 claim 可以被获取；可以由相同 owner 和 worker 续租；当另一个 owner
  或 worker 持有未过期 lease 时返回 busy；可以由持有它的 worker 释放，并
  支持可选 owner guard；lease 过期后可以被接管。
- claim record 和 result 通过 `as_dict()` 保持 JSON 安全。
- `PlannerRuntime.claim_schedulable_plans(...)` 必须依赖 claim store，并且
  仍然按 owner/status/limit 限定范围。
- `PlannerTools` 暴露按 owner 限定范围的 `plan_claim_schedulable_plans`
  工具。
- 公共导出包含 claim store 协议、内存适配器、claim record、result 和状态类型。
- readiness、生产文档、目标覆盖审计、roadmap 和 agent-os skill 描述这个新的
  SDK primitive，但不宣称拥有生产级分布式锁。
- `QueryLoop` 和 `AsyncQueryLoop` 仍然不引入 planner、claim、lock、team 和
  A2A 编排概念。
