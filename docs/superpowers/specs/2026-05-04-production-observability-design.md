# 生产级可观测性设计

## 目标

把 agentos 的 Phase 6 observability 从当前的轻量 stub 升级为生产级观测架构：业务 runtime 不依赖 OpenTelemetry 或 Langfuse，但用户开启观测后，可以在 Langfuse 里看到一次 user request 触发的完整 LLM 交互、工具调用、压缩和错误链路。

这份 spec 只设计 observability。它不改变默认 LLM-visible context，不把 runtime metadata 渲染进 `ContextRenderer`，也不把 hook 当日志系统使用。

## 设计参考

- `AGENTS.md`
- `docs/design/sdk-architecture.md`
- `docs/design/llm-context-only-example.md`
- `docs/superpowers/specs/2026-05-03-phase-5-6-skills-mcp-persistence-observability-design.md`
- `../ai-knowledge/wiki/evaluation-observability.md`
- `../ai-knowledge/wiki/_patterns/otel-eval-bridge.md`
- `../ai-knowledge/wiki/hooks.md`
- `../ai-knowledge/wiki/query-loop.md`
- `../ai-knowledge/wiki/tool-system.md`
- `../ai-knowledge/wiki/prompt-system.md`
- `../ai-knowledge/projects/neoagent/decisions/2026-04-22-langfuse-integration.md`
- Langfuse OpenTelemetry integration docs
- Langfuse observation type docs
- OpenTelemetry GenAI semantic conventions

## Scope Contract

这属于 Phase 6 的生产级 observability 专项。

本设计要完成的验收项：

- 通过 span 观测真实 provider request/response，包括受策略控制的 LLM input/output 捕获。
- 通过 OTLP 让 Langfuse 展示 generation、tool、agent observation，而不是 ad hoc fake trace record。
- OpenTelemetry 是 optional dependency；不安装 OTel 时，核心 `agentos` import 和测试仍然能跑。
- 明确拆分 hooks、runtime events、debug projection 和 production tracing 的职责。
- 定义 capture policy 和 redaction 默认值：本地调试能看清楚，生产默认不泄露 prompt/tool payload。
- 统一 provider usage，让 token/cost attribution 能挂到 provider generation span 上。

本设计明确不做的内容：

- Streaming token/chunk 级观测。第一版生产实现只记录 provider call 完成后的完整请求/响应。
- 跨进程 trace propagation 到 MCP server 和未来 subagent。第一版只记录 client 侧 MCP/tool span；W3C `traceparent` 传播放到 MCP/multi-agent 后续专项。
- Eval runner 和 in-memory exporter 聚合。Tracer protocol 会为它留接口，但 eval 是后续阶段。
- Langfuse prompt management、datasets、scores、prompt versioning。这份 spec 只覆盖 runtime traces。
- pricing table 和本地 cost 计算。这份 spec 只记录 token usage 和 provider 可能返回的 cost；价格归因可以后续叠加。

不能被简化掉的设计规则：

- 生产 observability 不能只靠 `EventBus` 投影实现。`EventBus` 不携带完整 provider input/output、嵌套生命周期、调用耗时和异常边界。它仍然用于 append-only runtime facts、session recovery 和 debug projection；生产链路必须由 runtime 边界上的 instrumentation 产生 OTel spans。

## 当前问题

当前 Phase 6 observability 模块是有意做薄的：

- `EventLog` 记录 typed runtime events。
- `EventTraceProjector` 把 event record 转成内部 `TraceRecord`。
- `OTelAdapter` 和 `LangfuseAdapter` 把这些 trace record 输出给注入的 client。

这能证明边界存在，但不是生产级 LLM observability：

- `ProviderRequestBuiltEvent` 和 `ProviderResponseReceivedEvent` 没有 rendered system prompt、active messages、provider tool schema、model、usage 或 response payload。
- Event projection 生成的是一批离散记录，不是有父子关系、耗时和异常状态的 span 树。
- Langfuse 不能稳定把 provider call 识别成 generation，除非我们设置 generation-specific attributes 和 input/output。
- hooks 曾经容易被当成日志 callback，但 hook 的职责是 policy/interception，不应该承担观测管道。

