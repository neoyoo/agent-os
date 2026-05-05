---
name: agentos streaming thinking otel design
description: 为 agentos 设计流式输出、thinking 输出和 OTel/Langfuse 观测适配。
type: design-spec
status: draft
date: 2026-05-05
relates_to:
  - docs/design/sdk-architecture.md
  - docs/design/llm-context-only-example.md
  - docs/superpowers/specs/2026-05-04-production-observability-design.md
  - docs/superpowers/specs/2026-05-04-trace-context-propagation-design.md
  - ../ai-knowledge/wiki/_impl/query-loop--claude-code.md
  - ../ai-knowledge/wiki/_impl/evaluation-observability--agentscope.md
  - ../ai-knowledge/wiki/evaluation-observability.md
---

# Streaming、Thinking 与 OTel 适配设计

## 背景

当前 agentos 的 provider 边界只有：

```python
Provider.complete(request) -> ProviderResponse
```

`QueryLoop` 会等待完整 `ProviderResponse` 返回后再把 assistant message 写入 `MessageRuntime`。这个模式适合最小 agent loop，但不适合 SDK 交互层：用户调用 SDK 时需要实时看到文本、thinking、工具状态和最终完成事件，也需要在 HTTP/SSE 场景中直接把事件流推给前端。

同时，当前 production observability 的 provider span 也基于完整响应：span 在 `complete()` 调用前开始，在完整 `ProviderResponse` 返回后写 output、usage、finish reason 并结束。流式输出引入后，OTel span 不能在第一个 token 后结束，也不能等到下一轮 tool call 后才结束。provider generation span 必须覆盖一次 LLM streaming request 的完整生命周期，并在 stream terminal event 后写聚合后的 response attributes。

参考实现：

- Claude Code 的 query loop 是 async generator，核心 runtime 产出结构化 streaming events，REPL 和 SDK 共享同一套事件流。
- Claude Code 把 thinking block 作为协议对象处理，不把 thinking 当普通 assistant 文本。
- AgentScope 的 OTel tracing 在 streaming generator 最后一个 chunk 后才写 response attributes 并结束 span，避免 span 只覆盖首 token。
- OpenTelemetry GenAI semantic conventions 当前要求 GenAI inference span 设置 `gen_ai.request.stream`，但 streaming chunks 小节仍未定义稳定字段。
- Langfuse OTel mapping 推荐手动 instrumentation 使用 `langfuse.*` attributes，并用 `langfuse.observation.input/output`、`langfuse.trace.input/output` 提供 UI 可见的 input/output。

外部参考：

- OpenTelemetry GenAI spans: https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-spans/
- Langfuse OpenTelemetry integration: https://langfuse.com/integrations/native/opentelemetry
- Langfuse observation types: https://langfuse.com/docs/observability/features/observation-types
- Langfuse empty input/output FAQ: https://langfuse.com/faq/all/empty-trace-input-and-output

## Scope Contract

本设计属于 Phase 6 observability 的增量专项，同时补齐 Phase 4 provider streaming 边界和 SDK 交互入口。

本设计要完成的验收项：

- SDK 用户不需要每次手动 new 不同 proxy、channel 或 tracer 对象；开启 streaming、SSE 和 thinking 通过每轮轻量参数控制。
- 核心 runtime 产出 typed stream events，SSE/JSONL/callback 只是事件适配层。
- `Provider.complete()` 保留，新增流式 provider 协议；`run()` 继续返回完整结果，`stream()` 返回 typed events，`stream_sse()` 返回 SSE 字符串。
- thinking 输出与 assistant content 分离。thinking 默认不进入 LLM context、不进入 OTel payload、不写入 message store 的普通 content。
- OTel provider generation span 覆盖一次 streaming request 的完整生命周期，在 stream terminal event 后写 output、usage、finish reason 并结束。
- OTel 不为每个 token 创建 span；chunk 只作为受 capture policy 控制的 span event 或聚合统计。
- Langfuse 能看到 agent、generation、tool 的层级，以及完整 generation input/output。
- 默认 LLM-visible prompt 不出现 streaming、thinking、trace、span、session 等 runtime metadata。

本设计明确不做：

