# Phase E — 生产硬化横切：背压、并发限制、可观测性与负载基线

## Status

**Draft — 待 Phase A–D 落地后实施。**

**推荐实施顺序(经 review 调整):D → A → C → B(B1→B2→B3→B4)→ E。本 spec(E)为最后一位,依赖 A–C 落地;实现时务必采用上面修正后的并发注入语义和 outbound-only trace 范围。**

本 spec 依赖以下四个先行 spec 全部落地：

- Phase A：`2026-05-28-async-runtime-completion-design.md`（全异步 runtime）
- Phase B：`2026-05-28-persistent-session-provider-design.md`（跨节点 session + drain）
- Phase C：`2026-05-28-cross-machine-a2a-dispatch-design.md`（出站 A2A HTTP transport）
- Phase D：`2026-05-28-execution-backend-sandbox-seam-design.md`（执行后端 seam）

Phase A–D 新增的 async query loop 路径、跨机 A2A dispatch 路径，在本 spec 实施前**没有并发保护、没有完整 trace 链**。本 spec 横切补齐这两个缺口，同时留下可重复运行的性能基线脚本。

---

## Goal

为 Phase A–C 产出的新路径补充三项生产横切能力：

1. **并发限制 + 背压**：per-node in-flight turn 上限，超限返回 HTTP 429 + `Retry-After`；并发限制器以 Protocol seam 形式注入，不内置策略。
2. **可观测性补全**：async query loop 的每次 provider 调用和 cross-machine A2A dispatch hop 必须有完整 span + EventRecord，确保分布式 trace 链端到端可见。
3. **负载基线脚本**：`scripts/load_test_baseline.py`，可重复执行，输出 QPS / p99 延迟 / 内存数字，作为回归参考（不是 CI gate）。

**强目标**：
- 第 N+1 个并发 turn（超过配置上限）在 `__call__` 入口返回 HTTP 429，body 含 `retry_after_seconds`，header 含 `Retry-After`。
- 跨机 dispatch 产生一条端到端连通的 trace，从调用方 span → `RemoteTaskExecutor._run` 内 span → 远端 `/a2a/tasks` 入口 span，三段共享同一 `trace_id`。
- `scripts/load_test_baseline.py` 单次执行后在 stdout 打印 QPS、p99 延迟（ms）、RSS 内存（MB）三行数字。

---

## Design References

- `src/agentos/channels/rate_limit.py` — `RateLimiter` Protocol + `SlidingWindowRateLimiter`（并发限制器完全镜像此结构）
- `src/agentos/channels/asgi.py` — `AsgiAgentApp.__call__` 的 rate-limit 注入点（L124–L133）；429 response shape（L127–L133）；`_sse_turns_by_session` in-flight 计数结构（L66–L68）
- `src/agentos/observability/tracer.py` — `Tracer` / `Span` Protocol；`InMemoryTracer.use_incoming_headers` W3C traceparent 提取（L217–L238）；`inject_headers`（L240–L246）
- `src/agentos/observability/events.py` — `EventRecord` / `EventLog.record`（L9–L40）
- `src/agentos/observability/conventions.py` — 现有 span attribute 常量（全文）
- `src/agentos/runtime/async_query_loop.py` — `AsyncQueryLoop._run_provider_loop_stream`（L238–L380）；当前无 span 包裹
- `src/agentos/multi/remote.py` — `RemoteTaskExecutor._run`（L47–L63）；当前无 span / 无 trace header 注入
- `src/agentos/multi/coordinator.py` — `AgentCoordinator._submit_remote_task`（L252–L275）；`_trace_context()`（L641–L644）已调用 `inject_trace_headers`，但 `RemoteTaskExecutor` 侧尚无 span
- `src/agentos/channels/a2a.py` — `A2AAdapter.send_task`（L86–L112）；`request.trace_context` 已作为 HTTP header 透传，但发送侧未开 span

---

## Contracts

### 1. ConcurrencyLimiter Protocol（镜像 `RateLimiter`）

```python
# src/agentos/channels/concurrency.py（新文件）

from dataclasses import dataclass
import threading
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ConcurrencyDecision:
    """并发判断结果。"""

    allowed: bool
    retry_after_seconds: int = 0


class ConcurrencyLimiter(Protocol):
    """Channel 层可注入并发限制器边界。"""

    def acquire(self, key: str) -> ConcurrencyDecision:
        """尝试占用一个并发 slot；allowed=True 时调用方必须在 turn 结束后调用 release。"""

    def release(self, key: str) -> None:
        """释放一个并发 slot。"""


class InFlightConcurrencyLimiter:
    """基于内存计数的 per-key in-flight 并发限制器。"""

    def __init__(
        self,
        *,
        max_concurrent: int = 10,
        retry_after_seconds: int = 5,
    ) -> None: ...

    def acquire(self, key: str) -> ConcurrencyDecision: ...
    def release(self, key: str) -> None: ...
```