生产设计要从“event projection 作为 observability”改成“proxy instrumentation 作为 observability”。Event projection 保留为 debug/export helper。

## 方案对比

### 方案 A：只增强 EventBus Projector

继续沿用当前模型，把 typed event payload 加大，再投影到 trace。

优点：

- 改动最小。
- 不需要 optional dependency。
- Event records 已经可以持久化。

缺点：

- 会把大块 prompt/response payload 塞进 runtime events。
- 无法自然表达 span lifetime、嵌套关系和异常边界。
- 会诱导 `QueryLoop` 等 runtime 模块直接发 observability metadata。
- 生产隐私风险更高，因为 event persistence 会保存和 trace 一样敏感的 payload。

结论：不作为生产 observability 方案。只保留用于 debug projection 和持久化 runtime facts。

### 方案 B：直接接 Langfuse SDK

在 runtime 或 instrumentation 层直接调用 Langfuse Python SDK。

优点：

- Langfuse 相关功能接入最快。
- 以后接 prompt、dataset、score 等 Langfuse 功能更直接。

缺点：

- SDK 的生产观测路径被 Langfuse 绑定。
- 核心 runtime 或 adapter 测试会更依赖 Langfuse client。
- Jaeger、Tempo、SkyWalking、Datadog 会变成二等 backend。

结论：不作为默认生产架构。未来如果确实需要 Langfuse SDK 的高级功能，可以作为 optional sink 单独增加。

### 方案 C：OTel Spans + Langfuse OTLP Backend

定义 agentos 自己的轻量 `Tracer/Span` Protocol，通过 proxy instrumentation 包住 runtime 边界。可选 OTel 实现把 span 发到任何 OTLP backend；Langfuse 是推荐的默认 LLM 观测后端。

优点：

- 业务代码保持干净。
- Backend 可替换：本地/生产可以用 Langfuse，也可以换 Tempo、Jaeger、SkyWalking。
- Langfuse 能根据 OTel span attributes 展示 generation、tool、agent observation。
- 同一套 span 后续能给 eval aggregation 使用，例如 in-memory exporter。

缺点：

- 首次实现比 stub 复杂。
- 必须认真做 capture policy，否则容易把 prompt 和 tool payload 泄露到监控系统。

结论：采用方案 C。

## 职责边界

### Hooks

Hooks 是用户干预执行流的扩展点：

- `before_provider_call`
- `after_provider_call`
- `before_tool_call`
- `after_tool_call`

Hook 可以通过 `HookResult` 显式返回 allow、deny、modify。Hook 不是日志 API，不产生 trace。Hook failure 可以被 `HookManager` 记录，未来也可以作为当前 span 上的 event，但 hook 本身不承担 observability pipeline。

### EventBus

`EventBus` 发布 typed runtime facts：

- turn started/completed/failed
- messages appended
- provider request/response lifecycle facts
- tool execution facts
- context/compression/recall/persistence facts

Event handler 是 observation-only，不能改变执行流。`EventLog` 持久化这些 facts，用于 session recovery 和 debug projection。

### Debug Projection

`context/debug_projection.py` 是显式的本地调试视图。它可以展示 runtime ids、compression index 和 recent events。它永远不由 `ProviderRequestBuilder` 调用，也不影响默认 prompt。

### Production Observability

`observability/` 负责 traces、spans、capture policy、redaction 和 exporter setup。它通过 wrappers 观测 runtime 边界。`runtime/`、`providers/`、`capabilities/`、`context/` 不 import OTel、Langfuse 或 span API。

## 生产架构

