# 跨机 A2A Dispatch 设计（Phase C）

## Status

**Draft — 待实现。**

现状梳理（诚实汇报，避免重复造轮子）：

- `RemoteTaskAdapter` Protocol 已在 `multi/remote.py:11`，`RemoteTaskExecutor` 已在 `multi/remote.py:18`。
- `coordinator.py` 的 `dispatch()` 已在 `L196-L231` 区分 `target.endpoint is not None` 并走 `_submit_remote_task`，`on_result` 回调 `_handle_remote_result`（含 `store_late_result` 路径）已全部实现。
- 现有 `A2AAdapter`（`channels/a2a.py:80`）**已经**实现了 `send_task(card, request) -> TaskResult`，内部用 `UrllibA2ATransport` 做 urllib POST，并将 `trace_context` dict 直接作为 HTTP headers 传出（`L99`），`A2AServerAdapter` 的 `handle_task` 通过 `use_incoming_trace_headers(headers)`（`a2a_server.py:55`）恢复 trace context。
- `registry/resolver.py` 中 `StaticResolver`（`L38`）和 `ServiceResolver`（`L85`）均已实现 `resolve/discover/select`；`ServiceResolver` 已支持 session affinity + round-robin。
- `RedisContinuationTrigger`（`multi/redis_continuation.py:26`）和 `OutboxReconciler`（`multi/reconciler.py:35`）已存在，和结果回收路径已接通。

**真正缺失的只有一件事**：`A2AAdapter` 底层 `UrllibA2ATransport` 使用阻塞 urllib，**没有** timeout-retry 和 httpx 连接池。Phase C 的核心交付是 `HttpA2ATransport`——用 httpx 替换 urllib 层，加 retry/timeout/连接池，作为 `async-http` extra 的参考实现；以及补一个面向 `AgentCoordinator` 的**端到端组装示例和集成测试**，验证跨进程 dispatch 链路真实可用。

本设计**不会重复定义 coordinator 的 dispatch 路径或 outbox reconciler**，它们已经正确存在。

**推荐实施顺序(经 review 调整):D → A → C → B(B1→B2→B3→B4)→ E。本 spec(C)为第 3 位:只新增 `HttpA2ATransport` + `EnvVarResolver`,不修改 `AgentCoordinator` 内部逻辑(远程分支已存在)。**

## Design References

- `src/agentos/multi/remote.py` — `RemoteTaskAdapter` Protocol（`L11`），`RemoteTaskExecutor.__init__`（`L21`），`_run`（`L47`）
- `src/agentos/multi/coordinator.py` — `dispatch()`（`L179`），`_submit_remote_task`（`L252`），`_handle_remote_result`（`L277`），`_store_late_result`（`L564`），`_trace_context()`（`L641`），`_notify_task_completed()`（`L646`）
- `src/agentos/channels/a2a.py` — `A2ATransport` Protocol（`L22`），`UrllibA2ATransport`（`L43`），`A2AAdapter.send_task`（`L86`），trace_context headers 传递（`L99`）
- `src/agentos/channels/a2a_server.py` — `A2AServerAdapter.handle_task`（`L48`），`use_incoming_trace_headers`（`L55`），inbound parse（`L73`）
- `src/agentos/multi/types.py` — `AgentCard.endpoint`（`L31`），`TaskRequest.trace_context`（`L45`），`TaskResult`（`L49`），`TaskRecord.late_result`（`L83`）
- `src/agentos/registry/resolver.py` — `AgentResolver` Protocol（`L10`），`StaticResolver`（`L38`），`ServiceResolver`（`L85`）
- `src/agentos/registry/persistent.py` — `PersistentAgentRegistry`（`L176`），`AgentRegistryStore` Protocol（`L23`）
- `src/agentos/multi/reconciler.py` — `OutboxReconciler.run_once`（`L42`）
- `src/agentos/multi/redis_continuation.py` — `RedisContinuationTrigger.on_task_completed`（`L34`）
- `src/agentos/observability/context.py` — `inject_trace_headers`（`L105`），`use_incoming_trace_headers`（`L116`）
- `pyproject.toml:29` — `async-http = ["httpx>=0.27"]` extra 已存在

