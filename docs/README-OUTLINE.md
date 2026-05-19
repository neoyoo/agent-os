# Agent-OS SDK Architecture README — Outline

> 本文件是 README 结构大纲，细节由 Codex 维护填充。

---

## 1. Core Philosophy（SDK 核心思想）

### 1.1 Context-First Architecture

- Agent 的认知能力由它每轮看到的 context 决定，不由代码逻辑决定
- SDK 的核心问题是：如何让 LLM 在每一轮都看到最有价值的信息
- 三层 context 模型：
  - **Working State**：当前任务的结构化状态（schema-driven，LLM 自主维护）
  - **Compressed History**：超出窗口的历史被压缩为 segment，按 handle 可召回
  - **Memory Context**：跨 session 的长期记忆注入

### 1.2 Zero-Dependency Core

- `pip install agent-os` 不安装任何第三方包
- 所有外部集成通过 optional extras：`[redis]` `[postgres]` `[qdrant]` `[observability]` `[async-http]`
- 未安装 extra 时 import 不报错，使用时给出明确 RuntimeError 提示

### 1.3 Protocol Boundaries（结构化类型边界）

- 模块间通过 `typing.Protocol` 通信，不依赖具体实现
- 任何层都可以被替换：自定义 Provider、自定义 SessionStore、自定义 Transport
- 测试可以 fake 任何边界，无需 mock 框架

### 1.4 Single Source of Truth

- QueryLoop 是唯一的 agent 主循环逻辑
- 同步 / 异步 / streaming / ASGI 都复用同一个 QueryLoop
- 不存在逻辑分裂的"async 版 QueryLoop"

---

## 2. Architecture Layers（分层架构图）

```
┌─────────────────────────────────────────────────────────┐
│  Channels (ASGI / HTTP / SSE / A2A / CLI)               │  ← 入口层
├─────────────────────────────────────────────────────────┤
│  Runtime (Agent / QueryLoop / Session / Turn)            │  ← 调度层
├─────────────────────────────────────────────────────────┤
│  Context (State / Schema / Renderer / Projection)       │  ← 认知层
│  Messages (Store / Window / Runtime)                    │
│  Compression (Runtime / Compressor / Evictor / Index)   │
├─────────────────────────────────────────────────────────┤
│  Capabilities (Tools / Router / MCP / Skills)           │  ← 能力层
│  Hooks (Registry / Manager / Handler)                   │
├─────────────────────────────────────────────────────────┤
│  Providers (OpenAI-compatible / Anthropic / OpenAI)     │  ← 模型层
├─────────────────────────────────────────────────────────┤
│  Memory (Hot Store / Durable Store / Recall Index)      │  ← 记忆层
│  Persistence (Filesystem / SQLite / Postgres)           │
├─────────────────────────────────────────────────────────┤
│  Multi-Agent (Coordinator / TaskTable / Inbox / Spawn)  │  ← 协作层
│  Registry (AgentCard / Resolver / Discovery)            │
├─────────────────────────────────────────────────────────┤
│  Observability (OTel / Langfuse / Tracer / Snapshots)   │  ← 观测层
│  Policies (Security / Budget)                           │
└─────────────────────────────────────────────────────────┘
```

---

## 3. Context Protocol（上下文协议详解）

### 3.1 LLM 每轮看到什么

说明 ProviderRequest 的最终结构：

```
system prompt =
  identity（agent 身份）
  + capability plane（可用工具声明）
  + working state schema + values（当前任务状态）
  + compressed history segments（历史摘要）
  + memory context（跨 session 记忆）
  + runtime notices（一次性系统通知）

messages =
  active window 中的 user / assistant / tool 消息序列
```

### 3.2 Context Protocol Tools（agent 自主调用的内置工具）