- 不实现 HTTP server 或 FastAPI 集成；只提供 SSE iterator 适配，HTTP channel 属于 Phase 8。
- 不实现 async API 第一版。第一版用同步 `Iterator`，保留未来 `AsyncIterator` 扩展点。
- 不实现跨进程 subagent streaming。当前只保证事件模型能承载未来 `subagent.*` events。
- 不把 thinking 默认持久化为普通 assistant content。provider replay 需要的 reasoning details 后续单独设计。
- 不实现 token 级成本估算。usage 以 provider terminal event 返回为准。
- 不在 OTel 中记录每个 token 的完整文本，除非本地 full capture 明确开启。

不能被简化掉的规则：

- streaming 的事实源必须是 typed stream events，而不是 print callback。
- OTel span 生命周期必须跟 provider stream terminal event 对齐，不能在首 token、首 tool call 或 agent turn 结束时误结束 provider span。
- capture policy 必须独立控制 prompt、assistant output、thinking、tool payload 和 stream deltas。
- `runtime/query_loop.py` 仍只负责编排，不 import OpenTelemetry、Langfuse 或 span API。

## 用户 API

用户只在 agent 初始化时装配长期依赖：

```python
agent = Agent(
    provider=OpenAICompatibleProvider(...),
    tools=[read_file_tool(root=".")],
    observability=ObservabilityConfig(...),
)
```

每轮调用通过轻量参数决定交互形态：

```python
result = agent.run(
    "读取 pyproject.toml 并总结项目名",
    thinking=True,
)
```

```python
for event in agent.stream(
    "读取 pyproject.toml 并总结项目名",
    thinking=True,
    show_thinking=True,
):
    ...
```

```python
for chunk in agent.stream_sse(
    "读取 pyproject.toml 并总结项目名",
    thinking=True,
    show_thinking=True,
):
    yield chunk
```

也可以通过 `RunOptions` 传入，便于 Web 层复用：

```python
options = RunOptions(
    stream=True,
    output_format="sse",
    thinking=True,
    show_thinking=False,
)
```

公开 API 约束：

- `run()` 返回完整 `AgentResult`，不返回 iterator。
- `stream()` 返回 `Iterator[TurnStreamEvent]`，是 SDK 的核心流式 API。
- `stream_sse()` 返回 `Iterator[str]`，是 channel adapter，不是核心 runtime。
- callback API 只消费 `stream()`：

```python
agent.run_with_callbacks(
    "hello",
    thinking=True,
    on_content_delta=...,
    on_thinking_delta=...,
    on_tool_started=...,
)
```

## 核心事件模型

新增 `runtime/stream_events.py` 或 `providers/stream.py`。事件分两层：

1. Provider stream events：只描述一次 provider request 的流式输出。
2. Turn stream events：描述 agent turn，包括 provider、tool、assistant 和 turn 生命周期。

Provider events：

```python
ProviderStreamStarted
ProviderContentDelta
ProviderThinkingDelta
ProviderToolCallDelta
ProviderUsageDelta
ProviderStreamCompleted
ProviderStreamFailed
ProviderStreamCancelled
```

Turn events：

```python
TurnStreamStarted
ProviderStreamEventEmitted
ToolStreamStarted
ToolStreamCompleted
ToolStreamFailed
AssistantContentDelta
AssistantThinkingDelta
AssistantCompleted
TurnStreamCompleted
TurnStreamFailed
TurnStreamCancelled
```

事件命名规则：

- runtime lifecycle facts 继续使用 `*Event` dataclass。
- streaming SDK events 可以使用 `*Delta` / `*Completed` dataclass，但必须 typed，不使用 loose string dict 作为内部事实源。
- SSE/JSONL 输出可以序列化成 string event name，但那是 channel adapter，不反向污染内部类型。

最小事件字段：

```python
@dataclass(frozen=True, slots=True)
class ProviderContentDelta:
    request_id: str
    index: int
    text: str

@dataclass(frozen=True, slots=True)
class ProviderThinkingDelta:
    request_id: str
    index: int
    text: str

@dataclass(frozen=True, slots=True)
class ProviderToolCallDelta:
    request_id: str
    index: int
    tool_call_id: str | None
    name_delta: str | None
    arguments_delta: str | None

@dataclass(frozen=True, slots=True)
class ProviderStreamCompleted:
    request_id: str
    response: ProviderResponse
    stop_reason: str | None
```