## Goal

**强目标（可验证）：**

1. 提供 `HttpA2ATransport`，实现 `A2ATransport` Protocol（`channels/a2a.py:22`），用 httpx 替代 urllib，支持 timeout、指数退避 retry 和连接池，注册为 `async-http` extra 的 opt-in 实现。
2. 在无新增 Protocol 的前提下，将 `HttpA2ATransport` 注入 `A2AAdapter` → `RemoteTaskExecutor` → `AgentCoordinator.remote_task_executor`，完成端到端跨进程 dispatch 路径的集成测试覆盖。
3. 补充 `EnvVarResolver`——一个基于环境变量 JSON 配置的 `AgentResolver` 参考实现，供无 DB 场景的 `AgentCard` 静态解析（比 `StaticResolver` 更适合容器部署）。
4. 不破坏现有 `UrllibA2ATransport` 作为零依赖默认路径，不改 `AgentCoordinator` 公共 API。

## Contracts

### HttpA2ATransport

文件：`src/agentos/channels/http_transport.py`（新建）

```python
class HttpA2ATransport:
    """基于 httpx 的 A2ATransport 实现，支持 timeout / retry / 连接池。
    
    需要 async-http extra: pip install agent-os[async-http]
    """

    def __init__(
        self,
        *,
        timeout_seconds: float = 30.0,
        max_retries: int = 3,
        retry_backoff_factor: float = 0.5,
        client: "httpx.Client | None" = None,   # 注入用于测试
    ) -> None: ...

    def post_json(
        self,
        url: str,
        payload: dict[str, object],
        timeout_seconds: float,
        *,
        headers: dict[str, str] | None = None,
    ) -> dict[str, object]: ...

    def get_json(
        self,
        url: str,
        timeout_seconds: float,
    ) -> dict[str, object]: ...

    def close(self) -> None:
        """关闭底层 httpx.Client 连接池。"""
```

参数语义：
- `timeout_seconds`：默认请求超时（被 `TaskRequest.timeout_seconds` 覆盖）。
- `max_retries`：初始请求失败后的额外重试次数，不包含第一次尝试。仅对 5xx / `httpx.ConnectError` / `httpx.TimeoutException` 重试；4xx 不重试。例如 `max_retries=2` 表示最多 3 次总尝试。
- `retry_backoff_factor`：第 n 次重试等待 `backoff_factor * 2^(n-1)` 秒（标准指数退避）。
- `client`：外部注入 `httpx.Client`，便于测试 mock；None 时内部创建，使用连接池（httpx 默认连接池）。

`HttpA2ATransport` 满足 `A2ATransport` Protocol（`channels/a2a.py:22`），可直接传入 `A2AAdapter(transport=HttpA2ATransport(...))` 替换默认的 `UrllibA2ATransport`。

### EnvVarResolver

文件：`src/agentos/registry/env_resolver.py`（新建）

```python
class EnvVarResolver:
    """从环境变量 JSON 加载 AgentCard 列表，满足 AgentResolver Protocol。
    
    环境变量格式（默认键 AGENTOS_AGENT_CARDS）：
        JSON array of AgentCard-compatible dicts，必须含 agent_id / name /
        description / capabilities / endpoint 字段。
    
    用途：无 DB / 无 HTTP registry 时的容器化静态配置，比 StaticResolver
    更适合 docker-compose / k8s ConfigMap 注入。
    """

    def __init__(
        self,
        env_key: str = "AGENTOS_AGENT_CARDS",
        *,
        cards: "list[AgentCard] | None" = None,  # 直接注入，跳过 env（测试用）
    ) -> None: ...

    def resolve(
        self,
        agent_id: str,
        *,
        session_id: str | None = None,
    ) -> AgentCard | None: ...

    def discover(
        self,
        capabilities: "Sequence[str]",
        *,
        session_id: str | None = None,
    ) -> list[AgentCard]: ...

    def select(
        self,
        capabilities: "Sequence[str]",
        *,
        session_id: str | None = None,
    ) -> AgentCard | None: ...
```