```text
Application composition
  -> QueryLoop
  -> instrument_query_loop(loop, config)
  -> InstrumentedQueryLoop
       root span: agent.turn
       wraps:
         ProviderRequestBuilder
         Provider
         ToolCallRouter
         CompressionRuntime

Runtime execution
  -> agent.turn span
     -> compression.maybe_compress span
     -> provider.request.build span
     -> provider.complete generation span
     -> tool.<name> tool span
     -> provider.request.build span
     -> provider.complete generation span
```

是否开启观测是构造时决策：

- 不开启：应用直接使用原始 `QueryLoop`。
- 开启：应用通过 `instrument_query_loop(...)` 包一层。

`instrument_query_loop(...)` 不能 mutate 调用方传进来的原始 `QueryLoop`。它创建一个浅拷贝配置后的 `QueryLoop`，把其中 provider、request builder、tool router、compression runtime 替换为 instrumented proxies，然后返回拥有 root `agent.turn` span 的 `InstrumentedQueryLoop`。

业务模块不知道自己正在被观测。

## 模块设计

新增或重写这些模块：

- `src/agentos/observability/config.py`
  - `ObservabilityConfig`
  - `CapturePolicy`
  - `CaptureMode`
  - `Redactor`
  - 默认 redaction helpers

- `src/agentos/observability/tracer.py`
  - agentos 自己的 `Tracer` Protocol
  - agentos 自己的 `Span` Protocol
  - `NoOpTracer`
  - `InMemoryTracer`，供测试和后续 eval 使用

- `src/agentos/observability/snapshots.py`
  - `ProviderRequestSnapshot`
  - `ProviderResponseSnapshot`
  - `ToolCallSnapshot`
  - `ToolResultSnapshot`
  - 安全的 Langfuse `input`/`output` serializer

- `src/agentos/observability/conventions.py`
  - Langfuse attribute names
  - OpenTelemetry GenAI semantic convention names
  - agentos extension attribute names

- `src/agentos/observability/instrumented.py`
  - `InstrumentedQueryLoop`
  - `InstrumentedProviderRequestBuilder`
  - `InstrumentedProvider`
  - `InstrumentedToolCallRouter`
  - `InstrumentedCompressionRuntime`

- `src/agentos/observability/instrument.py`
  - `instrument_query_loop(loop, config) -> InstrumentedQueryLoop`
  - 测试中可使用的单组件 wrapping helper

- `src/agentos/observability/otel.py`
  - 可选 OTel tracer 实现
  - `create_otel_tracer(...)`
  - `create_langfuse_otel_tracer(...)`
  - 如果用户调用 factory 但没安装 OTel extras，给出明确安装提示

- `src/agentos/observability/langfuse.py`
  - Langfuse OTLP endpoint helper
  - Langfuse OTLP auth/header helper
  - 不依赖 Langfuse SDK

保留但收窄职责的模块：

- `src/agentos/observability/events.py`
  - 只负责 `EventLog` 和 `EventRecord`。

- `src/agentos/observability/traces.py`
  - 只负责 EventLog-to-debug-trace projection。
  - 它不是生产 LLM trace pipeline。

## 公共 API

从 `agentos.observability` 导出：

```python
from agentos.observability import (
    CaptureMode,
    CapturePolicy,
    InMemoryTracer,
    NoOpTracer,
    ObservabilityConfig,
    ProviderRequestSnapshot,
    ProviderResponseSnapshot,
    Redactor,
    Span,
    Tracer,
    create_langfuse_otel_tracer,
    create_otel_tracer,
    instrument_query_loop,
)
```

公共 import 包名必须保持 `agentos`。

## Tracer Protocol

核心 SDK 不能依赖 OpenTelemetry 类型。agentos 内部 protocol 是：

```python
class Span(Protocol):
    def set_attribute(self, key: str, value: object) -> None: ...
    def set_attributes(self, attributes: Mapping[str, object]) -> None: ...
    def add_event(self, name: str, attributes: Mapping[str, object] | None = None) -> None: ...
    def record_exception(self, error: BaseException) -> None: ...
    def set_status(self, status: str, description: str | None = None) -> None: ...
    def __enter__(self) -> "Span": ...
    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool | None: ...


class Tracer(Protocol):
    def start_span(
        self,
        name: str,
        *,
        attributes: Mapping[str, object] | None = None,
    ) -> Span: ...
```

