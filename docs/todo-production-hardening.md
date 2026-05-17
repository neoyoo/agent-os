# TODO: Production Hardening & SDK 完善

## 当前状态

SDK 核心引擎已达 production-grade 架构质量（Protocol 边界、CAS 状态机、零硬依赖、520 个本地测试通过，4 个 live integration cases 默认跳过）。
以下是从"可运行"到"可在生产环境部署和被外部开发者采用"之间的差距清单。

---

## P0: 生产部署必须项

### 1. Provider Retry & Circuit Breaker

**现状**：Provider 调用失败直接 raise RuntimeError，无重试。
**要求**：
- [x] 在 `QueryLoop._consume_provider_stream` 层加入可配置 retry（指数 backoff + jitter）
- [x] 支持 `max_retries`、`retry_on`（异常类型白名单）、`backoff_base` 配置
- [x] 连续失败 N 次进入 circuit-open 状态，快速 fail 避免雪崩
- [x] retry 期间发出 `ProviderRetryEvent`，可观测

**文件**：
- 新建 `src/agentos/runtime/retry.py`（RetryPolicy dataclass + retry decorator）
- 修改 `src/agentos/runtime/query_loop.py`（在 provider stream 消费处包装）
- 测试 `tests/runtime/test_provider_retry.py`

---

### 2. Graceful Shutdown

**现状**：SpawnExecutor 用 ThreadPoolExecutor 但无 drain；ExpertAgentRunner.stop() 只设 event 不等待完成。
**要求**：
- [x] `SpawnExecutor.shutdown(timeout_seconds)` 等待运行中任务完成或 timeout 后 cancel
- [x] `ExpertAgentRunner.stop(timeout_seconds)` 等待当前 task 执行完后退出
- [x] `AsgiAgentApp` 支持 ASGI lifespan protocol（startup / shutdown 事件）
- [x] shutdown 时 TaskTable 中 running 任务标记为可重新 claim（释放 lease）

**文件**：
- 修改 `src/agentos/multi/spawn.py`
- 修改 `src/agentos/multi/expert.py`
- 修改 `src/agentos/channels/asgi.py`
- 测试 `tests/multi/test_graceful_shutdown.py`

---

### 3. Health & Readiness Endpoint

**现状**：ASGI app 无健康检查路由。
**要求**：
- [x] `GET /health` 返回 `{"status": "ok"}` + 200
- [x] `GET /ready` 检查 provider 可达（可选）、DB 连接（可选）后返回状态
- [x] 支持注入自定义 health check callable

**文件**：
- 修改 `src/agentos/channels/asgi.py`（加 `/health` 和 `/ready` 路由）
- 测试 `tests/channels/test_health_endpoint.py`

---

### 4. Structured Logging

**现状**：只有 EventBus + OTel span，无 Python logging 输出。生产环境无法用 stdout/stderr 收集日志。
**要求**：
- [x] 引入 `structlog` 或标准 `logging` + JSON formatter
- [x] QueryLoop 关键节点写 structured log（turn_start, provider_call, tool_exec, turn_end）
- [x] log 中携带 trace_id / session_id / turn_id
- [x] 默认不输出，通过 `ObservabilityConfig.logging_enabled` 开启

**文件**：
- 新建 `src/agentos/observability/logging.py`
- 修改 `src/agentos/observability/config.py`
- 修改 `src/agentos/runtime/query_loop.py`
- 测试 `tests/observability/test_structured_logging.py`

---

### 5. Rate Limiting (Channel 层)

**现状**：ASGI channel 无限流保护。
**要求**：
- [x] 基于 session_id 的滑动窗口限流（默认 60 req/min）
- [x] 超限返回 429 + `Retry-After` header
- [x] 可注入自定义 `RateLimiter` Protocol

**文件**：
- 新建 `src/agentos/channels/rate_limit.py`
- 修改 `src/agentos/channels/asgi.py`
- 测试 `tests/channels/test_rate_limit.py`

---

## P1: 开发者体验

### 6. README & Quickstart