`EnvVarResolver` 满足 `registry/resolver.py:10` 的 `AgentResolver` Protocol。与 `StaticResolver` 的区别：cards 在首次调用时 lazy 从 env 读取，不在 `__init__` 时要求 caller 自己解析 JSON。`select` 实现：按 capabilities 过滤后返回第一个非 offline card（无 affinity，无 round-robin——参考实现，够用即可）。

### 组装路径（集成示例，不是新类）

```python
# node A 侧（coordinator 节点）
transport = HttpA2ATransport(timeout_seconds=30, max_retries=2)
adapter = A2AAdapter(transport=transport)
executor = RemoteTaskExecutor(a2a_adapter=adapter)
coordinator = AgentCoordinator(
    ...
    remote_task_executor=executor,
)

# node B 侧（server 节点）已有
# A2AServerAdapter(AgentA2ATaskRunner(agent)) → ASGI router → /a2a/tasks
```

这段代码放进 `docs/superpowers/specs/` 里的本文件，**不需要额外新建 example 文件**——测试中直接实例化验证即可。

## File Change Map

| 操作 | 文件 | 说明 |
|------|------|------|
| 新建 | `src/agentos/channels/http_transport.py` | `HttpA2ATransport`，lazy import httpx |
| 新建 | `src/agentos/registry/env_resolver.py` | `EnvVarResolver` |
| 修改 | `src/agentos/channels/__init__.py` | 导出 `HttpA2ATransport`（`TYPE_CHECKING` guard，避免强依赖 httpx） |
| 修改 | `src/agentos/registry/__init__.py` | 导出 `EnvVarResolver` |
| 新建 | `tests/test_http_a2a_transport.py` | `HttpA2ATransport` 单元测试（mock httpx.Client） |
| 新建 | `tests/test_env_resolver.py` | `EnvVarResolver` 单元测试 |
| 新建 | `tests/test_cross_machine_dispatch.py` | 端到端集成测试（两个本地进程模拟跨机） |

### 不需要改的文件

- `src/agentos/channels/a2a.py`：`A2ATransport` Protocol 和 `A2AAdapter` 已足够，`UrllibA2ATransport` 保留作零依赖默认。
- `src/agentos/multi/remote.py`：`RemoteTaskAdapter` Protocol 和 `RemoteTaskExecutor` 不变。
- `src/agentos/multi/coordinator.py`：`dispatch` / `_submit_remote_task` / `_handle_remote_result` 路径已完整，不动。
- `src/agentos/multi/reconciler.py`、`src/agentos/multi/redis_continuation.py`：result 收集路径已存在，直接被现有 coordinator 使用。
- `pyproject.toml`：`async-http = ["httpx>=0.27"]` 已存在（`L29`），不需要新增 extra。

## Acceptance Criteria

> 真 subprocess 跨进程测试标 `@pytest.mark.integration`,默认 CI 不跑;transport 逻辑用 httpx mock 覆盖以保证稳定。

### AC-1：HttpA2ATransport 单元测试（默认跑，httpx mock）

```
使用 httpx mock / fake transport（如 httpx._transports.mock.MockTransport 或
pytest-httpx），不启动任何真实服务器或子进程。

覆盖点：
  a) retry/backoff：mock httpx.Client，前 N 次调用抛 httpx.ConnectError，
     第 N+1 次返回正常 JSON；assert max_retries=2 时 N=2 成功、N=3 耗尽抛异常。
  b) header propagation（含 traceparent）：post_json 被调用时，headers 参数
     包含 "traceparent" key，且值与 inject_trace_headers 构造的一致。
  c) timeout 行为：mock 抛 httpx.TimeoutException，assert 被捕获后触发重试；
     timeout_seconds 参数被正确传给 httpx request。
  d) 4xx 不重试：mock 返回 404，assert 直接抛异常，httpx.Client.post 只调用一次。
```