OTel 实现把它映射到 `start_as_current_span`。测试 fake 可以实现同样 protocol，不需要 import OTel。

## Capture Policy

生产默认必须安全：

```python
@dataclass(frozen=True, slots=True)
class CapturePolicy:
    mode: CaptureMode = "metadata"
    capture_system: bool = False
    capture_messages: bool = False
    capture_tool_schemas: bool = False
    capture_provider_output: bool = False
    capture_tool_arguments: bool = False
    capture_tool_result: bool = False
    max_string_length: int = 4000
    redactor: Redactor = default_redactor
```

模式：

- `metadata`：默认模式。只记录 counts、lengths、hashes、ids、model、stop reason、tool names 和 usage。不记录 prompt、messages、tool args 或 tool results。
- `redacted`：记录经过 redactor 和长度限制处理后的结构化 input/output。
- `full`：记录原始 provider input/output 和 tool input/output，只做长度限制。只建议用于本地开发和自托管 Langfuse。

便利构造函数：

- `CapturePolicy.metadata_only()`
- `CapturePolicy.redacted()`
- `CapturePolicy.full_for_local_development()`

默认 redactor 替换字符串中的疑似 secret：

- OpenAI、Anthropic、Langfuse 风格 API keys。
- `Authorization`、`api_key`、`secret`、`token`、`password` 等 object key。
- PEM private key blocks。

所有值写入 span attribute 前必须先过 redactor。

## Provider Usage

新增标准化 provider usage：

```python
@dataclass(frozen=True, slots=True)
class ProviderUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cached_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    reasoning_output_tokens: int | None = None
    cost_usd: float | None = None
```

扩展 `ProviderResponse`：

```python
usage: ProviderUsage | None = None
model: str | None = None
provider_name: str | None = None
response_id: str | None = None
```

Provider adapter 在 provider response 暴露 usage 时应填充这些字段。Instrumentation 层必须容忍 `None`。

## Snapshot Model

Provider request snapshot：

```python
@dataclass(frozen=True, slots=True)
class ProviderRequestSnapshot:
    system: str | None
    messages: tuple[dict[str, object], ...] | None
    tools: tuple[dict[str, object], ...] | None
    system_length: int
    message_count: int
    tool_count: int
    system_sha256: str
    messages_sha256: str
    tools_sha256: str
```

Provider response snapshot：

```python
@dataclass(frozen=True, slots=True)
class ProviderResponseSnapshot:
    content: str | None
    content_length: int
    content_sha256: str
    tool_calls: tuple[ToolCallSnapshot, ...]
    stop_reason: str | None
    usage: ProviderUsage | None
    model: str | None
    provider_name: str | None
    response_id: str | None
```

`None` content 表示 capture policy 没有捕获原始内容。length 和 hash 仍会记录，用于调试和关联。

Snapshot hash 使用 canonical JSON 计算：`sort_keys=True`、`ensure_ascii=False`、compact separators。这样等价 dict ordering 的 hash 稳定，同时 capture 打开时中文内容仍可读。

## Span 层级

一次包含一个工具调用的 turn：

```text
agent.turn
├─ compression.maybe_compress
├─ provider.request.build
├─ provider.complete
├─ tool.read_file
├─ provider.request.build
└─ provider.complete
```

Root span 从 `InstrumentedQueryLoop.run_turn(...)` 开始，在 turn 成功或失败时结束。

子 span 由组件 proxy 创建：

- `InstrumentedCompressionRuntime.maybe_compress(...)`
- `InstrumentedProviderRequestBuilder.build(...)`
- `InstrumentedProvider.complete(...)`
- `InstrumentedToolCallRouter.execute_tool_call(...)`

如果被包裹调用抛异常，当前 span 记录 exception 和 `error` status，然后重新抛出。Instrumentation 不能吞异常。

