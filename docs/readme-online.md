# Agent OS SDK

Agent OS 是一个 context-first 的 Python agent runtime SDK。当前公开导入包名是 `agentos`，项目发布名是 `agent-os`，版本为 `0.1.0`。

源码来源：[`pyproject.toml`](../pyproject.toml)，[`src/agentos/__init__.py`](../src/agentos/__init__.py)，[`tests/architecture/test_public_api.py`](../tests/architecture/test_public_api.py)

---

## 1. Core Philosophy

### 1.1 Context-First Architecture

当前 SDK 的运行边界围绕 `ProviderRequest` 组织：`ProviderRequestBuilder` 从 `ContextRuntime.snapshot()` 读取可渲染状态，用 `ContextRenderer` 生成 `system`，从 `MessageRuntime` materialize active messages，并附带 provider tool schemas。

默认 LLM 可见 context 由 `ContextRenderer` 渲染，包含 `Runtime Contract`、`Capability Plane`、`Context Management Rules`、可选的 `Declared Working State Schema`、可选的 `Working State`、可选的 `Inherited State`、`Compressed History`、`Memory Context`，以及一次性 `Runtime Notice`。空 schema 时 renderer 会省略 declared schema 和 working state sections。

源码来源：[`src/agentos/runtime/provider_request_builder.py`](../src/agentos/runtime/provider_request_builder.py)，[`src/agentos/context/renderer.py`](../src/agentos/context/renderer.py)，[`src/agentos/context/runtime.py`](../src/agentos/context/runtime.py)，[`tests/context/test_renderer.py`](../tests/context/test_renderer.py)，[`tests/runtime/test_query_loop_boundaries.py`](../tests/runtime/test_query_loop_boundaries.py)

### 1.1.1 LLM-Visible Context

Agent OS 的核心不是把更多历史消息塞进 prompt，而是把 LLM 每轮需要遵守、引用和回调的内容组织成稳定的 context protocol。默认渲染顺序如下：

```text
Runtime Contract
  -> agent 身份、行为边界和安全约束

Capability Plane
  -> 可用工具、context protocol tools、MCP server、skills 的 LLM 可见说明

Context Management Rules
  -> working state、schema、chapter、recall、trust order 的更新规则

Declared Working State Schema
  -> 当前 chapter 的结构化状态字段；未声明 schema 时省略

Working State
  -> 当前任务目标、约束、决策、已验证事实、开放问题和下一步；未声明 schema 时省略

Inherited State
  -> 跨 chapter 继承的稳定目标、约束或决策；无继承状态时省略

Compressed History
  -> 被压缩的历史 segment，只暴露 handle、topic 和摘要

Memory Context
  -> 跨 session 检索出的长期记忆

Runtime Notice
  -> 本轮一次性系统通知；消费后清除
```

这些 section 的优先级不同。当前 active messages 和本轮加载的附件代表最新事实；compressed history 和 memory context 是有损或检索得到的上下文；working state 是 LLM 当前维护的任务状态，不应该覆盖更新的用户消息。默认 trust order 是：

```text
1. Active messages and currently loaded attachments
2. Inherited state
3. Compressed history
4. Memory context
5. Working state
6. Attachment placeholders / previews
```

完整的 LLM 可见上下文范文见 [`docs/design/llm-context-only-example.md`](design/llm-context-only-example.md)。这个文件只展示应该进入 provider `system` 的内容，不展示 SDK 内部 runtime metadata。

### 1.2 Zero-Dependency Core

`pyproject.toml` 的 core `dependencies` 为空；Redis、Postgres、Qdrant、OpenTelemetry 和 async HTTP transport 都通过 optional extras 声明。对应 adapter 在缺少 optional dependency 时会在使用点抛出清晰的 `RuntimeError`。

当前 extras：