`ProviderStreamCompleted.response` 必须是聚合后的标准 `ProviderResponse`。这保证旧的 `complete()`、message append、observability output 和后续 tool routing 都能复用同一标准对象。

## Provider 协议

保留：

```python
class Provider(Protocol):
    def complete(self, request: ProviderRequest) -> ProviderResponse: ...
```

新增：

```python
class StreamingProvider(Provider, Protocol):
    def stream(
        self,
        request: ProviderRequest,
        options: ProviderStreamOptions | None = None,
    ) -> Iterator[ProviderStreamEvent]: ...
```

兼容策略：

- 如果 provider 实现 `stream()`，`QueryLoop.run_turn_stream()` 使用真实 streaming。
- 如果 provider 只实现 `complete()`，runtime 用 compatibility adapter 产出：

```text
ProviderStreamStarted
ProviderContentDelta(full response content)   # 可选，便于统一 UI
ProviderStreamCompleted(response)
```

`complete()` 的默认实现不强制调用 `stream()`，避免打破已有同步 provider。后续可以增加 `CompleteFromStreamMixin`，由 provider 主动选择。

`ProviderStreamOptions`：

```python
@dataclass(frozen=True, slots=True)
class ProviderStreamOptions:
    thinking: bool = False
    show_thinking: bool = False
    max_thinking_chars: int | None = None
```

`thinking=True` 表示请求 provider 启用 reasoning/thinking 能力。`show_thinking=True` 表示把 thinking delta 暴露给 SDK/channel。两者分开是为了支持生产环境启用 reasoning 但不把 thinking 展示给用户。

## Complete 判定

### Provider Generation Complete

一次 provider generation complete 的条件：

- provider stream 正常结束，且 adapter 已经构造 `ProviderStreamCompleted`；
- 或 provider 明确发出 terminal event，例如 Anthropic `message_stop`；
- 或 OpenAI-compatible stream 出现最终 `finish_reason` 并且 iterator 结束；
- 或非流式 fallback 收到完整 `ProviderResponse`。

generation complete 时必须完成这些动作：

- 产出 `ProviderStreamCompleted(response=...)`。
- 聚合 content、thinking summary、tool calls、usage、model、provider_name、response_id。
- 结束当前 provider generation span。
- 如果 response 中有 tool calls，进入 tool execution，不结束 agent turn。

### Assistant Complete

assistant complete 的条件：

- 当前 provider generation complete；
- 聚合后的 `ProviderResponse.tool_calls` 为空；
- stop reason 不是 `length`、`max_tokens`、`content_filter` 这类不可作为最终回答的状态。

assistant complete 时：

- 把最终 assistant content 写入 `MessageRuntime`。
- 产出 `AssistantCompleted`。
- 结束 agent turn root span。

### Turn Complete

turn complete 的条件：

- assistant complete；
- 没有待执行 tool calls；
- 没有 pending provider stream；
- 没有错误或取消。

如果 provider generation complete 但带 tool calls：

```text
provider stream #1 completed
  -> append assistant tool_use message
  -> execute tool spans
  -> append tool results
  -> provider stream #2
```

这时只结束 provider span #1，不结束 `agent.turn` span。

## Thinking 处理

thinking 是 provider output 的独立通道，不是普通 assistant content。

规则：

- `ProviderThinkingDelta` 不追加到 `MessageRuntime` 的 assistant content。
- 默认 `show_thinking=False`，SDK 不向调用方暴露 thinking delta。
- 默认 `capture_thinking=False`，OTel/Langfuse 不记录 thinking 内容。
- thinking 的长度、chunk 数、是否存在可以作为 metadata 记录。
- 如果用户显式 `show_thinking=True`，SDK/channel 才产出 `AssistantThinkingDelta`。
- 如果用户显式 `capture_policy.capture_thinking=True` 且 capture mode 允许，OTel 才记录 thinking summary 或 delta。
- thinking 不能成为 assistant trajectory 的最后一个可见 block。若 provider 只返回 thinking 而没有 content/tool call，runtime 应把它视为 provider response 不可用，抛出明确错误或要求 provider adapter 转换成普通内容。