### AC-2：A2AServerAdapter in-process ASGI 测试（默认跑，无真实网络）

```
使用 httpx.AsyncClient(app=asgi_app, base_url="http://test") 或
starlette.testclient.TestClient 做 in-process ASGI 测试，不启动子进程。

断言：
  - POST /a2a/tasks 返回 200，body 为合法 TaskResult JSON。
  - use_incoming_trace_headers 在 handle_task 内被调用（可 mock 断言调用次数）。
  - 非法 payload 返回 422 / 400。
```

### AC-3（可选，@pytest.mark.integration）：跨进程 dispatch 端到端

```
标记：@pytest.mark.integration —— 默认 CI 不跑，本地或专项 CI job 手动触发。

两个独立本地进程模拟跨机：
- 进程 B：启动一个 ASGI HTTP server，挂载 A2AServerAdapter + AgentA2ATaskRunner(agent)，
  监听 /a2a/tasks（用真实 echo agent，返回 instruction 原文）。
- 进程 A：coordinator 用 HttpA2ATransport + A2AAdapter + RemoteTaskExecutor，
  注册一个 AgentCard(endpoint="http://localhost:<port>")，
  调用 coordinator.dispatch(required_capabilities=("echo",), ...) 。

断言：
  - dispatch 返回 TaskHandle，status 初始为 "queued"。
  - 等待 on_result 回调触发（或 coordinator.collect_results）。
  - TaskResult.status == "completed"，TaskResult.summary 包含原始 instruction。
  - coordinator task_table.get(task_id).status == "completed"。
```

### AC-4（可选，@pytest.mark.integration）：超时走 late-result 路径

```
标记：@pytest.mark.integration —— 同上，不默认跑。

进程 B：/a2a/tasks 故意 sleep(5s) 后才返回。
进程 A：HttpA2ATransport(timeout_seconds=1, max_retries=1)。

断言：
  - RemoteTaskExecutor._run 捕获 httpx.TimeoutException（或重试耗尽后的异常），
    构造 status="failed" TaskResult 并调用 on_result 回调。
  - coordinator._handle_remote_result 被触发，task_table.mark_failed 被调用
    或 store_late_result 被调用（取决于 task 是否已超期）。
  - TaskResult.status 为 "failed"，error 字段非空。
```

### AC-5：traceparent 跨 hop 传播

```
在进程 A 侧设置一个可观测 trace propagator（InMemoryTracer）。
dispatch 后，进程 B 侧的 A2AServerAdapter.handle_task 内，
use_incoming_trace_headers 恢复了和进程 A 相同的 trace_id。

断言（单进程模拟版本，mock httpx.Client）：
  - HttpA2ATransport.post_json 被调用时，headers 参数包含 "traceparent" key。
  - traceparent 值和 coordinator._trace_context() 返回的一致（inject_trace_headers 已注入）。
  
注：traceparent 来自 TaskRequest.trace_context dict，由 coordinator._trace_context() 
在 dispatch 时通过 inject_trace_headers 构造（coordinator.py:L641），
A2AAdapter.send_task 在 L99 将其作为 HTTP headers 传出——
HttpA2ATransport 需保证 post_json headers 参数被原样透传给 httpx request。
```

### AC-6：EnvVarResolver 解析和发现

```
AGENTOS_AGENT_CARDS='[{"agent_id":"a1","name":"A1","description":"","capabilities":["cap_x"],"endpoint":"http://host:8080","version":"0.1.0","status":"idle","lifecycle":"persistent","max_concurrent_tasks":1}]'

resolver = EnvVarResolver()
断言：
  - resolver.resolve("a1").endpoint == "http://host:8080"
  - resolver.discover(["cap_x"]) 返回长度 1 的列表
  - resolver.discover(["cap_y"]) 返回空列表
  - resolver.select(["cap_x"]) 返回 a1 的 card
  - 环境变量未设置时：resolve 返回 None，discover 返回 []
```