| Extra | 依赖 | 当前用途 |
|---|---|---|
| `redis` | `redis>=5.0` | `RedisHotSessionStore`、`RedisAgentMessageQueue` |
| `postgres` | `psycopg[binary]>=3.1`、`psycopg-pool>=3.2` | `PostgresDurableSessionStore`、`PostgresAgentRegistryStore`、`PostgresTaskStore` |
| `qdrant` | `qdrant-client>=1.9` | `QdrantRecallIndex` |
| `observability` | OpenTelemetry API/SDK/OTLP HTTP exporter | OTel 和 Langfuse OTLP tracer |
| `async-http` | `httpx>=0.27` | OpenAI-compatible async transport |
| `production-memory` | redis + qdrant + psycopg | 组合安装生产 memory adapters |

源码来源：[`pyproject.toml`](../pyproject.toml)，[`src/agentos/memory/redis_store.py`](../src/agentos/memory/redis_store.py)，[`src/agentos/multi/redis_queue.py`](../src/agentos/multi/redis_queue.py)，[`src/agentos/persistence/postgres.py`](../src/agentos/persistence/postgres.py)，[`src/agentos/registry/postgres.py`](../src/agentos/registry/postgres.py)，[`src/agentos/multi/postgres_tasks.py`](../src/agentos/multi/postgres_tasks.py)，[`src/agentos/memory/qdrant_index.py`](../src/agentos/memory/qdrant_index.py)，[`src/agentos/observability/otel.py`](../src/agentos/observability/otel.py)，[`src/agentos/providers/openai_compatible.py`](../src/agentos/providers/openai_compatible.py)

### 1.3 Protocol Boundaries

项目内多处边界使用 `typing.Protocol`：provider、async provider、context snapshot provider、tool handler、MCP client、session provider、channel auth、hot/durable session store、recall index、embedding provider、session persistence、registry store、A2A transport、subagent factory 和 remote task submitter 等。

源码来源：[`src/agentos/providers/base.py`](../src/agentos/providers/base.py)，[`src/agentos/runtime/provider_request_builder.py`](../src/agentos/runtime/provider_request_builder.py)，[`src/agentos/capabilities/tools.py`](../src/agentos/capabilities/tools.py)，[`src/agentos/capabilities/mcp.py`](../src/agentos/capabilities/mcp.py)，[`src/agentos/channels/session.py`](../src/agentos/channels/session.py)，[`src/agentos/channels/auth.py`](../src/agentos/channels/auth.py)，[`src/agentos/memory/store.py`](../src/agentos/memory/store.py)，[`src/agentos/memory/recall_index.py`](../src/agentos/memory/recall_index.py)，[`src/agentos/persistence/base.py`](../src/agentos/persistence/base.py)，[`src/agentos/multi/coordinator.py`](../src/agentos/multi/coordinator.py)

---

## 2. Architecture Layers

当前源码中的主要分层如下：

```text
agentos/
  builder.py                     # AgentBuilder 组装默认运行时
  runtime/                       # Agent facade、QueryLoop、ProviderRequestBuilder、Session/Turn
  context/                       # ContextState、schema、renderer、projection、ContextRuntime
  messages/                      # MessageStore、ActiveWindow、MessageRuntime
  compression/                   # CompressionRuntime、Evictor、Compressor、CompressionIndex
  recall/                        # RecallRuntime
  capabilities/                  # ToolRegistry、ToolExecutor、ToolCallRouter、MCP、Skills
  providers/                     # Provider 协议和 OpenAI/Anthropic/OpenAI-compatible/Fake adapters
  channels/                      # HTTP、SSE、ASGI、A2A、session provider、auth
  memory/                        # hot/durable store 协议、in-memory/Redis/Qdrant adapters、MemoryRuntime
  persistence/                   # SessionSnapshot、memory/filesystem/sqlite/postgres persistence
  multi/                         # AgentCoordinator、TaskStore/TaskTable、AgentMessageQueue/AgentInbox、Postgres/Redis adapters
  registry/                      # persistent registry、Postgres store、resolver
  events/                        # typed EventBus
  hooks/                         # HookRegistry、HookManager
  observability/                 # capture policy、instrumentation、OTel/Langfuse helpers
  policies/                      # SecurityPolicy、BudgetPolicy
```

源码来源：[`src/agentos`](../src/agentos)，[`src/agentos/builder.py`](../src/agentos/builder.py)，[`tests/architecture/test_public_api.py`](../tests/architecture/test_public_api.py)