| Tool | 作用 | 触发时机 |
|------|------|---------|
| `declare_schema` | 声明 working state 字段结构 | 任务开始时 |
| `update_state` | 更新一个已声明字段的值 | 任务推进时 |
| `extend_schema` | 追加新字段 | 任务复杂化时 |
| `start_chapter` | 开启新 chapter，重置 working state | 主题切换时 |
| `recall_context` | 按 handle 或 query 召回压缩文本/历史，结果作为 tool result 返回 | 需要回顾时 |
| `load_image` | 将已上传图片附件加载到下一次模型请求 | 需要重新查看图片时 |

### 3.3 Context 生命周期示例

用一个具体的 3-turn 对话示例展示：
- Turn 1：用户提问 → agent 调 declare_schema → working state 可见
- Turn 2：agent 调 update_state → 状态推进 → 窗口触发 compression → 旧消息被摘要
- Turn 3：agent 调 recall_context → 召回压缩段原文 → 以标准 tool result 回写消息序列

---

## 4. Minimal MVP Agent（最小可运行 agent）

### 4.1 必需 Modules

```
runtime/        → Agent, QueryLoop, ProviderRequestBuilder
context/        → ContextRuntime, ContextRenderer, ContextState
messages/       → MessageRuntime, MessageStore, ActiveWindow
providers/      → 至少一个 Provider 实现（如 OpenAICompatibleProvider）
capabilities/   → ToolCallRouter（即使不注册外部工具也需要，因为 context protocol tools）
```

### 4.2 最小代码示例

```python
from agentos import AgentBuilder
from agentos.providers.openai_compatible import OpenAICompatibleProvider

agent = (
    AgentBuilder()
    .provider(OpenAICompatibleProvider(
        api_key="...",
        base_url="https://api.openai.com/v1",
        model="gpt-4o",
    ))
    .build()
)

result = agent.run("Hello, world!")
print(result.content)
```

### 4.3 最小 Agent 数据流

```
user input
  → MessageRuntime.append_user()
  → ProviderRequestBuilder.build()  ← 读 ContextState + ActiveWindow
  → Provider.complete() / .stream()
  → QueryLoop 处理 response（tool calls → 路由 → 结果回写 → 循环）
  → MessageRuntime.append_assistant()
  → AgentResult
```

### 4.4 加 Compression 的 MVP

```python
agent = (
    AgentBuilder()
    .provider(provider)
    .with_compression()  # 启用默认 rule-based compressor
    .build()
)
```

说明 compression 何时触发、budget policy 的含义。

### 4.5 加外部工具的 MVP

```python
from agentos.capabilities import RegisteredTool

agent = (
    AgentBuilder()
    .provider(provider)
    .tools([
        RegisteredTool(
            name="search",
            description="Search the web.",
            parameters={...},
            handler=lambda args: do_search(args["query"]),
        ),
    ])
    .build()
)
```

---

## 5. Web Agent（HTTP 服务化部署）

### 5.1 必需 Modules

在 MVP 基础上追加：

```
channels/       → AsgiAgentApp, InMemoryAgentSessionProvider
```

### 5.2 最小 ASGI 部署

```python
from agentos import AsgiAgentApp, InMemoryAgentSessionProvider

sessions = InMemoryAgentSessionProvider(agent_factory=make_agent)
app = AsgiAgentApp(sessions=sessions)
# uvicorn app:app
```

### 5.3 Endpoints

| Method | Path | 作用 |
|--------|------|------|
| GET | `/v1/health` | 健康检查 |
| POST | `/v1/sessions/{id}/turns` | 同步 turn |
| POST | `/v1/sessions/{id}/turns/stream` | SSE streaming turn |
| POST | `/a2a/tasks` | A2A 协议任务入口 |
| GET | `/a2a/health` | A2A 健康检查 |

### 5.4 Session 生命周期

说明 session_id 如何映射到 Agent 实例，get_agent / release_agent 语义。

---

## 6. Production Distributed Agent（生产分布式部署）

### 6.1 架构拓扑

