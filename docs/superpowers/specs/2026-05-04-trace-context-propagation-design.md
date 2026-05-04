---
name: agentos trace context propagation design
description: Phase 6 observability 增量设计。补齐 OTel trace context 生命周期、跨服务传播、Langfuse metadata 聚合字段，以及旧 event-to-trace 代码清理策略。
type: design-spec
status: draft
date: 2026-05-04
relates_to:
  - docs/design/sdk-architecture.md
  - docs/design/llm-context-only-example.md
  - docs/superpowers/specs/2026-05-04-production-observability-design.md
  - ../ai-knowledge/wiki/evaluation-observability.md
  - ../ai-knowledge/wiki/_patterns/otel-eval-bridge.md
  - ../ai-knowledge/wiki/channel-remote.md
  - ../ai-knowledge/wiki/multi-agent.md
---

# Trace Context Propagation Design

## 背景

当前 production observability 已经把 `QueryLoop`、provider、tool router、compression 包装成 OTel spans，并通过 OTLP 发送到 Langfuse。它解决了“能看到每个边界做了什么”的问题，但还没有完整解决“一个请求跨多个服务、远程 API 或未来 subagent 时如何用同一个 trace id 串起来”的问题。

用户期望的模型接近 Java 分布式链路追踪：

```text
request headers + ThreadLocal
```

在 agentos 的 Python + OpenTelemetry 方案里，对应关系应该是：

```text
W3C traceparent/tracestate headers + OTel Context/contextvars
```

OpenTelemetry 官方 Python 文档把 propagation 定义为跨服务和跨进程移动 context 的机制，并使用 W3C Trace Context HTTP headers。OpenTelemetry Propagators 规范要求 W3C Trace Context propagator 解析和传播 `traceparent`、`tracestate`，其中携带 TraceID、SpanID、TraceFlags 和 TraceState。OpenTelemetry Trace API 也明确 `SpanContext` 包含 TraceId 与 SpanId，并且它是需要序列化和传播的部分。

Langfuse 的 OTel 文档另有一个重要要求：如果要可靠按 `userId`、`sessionId`、`metadata`、`tags` 等过滤和聚合，这些 trace-level attributes 需要传播到 trace 内每个 span，而不是只写 root span。

参考文档：

- OpenTelemetry Python propagation: https://opentelemetry.io/docs/languages/python/propagation/
- OpenTelemetry Propagators API: https://opentelemetry.io/docs/specs/otel/context/api-propagators/
- OpenTelemetry Trace API: https://opentelemetry.io/docs/specs/otel/trace/api/
- Langfuse OpenTelemetry integration: https://langfuse.com/integrations/native/opentelemetry

## 目标

本 spec 是 Phase 6 Observability 的增量 spec。它只补 trace context propagation 地基，不实现 multi-agent / subagent 功能本身。

必须完成：

- OTel trace id 作为观测链路 id 的唯一权威来源。
- 支持从 incoming headers 提取 W3C trace context。
- 支持把当前 trace context 注入 outgoing headers。
- 在 root span 和子 span 上稳定写入必要观测 attributes：`trace_id`、`session_id`、`turn_id`、`user_id`。`span_id` 保留为 OTel 原生 span identity，默认不复制成普通 attribute。
- metadata capture mode 的 Langfuse Input/Output 降噪，优先展示可关联身份，而不是 sha256 大段摘要。
- 明确废弃代码清理：保留 EventLog/debug projection，移除或降级不再承担生产 trace 的 EventTraceProjector/TraceRecord/OTelAdapter/LangfuseAdapter 路径。
- 保持默认 LLM-visible context 完全不出现 runtime metadata。

明确不做：

- 不实现 subagent 模块、remote agent API、agent registry 或 task delegation。
- 不实现 HTTP server/channel。
- 不实现自动 instrument 第三方 HTTP client。
- 不把 `trace_id`、`span_id`、`session_id`、`turn_id`、`user_id` 渲染进默认 prompt。
- 不用 Langfuse Python SDK 替代 OTel。

## 核心决策