## Attribute 映射

### Root Span：`agent.turn`

Attributes：

- `langfuse.observation.type = "agent"`
- `langfuse.trace.name = "agentos.turn"`
- `langfuse.session.id = session_id`，如果存在
- `agentos.session.id = session_id`，如果存在
- `agentos.turn.id = turn_id`，如果存在
- `agentos.turn.max_tool_iterations`
- `agentos.capture.mode`

Root span input/output 遵守 capture policy。`metadata` 模式只记录 user input length/hash 和 final output length/hash。

### Provider Request Build Span：`provider.request.build`

Attributes：

- `langfuse.observation.type = "span"`
- `agentos.provider_request.system.length`
- `agentos.provider_request.messages.count`
- `agentos.provider_request.tools.count`
- `agentos.provider_request.system.sha256`
- `agentos.provider_request.messages.sha256`
- `agentos.provider_request.tools.sha256`

当 policy 允许捕获内容时：

- `langfuse.observation.input`
- `agentos.provider_request.system`
- `agentos.provider_request.messages`
- `agentos.provider_request.tools`

### Provider Complete Span：`provider.complete`

Attributes：

- `langfuse.observation.type = "generation"`
- `langfuse.observation.model.name`
- `gen_ai.operation.name = "chat"`
- `gen_ai.provider.name`
- `gen_ai.request.model`
- `gen_ai.response.finish_reasons`
- `gen_ai.usage.input_tokens`
- `gen_ai.usage.output_tokens`
- `gen_ai.usage.total_tokens`
- `agentos.provider.response_id`
- `agentos.provider.tool_call_count`

当 policy 允许捕获内容时：

- `langfuse.observation.input`
- `langfuse.observation.output`

为了 Langfuse 兼容，token usage 还要序列化进 `langfuse.observation.usage_details`。

### Tool Span：`tool.<name>`

Attributes：

- `langfuse.observation.type = "tool"`
- `tool.name`
- `tool.call_id`
- `agentos.tool.kind`
- `agentos.tool.arguments.sha256`
- `agentos.tool.result.sha256`
- `agentos.tool.result.length`

当 policy 允许捕获内容时：

- `langfuse.observation.input`
- `langfuse.observation.output`

Router wrapper 必须覆盖四类工具路径：context protocol tools、external tools、skill tools 和 MCP tools，因为它们都通过 `ToolCallRouter`。

### Compression Span：`compression.maybe_compress`

Attributes：

- `langfuse.observation.type = "span"`
- `agentos.compression.executed`
- `agentos.compression.segment_id`，如果产生 segment
- `agentos.compression.source_message_count`，如果可得
- `agentos.compression.reason`，如果 skipped

第一版可以只记录当前 return values 和 events 能得到的 before/after metadata。不能记录原始 message content。

## Langfuse OTLP 配置

Langfuse 是推荐 backend，但 SDK 发送的是标准 OTLP spans。

Factory：

```python
tracer = create_langfuse_otel_tracer(
    host="http://localhost:3000",
    public_key="pk-lf-...",
    secret_key="sk-lf-...",
    service_name="agentos",
    environment="local",
)
```

Helper 配置：

- OTLP HTTP trace endpoint：`{host}/api/public/otel/v1/traces`
- Authorization header：`Basic base64(public_key:secret_key)`
- Langfuse ingestion header：`x-langfuse-ingestion-version: 4`
- OTel resource attributes：
  - `service.name`
  - `deployment.environment.name`
  - `telemetry.sdk.language = python`，如果 OTel SDK 提供

Helper 位于 optional dependency 后面。Import `agentos` 或 `agentos.observability` 不应该要求已安装 OpenTelemetry；只有调用 factory 时才需要。

## Optional Dependencies

`pyproject.toml` 增加：

```toml
[project.optional-dependencies]
observability = [
    "opentelemetry-api>=1.28",
    "opentelemetry-sdk>=1.28",
    "opentelemetry-exporter-otlp-proto-http>=1.28",
]
```