---

## 3. Context Protocol

### 3.1 ProviderRequest Shape

Provider 边界接收标准化 `ProviderRequest`：

```python
ProviderRequest(
    system="<rendered context>",
    messages=[UserMessage(...), AssistantMessage(...), ToolResultMessage(...)],
    tools=[ProviderToolSpec(...)]
)
```

`ProviderRequestBuilder.build()` 不暴露 context 内部对象，只依赖 `snapshot()`；active messages 由 `MessageRuntime.materialize_provider_messages()` 转成强类型 provider messages。

源码来源：[`src/agentos/providers/base.py`](../src/agentos/providers/base.py)，[`src/agentos/providers/messages.py`](../src/agentos/providers/messages.py)，[`src/agentos/runtime/provider_request_builder.py`](../src/agentos/runtime/provider_request_builder.py)，[`src/agentos/messages/runtime.py`](../src/agentos/messages/runtime.py)，[`tests/providers/test_provider_messages.py`](../tests/providers/test_provider_messages.py)

### 3.2 Built-in Context Tools

默认 context protocol tools 的单一来源是 `CONTEXT_PROTOCOL_TOOL_DEFINITIONS` 和 `context_protocol_tool_specs()`：

| Tool | 当前作用 |
|---|---|
| `declare_schema` | 声明当前 chapter 的 working state 字段 |
| `update_state` | 更新一个已声明 working state 字段 |
| `extend_schema` | 在已有 schema 上追加字段 |
| `start_chapter` | 开启新 chapter，并重置 working state |
| `recall_context` | 按 handle 或 query 恢复压缩文本/历史，并以 tool result 返回 |
| `load_image` | 将已上传图片附件加载到当前 turn 的后续 provider requests |

`ToolCallRouter.tool_specs()` 会把这些 context tools 与外部工具、skill tool、MCP tools 一起作为 provider tools 暴露；`ToolCallRouter.execute_tool_call()` 会把 context tools 路由到 `ContextRuntime`、`RecallRuntime` 或 `AttachmentRuntime`。

源码来源：[`src/agentos/context_protocol.py`](../src/agentos/context_protocol.py)，[`src/agentos/capabilities/router.py`](../src/agentos/capabilities/router.py)，[`src/agentos/context/runtime.py`](../src/agentos/context/runtime.py)，[`src/agentos/recall/runtime.py`](../src/agentos/recall/runtime.py)，[`src/agentos/attachments/runtime.py`](../src/agentos/attachments/runtime.py)，[`tests/architecture/test_public_api.py`](../tests/architecture/test_public_api.py)

### 3.3 Compression And Recall Flow

`CompressionRuntime.maybe_compress()` 在 provider request 构建前运行。它读取 active messages，交给 `Evictor` 选择要压缩的 message ids，用 compressor 生成 `CompressedSegmentPackage`，先写 memory sink，再把 LLM 可见 segment 追加到 `ContextRuntime`，记录 `CompressionIndex`，最后从 active window 移除 refs。

`RecallRuntime.recall_context()` 支持两条路径：按 handle 从 `CompressionIndex` 找 source refs；按 query 需要 `MemoryRuntime` 和 `session_id`，先查 recall index，再按 handle 恢复原始消息。`ToolCallRouter` 会把召回消息格式化为 `<recalled-context>` tool result，随后作为标准 tool result 进入消息序列；它不会伪装成新的 user/assistant message，也不会写入 system prompt。

图片附件走独立的 `AttachmentRuntime`。首轮上传的图片会作为 user message 的 `ImagePart` 投影给 provider；后续如果模型需要重新查看图片，必须调用 `load_image(handle="att:...")`。调用后，该图片在当前 turn 的后续所有 provider requests 中持续可见；turn 结束时 SDK 自动清空已加载图片列表。下一个 turn 如需引用同一附件，模型必须显式重新调用 `load_image`。