### 1. Trace Id 不由 runtime 生成

`runtime` 不应该生成或维护观测层 `trace_id`。它只负责 agent 语义身份：

```text
runtime:
  session_id
  turn_id
  message_id
  tool_call_id

observability:
  trace_id
  span_id
  traceparent/tracestate extract/inject

application:
  user_id
  request_id，如果业务自己有
```

`trace_id` 来自 OTel span context：

```text
有 incoming traceparent:
  extract incoming context
  agent.turn span 继承上游 trace_id

没有 incoming traceparent:
  agent.turn span 创建新的 OTel trace_id
```

实现层不能用 `uuid4()` 生成真实 OTel trace id。`uuid4()` 只允许出现在 `InMemoryTracer` 测试实现里，用来模拟 trace/span ids。

### 2. Python contextvars 对应 Java ThreadLocal

Java 分布式链路里常见的 `ThreadLocal` 在 Python SDK 里不应直接使用。agentos 需要用两层 context：

- OTel 当前 Context：由 OTel SDK 管理 active span 与 trace context。
- agentos `ObservabilityContext`：由 `contextvars` 保存业务关联字段，如 `user_id`、incoming headers 和 extra metadata。

这样同步代码、async 代码和后续同进程 subagent 都能沿当前 context 继承观测身份。

### 3. Headers 传播只传播观测上下文，不传播 prompt

跨服务调用只注入标准 tracing headers：

```text
traceparent
tracestate
```

可选地通过 OTel Baggage 传播非敏感业务标识，但第一版不默认使用 baggage 跨服务传播 `user_id`、`session_id`。原因是 Langfuse 明确提醒 baggage 会跨服务和第三方 API 传播，不能放敏感信息。agentos 第一版只把 `user_id/session_id/turn_id` 写入本进程 spans；如果需要跨进程业务身份，由应用层显式传自己的业务 headers 或请求 body。

第一版必须提供 trace context inject/extract；baggage 自动传播作为后续增强，不作为本轮验收。

### 4. Runtime Id 与 OTel metadata 必须分层

agentos runtime 可以并且应该维护完整运行时身份：

```text
session_id
turn_id
message_id
tool_call_id
schema_id
projection_id
compression_id
```

这些 id 服务 persistence、resume、event log、debug projection 和内部关联。但它们不应该无脑写进每个 OTel span。Observability 只暴露排障和聚合必要字段。

默认每个 span 写：

Langfuse OTel 要可靠按用户和 session 聚合，需要每个 span 都带 trace-level attributes。agentos instrumentation 必须为 root 和所有子 span 写：

```text
langfuse.user.id              # 如果 user_id 存在
user.id                       # 如果 user_id 存在
langfuse.session.id           # 如果 session_id 存在
session.id                    # 如果 session_id 存在
agentos.session.id            # 如果 session_id 存在
agentos.turn.id               # 如果 turn_id 存在
agentos.trace.id              # OTel current trace id
langfuse.trace.metadata.turn_id
langfuse.trace.metadata.capture_mode
```

不默认写：

```text
agentos.user.id               # 重复；user_id 是应用层身份，不是 agentos runtime id
agentos.span.id               # OTel/Langfuse 原生已有 span id，人工排障价值低
message_id                    # 只在 message append/debug event 中需要
tool_call_id                  # 只写 tool/provider tool-call 相关 span
schema_id/projection_id       # 只在 debug projection 中需要
compression_id                # 只写 compression span
```

`agentos.trace.id` 是 OTel trace id 的 attribute 副本，用于本地日志、属性面板搜索和跨系统查询。如果它与 Langfuse UI 里的 trace id 不一致，说明实现有 bug。`span_id` 默认不复制为 attribute；需要时可以从 OTel/Langfuse 原生 span identity 查看。

### 5. Metadata capture mode 降噪

当前 metadata input/output 包含 sha256，用户在 Langfuse UI 里看到大量不可读字段。hash 对机器有价值，对人排查一般无价值。

调整后：