不增加 required runtime dependency。

## 用户 API

本地开发，打开 full capture：

```python
from agentos.observability import (
    CapturePolicy,
    ObservabilityConfig,
    create_langfuse_otel_tracer,
    instrument_query_loop,
)

tracer = create_langfuse_otel_tracer(
    host="http://localhost:3000",
    public_key="pk-lf-...",
    secret_key="sk-lf-...",
    service_name="agentos-local",
    environment="local",
)

loop = instrument_query_loop(
    loop,
    ObservabilityConfig(
        tracer=tracer,
        capture_policy=CapturePolicy.full_for_local_development(),
    ),
)

loop.run_turn("帮我分析这个 bug")
```

生产环境，默认 metadata-only capture：

```python
loop = instrument_query_loop(
    loop,
    ObservabilityConfig(
        tracer=tracer,
        capture_policy=CapturePolicy.metadata_only(),
    ),
)
```

关闭观测：

```python
loop.run_turn("不启用观测时无需改业务代码")
```

## 实现规则

- `runtime/query_loop.py` 不得 import `agentos.observability`。
- `runtime/provider_request_builder.py` 不得 import `agentos.observability`。
- `providers/*` 不得 import OTel 或 Langfuse。
- `capabilities/router.py` 不得 import OTel 或 Langfuse。
- Observability wrappers 可以 import runtime/provider/capability protocols。
- Instrumentation 必须保留现有行为和 exception semantics。
- Instrumentation 不得修改 provider request、provider response、tool arguments 或 tool results。
- Capture policy 必须在任何值序列化进 span attributes 之前应用。
- 默认 prompt golden tests 必须继续拒绝 `session_id`、`trace_id`、`span_id`、`tool_call_id`、`schema_id`、`projection_id`、`compression_id`、`source` 和 `relevance`。

## 测试矩阵

### Unit Tests

- `tests/observability/test_capture_policy.py`
  - metadata 模式只记录 length/hash，不记录原始内容。
  - redacted 模式移除 API keys 和 secret-like fields。
  - full 模式在长度限制内捕获原始 provider request/response。

- `tests/observability/test_snapshots.py`
  - provider request snapshot 是 deterministic。
  - provider response snapshot 包含 tool calls、stop reason 和 usage。
  - 等价 dict ordering 的 hash 稳定。

- `tests/observability/test_in_memory_tracer.py`
  - nested spans 保持 parent/child 顺序。
  - wrapper 遇到异常时设置 error status 并重新抛出。

- `tests/observability/test_instrumented_provider.py`
  - provider span 记录 `generation` type、model、stop reason 和 usage。
  - provider wrapper 不改变 `ProviderResponse`。

- `tests/observability/test_instrumented_router.py`
  - external、context、skill、MCP tool calls 都产生 `tool` spans。
  - denied tool call 记录 error status，并保留原始异常。

- `tests/observability/test_otel_config.py`
  - Langfuse endpoint 是 `{host}/api/public/otel/v1/traces`。
  - Authorization 是 Basic `base64(public_key:secret_key)`。
  - `x-langfuse-ingestion-version` 设置为 `4`。
  - 不安装 OTel 时 import `agentos.observability` 不失败。

### Provider Adapter Tests

- `tests/providers/test_adapters.py`
  - OpenAI adapter 在字段存在时映射 prompt/completion/cached/reasoning usage。
  - Anthropic adapter 在字段存在时映射 input/output/cache usage。
  - provider 没有 usage 时保持 `None`，不破坏旧 fake clients。

### Integration Tests

- `tests/observability/test_query_loop_instrumentation.py`
  - 一个会调用工具的 fake provider 产生：

```text
agent.turn
├─ compression.maybe_compress
├─ provider.request.build
├─ provider.complete
├─ tool.<name>
├─ provider.request.build
└─ provider.complete
```

  - final assistant response 不变。
  - provider request span 包含 rendered system length/hash 和 tool count。
  - provider generation span 包含 assistant output length/hash 和 usage。