provider adapter 映射：

- Anthropic thinking block：映射为 `ProviderThinkingDelta`。
- OpenAI-compatible `reasoning_content` 或 DeepSeek reasoning delta：映射为 `ProviderThinkingDelta`。
- OpenAI `completion_tokens_details.reasoning_tokens`：只进入 usage，不产生 thinking content。
- 不支持 thinking 输出的 provider：忽略 `thinking=True` 或抛 `ProviderFeatureUnsupportedError`，由 provider adapter 明确声明。第一版建议兼容忽略，但在 `ProviderStreamStarted` metadata 中标记 `thinking_supported=False`。

## QueryLoop 流式状态机

新增：

```python
QueryLoop.run_turn_stream(user_message, options=None) -> Iterator[TurnStreamEvent]
```

现有：

```python
QueryLoop.run_turn(user_message) -> str
```

改为通过消费 `run_turn_stream()` 聚合最终 `AssistantCompleted`，保持兼容。

内部状态：

```text
append user
while True:
  maybe_compress
  build provider request
  consume provider stream
  if provider failed/cancelled -> rollback partial active refs, fail turn
  append assistant message with final content/tool_calls
  if no tool_calls -> complete turn
  execute tool calls
  append tool results
```

partial message 策略：

- streaming delta 到达时，不立刻写入 `MessageStore`。
- 只有 provider generation complete 后，才把聚合后的 assistant message 写入 `MessageRuntime`。
- 如果 stream 中途失败或取消，不把半截 assistant message 写入 active window。
- 可以通过 stream events 给 UI 展示 partial 内容；这是 presentation，不是 message truth source。

这个策略优先保证 session recovery 的一致性。后续如果需要崩溃后恢复半截输出，可以单独设计 `PartialMessageStore` 或 event log checkpoint。

## SSE / JSONL / Callback 适配

核心 runtime 只产出 typed events。新增 `channels/stream.py` 或 `runtime/stream_serializers.py` 提供纯函数：

```python
to_sse(event: TurnStreamEvent) -> str
to_jsonl(event: TurnStreamEvent) -> str
dispatch_callbacks(event, callbacks) -> None
```

SSE event 示例：

```text
event: content_delta
data: {"text":"项目名","index":1}

event: thinking_delta
data: {"text":"需要读取 pyproject.toml","index":1}

event: tool_started
data: {"tool_name":"read_file","tool_call_id":"call_1"}

event: done
data: {"turn_id":"turn_1"}
```

SSE 规则：

- SSE adapter 不直接调用 provider。
- SSE adapter 不写 MessageRuntime。
- SSE adapter 不写 OTel span。
- SSE adapter 只做序列化和可选 heartbeat。

## OTel Span 设计

### Span Tree

一次带工具调用的 streaming turn：

```text
agent.turn                         observation type: agent
  provider.stream                  observation type: generation
  tool.read_file                   observation type: tool
  provider.stream                  observation type: generation
```

`provider.stream` 与非流式 `provider.complete` 同属 generation observation。为了避免 Langfuse 上出现两个不同概念，第一版 span name 建议：

```text
provider.complete   # 非流式
provider.stream     # 流式
```

都设置：

```text
langfuse.observation.type = "generation"
gen_ai.operation.name = "chat"
gen_ai.request.stream = true|false
```

OpenTelemetry GenAI 语义约定推荐 inference span name 使用
`{gen_ai.operation.name} {gen_ai.request.model}`。agentos 第一版允许保留
`provider.stream` / `provider.complete` 作为框架内的逻辑 span name，原因是有些
provider adapter 只有在 response terminal event 后才能确认最终 model。实现必须
稳定写入 `gen_ai.request.model` 和 `gen_ai.response.model`；如果 adapter 在请求发出前
已经知道 model，则可以把实际 OTel span name 设为 `chat {model}`。

### Provider Stream Span 生命周期

provider stream span 开始：

- provider request build 完成后；
- 第一个 provider chunk 到达前；
- 写入 request metadata、input payload、model、provider name、`gen_ai.request.stream=true`。

provider stream span 过程中：