- `langfuse.trace.input/output` 和 `langfuse.observation.input/output` 在 metadata 模式下只放可读摘要和关联身份。
- sha256、length、count 仍作为 span attributes 存在，供机器查询和差异判断。
- full/redacted 模式继续按 capture policy 展示内容。

metadata 模式 root input 示例：

```json
{
  "capture_mode": "metadata",
  "content_hidden": true,
  "session_id": "small_openai_agent",
  "turn_id": "turn_1",
  "user_id": "local_user",
  "user_message_chars": 28
}
```

metadata 模式 provider input 示例：

```json
{
  "capture_mode": "metadata",
  "content_hidden": true,
  "session_id": "small_openai_agent",
  "turn_id": "turn_1",
  "user_id": "local_user",
  "system_chars": 3200,
  "message_count": 3,
  "tool_count": 6
}
```

metadata 模式 provider output 示例：

```json
{
  "capture_mode": "metadata",
  "content_hidden": true,
  "session_id": "small_openai_agent",
  "turn_id": "turn_1",
  "user_id": "local_user",
  "content_chars": 128,
  "tool_call_count": 1,
  "stop_reason": "tool_calls"
}
```

## Public API

### ObservabilityContext

新增 `agentos.observability.context` 模块。

```python
from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True, slots=True)
class ObservabilityContext:
    """当前调用链上的观测上下文，不进入 LLM prompt。"""

    user_id: str | None = None
    incoming_headers: Mapping[str, str] | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)
```

`metadata` 只能包含低敏、低基数字段，例如 `request_id`、`channel`、`tenant_tier`。它不能包含 prompt、tool payload、API key、access token、文件内容或用户私密文本。

### Context helpers

```python
def current_observability_context() -> ObservabilityContext:
    """返回当前 contextvars 中的 ObservabilityContext。"""


def use_observability_context(
    context: ObservabilityContext | None = None,
    *,
    user_id: str | None = None,
    incoming_headers: Mapping[str, str] | None = None,
    metadata: Mapping[str, object] | None = None,
) -> ContextManager[ObservabilityContext]:
    """在当前作用域设置观测上下文。"""
```

使用示例：

```python
with use_observability_context(
    user_id="u_123",
    incoming_headers=request.headers,
    metadata={"channel": "web"},
):
    loop.run_turn(user_message)
```

### Trace propagation helpers

```python
def inject_trace_headers(
    headers: MutableMapping[str, str],
    tracer: TraceContextPropagator | None = None,
) -> MutableMapping[str, str]:
    """把当前 OTel trace context 注入 outgoing headers。"""


def current_trace_ids(
    tracer: TraceContextPropagator | None = None,
) -> TraceIds:
    """读取当前 active span 的 trace_id/span_id。"""
```

`inject_trace_headers(...)` 是给工具、远程 API、未来 remote subagent 复用的最小 API。

## Tracer Protocol 扩展

当前 `Tracer` 只有 `start_span(...)`。需要扩展为：

```python
@dataclass(frozen=True, slots=True)
class TraceIds:
    trace_id: str | None
    span_id: str | None
    is_remote: bool = False


class TraceContextPropagator(Protocol):
    def use_incoming_headers(
        self,
        headers: Mapping[str, str] | None,
    ) -> ContextManager[None]:
        """提取 incoming headers，并在作用域内设为当前 OTel context。"""

    def inject_headers(self, headers: MutableMapping[str, str]) -> None:
        """把当前 OTel context 写入 outgoing headers。"""

    def current_trace_ids(self) -> TraceIds:
        """读取当前 active span 的 trace/span ids。"""
```

`Tracer` 可以继承或组合这个 protocol：

```python
class Tracer(TraceContextPropagator, Protocol):
    def start_span(...): ...
```

`NoOpTracer` 必须实现这些方法但不产生副作用。没有安装 OTel 时，基础 SDK 仍能 import 和运行。

## OTel 实现

`_OTelTracer` 负责把 agentos protocol 映射到 OpenTelemetry：