**MIME types**: `AttachmentRuntime` 仅接受 `image/gif`、`image/jpeg`、`image/png`、`image/webp`。上传 PDF 或其他类型将抛 `AttachmentError("unsupported attachment MIME type")`。`FilePart` 类仍保留，直接构造 provider message 不受影响。

源码来源：[`src/agentos/runtime/query_loop.py`](../src/agentos/runtime/query_loop.py)，[`src/agentos/compression/runtime.py`](../src/agentos/compression/runtime.py)，[`src/agentos/compression/index.py`](../src/agentos/compression/index.py)，[`src/agentos/compression/compressor.py`](../src/agentos/compression/compressor.py)，[`src/agentos/recall/runtime.py`](../src/agentos/recall/runtime.py)，[`src/agentos/messages/runtime.py`](../src/agentos/messages/runtime.py)，[`tests/compression/test_runtime.py`](../tests/compression/test_runtime.py)，[`tests/recall/test_runtime.py`](../tests/recall/test_runtime.py)

---

## 4. Minimal Agent

### 4.1 最小可运行示例

不依赖第三方服务的最小示例可以使用 `FakeProvider`：

```python
from agentos import AgentBuilder
from agentos.providers import FakeProvider

agent = (
    AgentBuilder()
    .provider(FakeProvider(["Hello from agentos."]))
    .build()
)

result = agent.run("Hello")
print(result.content)
```

真实 OpenAI-compatible endpoint 可使用：

```python
from agentos import AgentBuilder
from agentos.providers.openai_compatible import OpenAICompatibleProvider

provider = OpenAICompatibleProvider(
    api_key="...",
    base_url="https://api.openai.com/v1",
    model="gpt-4o",
)

agent = AgentBuilder().provider(provider).build()
result = agent.run("Hello")
print(result.content)
```

源码来源：[`src/agentos/builder.py`](../src/agentos/builder.py)，[`src/agentos/runtime/agent.py`](../src/agentos/runtime/agent.py)，[`src/agentos/providers/fake.py`](../src/agentos/providers/fake.py)，[`src/agentos/providers/openai_compatible.py`](../src/agentos/providers/openai_compatible.py)，[`tests/runtime/test_agent_builder.py`](../tests/runtime/test_agent_builder.py)，[`tests/providers/test_openai_compatible.py`](../tests/providers/test_openai_compatible.py)

### 4.2 AgentBuilder 默认装配

`AgentBuilder.build()` 至少要求 `.provider()`。默认会创建 `MessageRuntime`、`ContextRuntime`、`RecallRuntime`、`ToolRegistry`、`ToolCallRouter`、`ContextRenderer` 和 `ProviderRequestBuilder`，最后返回 `Agent` facade。`.tools([...])` 会注册外部 `RegisteredTool`；`.with_compression()` 会创建 `CompressionRuntime`，默认预算为 `max_active_messages=20`、`retain_latest_messages=6`。

源码来源：[`src/agentos/builder.py`](../src/agentos/builder.py)，[`src/agentos/capabilities/tools.py`](../src/agentos/capabilities/tools.py)，[`src/agentos/capabilities/registry.py`](../src/agentos/capabilities/registry.py)，[`src/agentos/capabilities/router.py`](../src/agentos/capabilities/router.py)

### 4.3 外部工具

外部工具使用 `RegisteredTool` 声明 name、description、JSON schema parameters 和 handler。`ToolRegistry` 负责注册和 provider schema 输出；`ToolExecutor` 执行 handler，并在执行前应用 `SecurityPolicy`。

```python
from agentos import AgentBuilder
from agentos.capabilities import RegisteredTool
from agentos.providers import FakeProvider

def echo(args: dict[str, object]) -> str:
    return str(args["text"])

agent = (
    AgentBuilder()
    .provider(FakeProvider(["ready"]))
    .tools([
        RegisteredTool(
            name="echo",
            description="Echo text.",
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            handler=echo,
        )
    ])
    .build()
)
```

源码来源：[`src/agentos/capabilities/tools.py`](../src/agentos/capabilities/tools.py)，[`src/agentos/capabilities/registry.py`](../src/agentos/capabilities/registry.py)，[`src/agentos/capabilities/executor.py`](../src/agentos/capabilities/executor.py)，[`src/agentos/policies/security.py`](../src/agentos/policies/security.py)，[`tests/capabilities/test_tools.py`](../tests/capabilities/test_tools.py)