- 每个 content/thinking/tool delta 只更新内存聚合器；
- 默认不写 token 文本到 span event；
- 可选写低容量 span events：

```text
agentos.stream.content_delta
  sequence: int
  char_count: int

agentos.stream.thinking_delta
  sequence: int
  char_count: int
  content_captured: bool

agentos.stream.tool_call_delta
  sequence: int
  tool_call_id: str | null
  name_delta_chars: int
  arguments_delta_chars: int
```

full local capture 且 `capture_stream_deltas=True` 时，可以在 event attributes 中加入 `text` 或 `arguments_delta`，但必须经过 redactor 和 max length 限制。

provider stream span 结束：

- 收到 `ProviderStreamCompleted` 后；
- 写入 `langfuse.observation.output`；
- 写入 `gen_ai.response.finish_reasons`；
- 写入 `gen_ai.response.model`、`gen_ai.response.id`；
- 写入标准 `gen_ai.usage.*`、`gen_ai.response.time_to_first_chunk` 和 `langfuse.observation.usage_details`；
- 写入聚合统计：

```text
agentos.stream.content.delta_count
agentos.stream.content.char_count
agentos.stream.thinking.delta_count
agentos.stream.thinking.char_count
agentos.stream.tool_call.delta_count
agentos.provider.tool_call_count
```

如果 stream 失败：

- 设置 span status error；
- `error.type` 使用异常类名或 provider 错误码；
- 记录 `agentos.stream.partial=true`；
- 记录 partial char counts 和 hashes；
- 不写 `langfuse.observation.output` 为完整回答。

如果用户取消：

- 设置 `agentos.stream.cancelled=true`；
- span status 可设为 error 或 unset，并记录 `agentos.status=cancelled`。第一版建议 status unset + cancelled attribute，避免把用户主动取消计入 provider error rate。

### Agent Turn Span 生命周期

`agent.turn` span 从 `run_turn_stream()` 开始，到 `TurnStreamCompleted`、`TurnStreamFailed` 或 `TurnStreamCancelled` 结束。

root span input/output：

- input：用户输入 metadata 或 full input，由 capture policy 控制。
- output：最终 assistant content metadata 或 full output，由 capture policy 控制。
- 如果 turn 失败或取消，不写完整 output，只写 partial stats。

Langfuse trace-level attributes：

```text
langfuse.trace.name = "agentos.turn"
langfuse.trace.input
langfuse.trace.output
langfuse.user.id
langfuse.session.id
langfuse.trace.metadata.turn_id
langfuse.trace.metadata.capture_mode
```

按照 Langfuse OTel mapping，`langfuse.trace.metadata.*` 才是可过滤的一层 metadata；普通 OTel attributes 只会落入 catch-all metadata，不适合重要查询字段。

### Tool Span

工具执行仍由 `InstrumentedToolCallRouter` 创建 tool span。

补充标准 OTel GenAI tool attributes：

```text
gen_ai.operation.name = "execute_tool"
gen_ai.tool.name
gen_ai.tool.call.id
gen_ai.tool.type
```

tool arguments 和 result 是敏感字段。默认只写 length/hash。full/redacted 模式下才写：

```text
gen_ai.tool.call.arguments
gen_ai.tool.call.result
langfuse.observation.input
langfuse.observation.output
```

## Capture Policy 扩展

当前 `CapturePolicy` 有 metadata/redacted/full 三种模式。streaming/thinking 需要新增开关：

```python
capture_thinking: bool = False
capture_stream_deltas: bool = False
capture_stream_delta_text: bool = False
max_stream_delta_events: int = 200
```

建议默认：

```python
CapturePolicy.metadata_only():
  capture_thinking=False
  capture_stream_deltas=False
  capture_stream_delta_text=False

CapturePolicy.redacted():
  capture_thinking=False
  capture_stream_deltas=True
  capture_stream_delta_text=False

CapturePolicy.full_for_local_development():
  capture_thinking=True
  capture_stream_deltas=True
  capture_stream_delta_text=True
```

`show_thinking=True` 只影响 SDK/channel 是否展示 thinking。`capture_thinking=True` 只影响 observability 是否记录 thinking。两者不能互相隐式开启。

如果 `show_thinking=True` 但 `capture_thinking=False`，用户界面能看到 thinking，OTel 仍只记录 thinking length/count。