- `start_span(...)` 继续使用 `tracer.start_as_current_span(...)`，保证进程内 current context 生效。
- `use_incoming_headers(headers)` 使用 OTel propagator extract，再 attach 到当前 context；退出作用域时 detach。
- `inject_headers(headers)` 使用 OTel propagator inject，把当前 context 写入 carrier。
- `current_trace_ids()` 使用 `trace.get_current_span().get_span_context()`，返回 32 hex trace id 和 16 hex span id；无有效 span 时返回 `None`。

实现必须只在 `observability/otel.py` 内 import OpenTelemetry。`runtime/`、`providers/`、`capabilities/`、`context/` 不得 import OTel、Langfuse 或 observability。

## InMemoryTracer 实现

`InMemoryTracer` 需要模拟真实 trace 行为，测试不依赖 OTel：

- root span 没有 incoming trace 时生成一个 fake 32 hex trace id。
- 子 span 继承当前 trace id，生成新 span id。
- `parent_span_id` 保持当前行为。
- `use_incoming_headers({"traceparent": "00-<trace>-<span>-01"})` 提取 trace id 和 remote parent span id。
- `inject_headers(headers)` 写入合法格式 `traceparent`。
- `current_trace_ids()` 返回当前 span 的 ids。

`InMemoryTracer` 不需要实现完整 `tracestate` 验证；它只用于单元测试 trace propagation 语义。

## Instrumentation 数据流

### QueryLoop root span

`InstrumentedQueryLoop.run_turn(...)` 的顺序必须是：

```text
1. 读取 current_observability_context()
2. 用 tracer.use_incoming_headers(context.incoming_headers) 包住 root span 创建
3. 根据 session_state.next_turn_number() 预计算 turn_id
4. start_span("agent.turn", root attributes)
5. 进入 span 后读取 current_trace_ids()
6. 写入 trace_id/session_id/turn_id/user_id metadata
7. 设置 langfuse.trace.input / langfuse.observation.input
8. 调用 inner.run_turn(user_message)
9. 设置 langfuse.trace.output / langfuse.observation.output
10. 退出 span
```

注意：`inner.run_turn(...)` 会真正调用 `session_state.new_turn(...)`，所以 wrapper 只能预计算 turn id，不能提前递增 turn counter。

### 子 span 公共 metadata

Provider request build、provider complete、tool 和 compression wrappers 都必须在 span 进入后调用同一个 helper：

```python
apply_common_observability_attributes(span, tracer, context, session_id, turn_id)
```

公共 helper 写入：

- `agentos.trace.id`
- `langfuse.user.id`、`user.id`，如果 user_id 存在
- `langfuse.session.id`、`session.id`、`agentos.session.id`，如果 session_id 存在
- `agentos.turn.id`，如果 turn_id 存在
- Langfuse trace-level attributes
- `langfuse.trace.metadata.*` 中允许的低敏 metadata

`session_id` 和 `turn_id` 来源优先级：

1. 当前 root wrapper 显式写入的 per-turn context。
2. `inner.session_state` 和预计算 turn id。
3. 空值。

为了让 provider/tool 子 span 拿到 `turn_id`，需要一个 `CurrentTurnContext` 或在 `ObservabilityContext` 作用域中临时叠加 `session_id/turn_id`。推荐增加内部 contextvars：

```python
@dataclass(frozen=True, slots=True)
class RuntimeTraceContext:
    session_id: str | None = None
    turn_id: str | None = None
```

它只在 observability 包内部使用，不暴露给 LLM，不改变 `QueryLoop`。

## Cross-Service API 调用

工具或远程能力调用其他服务时，应用代码可以这样写：

```python
headers: dict[str, str] = {}
inject_trace_headers(headers)
client.post(url, headers=headers, json=payload)
```

这只保证 trace context 传播。业务身份传播由调用方自己决定：

```python
headers["x-agentos-session-id"] = session_id
headers["x-agentos-user-id"] = user_id
```

agentos 第一版不自动把 user/session headers 注入第三方 API，避免把用户身份泄露给不可信下游。

## Future Subagent Readiness

本 spec 不实现 subagent，但必须让未来 subagent 不需要重做 trace 方案。

同进程 subagent：