```
Client → Load Balancer → N × ASGI instances
                              ↓
                    Redis (hot session state)
                    Postgres (durable state)
                    Qdrant (recall index)
                    OTel Collector (traces)
```

### 6.2 中间件清单

| 中间件 | 用途 | SDK 对应模块 | Extra |
|--------|------|-------------|-------|
| **Redis** | 热点 session state（active refs, 最近消息, segment refs） | `RedisHotSessionStore` | `[redis]` |
| **Postgres** | 持久化（session, 原始消息, compressed segments, agent registry） | `PostgresDurableSessionStore`, `PostgresAgentRegistryStore` | `[postgres]` |
| **Qdrant** | 语义召回索引（按 query 搜索相关 segment） | `QdrantRecallIndex` | `[qdrant]` |
| **OTel Collector** | 分布式 tracing + generation spans | `observability/otel.py`, `InstrumentedQueryLoop` | `[observability]` |
| **Langfuse**（可选） | LLM 专用 tracing + cost tracking | `observability/langfuse.py` | `[observability]` |

### 6.3 各中间件解决什么问题

**Redis — 为什么需要：**
- 多实例共享 session state（哪些消息在 active window、最近 N 条原文消息）
- 滑动 TTL 自动清理不活跃 session
- 原子 consume temporary recalled refs（GETDEL / Lua script）
- 不用 Redis 时退化为单实例 in-memory（开发用）

**Postgres — 为什么需要：**
- Session 断点恢复（agent crash 后重建 Agent 实例）
- 消息永久存储（hot store miss 时的 fallback）
- Compressed segment 持久化（recall 召回的底层数据源）
- Agent registry 持久化（重启后 agent card 不丢）
- 不用 Postgres 时退化为 SQLite / filesystem / memory（开发用）

**Qdrant — 为什么需要：**
- 语义搜索：按 query 找到相关的 compressed segment
- 比 keyword match 更准确的 recall
- 不用 Qdrant 时退化为 in-memory linear scan（少量 segment 够用）

### 6.4 Production Agent 组装示例

```python
from agentos import (
    AgentBuilder,
    AsgiAgentApp,
    MemoryRuntime,
    QdrantRecallIndex,
    RedisHotSessionStore,
    PostgresDurableSessionStore,
)

hot_store = RedisHotSessionStore(url="redis://...", ttl_seconds=3600)
durable_store = PostgresDurableSessionStore(dsn="postgresql://...")
recall_index = QdrantRecallIndex(url="http://...", collection="agent_recall")
memory = MemoryRuntime(
    hot_store=hot_store,
    durable_store=durable_store,
    recall_index=recall_index,
)

def make_agent(session_id: str) -> Agent:
    return (
        AgentBuilder()
        .provider(provider)
        .tools(my_tools)
        .with_compression(compressor=LlmCompressor(provider))
        .build()
    )
    # + 水合 session state from Redis/Postgres

sessions = InMemoryAgentSessionProvider(agent_factory=make_agent)
app = AsgiAgentApp(sessions=sessions, auth_policy=my_auth)
```

### 6.5 Session Affinity vs Stateless

说明两种部署模式：
- **Session affinity**：同一 session 路由到同一实例，Agent 对象常驻内存，Redis 做 backup
- **Stateless**：每次请求从 Redis + Postgres 重建 Agent 状态，任何实例可服务任何 session

### 6.6 当前分布式边界的 Production Ready 状态

| 层 | Production Ready | 说明 |
|----|-----------------|------|
| Session State (Redis + Postgres) | ✅ | 多实例共享 session 数据 |
| Agent Registry (Postgres) | ✅ | 多实例共享 agent card |
| Recall Index (Qdrant) | ✅ | 多实例共享语义索引 |
| Observability (OTel) | ✅ | 跨实例 trace 聚合 |
| Multi-agent TaskStore | 🟡 adapter 已有 | `TaskTable` in-memory；`PostgresTaskStore` 已有，live integration tests / reconciler 待补 |
| Multi-agent MessageQueue | 🟡 adapter 已有 | `AgentInbox` in-memory；`RedisAgentMessageQueue` 已有，pending/retry reclaim 待补 |
| Async HTTP Cancel | ❌ 施工中 | sync provider 只能等 timeout |