如果 `capture_thinking=True` 但 `show_thinking=False`，OTel 可记录 thinking，但 SDK 不向终端用户展示。这个组合只适合受控本地调试，不建议生产使用。

## Langfuse 适配

Langfuse 通过 OTel attributes 映射 trace 和 observation。agentos 手写 instrumentation 必须优先使用 `langfuse.*` keys：

generation span：

```text
langfuse.observation.type = "generation"
langfuse.observation.input = JSON string
langfuse.observation.output = JSON string
langfuse.observation.model.name = model
langfuse.observation.usage_details = JSON string
```

root span：

```text
langfuse.observation.type = "agent"
langfuse.trace.name = "agentos.turn"
langfuse.trace.input = JSON string
langfuse.trace.output = JSON string
```

metadata：

```text
langfuse.trace.metadata.turn_id
langfuse.trace.metadata.capture_mode
langfuse.observation.metadata.stream = true
langfuse.observation.metadata.thinking_requested = true|false
langfuse.observation.metadata.thinking_shown = true|false
```

注意：

- Langfuse 文档说明 `langfuse.*` attributes 优先于 generic OTel conventions。手动 instrumentation 必须设置这些字段，避免 input/output 在 UI 中显示为空。
- `langfuse.trace.input/output` 用于 trace 层；`langfuse.observation.input/output` 用于 span/generation 层。
- full payload 必须仍受 capture policy 控制，不能因为 Langfuse 支持 input/output 就默认上传 prompt。

## OpenTelemetry 语义约定适配

OTel GenAI semantic conventions 目前仍处于 Development。实现策略：

- 使用稳定和已明确的属性：

```text
gen_ai.operation.name
gen_ai.provider.name
gen_ai.conversation.id
gen_ai.request.model
gen_ai.request.stream
gen_ai.response.model
gen_ai.response.finish_reasons
gen_ai.response.id
gen_ai.usage.input_tokens
gen_ai.usage.output_tokens
gen_ai.usage.cache_read.input_tokens
gen_ai.usage.cache_creation.input_tokens
gen_ai.usage.reasoning.output_tokens
gen_ai.tool.name
gen_ai.tool.call.id
gen_ai.tool.type
```

- streaming chunks 不使用臆造的 `gen_ai.*` 字段。由于官方文档 streaming chunks 小节未定义稳定字段，第一版使用 `agentos.stream.*` span events 和 attributes。
- 敏感内容默认不写 `gen_ai.system_instructions`、`gen_ai.input.messages`、`gen_ai.output.messages`。官方文档明确提示模型指令、用户消息和输出通常敏感且体积大，不应默认捕获。
- OTel GenAI 当前没有 `gen_ai.usage.total_tokens` 标准字段。总 token 可写入 `langfuse.observation.usage_details`，必要时另写 `agentos.usage.total_tokens`。
- 如果未来 OTel 稳定定义 streaming chunk semantic conventions，再通过 `OTEL_SEMCONV_STABILITY_OPT_IN` 或 agentos config 增加兼容输出，不破坏已有 `agentos.stream.*`。

## Error、Cancel 与 Backpressure

错误：

- provider stream 抛异常时，产出 `ProviderStreamFailed` 和 `TurnStreamFailed`。
- 已展示给 UI 的 partial deltas 不写入 `MessageRuntime`。
- provider span status=error，root span status=error。
- `TurnFailedEvent` 仍通过 `EventBus` 发出，用于 persistence/debug。

取消：

- 用户取消时产出 `TurnStreamCancelled`。
- 如果 provider client 支持 abort，adapter 应调用 abort。
- 不把 partial assistant message 写入 active window。
- OTel 记录 cancelled stats，但不把它算作 provider error。

backpressure：

- 第一版同步 iterator 由 consumer 拉取事件，自然提供 backpressure。
- SSE adapter 不缓存无限事件。heartbeat 可选，但不属于 core runtime。
- 如果 callback 抛异常，stream 应取消并标记 turn failed/cancelled，避免后台继续调用 provider。

## 测试策略

Provider 层：