**设计约束**：
- `key` 与 `RateLimiter` 保持一致，当前传 `session_id`；node-level 上限可传固定 key `"node"`。
- `acquire` + `release` 配对调用责任及注入位置按 turn 类型区分（见下）：
  - **JSON turn**（POST `/v1/sessions/{id}/turns`）：在请求入口处 acquire，在 `finally` 块 release。
  - **SSE new turn**：acquire 在 `_create_sse_turn_entry` 内部（创建新 `SseTurnEntry` 时）；release 在 turn runner 完成 / grace-cancel / GC 时（即 `_close_sse_reader` / `_schedule_sse_grace` / GC 路径的 `finally`）。
  - **SSE resume**（`_handle_sse_resume`）：只读已有 buffer，**不 acquire、不消耗 slot**。
- 并发 slot 绑定 turn 生命周期，不绑定连接生命周期；SSE resume 是读已有 buffer，不占新 slot。
- `max_concurrent` 是数字，具体值由部署方注入；SDK 不内置默认策略，只提供 `InFlightConcurrencyLimiter` 作为参考实现。

### 2. AsgiAgentApp 注入点

```python
# src/agentos/channels/asgi.py — __init__ 新增参数

concurrency_limiter: ConcurrencyLimiter | None = None,
```

`__call__` 内 rate-limit check 之后（L133 之后）、`_handle_sse_turn` / `asyncio.to_thread` 之前，仅对 **JSON turn** 插入 acquire/release：

```python
if self._concurrency_limiter is not None:
    decision = self._concurrency_limiter.acquire(session_id)
    if not decision.allowed:
        await self._send_json(
            send,
            429,
            {"status": "failed", "error": "concurrency limit exceeded"},
            headers=[(b"retry-after", str(decision.retry_after_seconds).encode("ascii"))],
        )
        return
    # release 在 finally 块
```

**SSE new turn**：acquire 发生在 `_create_sse_turn_entry` 内（创建 `SseTurnEntry` 时），release 在 `_close_sse_reader` / `_schedule_sse_grace` / GC 路径的 `finally`——**不在 `__call__` 入口**。
**SSE resume**（`_handle_sse_resume`）：不插入任何 acquire/release，直接读 buffer。

### 3. 新增 EventRecord 类型

以下事件类型需新增到 `src/agentos/runtime/event_bus.py`（或 `events/types.py`，与现有位置保持一致）：

| 事件类型名 | 触发位置 | 关键字段 |
|-----------|---------|---------|
| `AsyncTurnStartedEvent` | `AsyncQueryLoop.run_turn_stream` 入口 | `session_id`, `turn_id` |
| `AsyncTurnCompletedEvent` | `AsyncQueryLoop.run_turn_stream` 完成 | `session_id`, `turn_id`, `elapsed_seconds` |
| `AsyncProviderCallStartedEvent` | `_run_provider_loop_stream` 每次 provider 调用前 | `session_id`, `turn_id`, `iteration` |
| `AsyncProviderCallCompletedEvent` | `_run_provider_loop_stream` provider 返回后 | `session_id`, `turn_id`, `iteration` |
| `RemoteTaskDispatchStartedEvent` | `RemoteTaskExecutor._run` 调用 `send_task` 前 | `task_id`, `target_agent_id`, `trace_id` |
| `RemoteTaskDispatchCompletedEvent` | `RemoteTaskExecutor._run` `send_task` 返回后 | `task_id`, `status`, `elapsed_seconds` |

> 如果 Phase A 已经在 `AsyncQueryLoop` 补了 `TurnStartedEvent`/`TurnCompletedEvent`，则 `AsyncTurnStartedEvent`/`AsyncTurnCompletedEvent` 合并到同名事件，不重复新增。实现前先检查。

### 4. 新增 Span 覆盖点

**async_query_loop.py — `_run_provider_loop_stream`**

每次 `while True` 循环迭代用 `tracer.start_span("agentos.async_provider_call")` 包裹，span attributes：

```
agentos.session.id  = session_id（从 sync_loop._event_context 取）
agentos.turn.id     = turn_id
agentos.iteration   = iterations（int）
```

`AsyncQueryLoop` 需接收可选 `tracer: Tracer | None = None`（或复用 `sync_loop` 上已有的 tracer 引用，取决于 Phase A 实现形状）。

**remote.py — `RemoteTaskExecutor._run`**