```text
父 agent 当前 OTel context
  -> subagent.run span
    -> 子 QueryLoop spans
```

trace id 不变，span id 分叉。

远程 subagent：

```text
父 agent 调用 remote subagent API
  -> inject_trace_headers(headers)
  -> remote channel 收到 headers
  -> use_observability_context(incoming_headers=headers)
  -> remote QueryLoop 创建 child trace
```

这部分只要求 API 能支持，不在本轮实现 `subagent.run`。

## Metadata And Capture Policy

### metadata 模式

metadata 模式继续是默认安全模式，但 UI 展示内容降噪：

- Input/Output：展示 `capture_mode`、`content_hidden`、`session_id`、`turn_id`、`user_id` 和少量 count/length。
- Attributes：保留 sha256、count、length、usage、model、stop reason。

### redacted/full 模式

- `redacted`：Input/Output 展示脱敏内容，公共 metadata 仍写 attributes。
- `full`：Input/Output 展示完整内容，公共 metadata 仍写 attributes。

三种模式都不能把 metadata 写进默认 prompt。

## 废弃代码审查与清理

当前 observability 有两条 trace 路径：

1. 新生产路径：instrumentation wrappers -> OTel spans -> OTLP/Langfuse。
2. 旧投影路径：EventLog -> EventTraceProjector -> TraceRecord -> OTelAdapter/LangfuseAdapter。

清理原则：

- `EventLog` 保留。它服务 session recovery、debug projection 和 append-only runtime facts。
- `context/debug_projection.py` 保留。它是显式 debug/ops API，不进入默认 prompt。
- `EventTraceProjector` 不再作为生产 observability 路径。它把 event log 转成伪 trace，无法正确表达 parent/child span、trace context propagation、provider payload capture policy。
- `TraceRecord`、`TraceSink`、`OTelAdapter`、`LangfuseAdapter` 不再保留为 public API。本项目尚未发布稳定 1.0 API，本轮直接删除旧 production-looking trace projection API，不做兼容 shim，不改名保留。

本轮删除：

```text
src/agentos/observability/traces.py
src/agentos/observability.langfuse.LangfuseAdapter
src/agentos/observability.otel.OTelAdapter
tests/observability/test_traces.py 中 adapter/projector 相关测试
tests/architecture/test_public_api.py 中旧 public names
```

本轮保留：

```text
src/agentos/observability/events.py
src/agentos/context/debug_projection.py
tests/observability/test_event_log.py
tests/context/test_debug_projection.py
```

如果实现时发现 persistence serializers 或 debug projection 仍依赖 `TraceRecord`，不得做兼容 shim。应把调用改回 EventLog/EventRecord，因为 trace projection 已不再是事实源。

## Testing Requirements

### Unit Tests

新增：

- `tests/observability/test_context.py`
  - `use_observability_context(...)` 在作用域内设置 user/incoming metadata，退出后恢复。
  - 嵌套 context 正确恢复外层值。

- `tests/observability/test_trace_propagation.py`
  - `InMemoryTracer` root span 创建 trace id。
  - nested spans 继承 trace id，span id 不同。
  - `inject_headers(...)` 写出 `traceparent`。
  - `use_incoming_headers(...)` 让 root span 继承 incoming trace id。
  - `current_trace_ids(...)` 在 span 内返回 ids，span 外返回空 ids。

更新：

- `tests/observability/test_query_loop_instrumentation.py`
  - root span 有 `agentos.trace.id`、`agentos.session.id`、`agentos.turn.id`。
  - root span 默认没有 `agentos.span.id`。
  - 当 `ObservabilityContext(user_id="u1")` 存在时，root 和所有子 span 都有 `langfuse.user.id=user_id`。
  - root 和所有子 span 默认没有 `agentos.user.id`。
  - 所有子 span 都有相同 `agentos.trace.id`、`agentos.session.id`、`agentos.turn.id`。
  - incoming traceparent 被继承。

- `tests/observability/test_instrumented_provider.py`
  - metadata mode Input 不再展示 sha256。
  - sha256 仍保留在 attributes。