**现状**：README.md 基本为空，无 quickstart。
**要求**：
- [x] 项目 README：一句话定位 + 安装 + 最小示例（5 行代码跑起来）
- [x] `docs/quickstart.md`：从零构建一个 tool-calling agent（10 分钟）
- [x] `docs/architecture.md`：模块依赖图 + 数据流图（可用 mermaid）

**文件**：
- 修改 `README.md`
- 新建 `docs/quickstart.md`
- 新建 `docs/architecture.md`

---

### 7. 更多 Examples

**现状**：只有一个 `small_openai_agent.py`。
**要求**：
- [x] `examples/streaming_agent.py` — SSE streaming 部署
- [x] `examples/multi_agent_dispatch.py` — expert dispatch 完整示例
- [x] `examples/mcp_agent.py` — 连接 MCP server
- [x] `examples/persistent_agent.py` — session 持久化 + 恢复
- [x] 每个 example 可独立运行，有 `if __name__ == "__main__"` 入口

**文件**：
- 新建 `src/agentos/examples/` 下各文件

---

### 8. CLI Scaffold

**现状**：无 CLI 工具。
**要求**：
- [x] `agent-os init` 生成项目骨架（pyproject.toml + agent 配置）
- [x] `agent-os run` 启动 ASGI server（内嵌 uvicorn）
- [x] `agent-os migrate` 执行 Postgres migrations

**文件**：
- 新建 `src/agentos/cli/` 模块
- 修改 `pyproject.toml`（加 `[project.scripts]`）

---

## P2: 稳定性与健壮性

### 9. Provider Timeout 配置

**现状**：Provider 无统一 timeout 控制。
**要求**：
- [x] `Provider` Protocol 增加可选 `timeout_seconds` 参数
- [x] AnthropicProvider / OpenAIProvider / OpenAICompatibleProvider 支持连接/读取超时
- [x] 超时产生 `ProviderTimeoutError`，可触发 retry

**文件**：
- 修改 `src/agentos/providers/base.py`
- 修改各 provider 实现
- 测试 `tests/providers/test_provider_timeout.py`

---

### 10. Input Validation & Sanitization

**现状**：Channel 层对请求体只做基本 JSON parse，无 schema 校验。
**要求**：
- [x] `ChannelTurnRequest` 加 max_message_length 校验（防止 prompt injection payload 过大）
- [x] tool arguments 经过 JSON schema 校验后再传给 handler
- [x] 敏感字段（api_key、connection string）不出现在 error message 和 log 中

**文件**：
- 修改 `src/agentos/channels/types.py`
- 修改 `src/agentos/capabilities/executor.py`
- 修改 `src/agentos/observability/config.py`（Redactor 增强）
- 测试 `tests/channels/test_input_validation.py`、`tests/capabilities/test_tool_argument_validation.py`

---

### 11. Connection Pool & Reconnect

**现状**：PostgresTaskStore / RedisAgentMessageQueue 每实例一个连接，无池化和断线重连。
**要求**：
- [x] Postgres adapter 支持 connection pool（psycopg_pool）
- [x] Redis adapter 支持 ConnectionPool + auto-reconnect
- [x] 连接不可用时抛明确 `BackendUnavailableError`，上层可 retry

**文件**：
- 修改 `src/agentos/multi/postgres_tasks.py`
- 修改 `src/agentos/multi/redis_queue.py`
- 修改 `src/agentos/persistence/postgres.py`
- 测试 `tests/multi/test_connection_backends.py`

---

### 12. CI / Pre-commit

**现状**：无 CI 配置。
**要求**：
- [x] `.github/workflows/ci.yml`：pytest + compileall + ruff lint
- [x] `ruff.toml` 或 `pyproject.toml [tool.ruff]` 配置
- [x] pre-commit hooks（ruff format + ruff check）

**文件**：
- 新建 `.github/workflows/ci.yml`
- 修改 `pyproject.toml`（加 ruff 配置）
- 新建 `.pre-commit-config.yaml`

---

## P3: 功能补齐

### 13. Outbox Reconciler