---

## 7. Multi-Agent Coordination（多 agent 协作）

### 7.1 当前能力

- **Spawn**：创建 ephemeral subagent 在线程池中执行，结果回收到 parent inbox
- **Dispatch**：按 capability 发现 expert agent，通过 inbox 派发任务
- **Remote Dispatch**：对 endpoint-backed agent 通过 A2A HTTP 调用
- **Continuation**：subagent 完成后自动触发 parent 的 continuation turn

### 7.2 协作模式数据流

```
Parent Agent
  → coordinator.spawn(instruction="...")
  → TaskStore.create(queued)
  → SpawnExecutor.submit(child_agent.run)
  → TaskStore.mark_running / mark_completed
  → AgentMessageQueue.send(result envelope)
  → Parent: coordinator.collect_results()
  → ContinuationTrigger → parent 自动 continuation turn
```

### 7.3 部署限制

`TaskStore` / `AgentMessageQueue` 是分布式边界；`TaskTable` / `AgentInbox` 是 in-memory adapter，`PostgresTaskStore` / `RedisAgentMessageQueue` 是生产 adapter 骨架。
仍未完成：live Redis/Postgres integration tests、Redis pending/retry reclaim、outbox reconciler、跨节点 continuation trigger。

---

## 8. Observability（可观测性）

### 8.1 三层观测

- **Event Bus**：typed events（TurnStarted, ToolExecutionCompleted, CompressionCompleted...）— 内部 pub/sub
- **OTel Traces**：generation span、tool span、compression span — 标准化 tracing
- **Langfuse**：LLM 专用 trace + cost + evaluation — 通过 OTel exporter 桥接

### 8.2 接入方式

```python
from agentos.observability import instrument_query_loop, ObservabilityConfig

config = ObservabilityConfig(...)
instrumented_loop = instrument_query_loop(agent.query_loop, config)
```

不修改原始 QueryLoop，通过 Proxy 模式透明增强。

---

## 9. Hook System（扩展点）

### 9.1 可拦截 Hook 点

| Hook | 时机 | 能力 |
|------|------|------|
| `before_provider_call` | 发给 LLM 前 | deny / modify request |
| `after_provider_call` | LLM 返回后 | deny / modify response |
| `before_tool_call` | 工具执行前 | deny（返回替代 result） |
| `after_tool_call` | 工具执行后 | modify result |

### 9.2 Hook vs Event

- **Hook**：可拦截、可修改、可拒绝（影响流程）
- **Event**：只读观测（不影响流程）

---

## 10. Security（安全策略）

### 10.1 SecurityPolicy

- `denied_tools: set[str]` — 黑名单优先
- `allowed_tools: set[str] | None` — 白名单可选

### 10.2 Channel Auth

- `ChannelAuthPolicy` Protocol — ASGI 入口的认证边界
- 默认 `AllowAllChannelAuthPolicy`（开发用）

---

## 11. Quick Reference（速查）

### 11.1 Optional Extras

```
pip install agent-os[redis]              # Redis hot session store
pip install agent-os[postgres]           # Postgres durable store + registry
pip install agent-os[qdrant]             # Qdrant recall index
pip install agent-os[observability]      # OTel + Langfuse tracing
pip install agent-os[async-http]         # httpx async transport
pip install agent-os[production-memory]  # redis + postgres + qdrant 全家桶
```

### 11.2 Key Protocols（可替换边界）

列出所有 42 个 Protocol 的名称、所在模块、一句话用途。

### 11.3 Module Dependency Graph

哪个模块依赖哪个，import 方向图。
