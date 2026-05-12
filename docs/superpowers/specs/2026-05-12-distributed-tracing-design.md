# 分布式 Trace Context 传播设计规范

**日期**：2026-05-12
**状态**：已定稿，待实现
**关联文件**：`src/agentos/multi/`, `src/agentos/channels/a2a.py`, `src/agentos/observability/`

---

## 1. 问题描述

当前 `RemoteTaskExecutor` 向远端 agent 派发任务时，发送的 payload 不携带任何 trace 上下文：

```python
# channels/a2a.py - A2AAdapter.send_task()
payload = {
    "task_id": request.task_id,
    "instruction": request.instruction,
    "allowed_tool_names": list(request.allowed_tool_names),
    "timeout_seconds": request.timeout_seconds,
    # ← trace_id / traceparent 全部缺失
}
response = self._transport.post_json(url, payload, timeout_seconds)
```

结果：**跨 agent 请求链在 observability 层断裂**。

具体表现：
- 父 agent 的 span tree 在调用 `dispatch()` 时终止，子 span 无法关联到同一 trace
- Langfuse / OTEL collector 上看到两条独立 trace，无法还原调用链
- `AgentCoordinator.spawn()` 创建本地子 agent 时，`SpawnExecutor` 把 task 提交到新线程，`ContextVar` 不跨线程传播，本地多 agent 链路同样断裂

---

## 2. W3C Trace Context 简述