---

## 5. Web Agent

### 5.1 ASGI App

`AsgiAgentApp` 是无框架绑定的 ASGI HTTP app。它依赖 `AgentSessionProvider` 通过 `session_id` 取回 agent，并支持 auth policy、请求体大小限制、JSON turn、SSE turn、显式 interrupt、SSE heartbeat、health/readiness、可注入 rate limiter、ASGI lifespan shutdown handlers 和可选 A2A server。

```python
from agentos import AsgiAgentApp, InMemoryAgentSessionProvider

sessions = InMemoryAgentSessionProvider(agent_factory=make_agent)
app = AsgiAgentApp(sessions=sessions)
```

源码来源：[`src/agentos/channels/asgi.py`](../src/agentos/channels/asgi.py)，[`src/agentos/channels/rate_limit.py`](../src/agentos/channels/rate_limit.py)，[`src/agentos/channels/session.py`](../src/agentos/channels/session.py)，[`src/agentos/channels/auth.py`](../src/agentos/channels/auth.py)，[`tests/channels/test_asgi_app.py`](../tests/channels/test_asgi_app.py)，[`tests/channels/test_health_endpoint.py`](../tests/channels/test_health_endpoint.py)，[`tests/channels/test_rate_limit.py`](../tests/channels/test_rate_limit.py)，[`tests/channels/test_session_provider.py`](../tests/channels/test_session_provider.py)

### 5.2 Endpoints

当前 `AsgiAgentApp` 路由：

| Method | Path | 行为 |
|---|---|---|
| `GET` | `/health` | 返回 `{"status": "ok"}` |
| `GET` | `/v1/health` | 返回 `{"status": "ok"}` |
| `GET` | `/ready` / `/v1/ready` | 运行注入的 readiness checks，失败时返回 503 |
| `POST` | `/v1/sessions/{session_id}/turns` | JSON turn，内部调用 `HttpAgentChannel.handle_turn()` |
| `POST` | `/v1/sessions/{session_id}/turns/stream` | SSE turn，消费 `agent.async_stream()` 或 fallback 到同步 stream |
| `POST` | `/v1/sessions/{session_id}/interrupt` | 请求中断当前 session 的运行中 turn |
| `POST` | `/a2a/tasks` | 当配置 `a2a_server` 时处理 inbound A2A task |
| `GET` | `/a2a/health` | 当配置 `a2a_server` 时返回 A2A health |

请求体由 `parse_channel_turn_request()` 解析，要求 JSON object 中存在非空 `message`，并支持 `thinking`、`show_thinking` 和可选 `max_message_length` 校验。SSE turn 默认每 15 秒发送一次 heartbeat comment，可通过 `sse_heartbeat_interval_seconds=None` 或非正数关闭。配置 `SlidingWindowRateLimiter` 后，turn endpoint 会按 `session_id` 限流，超限返回 429 和 `Retry-After` header。

源码来源：[`src/agentos/channels/asgi.py`](../src/agentos/channels/asgi.py)，[`src/agentos/channels/rate_limit.py`](../src/agentos/channels/rate_limit.py)，[`src/agentos/channels/http.py`](../src/agentos/channels/http.py)，[`src/agentos/channels/sse.py`](../src/agentos/channels/sse.py)，[`src/agentos/channels/types.py`](../src/agentos/channels/types.py)，[`tests/channels/test_asgi_app.py`](../tests/channels/test_asgi_app.py)，[`tests/channels/test_health_endpoint.py`](../tests/channels/test_health_endpoint.py)，[`tests/channels/test_rate_limit.py`](../tests/channels/test_rate_limit.py)，[`tests/channels/test_turn_request_parser.py`](../tests/channels/test_turn_request_parser.py)

---

## 6. Memory, Persistence, And Production Adapters

### 6.1 Memory Runtime