### AC-7：回归——UrllibA2ATransport 路径不受影响

```
现有使用 A2AAdapter() 默认（不传 transport）的测试全部仍然通过。
HttpA2ATransport 未被 import 时，不因缺少 httpx 而导致 ImportError。
```

## Risks & Non-Goals

### 风险

- **httpx 在 extra 后面的 lazy import**：`http_transport.py` 顶部需要用 `try/import` 或在函数体内延迟 import httpx，避免安装 agent-os 基包时因缺少 httpx 报 `ImportError`。推荐在文件顶部用 `try: import httpx; _HTTPX_AVAILABLE = True except ImportError: _HTTPX_AVAILABLE = False`，在 `__init__` 中检查。
- **同步 vs 异步**：`RemoteTaskAdapter.send_task` 和 `A2ATransport.post_json` 都是**同步** interface（由 `RemoteTaskExecutor` 在 ThreadPoolExecutor 中调用），因此 `HttpA2ATransport` 也必须用 **httpx.Client**（同步），不是 `httpx.AsyncClient`。这和 extra 名称 `async-http` 的字面意思略有偏差——extra 名来自已有约定，实现者不要改名。
- **retry sleep 阻塞线程**：指数退避 `time.sleep()` 会阻塞 `RemoteTaskExecutor` 的线程池线程。对于大并发场景这是可接受的设计权衡（参考实现，非生产调度器）。
- **httpx 连接池生命周期**：`HttpA2ATransport` 内部创建的 `httpx.Client` 需要在 `close()` 时释放；调用者负责生命周期管理（传入已有 client 则调用者管理）。

### Non-Goals

- **不提供 k8s / Nacos / Consul 服务发现适配器**——留给采用者实现，满足 `AgentResolver` Protocol 即可。
- **不改 A2ATransport / RemoteTaskAdapter / AgentResolver Protocol 签名**——它们已正确定义，本 Phase 只添加实现。
- **不做 transport 层的认证/鉴权**（API key、mTLS）——在 httpx.Client 层注入 headers 或 cert 是采用者的职责，`HttpA2ATransport` 提供 `client` 注入口即可满足。
- **不做异步 A2A dispatch**（async send_task）——当前整个 coordinator dispatch 路径是同步 + ThreadPoolExecutor，Phase C 不改此架构；异步化属于后续 Phase。
- **HttpA2ATransport 不是 default**——`A2AAdapter` 默认仍使用 `UrllibA2ATransport`，zero-dep 优先原则不变。

## 实现交接须知（给实现者）

先读 `AGENTS.md`，命名/边界/typed event/test-first/完成度 checklist 一律遵守。本节只列 spec 特有点：

- **三个交付物彼此独立**：`HttpA2ATransport`（channels 层）/ `EnvVarResolver`（registry 层）/ 端到端集成测试互不依赖，可顺序实现。
- **lazy import httpx**：`http_transport.py` 顶部检查 httpx 是否可用，缺失时 `__init__` 给出明确提示（`ImportError: HttpA2ATransport requires 'agent-os[async-http]'`）。
- **AC-1/AC-2 是默认测试，使用 httpx mock / in-process ASGI**：不启动子进程，稳定、无服务器依赖。transport 的 retry/backoff/header/timeout 逻辑全部在 AC-1 httpx mock 中覆盖。
- **AC-3/AC-4（跨进程 subprocess 端到端）标 `@pytest.mark.integration`**：启动真实子进程跑 B 节点（一个极简 ASGI server），测试后 terminate；默认 CI 不跑，本地或专项 CI job 手动触发，不需要 docker。
- **AC-5 可在单进程完成**：mock `httpx.Client` 拦截请求，assert headers 含 traceparent；不需要真实跨进程。
- **diff surgical**：`channels/a2a.py` / `multi/remote.py` / `multi/coordinator.py` 不动；只新建文件 + `__init__.py` 导出。发现无关 dead code 在 PR 里提一句，不自作主张删。