**现状**：outbox 表和写入已存在，但无后台 reconciler 补发通知。
**要求**：
- [x] 后台 task 周期性扫描 `agentos_multi_agent_task_outbox` 中 `delivered_at IS NULL` 的行
- [x] 对每条未投递记录重新 send result_ready envelope
- [x] 投递成功后标记 `delivered_at`
- [x] 可配置扫描间隔和批次大小

**文件**：
- 新建 `src/agentos/multi/reconciler.py`
- 测试 `tests/multi/test_outbox_reconciler.py`

---

### 14. Redis Pending / Retry (XPENDING + XCLAIM)

**现状**：RedisAgentMessageQueue 支持 delivery/ack，但 worker crash 后 pending message 不会被重新领取。
**要求**：
- [x] 定期调用 `XPENDING` 检查 idle 超过阈值的 message
- [x] 用 `XCLAIM` 重新分配给当前 consumer
- [x] 超过最大重试次数的 message 转入 dead-letter stream
- [x] 可配置 idle_threshold / max_retries

**文件**：
- 修改 `src/agentos/multi/redis_queue.py`
- 测试 `tests/multi/test_redis_pending_retry.py`

---

### 15. Cross-Node Continuation Trigger

**现状**：`ContinuationTrigger` 只有 `LocalContinuationTrigger`，依赖本地 Event。
**要求**：
- [x] 新增 `RedisContinuationTrigger`，通过 Redis Pub/Sub 跨节点通知 parent agent
- [x] fallback 到 polling TaskStore（给不依赖 Redis 的部署）
- [x] 与 ExpertAgentRunner 集成测试

**文件**：
- 新建 `src/agentos/multi/redis_continuation.py`
- 测试 `tests/multi/test_redis_continuation.py`

---

### 16. Live Integration Tests (Postgres + Redis)

**现状**：所有 Postgres/Redis 测试用 fake connection/client。
**要求**：
- [x] `tests/integration/` 目录，pytest mark `@pytest.mark.integration`
- [x] Docker Compose 配置（postgres + redis）
- [x] CI 中可选运行 integration tests
- [x] 覆盖：concurrent claim、cancel race、outbox delivery、stream retry

**文件**：
- 新建 `tests/integration/`
- 新建 `docker-compose.test.yml`
- 修改 `.github/workflows/ci.yml`

---

## 优先级与工作量估算

| 编号 | 优先级 | 预计工作量 | 依赖 |
|---|---|---|---|
| 1. Provider Retry | P0 | 200 LOC | 无 |
| 2. Graceful Shutdown | P0 | 150 LOC | 无 |
| 3. Health Endpoint | P0 | 80 LOC | 无 |
| 4. Structured Logging | P0 | 200 LOC | 无 |
| 5. Rate Limiting | P0 | 120 LOC | 无 |
| 6. README & Quickstart | P1 | 文档 | 无 |
| 7. Examples | P1 | 400 LOC | 无 |
| 8. CLI Scaffold | P1 | 300 LOC | 7 |
| 9. Provider Timeout | P2 | 100 LOC | 1 |
| 10. Input Validation | P2 | 150 LOC | 无 |
| 11. Connection Pool | P2 | 200 LOC | 无 |
| 12. CI / Pre-commit | P2 | 配置 | 无 |
| 13. Outbox Reconciler | P3 | 200 LOC | 无 |
| 14. Redis Pending/Retry | P3 | 200 LOC | 无 |
| 15. Cross-Node Continuation | P3 | 250 LOC | 14 |
| 16. Live Integration Tests | P3 | 400 LOC | 11, 13, 14 |

**推荐执行顺序**：1 → 2 → 3 → 4 → 5 → 12 → 9 → 10 → 11 → 6 → 7 → 8 → 13 → 14 → 15 → 16

---

## 非目标

- 不加 DAG / state graph 编排（保持 linear tool loop 的简洁性）
- 不加内置 code interpreter 沙箱
- 不加 browser automation
- 不引入 LangChain / LlamaIndex 依赖
- 不改变 zero-dependency 核心原则（所有生产 adapter 保持 optional extras）