`MemoryRuntime` 连接三类边界：`HotSessionStore`、`DurableSessionStore`、`RecallIndex`。记录压缩片段时，它保存 segment refs 到 hot store，保存完整 compression package 到 durable store，并把 recall document 写入 recall index。按 query recall 时，它先搜索候选 segment，再按 handle 恢复原始消息并去重。

源码来源：[`src/agentos/memory/runtime.py`](../src/agentos/memory/runtime.py)，[`src/agentos/memory/store.py`](../src/agentos/memory/store.py)，[`src/agentos/memory/recall_index.py`](../src/agentos/memory/recall_index.py)，[`src/agentos/memory/types.py`](../src/agentos/memory/types.py)，[`tests/memory/test_runtime.py`](../tests/memory/test_runtime.py)

### 6.2 Implemented Stores And Indexes

| 类型 | 当前实现 |
|---|---|
| Hot session store | `InMemoryHotSessionStore`、`RedisHotSessionStore` |
| Durable session store | `InMemoryDurableSessionStore`、`PostgresDurableSessionStore` |
| Recall index | `InMemoryRecallIndex`、`QdrantRecallIndex` |
| Session snapshot persistence | `MemoryPersistence`、`FileSystemPersistence`、`SQLitePersistence` |
| Agent registry store | `InMemoryAgentRegistryStore`、`JsonFileAgentRegistryStore`、`PostgresAgentRegistryStore` |
| Multi-agent task store | `TaskTable`、`PostgresTaskStore` |
| Multi-agent message queue | `AgentInbox`、`RedisAgentMessageQueue` |

源码来源：[`src/agentos/memory/in_memory.py`](../src/agentos/memory/in_memory.py)，[`src/agentos/memory/redis_store.py`](../src/agentos/memory/redis_store.py)，[`src/agentos/memory/qdrant_index.py`](../src/agentos/memory/qdrant_index.py)，[`src/agentos/persistence/memory.py`](../src/agentos/persistence/memory.py)，[`src/agentos/persistence/filesystem.py`](../src/agentos/persistence/filesystem.py)，[`src/agentos/persistence/sqlite.py`](../src/agentos/persistence/sqlite.py)，[`src/agentos/persistence/postgres.py`](../src/agentos/persistence/postgres.py)，[`src/agentos/registry/persistent.py`](../src/agentos/registry/persistent.py)，[`src/agentos/registry/postgres.py`](../src/agentos/registry/postgres.py)，[`src/agentos/multi/tasks.py`](../src/agentos/multi/tasks.py)，[`src/agentos/multi/postgres_tasks.py`](../src/agentos/multi/postgres_tasks.py)，[`src/agentos/multi/inbox.py`](../src/agentos/multi/inbox.py)，[`src/agentos/multi/redis_queue.py`](../src/agentos/multi/redis_queue.py)

### 6.3 Production Schema Files

仓库提供 Postgres memory backend、Postgres agent registry、Postgres multi-agent task store、SQLite session persistence 和 Qdrant recall collection 的迁移/初始化脚本。SQLite persistence 源码注明 schema 必须由迁移流程预先准备；Postgres adapters 也只执行读写 SQL，不在运行时创建表。

源码来源：[`docs/migrations/2026-05-07-postgres-memory-backends.sql`](migrations/2026-05-07-postgres-memory-backends.sql)，[`docs/migrations/2026-05-07-postgres-agent-registry.sql`](migrations/2026-05-07-postgres-agent-registry.sql)，[`docs/migrations/2026-05-16-postgres-multi-agent-tasks.sql`](migrations/2026-05-16-postgres-multi-agent-tasks.sql)，[`docs/migrations/2026-05-07-sqlite-session-persistence.sql`](migrations/2026-05-07-sqlite-session-persistence.sql)，[`docs/migrations/2026-05-07-qdrant-recall-collection.py`](migrations/2026-05-07-qdrant-recall-collection.py)，[`src/agentos/persistence/sqlite.py`](../src/agentos/persistence/sqlite.py)，[`tests/architecture/test_public_api.py`](../tests/architecture/test_public_api.py)，[`tests/registry/test_remote_registry.py`](../tests/registry/test_remote_registry.py)，[`tests/multi/test_postgres_task_store.py`](../tests/multi/test_postgres_task_store.py)