W3C Trace Context（[W3C TR](https://www.w3.org/TR/trace-context/)）定义两个标准 HTTP header：

| Header | 格式 | 用途 |
|--------|------|------|
| `traceparent` | `00-{trace_id:32hex}-{parent_span_id:16hex}-{flags:2hex}` | 携带 trace_id 和 parent span |
| `tracestate` | `key=value,key=value,...` | 携带 vendor-specific 扩展（Langfuse, Datadog 等） |

示例：
```
traceparent: 00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01
tracestate: langfuse=xxx,dd=yyy
```

agentos 现有 `InMemoryTracer` 和 `observability/context.py` 已实现 W3C traceparent 的 inject/extract。本设计复用这套机制，**不引入新的 trace header 格式**。

---

## 3. 各层传播设计

```
父 Agent (AgentCoordinator.dispatch / spawn)
  │
  ├─ [remote dispatch] → A2ATransport.post_json(headers={traceparent, tracestate})
  │                          │
  │                    A2AServerAdapter.handle_task(payload, headers)
  │                          │
  │                    use_incoming_headers(headers) → 子 Agent span 继承 trace_id
  │
  └─ [local spawn]  → SpawnExecutor.submit(task_id, run, trace_context)
                           │
                     新线程中 copy_context().run(lambda: ...)
                     或显式传入 trace_context dict，子线程还原 ContextVar
```

### 3.1 远程 dispatch 传播路径

1. `AgentCoordinator._submit_remote_task()` 调用前，从当前 tracer 注入 headers
2. `A2AAdapter.send_task()` 把 headers 传给 `A2ATransport.post_json()`
3. `UrllibA2ATransport.post_json()` 把 headers 写入 HTTP 请求
4. 远端 `AsgiAgentApp` 从 ASGI scope 提取 headers
5. `A2AServerAdapter.handle_task()` 接收 headers，用 `use_incoming_headers()` 恢复 trace context
6. 子 agent 的所有 span 自动挂在父 trace 下

### 3.2 本地 spawn 传播路径

`ContextVar` 不跨线程自动传播，需要以下两种策略之一：

**策略 A（推荐）**：`copy_context().run()`
```python
import contextvars
ctx = contextvars.copy_context()
executor.submit(ctx.run, _run_task, ...)
```
`copy_context()` 捕获当前所有 `ContextVar`（包括 `_DEFAULT_TRACE_PROPAGATOR`, `_CURRENT_OBSERVABILITY_CONTEXT`），在新线程中完整还原。

**策略 B**：`trace_context: dict[str, str]` 显式传递
把 `{traceparent: ..., tracestate: ...}` 序列化为 dict 传入新线程，线程内用 `use_incoming_headers()` 还原。适合无法用 `copy_context()` 的场景（如跨进程序列化）。

本设计**两者都支持**：`SpawnExecutor` 优先用策略 A（更透明），`TaskRequest.trace_context` 字段支持策略 B（远程和序列化场景）。

---

## 4. 类型变更

### 4.1 TaskRequest 追加 trace_context 字段

```python
# src/agentos/multi/types.py

@dataclass(frozen=True, slots=True)
class TaskRequest:
    """跨 agent 派发的任务请求。"""

    task_id: str
    instruction: str
    allowed_tool_names: tuple[str, ...] = ()
    timeout_seconds: float = 300
    trace_context: dict[str, str] | None = None  # 新增：W3C Trace Context headers
```

`trace_context` 为可选字段，默认 `None`，完全向后兼容：现有构造 `TaskRequest` 的代码不需要改动。

字段语义：key 为 HTTP header 名（小写），value 为 header 值。示例：
```python
{"traceparent": "00-abc...-01", "tracestate": "langfuse=xyz"}
```

---

## 5. 签名变更

### 5.1 A2ATransport.post_json()

```python
# 现状
def post_json(self, url: str, payload: dict, timeout_seconds: float) -> dict: ...

# 变更后（新增可选 headers 参数）
def post_json(
    self,
    url: str,
    payload: dict[str, object],
    timeout_seconds: float,
    *,
    headers: dict[str, str] | None = None,  # 新增
) -> dict[str, object]: ...
```

`headers` 为可选关键字参数，默认 `None`（等价于无额外 header），向后兼容。

### 5.2 UrllibA2ATransport.post_json()

```python
def post_json(self, url, payload, timeout_seconds, *, headers=None):
    body = json.dumps(payload).encode("utf-8")
    merged_headers = {"Content-Type": "application/json"}
    if headers:
        merged_headers.update(headers)
    request = urllib_request.Request(
        url, data=body, headers=merged_headers, method="POST"
    )
    with urllib_request.urlopen(request, timeout=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))
```

### 5.3 A2AAdapter.send_task()

```python
def send_task(self, card: AgentCard, request: TaskRequest) -> TaskResult:
    payload = {
        "task_id": request.task_id,
        "instruction": request.instruction,
        "allowed_tool_names": list(request.allowed_tool_names),
        "timeout_seconds": request.timeout_seconds,
    }
    # 从 request.trace_context 提取 trace headers
    trace_headers = dict(request.trace_context) if request.trace_context else {}

    response = self._transport.post_json(
        self._url(card, "/a2a/tasks"),
        payload,
        request.timeout_seconds,
        headers=trace_headers or None,  # None 时不发 header
    )
    return TaskResult(...)
```

### 5.4 AgentCoordinator.dispatch() / spawn()

在构造 `TaskRequest` 时注入当前 trace context：

```python
# 在 dispatch() 和 spawn() 中，构造 TaskRequest 之前
from agentos.observability import inject_trace_headers

trace_headers: dict[str, str] = {}
inject_trace_headers(trace_headers)  # 从 ContextVar 写入 traceparent / tracestate

request = TaskRequest(
    task_id=task_id,
    instruction=instruction,
    allowed_tool_names=tuple(allowed_tool_names),
    timeout_seconds=timeout_seconds,
    trace_context=trace_headers if trace_headers else None,
)
```

`inject_trace_headers()` 在 `observability/context.py` 已存在，此处直接复用。当无活跃 span 时（`NoOpTracer`），`trace_headers` 为空 dict，`trace_context=None`，不影响现有行为。

### 5.5 A2AServerAdapter.handle_task()

```python
# 现状
def handle_task(self, payload: dict[str, object]) -> dict[str, object]: ...

# 变更后（新增可选 headers 参数）
def handle_task(
    self,
    payload: dict[str, object],
    headers: dict[str, str] | None = None,  # 新增
) -> dict[str, object]:
    from agentos.observability.context import use_default_trace_propagator
    from agentos.observability.tracer import InMemoryTracer  # 或 OTEL tracer

    propagator = _DEFAULT_TRACE_PROPAGATOR.get()
    with propagator.use_incoming_headers(headers):
        try:
            request = self._parse_request(payload)
            result = self._runner.run_task(request)
        except Exception as error:
            result = TaskResult(...)
    return self._result_to_dict(result)
```

### 5.6 AsgiAgentApp A2A handler

```python
# asgi.py - _handle_a2a_task()
async def _handle_a2a_task(self, receive, send):
    body = await self._read_body(receive)
    headers = self._headers(scope)  # 已有此方法
    payload = json.loads(body.decode())
    result = self._a2a_server.handle_task(payload, headers=headers)  # 传入 headers
    await self._send_json(send, 200, result)
```

---

## 6. SpawnExecutor trace context 传播

### 6.1 copy_context() 方案（策略 A）

```python
# src/agentos/multi/spawn.py

import contextvars

class SpawnExecutor:
    def submit(
        self,
        task_id: str,
        run: Callable[[], TaskResult],
    ) -> Future[TaskResult]:
        # copy_context() 捕获父线程所有 ContextVar，包括 tracer
        ctx = contextvars.copy_context()
        return self._executor.submit(ctx.run, run)
```

这是**最小改动**：只需一行 `ctx = contextvars.copy_context()`，父线程的 trace propagator、observability context 全部在子线程中生效。

### 6.2 TaskRequest.trace_context 方案（策略 B 补充）

当 `copy_context()` 不满足需求（如子 agent 需要序列化 task 到其他进程/服务）时，从 `TaskRequest.trace_context` 还原：

```python
def _run_spawned_subagent(self, ..., request: TaskRequest) -> TaskResult:
    if request.trace_context:
        propagator = _DEFAULT_TRACE_PROPAGATOR.get()
        with propagator.use_incoming_headers(request.trace_context):
            return self._run_agent(request)
    return self._run_agent(request)
```

---

## 7. TraceContextExtractor / TraceContextInjector 协议

为让 trace context 传播在不依赖 `InMemoryTracer` 具体实现的情况下可测试，新增两个轻量协议：

```python
# src/agentos/observability/tracer.py（追加）

class TraceContextInjector(Protocol):
    """把当前 trace context 写入 dict 的边界。"""

    def inject(self, carrier: MutableMapping[str, str]) -> None:
        """写入 trace context headers（如 traceparent, tracestate）。"""


class TraceContextExtractor(Protocol):
    """从 dict 提取 trace context 的边界。"""

    def extract(
        self,
        carrier: Mapping[str, str] | None,
    ) -> ContextManager[None]:
        """在当前作用域内应用提取到的 trace context。"""
```

`TraceContextPropagator`（已存在）已同时实现了这两个角色（`inject_headers` + `use_incoming_headers`），两个新协议是对已有接口的细粒度拆分，供测试和工厂方法使用。

---

## 8. OTEL 集成点

### 8.1 不强依赖 opentelemetry

`TaskRequest.trace_context` 是纯 `dict[str, str]`，不引入任何 OTEL 类型。

### 8.2 OTEL 集成路径

当用户安装并配置了 OTEL tracer 时：

```python
from opentelemetry.propagate import inject, extract
from opentelemetry import context as otel_context

class OtelTraceContextPropagator:
    def inject(self, carrier):
        inject(carrier)  # 写入 traceparent + tracestate

    def extract(self, carrier):
        ctx = extract(carrier)
        token = otel_context.attach(ctx)
        try:
            yield
        finally:
            otel_context.detach(token)
```

通过 `use_default_trace_propagator(OtelTraceContextPropagator())` 替换默认 propagator，**无需修改任何 multi/ 或 channels/ 代码**，trace context 自动在所有层透传。

### 8.3 不依赖 OTEL 的 fallback

默认 propagator 为 `NoOpTracer`（不写任何 header，不提取任何 context），所有新增代码在无 OTEL 环境下安全运行：

- `inject_trace_headers(headers)` → 不修改 headers
- `use_incoming_headers(None)` → no-op context manager
- `TaskRequest.trace_context = None` → 不影响 A2A payload

---

## 9. 对现有代码的影响汇总

| 文件 | 变更类型 | 具体内容 |
|------|---------|---------|
| `multi/types.py` | 字段追加 | `TaskRequest.trace_context: dict[str,str] | None = None` |
| `multi/coordinator.py` | 逻辑追加 | `dispatch()` / `spawn()` 构造 TaskRequest 前 inject trace headers |
| `multi/spawn.py` | 逻辑追加 | `submit()` 用 `copy_context().run()` 包装 callable |
| `channels/a2a.py` | 签名扩展 | `A2ATransport.post_json()` 加 `headers` 参数；`A2AAdapter.send_task()` 传 trace_headers |
| `channels/a2a_server.py` | 签名扩展 | `A2AServerAdapter.handle_task()` 加 `headers` 参数；`with propagator.use_incoming_headers(headers)` |
| `channels/asgi.py` | 逻辑追加 | `_handle_a2a_task()` 传 `headers` 给 `handle_task()` |
| `observability/tracer.py` | 接口追加 | 新增 `TraceContextInjector` / `TraceContextExtractor` protocol（可选） |

所有变更均为**向后兼容追加**（新增可选字段 / 可选参数），不破坏现有测试。

---

## 10. 测试计划

### 10.1 unit tests：trace context 注入

```
tests/multi/test_trace_context_propagation.py
```

- `AgentCoordinator.dispatch()` 在有活跃 `InMemoryTracer` span 时，`TaskRequest.trace_context` 包含合法 `traceparent`
- `AgentCoordinator.dispatch()` 无活跃 span 时，`trace_context` 为 `None`
- `A2AAdapter.send_task()` 把 `trace_context` 翻译成 HTTP headers（mock transport 验证）

### 10.2 unit tests：trace context 提取

```
tests/channels/test_a2a_server_trace.py
```

- `A2AServerAdapter.handle_task(payload, headers={"traceparent": "00-abc...-01"})` 正确设置 trace context
- 子 agent 在该上下文内产生的 span `parent_span_id` 等于 incoming span id

### 10.3 unit tests：SpawnExecutor context 传播

```
tests/multi/test_spawn_trace_context.py
```

- 父线程有活跃 span，`SpawnExecutor.submit()` 用 `copy_context()` 后，子线程 span 继承 `trace_id`
- 父线程无活跃 span，子线程 span 独立产生新 trace_id

### 10.4 integration tests：端到端 trace

```
tests/multi/test_e2e_trace_propagation.py
```

- 本地多 agent（`AgentCoordinator` + `SpawnExecutor`）：父 span 和子 span 共享同一 `trace_id`
- 远程 agent（mock HTTP server）：子 agent 产生的 span `parent_span_id` 等于父 agent 调用 dispatch 时的 `span_id`

### 10.5 向后兼容测试

- 现有 `tests/multi/test_coordinator.py` / `tests/channels/test_a2a_server.py` 全部通过（无需传 headers）

---

## 11. 验收标准

- [ ] `TaskRequest.trace_context: dict[str, str] | None = None` 字段存在，现有构造不传时默认 `None`
- [ ] `AgentCoordinator.dispatch()` 有活跃 span 时，生成的 `TaskRequest.trace_context` 包含合法 W3C `traceparent`
- [ ] `AgentCoordinator.spawn()` 有活跃 span 时，`SpawnExecutor` 子线程继承父线程 trace context
- [ ] `A2ATransport.post_json()` 接受可选 `headers` 参数；`UrllibA2ATransport` 实现正确合并到 HTTP headers
- [ ] `A2AServerAdapter.handle_task()` 接受可选 `headers` 参数；用 `use_incoming_headers()` 恢复 trace context
- [ ] `AsgiAgentApp._handle_a2a_task()` 把请求 headers 传给 `handle_task()`
- [ ] 端到端测试：两个本地 agent 的 span 共享同一 `trace_id`（`InMemoryTracer`）
- [ ] 端到端测试：远程 agent span 的 `parent_span_id` 等于父 agent dispatch 时的 `span_id`
- [ ] 无 tracer 配置（`NoOpTracer`）时，所有变更代码静默通过，不影响功能
- [ ] 现有所有 `tests/multi/` 和 `tests/channels/` 测试继续通过