```python
# _run 内，send_task 调用前后

with tracer.start_span("agentos.a2a.remote_dispatch") as span:
    span.set_attributes({
        "agentos.task.id": request.task_id,
        "agentos.agent.target": card.agent_id,
    })
    headers: dict[str, str] = {}
    inject_trace_headers(headers)
    outbound_request = replace(
        request,
        trace_context=headers if headers else request.trace_context,
    )
    result = self._a2a_adapter.send_task(card, outbound_request)
```

`RemoteTaskExecutor.__init__` 新增 `tracer: Tracer | None = None`；`_run` 内 `tracer or NoOpTracer()`。

**Trace header 透传（现状审计）**：

- `AgentCoordinator._trace_context()`（L641）已调用 `inject_trace_headers(headers)` 并写入 `TaskRequest.trace_context`。
- `A2AAdapter.send_task`（L95–L100）已把 `request.trace_context` 作为 HTTP headers 发出。
- 远端 `a2a_server.py:55` 已在 `_handle_a2a_task` 入口调用 `use_incoming_trace_headers(headers)`，**inbound 侧 trace 提取已正确工作**。

**实际 trace 链断点（outbound 侧）**：

- `RemoteTaskExecutor._run`（`src/agentos/multi/remote.py`）和 transport hop **没有发出任何 span / EventRecord**。
- `AsyncQueryLoop`（`src/agentos/runtime/async_query_loop.py`）**没有 tracer 字段，也没有 span 包裹**。

Phase E 的可观测性工作集中在补齐这两处 **outbound span**，inbound 侧无需改动。

`AsgiAgentApp.__init__` 新增 `tracer: Tracer | None = None`（用于并发/背压路径的 span，非 inbound trace 提取）。

### 5. 新增 conventions 常量

```python
# src/agentos/observability/conventions.py 新增

AGENTOS_TASK_ID        = "agentos.task.id"
AGENTOS_AGENT_TARGET   = "agentos.agent.target"
AGENTOS_ITERATION      = "agentos.iteration"
AGENTOS_A2A_HOP        = "agentos.a2a.hop"
```

---

## File Change Map

| 文件 | 变更类型 | 关键改动 |
|------|---------|---------|
| `src/agentos/channels/concurrency.py` | **新增** | `ConcurrencyDecision`、`ConcurrencyLimiter` Protocol、`InFlightConcurrencyLimiter` 实现 |
| `src/agentos/channels/asgi.py` | **修改** | `__init__` 加 `concurrency_limiter`、`tracer`；JSON turn 入口插入 acquire/release；`_create_sse_turn_entry` 内插入 SSE new turn acquire；resume 路径不变 |
| `src/agentos/channels/__init__.py` | **修改** | 导出 `ConcurrencyLimiter`、`ConcurrencyDecision`、`InFlightConcurrencyLimiter` |
| `src/agentos/runtime/async_query_loop.py` | **修改** | `AsyncQueryLoop` 加 `tracer: Tracer | None` 字段；`_run_provider_loop_stream` 每次迭代加 span；emit `AsyncTurnStartedEvent` / `AsyncTurnCompletedEvent` |
| `src/agentos/multi/remote.py` | **修改** | `RemoteTaskExecutor.__init__` 加 `tracer: Tracer | None`；`_run` 加 span 包裹 + emit `RemoteTaskDispatchStartedEvent` / `RemoteTaskDispatchCompletedEvent` |
| `src/agentos/runtime/event_bus.py` | **修改** | 新增第 3 节列出的 6 个 `*Event` dataclass（先检查 Phase A 是否已添加 Turn 事件） |
| `src/agentos/observability/conventions.py` | **修改** | 新增第 5 节 4 个常量 |
| `scripts/load_test_baseline.py` | **新增** | 负载基线脚本（见下节） |
| `tests/channels/test_concurrency_limiter.py` | **新增** | `InFlightConcurrencyLimiter` 单元测试 |
| `tests/channels/test_asgi_concurrency.py` | **新增** | 并发 N+1 返回 429 集成测试 |
| `tests/runtime/test_async_query_loop_spans.py` | **新增** | async loop span 覆盖测试（用 `InMemoryTracer`） |
| `tests/multi/test_remote_trace.py` | **新增** | 跨机 dispatch span 连通测试（用 `InMemoryTracer`） |

---

## 负载基线脚本（`scripts/load_test_baseline.py`）

脚本目标：无外部依赖（标准库 + `agentos` 本身），对 `AsgiAgentApp` 做 in-process 并发压测，输出三行数字。

```python
# scripts/load_test_baseline.py
# Usage: python scripts/load_test_baseline.py [--concurrency N] [--turns M]
# Output (stdout, 3 lines):
#   QPS: 42.3
#   p99_latency_ms: 187
#   rss_mb: 54.2
```