---

## 7. Multi-Agent Coordination

当前 multi-agent 是本地协调器加可选 A2A remote dispatch。`AgentCoordinator` 支持：

| 能力 | 当前实现 |
|---|---|
| `attach_agent` | 注册本地 `AgentCard`，创建 inbox，并保存本地 agent 实例 |
| `spawn` | 创建 ephemeral subagent，通过 `SpawnExecutor` 在线程池执行 |
| `dispatch` | 按 capability 从 registry 选择 expert；endpoint-backed agent 走 remote task executor |
| `collect_results` | drain inbox，并消费 parent 可见的终态 task results |
| `cancel` | queued task 直接取消；running task 写入 cancel intent，并 best-effort interrupt 本地目标 agent |
| `execute_expert_envelope` | 执行 expert inbox 中的 `task_request` envelope |

`TaskStore` 是分布式任务 truth source 边界；`TaskTable` 是 in-memory adapter，`PostgresTaskStore` 是 Postgres adapter。`AgentMessageQueue` 是 delivery / notification 边界；`AgentInbox` 是 in-memory adapter，`RedisAgentMessageQueue` 是 Redis Streams adapter。`OutboxReconciler` 会补发 Postgres outbox 中未投递的 terminal result notification；`RedisAgentMessageQueue.reclaim_pending()` 支持 Redis Streams pending reclaim 和 dead-letter；`RedisContinuationTrigger` 支持 Redis Pub/Sub continuation 通知，并可 fallback 到 TaskStore polling。远程 endpoint-backed agent 仍通过 `RemoteTaskExecutor` 和 `A2AAdapter` 提交 HTTP JSON task。

源码来源：[`src/agentos/multi/coordinator.py`](../src/agentos/multi/coordinator.py)，[`src/agentos/multi/task_store.py`](../src/agentos/multi/task_store.py)，[`src/agentos/multi/tasks.py`](../src/agentos/multi/tasks.py)，[`src/agentos/multi/message_queue.py`](../src/agentos/multi/message_queue.py)，[`src/agentos/multi/inbox.py`](../src/agentos/multi/inbox.py)，[`src/agentos/multi/postgres_tasks.py`](../src/agentos/multi/postgres_tasks.py)，[`src/agentos/multi/redis_queue.py`](../src/agentos/multi/redis_queue.py)，[`src/agentos/multi/reconciler.py`](../src/agentos/multi/reconciler.py)，[`src/agentos/multi/redis_continuation.py`](../src/agentos/multi/redis_continuation.py)，[`src/agentos/multi/spawn.py`](../src/agentos/multi/spawn.py)，[`src/agentos/multi/remote.py`](../src/agentos/multi/remote.py)，[`src/agentos/multi/registry.py`](../src/agentos/multi/registry.py)，[`src/agentos/channels/a2a.py`](../src/agentos/channels/a2a.py)，[`tests/multi`](../tests/multi)，[`tests/channels/test_a2a_adapter.py`](../tests/channels/test_a2a_adapter.py)

---

## 8. Observability

### 8.1 Events

`events/` 提供 typed dataclass events 和 observation-only `EventBus`。`EventBus.emit()` 会记录 event 并调用 subscribers；subscriber 异常会记录到 `subscriber_errors`，不会改变执行流。

源码来源：[`src/agentos/events/types.py`](../src/agentos/events/types.py)，[`src/agentos/events/bus.py`](../src/agentos/events/bus.py)，[`tests/runtime/test_typed_events.py`](../tests/runtime/test_typed_events.py)

### 8.2 Instrumentation

`instrument_query_loop(loop, config)` 不修改原始 loop，而是用 wrapper 包装 provider、provider request builder、tool router 和 compression runtime，并返回 `InstrumentedQueryLoop`。`CapturePolicy` 默认是 metadata-only；redacted/full 模式需要显式选择。