- fake streaming provider 逐个产出 content deltas，最后产出 completed response。
- OpenAI-compatible stream parser 能聚合 content、reasoning_content、tool_call delta、usage 和 finish reason。
- 不支持 stream 的 provider 走 complete fallback。

QueryLoop 层：

- `run_turn_stream()` 产出 started/content_delta/completed。
- `run_turn()` 消费 stream 后仍返回完整 answer。
- tool call streaming 后能执行 tool 并进入第二次 provider stream。
- stream 失败或取消不留下 active window 中的半截 assistant message。
- thinking delta 默认不进入 message store。

SSE/JSONL 层：

- typed events 能稳定序列化成 SSE。
- thinking hidden 时不输出 `thinking_delta`。
- final `done` event 只在 `TurnStreamCompleted` 后输出。

Observability 层：

- provider stream span 在 `ProviderStreamCompleted` 后才结束。
- provider stream span 包含 `gen_ai.request.stream=true`。
- content delta 默认只记录 count/length，不记录文本。
- full local capture 时可以记录 delta text，且受 max length 和 redactor 限制。
- thinking 默认不进入 OTel payload。
- stream failure span status 和 partial stats 正确。
- Langfuse input/output attributes 使用 `langfuse.observation.input/output`，不会出现 UI input/output undefined。

Architecture drift：

- `runtime/`、`providers/`、`capabilities/`、`context/` 不 import `opentelemetry` 或 `langfuse`。
- 默认 context golden 中仍无 `trace_id`、`span_id`、`session_id`、`thinking`、`stream` 等 runtime metadata。

## Completion Checklist

| Design requirement | Implementation files | Test files / verification | Status |
|---|---|---|---|
| SDK 每轮用参数控制 stream/SSE/thinking，不要求用户 new 多个对象。 | `runtime/agent.py`, `runtime/query_loop.py` | `tests/runtime/test_agent_stream_api.py` | required |
| 核心 streaming 使用 typed events。 | `providers/stream.py`, `runtime/stream_events.py` | `tests/providers/test_streaming.py`, `tests/runtime/test_streaming_query_loop.py` | required |
| `run()` 保持完整响应兼容，`stream()` 提供 typed events。 | `runtime/agent.py`, `runtime/query_loop.py` | `tests/runtime/test_streaming_query_loop.py` | required |
| SSE 是 adapter，不污染 core runtime。 | `channels/stream.py` 或 `runtime/stream_serializers.py` | `tests/channels/test_sse_stream.py` | required |
| thinking 与 content 分离，默认不展示、不持久化、不观测内容。 | `providers/stream.py`, `messages/types.py`, `observability/config.py` | `tests/providers/test_thinking_stream.py`, `tests/messages/test_runtime.py` | required |
| provider stream span 在 terminal event 后写 output/usage 并结束。 | `observability/instrumented.py`, `observability/tracer.py` | `tests/observability/test_streaming_provider_span.py` | required |
| OTel 使用标准 GenAI 属性，不臆造 streaming `gen_ai.*` chunk 字段。 | `observability/conventions.py`, `observability/instrumented.py` | `tests/observability/test_streaming_conventions.py` | required |
| Langfuse input/output 不再 undefined。 | `observability/conventions.py`, `observability/instrumented.py` | `tests/observability/test_langfuse_streaming_mapping.py` | required |
| stream 失败/取消不污染 active window。 | `runtime/query_loop.py`, `messages/window.py` | `tests/runtime/test_streaming_failure.py` | required |
| 默认 prompt 不暴露 runtime metadata。 | `context/renderer.py` | `tests/context/test_renderer.py`, golden tests | required |

## 实施顺序建议

1. 先落 typed stream event dataclasses 和 fake streaming provider。
2. 再让 `QueryLoop.run_turn_stream()` 跑通无工具的 content streaming。
3. 把 `run_turn()` 改成消费 stream 的兼容 API。
4. 加 tool call streaming 聚合和多 provider round loop。
5. 加 thinking delta 和 capture policy。
6. 加 SSE/JSONL/callback adapter。
7. 最后改 OTel instrumentation，让 provider stream span 包住 generator 并在 terminal event 后结束。

这个顺序能让每一步都有可验证行为，也避免先写 OTel 适配时没有稳定的 stream terminal event 可依赖。