- `tests/context/test_renderer.py`
  - 安装 observability 后，默认 context 仍然不包含 runtime metadata。

- `tests/architecture/test_public_api.py`
  - public observability API names 被导出。
  - 历史错误包名别名被拒绝。

### Smoke Test

保留并更新：

- `scripts/langfuse_otel_smoke_test.py`

实现后增加第二条 smoke path：

- 创建一个 fake `QueryLoop`。
- 用 `create_langfuse_otel_tracer(...)` instrument。
- 运行一个 turn。
- 打印 OTel trace id 和 Langfuse 搜索提示。

这个脚本不进入 unit tests，因为它需要本地 Langfuse 实例和 API keys。

## 必跑验证

```bash
uv run --python 3.11 --extra dev pytest -q
uv run --python 3.11 --extra dev python -m compileall -q src tests scripts
git diff --check
rg -n "agent[O]s|agent[_]os" src tests docs pyproject.toml AGENTS.md .gitignore
rg -n "from opentelemetry|import opentelemetry|langfuse" src/agentos/runtime src/agentos/providers src/agentos/capabilities src/agentos/context
rg -n "session_id|turn_id|message_id|trace_id|span_id|tool_call_id|schema_id|projection_id|compression_id|source|relevance" tests/context/goldens src/agentos/context/renderer.py
```

最后一条命令只允许命中“断言 forbidden metadata 不存在”的测试，或明确不是默认 prompt 的 debug projection 测试。

## 验收清单

| Requirement | Implementation files | Test files | Status |
|---|---|---|---|
| 通过构造期 instrumentation 开启观测，runtime 不直接 import observability。 | `observability/instrument.py`, `observability/instrumented.py` | `tests/observability/test_query_loop_instrumentation.py` | required |
| 为 turn、provider request build、provider complete、tool calls、compression 产生 OTel spans。 | `observability/instrumented.py`, `observability/tracer.py` | `tests/observability/test_query_loop_instrumentation.py`, `tests/observability/test_in_memory_tracer.py` | required |
| Langfuse 能通过 OTLP attributes 把 provider call 展示为 generation。 | `observability/conventions.py`, `observability/otel.py`, `observability/langfuse.py` | `tests/observability/test_instrumented_provider.py`, `tests/observability/test_otel_config.py` | required |
| 默认 capture policy 不记录原始 prompt/message/tool payload。 | `observability/config.py`, `observability/snapshots.py` | `tests/observability/test_capture_policy.py`, `tests/observability/test_snapshots.py` | required |
| Provider usage 被归一化并挂到 provider response span。 | `providers/base.py`, `providers/openai.py`, `providers/anthropic.py`, `providers/openai_compatible.py` | `tests/providers/test_adapters.py`, `tests/observability/test_instrumented_provider.py` | required |
| Tool spans 覆盖 external、context、skill、MCP 四类路由路径。 | `observability/instrumented.py`, `capabilities/router.py` boundary only | `tests/observability/test_instrumented_router.py` | required |
| EventBus 保持 typed facts/debug persistence，不作为生产 trace source。 | `observability/events.py`, `observability/traces.py` | `tests/observability/test_event_log.py`, `tests/observability/test_traces.py` | required |
| Hooks 保持 policy/interception 职责，不变成 logging events。 | `hooks/base.py`, `hooks/manager.py` | `tests/hooks/test_runtime.py`, `tests/observability/test_query_loop_instrumentation.py` | required |
| Core SDK 在未安装 OTel 时仍可 import 和运行测试。 | `observability/__init__.py`, `observability/otel.py`, `pyproject.toml` | `tests/observability/test_otel_config.py`, full test suite | required |
| 默认 LLM-visible context 保持 metadata-free。 | `context/renderer.py` 不因 observability 改动 | `tests/context/test_renderer.py`, renderer golden tests | required |

生产级 observability 只有在所有 required 行都实现、测试并通过验证后，才算完成。