实现要点：
- 用 `FakeProvider`（`src/agentos/providers/fake.py`）避免真实 LLM 调用。
- `asyncio.gather` 并发发送 `--turns` 个 turn，计时、统计 p99。
- `resource.getrusage(resource.RUSAGE_SELF).ru_maxrss` 取内存（macOS 单位 bytes / Linux 单位 KB，脚本内自动换算）。
- 脚本参数默认：`--concurrency 20`、`--turns 100`。
- 脚本完成后打印基线数字到 stdout，不 `sys.exit(1)` on any threshold——这是参考基线，不是 CI gate。

---

## Acceptance Criteria

1. **429 背压**：`InFlightConcurrencyLimiter(max_concurrent=2)` 注入后，第 3 个并发 turn 请求在 `__call__` 入口返回 HTTP 429，body `{"status":"failed","error":"concurrency limit exceeded"}`，header 含 `Retry-After`；前 2 个正常完成后再发第 3 个则 200。
2. **rate-limit 路径不受影响**：现有 `SlidingWindowRateLimiter` 的 429 路径（`asgi.py` L124–L133）逻辑不变，回归测试仍绿。
3. **async loop span 覆盖**：`AsyncQueryLoop` 配 `InMemoryTracer` 跑一个双轮工具调用 turn，`tracer.records` 中存在两条 `"agentos.async_provider_call"` span，均含 `agentos.session.id`、`agentos.turn.id`、`agentos.iteration`，且 `parent_span_id` 指向同一父 span。
4. **跨机 dispatch trace 连通**：`AgentCoordinator` 派发到 `endpoint` 非空的 card，经 `RemoteTaskExecutor._run` 触发 `A2AAdapter.send_task`；`RemoteTaskExecutor._run` 新增 `agentos.a2a.remote_dispatch` span，在该 span 内新建 headers 并调用 `inject_trace_headers`，用生成的 headers clone/replace `TaskRequest.trace_context` 后再传给 `send_task`，确保远端 parent 指向 remote dispatch span，而不是上游 coordinator span。远端 `a2a_server.py` 的 `use_incoming_trace_headers` 已正确提取（无需修改）。`InMemoryTracer` 验证调用方 span、`agentos.a2a.remote_dispatch` span、远端 span 三段共享同一 `trace_id`。
5. **EventRecord 完整性**：上述两条路径各自 emit 的 `*Event` 均可被 `EventLog.record` 记录，`event_type` 字符串与 dataclass 类名一致。
6. **负载基线脚本可运行**：`python scripts/load_test_baseline.py` 在无网络、无外部服务的情况下正常完成，stdout 输出包含 `QPS:`、`p99_latency_ms:`、`rss_mb:` 三行。
7. **diff surgical**：JSON turn 端点、health/ready 端点、现有 rate-limit 路径、SSE buffer/resume 逻辑一律不动；`SNAPSHOT_VERSION` 不变；现有全部测试仍绿。

---

## Risks & Non-Goals

### Risks

- **Phase A–C 接口未定**：`AsyncQueryLoop` 最终是否持有 `tracer` 字段、`RemoteTaskExecutor` 是否已改为 async，取决于 Phase A/C 落地形状。实现前先读两文件的最新版本，如与本 spec 合同冲突立即 STOP 报告，不猜不推断。
- **并发计数器线程安全**：SSE turn 的 release 在 `asyncio` 事件循环中，JSON turn 的 release 可能在 `to_thread` 线程；`InFlightConcurrencyLimiter` 内部必须用 `threading.Lock`（而非 asyncio.Lock）保证跨线程安全。
- **key 粒度**：当前设计 `key = session_id`（per-session 上限）与 `key = "node"`（per-node 上限）可组合。本 spec 只定义 seam；部署方按需选 key。

### Non-Goals

- 并发限制**不内置默认上限数字**：`InFlightConcurrencyLimiter` 只是参考实现，不在 `AsgiAgentApp` 里设默认值；不注入 `concurrency_limiter` 时行为与当前完全一致（无限制）。
- 负载测试脚本**不成为 CI gate**：`scripts/load_test_baseline.py` 不进 `pytest`，不做 threshold 断言，只输出数字供人工对比。
- **不做分布式速率聚合**：`SlidingWindowRateLimiter` 和 `InFlightConcurrencyLimiter` 均是单节点内存实现；跨节点聚合属 Phase B+ 范畴（分布式 session provider 层）。
- **不改 sync `QueryLoop`**：本 spec 只给 `AsyncQueryLoop` 加 span；sync loop 已有 `InstrumentedQueryLoop` 包装，不重复。
- **不做 Langfuse / OTel 对接层**：span 层面只加 `tracer.start_span()` 调用；具体导出目标由部署方通过 `ObservabilityConfig` 注入，本 spec 不新增 exporter。
