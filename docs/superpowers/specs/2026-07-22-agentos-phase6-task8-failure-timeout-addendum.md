# AgentOS Phase 6 Task 8 Failure Timeout Contract Addendum

> 状态：已确认，作为 Phase 6 Task 8 failure injection 的强制补充合同

## 1. 范围

本文只冻结 PostgreSQL/Redis 网络半开、Worker heartbeat 和 Outbox Relay 关闭时的期限语义，
不改变 PostgreSQL 唯一真值、Redis delivery/replay、Claim/Fencing、ACK 或 Side Effect 合同。

## 2. Backend I/O Deadline

- `DistributedRuntimeProfile.backend_operation_timeout` 默认 5 秒，必须为正 `timedelta`。
- PostgreSQL connection/transaction scope 和 Redis 非阻塞命令必须受该期限约束。
- PostgreSQL 超时映射为 `DistributedBackendUnavailableError`；Redis 超时映射为
  `DeliveryUnavailableError`，不得泄露 DSN、URL 或驱动错误文本。
- 外部取消必须保留 `CancelledError`，不得改写为 backend unavailable。
- Redis `XREAD`/`XREADGROUP` 是显式阻塞命令，其单次期限为
  `max(backend_operation_timeout, block_ms + 1 秒)`；close 仍必须主动取消阻塞读取。
- Adapter 的低层 deadline 只限制一次 I/O，不替代 Worker drain、Provider/Tool timeout 或
  Transport idle timeout。

## 3. Heartbeat Safety Budget

`DistributedRuntimeProfile.heartbeat_cycle_timeout` 默认 10 秒，包围一次 Redis Lease renew 与
随后一次 PostgreSQL Claim heartbeat。Profile 必须在构造期拒绝无法在 TTL 前 fail closed 的配置：

```text
heartbeat_interval + heartbeat_cycle_timeout < min(lease_ttl, claim_ttl)
```

组合期限覆盖先 renew Lease、再 heartbeat PostgreSQL Claim 的最坏串行路径；底层
`backend_operation_timeout` 是每次 I/O 的第二道期限。任一步超时或失权都必须取消当前
`AgentStream` 并等待 cleanup；PostgreSQL 未证明
WAITING/terminal commit 时不得 ACK delivery，也不得启动新的 Provider/Tool 调用。

## 4. Relay Batch Deadline

- `DistributedRuntimeProfile.relay_batch_timeout` 默认 30 秒，必须为正 `timedelta`。
- `OutboxRelay.relay_once()` 的 claim、publish、mark 和失败尾段 release 共用一个 batch deadline。
- batch 超时按 delivery unavailable 处理；已 mark 的前缀保持已发布，当前及未处理 tail 必须尝试
  release，恢复后仍复用相同 `outbox_id`。
- Relay 进入 closing 后拒绝新 batch。活动 batch 由自身 deadline 有界完成，因此 `close()` 不得
  永久等待网络半开 I/O。
- 自定义 Outbox/Queue Port 必须可取消；生产 PostgreSQL/Redis Adapter 还必须满足第 2 节的 I/O
  deadline。SDK 不提供绕过 deadline 的生产 fallback。

## 5. Live Evidence

Task 8 至少证明：

1. Redis heartbeat 故障会关闭活动 stream、保持 no-ACK，并可由替代 Worker 恢复；
2. PostgreSQL heartbeat 故障遵循相同 fail-closed 语义；
3. Relay publish/mark 超时不会把未提交的 Outbox 误记为 published；
4. drain/close 在上述故障中有界返回；
5. 后端恢复后 PostgreSQL 权威 Run、AcceptedInput、Claim/Fence 和 Outbox 可继续推进。

## 6. Shutdown Cleanup Deadline

- `DistributedRuntimeProfile.shutdown_cleanup_timeout` 默认 5 秒，必须为正 `timedelta`。
- `Worker.drain(timeout)` 的调用参数只表示活动 execution 的 graceful budget；receiver cancel、
  stream cleanup、Lease/Queue close 各自受 shutdown cleanup deadline 约束。
- cleanup 超时抛出稳定 `DistributedShutdownTimeoutError`；Worker 保持 `draining`、readiness=false，
  不得伪装为 `closed`。
- Profile 对每个资源单独应用 cleanup deadline；一个资源超时后仍继续尝试关闭后续资源，并在末尾
  抛出首个失败。
- 外部 cancellation 可以延迟到有界 cleanup 完成，但最终必须按原始 `CancelledError` 传播。