- `tests/examples/test_small_openai_agent.py`
  - small agent 通过 env `AGENTOS_USER_ID` 注入 `ObservabilityContext.user_id`。
  - 本轮不增加新的 user id CLI 参数，避免 demo CLI 参数膨胀；正式 channel/http 入口在后续 channel spec 中设计。

- `tests/architecture/test_public_api.py`
  - 新 public API 导出。
  - 旧 `TraceRecord/EventTraceProjector/TraceSink/OTelAdapter/LangfuseAdapter` public API 断言删除。

### Optional OTel Tests

`tests/observability/test_otel_propagation.py` 可在 `observability` extra 下运行：

- `_OTelTracer.inject_headers(...)` 使用 OTel propagator 写 `traceparent`。
- `_OTelTracer.use_incoming_headers(...)` 接受 W3C traceparent。
- `_OTelTracer.current_trace_ids()` 返回 32/16 hex ids。

如果不想让普通 dev tests 安装 OTel，这组测试必须能在缺少 OTel 时 skip，而不是 fail。

### Drift Checks

实现完成必须跑：

```bash
uv run --python 3.11 --extra dev pytest -q
uv run --python 3.11 --extra dev python -m compileall -q src tests scripts
git diff --check
rg -n "agent[O]s|agent[_]os" src tests docs pyproject.toml AGENTS.md .gitignore
rg -n "from opentelemetry|import opentelemetry|langfuse" src/agentos/runtime src/agentos/providers src/agentos/capabilities src/agentos/context
rg -n "session_id|turn_id|message_id|trace_id|span_id|tool_call_id|schema_id|projection_id|compression_id|source|relevance" tests/context/goldens src/agentos/context/renderer.py
```

最后一条只允许命中明确断言 forbidden metadata 不出现在默认 prompt 的测试。

## Acceptance Checklist

| Requirement | Implementation files | Test files | Status |
|---|---|---|---|
| Trace id 来源为 OTel current span context，不由 runtime 生成。 | `observability/otel.py`, `observability/tracer.py`, `observability/instrumented.py` | `tests/observability/test_trace_propagation.py`, `tests/observability/test_query_loop_instrumentation.py` | required |
| 支持 incoming headers extract，继承上游 trace id。 | `observability/otel.py`, `observability/tracer.py`, `observability/context.py` | `tests/observability/test_trace_propagation.py`, `tests/observability/test_otel_propagation.py` | required |
| 支持 outgoing headers inject，供 tool/remote API/subagent 复用。 | `observability/context.py`, `observability/otel.py`, `observability/tracer.py` | `tests/observability/test_trace_propagation.py` | required |
| root 和所有子 span 都写必要 user/session/turn metadata，且默认不写 `agentos.user.id` / `agentos.span.id`。 | `observability/instrumented.py`, `observability/conventions.py` | `tests/observability/test_query_loop_instrumentation.py` | required |
| Langfuse filter 字段按官方约定写入每个 span。 | `observability/conventions.py`, `observability/instrumented.py` | `tests/observability/test_query_loop_instrumentation.py` | required |
| metadata mode Input/Output 降噪，sha256 保留在 attributes。 | `observability/instrumented.py`, `observability/snapshots.py` | `tests/observability/test_instrumented_provider.py`, `tests/observability/test_query_loop_instrumentation.py` | required |
| 保留 EventLog/debug projection，删除或降级旧 event-to-trace production 路径。 | `observability/traces.py`, `observability/langfuse.py`, `observability/otel.py`, `observability/__init__.py` | `tests/observability/test_traces.py`, `tests/architecture/test_public_api.py` | required |
| 默认 LLM-visible context 不出现 runtime metadata。 | `context/renderer.py` 不因本改动变化 | `tests/context/test_renderer.py`, drift search | required |
| 未安装 OTel 时 core SDK 仍可 import。 | `observability/__init__.py`, `observability/otel.py` | `tests/observability/test_otel_config.py`, full test suite | required |

全部 required 项完成并通过验证后，这个 trace propagation 增量才算完成。