`ObservabilityConfig.logging_enabled` 默认关闭。开启后，`configure_structured_logger()` 使用标准库 logging 输出 JSON lines，`QueryLoop` 记录 `turn_start`、`provider_call`、`tool_exec` 和 `turn_end`。

源码来源：[`src/agentos/observability/instrument.py`](../src/agentos/observability/instrument.py)，[`src/agentos/observability/instrumented.py`](../src/agentos/observability/instrumented.py)，[`src/agentos/observability/config.py`](../src/agentos/observability/config.py)，[`src/agentos/observability/logging.py`](../src/agentos/observability/logging.py)，[`src/agentos/runtime/query_loop.py`](../src/agentos/runtime/query_loop.py)，[`tests/observability/test_query_loop_instrumentation.py`](../tests/observability/test_query_loop_instrumentation.py)，[`tests/observability/test_capture_policy.py`](../tests/observability/test_capture_policy.py)，[`tests/observability/test_structured_logging.py`](../tests/observability/test_structured_logging.py)

### 8.3 OTel And Langfuse

`create_otel_tracer()` 创建 OTLP HTTP tracer；`create_langfuse_otel_tracer()` 使用 Langfuse OTLP endpoint 和 Basic Auth headers。trace context 支持 incoming header extraction 和 outgoing header injection。

源码来源：[`src/agentos/observability/otel.py`](../src/agentos/observability/otel.py)，[`src/agentos/observability/langfuse.py`](../src/agentos/observability/langfuse.py)，[`src/agentos/observability/context.py`](../src/agentos/observability/context.py)，[`tests/observability/test_otel_config.py`](../tests/observability/test_otel_config.py)，[`tests/observability/test_trace_propagation.py`](../tests/observability/test_trace_propagation.py)

---

## 9. Hooks And Security

`HookManager` 执行 `HookRegistry` 中匹配的 hook。当前 hook names 是 `before_provider_call`、`after_provider_call`、`before_tool_call`、`after_tool_call`；hook 可返回 `allow`、`deny` 或 `modify`。`QueryLoop` 在 provider call 和 tool call 前后调用 hook manager。

`SecurityPolicy` 是工具执行前的最小安全策略：`denied_tools` 优先，`allowed_tools` 可选。`ToolCallRouter` 和 `ToolExecutor` 都会在工具执行前调用 `ensure_tool_allowed()`。

源码来源：[`src/agentos/hooks/base.py`](../src/agentos/hooks/base.py)，[`src/agentos/hooks/registry.py`](../src/agentos/hooks/registry.py)，[`src/agentos/hooks/manager.py`](../src/agentos/hooks/manager.py)，[`src/agentos/runtime/query_loop.py`](../src/agentos/runtime/query_loop.py)，[`src/agentos/policies/security.py`](../src/agentos/policies/security.py)，[`src/agentos/capabilities/router.py`](../src/agentos/capabilities/router.py)，[`src/agentos/capabilities/executor.py`](../src/agentos/capabilities/executor.py)，[`tests/hooks/test_runtime.py`](../tests/hooks/test_runtime.py)，[`tests/runtime/test_query_loop_hooks.py`](../tests/runtime/test_query_loop_hooks.py)

---

## 10. Quick Reference

### 10.1 Install From This Repo

```bash
pip install -e .
pip install -e ".[redis]"
pip install -e ".[postgres]"
pip install -e ".[qdrant]"
pip install -e ".[observability]"
pip install -e ".[async-http]"
pip install -e ".[production-memory]"
```

源码来源：[`pyproject.toml`](../pyproject.toml)

### 10.2 Key Public Names

Root package exports include `AgentBuilder`, `Agent`, `QueryLoop`, `ProviderRequestBuilder`, `Provider`, `ToolCallRouter`, `HookManager`, channel classes, multi-agent classes, memory adapters, and registry adapters.

源码来源：[`src/agentos/__init__.py`](../src/agentos/__init__.py)，[`tests/architecture/test_public_api.py`](../tests/architecture/test_public_api.py)

### 10.3 Verification

当前项目测试入口：

```bash
uv run pytest -q
```

源码来源：[`pyproject.toml`](../pyproject.toml)，[`README.md`](../README.md)
