---
name: agentos v3 sdk architecture
description: agentos v3 重写版 SDK 工程骨架。以 context protocol 作为认知模型，以 ai-knowledge 概念模块作为 SDK 运行时结构。
type: architecture
status: inbox
date: 2026-05-03
relates_to:
  - ideas/2026-05-02-neoagent-context-protocol-v3.md
  - ideas/2026-05-03-neoagent-llm-context-only-example.md
  - wiki/_index.md
  - docs/superpowers/specs/2026-04-14-neoagent-sdk-skill-design.md
---

# agentos v3 SDK Architecture

## 1. 顶层原则

agentos v3 的重写不以旧 neoagent 包结构为架构约束，而以两个上层设计作为边界：

```text
Context protocol 决定 agent 的认知模型。
ai-knowledge 模块体系决定 SDK 的工程骨架。
旧 neoagent 代码只作为局部实现参考，不作为架构约束。
```

这意味着：

- LLM 每轮看到什么、怎么维护 working state、怎么压缩和召回，由 context protocol 决定。
- SDK 有哪些运行时模块、模块之间怎么协作，由 ai-knowledge 的概念地图决定。
- 旧代码中可复用的 provider、tool、MCP、配置、测试经验可以搬，但旧 prompt / working memory / compression 抽象不能直接搬。

---

## 2. ai-knowledge → agentos v3 模块映射

| ai-knowledge 概念 | v3 模块 | 责任 |
|---|---|---|
| `query-loop` | `runtime/query_loop.py` | Agent 主循环和 turn 调度 |
| `runtime-state` | `runtime/session.py`, `runtime/turn.py` | session、turn、运行状态生命周期 |
| `context-management` | `context/`, `messages/`, `compression/`, `recall/` | 上下文投影、窗口、压缩、恢复 |
| `prompt-system` | `context/renderer.py`, `context/projection.py` | 渲染 LLM 可见 context，不做旧式 PromptBuilder 拼接 |
| `tool-system` | `capabilities/tools.py`, `capabilities/executor.py` | 工具注册、权限、执行、结果回写 |
| `memory-system` | `memory/` | 跨 session memory 的提取、存储、召回 |
| `mcp-skills` | `capabilities/mcp.py`, `capabilities/skills.py` | MCP 连接和 skill 加载 |
| `multi-agent` | `multi/` | subagent 派发、隔离、结果回收 |
| `agent-registry-discovery` | `registry/`, `multi/registry.py`, `channels/` | AgentCard、能力发现、远程 agent 寻址 |
| `hooks` | `events/`, `hooks/` | 观察型事件和可拦截 hook 扩展点 |
| `session-recovery` | `persistence/`, `runtime/session.py` | 断点恢复和持久化 |
| `evaluation-observability` | `observability/`, `eval/` | 事件日志、trace、Langfuse/OTel、评估 |
| `finetuning-system` | `finetuning/`, `eval/`, `observability/` | 训练数据导出、prompt 优化、微调评估管道 |
| `channel-remote` | `channels/` | CLI / HTTP / 服务化入口 |
| `sandbox-isolation` | `policies/security.py`, `capabilities/executor.py` | 工具权限和执行隔离 |

`capabilities/skills.py` 的内容来源边界是 async `SkillContentSource`。
`SkillRegistry.aload(...)` 只在启动阶段加载 metadata，`load_skill` 和
`load_skill_resource` 作为 async tool handler 执行，供 Redis、HTTP 或
filesystem-backed source 在不阻塞 async query loop 的情况下按需加载正文和资源。

---

## 3. 目标包结构

```text
agentos/
  runtime/
    agent.py
    query_loop.py
    provider_request_builder.py
    session.py
    turn.py

  hooks/
    base.py
    registry.py
    manager.py

  events/
    bus.py
    types.py

  context/
    state.py
    schema.py
    renderer.py
    projection.py
    runtime.py
    chapter.py

  messages/
    types.py
    store.py
    window.py
    runtime.py

  compression/
    evictor.py
    compressor.py
    index.py
    runtime.py

  recall/
    runtime.py

  capabilities/
    registry.py
    tools.py
    executor.py
    router.py
    context_tools.py
    skills.py
    mcp.py

  providers/
    base.py
    openai.py
    anthropic.py
    stream.py

  memory/
    extractor.py
    retriever.py
    store.py
    runtime.py

  observability/
    events.py
    traces.py
    langfuse.py
    otel.py

  persistence/
    base.py
    memory.py
    sqlite.py
    filesystem.py

  policies/
    budget.py
    security.py
    tool_policy.py

  multi/
    orchestrator.py
    worker.py
    result.py
    registry.py

  registry/
    agent_card.py
    discovery.py
    in_memory.py
    resolver.py

  channels/
    base.py
    cli.py
    http.py

  eval/
    runner.py
    cases.py
    metrics.py

  finetuning/
    dataset.py
    exporter.py
    prompt_optimizer.py
    runner.py
```

---

## 4. 命名规则

命名必须表达对象职责，而不是只表达它“属于 runtime”。旧 `neoagent` 的清晰命名可以作为风格参考，但不能把旧 prompt / working memory / compression 架构带回 v3。

公共命名规则：

- Python import 包名统一为 `agentos`，遵循 PEP 8 lowercase package naming。
- 对外示例、测试和文档中的 import 都使用 `agentos`。
- 类和函数维护中文 docstring；协议标识符、provider/tool/schema 名称保留英文。
- `Runtime` 只用于长期持有子系统状态并协调该领域生命周期的对象，例如 `ContextRuntime`、`MessageRuntime`、`CompressionRuntime`、`RecallRuntime`。

职责后缀规则：

| 后缀 | 语义 | 示例 |
|---|---|---|
| `Loop` | agent 主循环和 turn 状态机 | `QueryLoop` |
| `Builder` | 组装值，不持有生命周期状态 | `ProviderRequestBuilder` |
| `Provider` | 模型后端边界 | `Provider`, `OpenAICompatibleProvider` |
| `Registry` | 名称到对象/schema 的注册表 | `ToolRegistry` |
| `Executor` | 执行具体副作用 | `ToolExecutor` |
| `Router` | 分发请求到正确执行路径 | `ToolCallRouter` |
| `Manager` | 管理可拦截策略或协调规则 | `HookManager` |
| `Bus` | 观察型 pub/sub，不改变执行 | `EventBus` |
| `Event` | 已发生事实的类型化事件 | `ProviderRequestBuiltEvent` |

当前标准命名：

| 职责 | 标准名 | 原因 |
|---|---|---|
| query/turn 执行循环 | `QueryLoop` | 对齐 ai-knowledge 的 `query-loop`。 |
| provider request 组装 | `ProviderRequestBuilder` | 明确只构建 provider request。 |
| 模型后端协议 | `Provider` | provider 是模型后端边界，不拥有 SDK runtime。 |
| provider tool call 分发 | `ToolCallRouter` | 把 tool call 分发到 context tool 或外部工具。 |
| hook 策略协调 | `HookManager` | hook 是可拦截策略管理，不是运行时主循环。 |
| runtime 生命周期事实 | typed `*Event` dataclass | 基础事件必须可发现、可订阅、可测试。 |

Event 与 Hook 必须分开：

- `EventBus` 只发布观察型 typed events，handler 不改变执行结果。
- `HookManager` 管 pre/post hook，可返回 allow / deny / modify。
- trace / observability 不要塞进 hook；需要看完整 LLM 上下文时，优先在 provider 边界或 typed event subscriber 上实现。

---

## 5. 核心数据流

```text
User input
  ↓
MessageRuntime.append_user()
  ↓
QueryLoop
  ↓
ContextRuntime.prepare_for_request()
  ├─ apply pending context state
  ├─ maybe compress active messages
  ├─ maybe inject recalled context
  └─ render LLM-visible context
  ↓
ProviderRequestBuilder.build()
  ├─ system = ContextRuntime.render()
  ├─ messages = MessageRuntime.materialize_active()
  └─ tools = ToolRegistry.provider_tool_specs()
  ↓
Provider.complete()
  ↓
MessageRuntime.append_assistant()
  ↓
ToolCallRouter.execute(tool_calls)
  ├─ ContextToolExecutor → ContextRuntime
  ├─ ToolExecutor → files/db/shell/http
  └─ Skill/MCP executors
  ↓
MessageRuntime.append_tool_results()
  ↓
loop until assistant final response
```

`QueryLoop` 只做调度，不直接拼 prompt、不直接改 context、不直接执行具体工具。

---

## 6. Context Runtime

Context Runtime 是 agent 的认知模型实现。

### 6.1 责任

- 保存 `ContextState`。
- 管理 `WorkingStateSchema` 和 `WorkingState`。
- 执行 context protocol tools。
- 渲染 LLM 可见上下文。
- 管理 compressed history、inherited state、memory context 的 projection。
- 默认不向 prompt 暴露 runtime metadata。

### 6.2 关键对象

```text
ContextState
WorkingStateSchema
WorkingState
WorkingStateField
InheritedState
CompressedSegment
MemoryContext
ContextProjection
```

### 6.3 默认可见 context sections

与 `agentos LLM 可见上下文范文` 对齐：

```text
Runtime Contract
Capability Plane
Context Management Rules
Declared Working State Schema
Working State
Compressed History
Memory Context
```

跨 chapter 场景可以在 `Working State` 和 `Compressed History` 之间额外渲染 `Inherited State`。无 inherited state 时不渲染该段，保持默认七段结构。

### 6.4 Context tools

```text
declare_schema
update_state
extend_schema
start_chapter
recall_context
load_image
```

统一使用 `recall_context` 命名。它召回的是压缩文本/历史对应的原始消息片段，不是某个固定 turn；旧设计笔记中的 `recall_turn` 应视为被取代的旧称。召回内容由 `ToolCallRouter` 格式化为标准 tool result，不作为 system prompt 或临时 user/assistant message 注入。

图片附件使用独立的 `load_image(handle="att:...")` 工具重新加载。`AttachmentRuntime` 当前只面向 image 投影；同一 turn 内，`load_image` 后图片在后续 provider requests 中持续可见，turn 结束自动清空。

`read_state`、`abort_chapter`、`mark_important` 不作为默认 LLM 可见工具，可作为 debug/ops 能力后续添加。

### 6.5 不做什么

- 不存储 provider 原始 messages。
- 不执行外部工具。
- 不直接管理 session 持久化。
- 不把 `session_id`、`trace_id`、`message_id`、`compression_id` 等 runtime metadata 渲染进默认 prompt。

---

## 7. Message Runtime

Message Runtime 是 messages 真值源和 active window 管理器。

### 7.1 责任

- append-only 保存原始 messages。
- 维护 `ActiveWindow`。
- 保护 `tool_use` / `tool_result` 配对。
- 给 provider request materialize active messages。

### 7.2 关键对象

```text
Message
MessageRef
MessageStore
ActiveWindow
TemporaryMessage
```

### 7.3 压缩边界

压缩只从 `ActiveWindow` 移除 message refs，不删除 `MessageStore` 原文。

---

## 8. Compression + Recall

Compression 和 Recall 是 Message Runtime 与 Context Runtime 的桥。

### 8.1 压缩流程

```text
BudgetPolicy detects overflow
  ↓
Evictor selects contiguous message refs
  ↓
Compressor reads original messages from MessageStore
  ↓
CompressedSegment is created
  ↓
CompressionIndex maps seg handle to source message refs
  ↓
ActiveWindow removes selected refs
  ↓
ContextState.M3 appends segment
```

### 8.2 Recall 流程

```text
LLM calls recall_context(handle="seg_1")
  ↓
RecallRuntime looks up CompressionIndex
  ↓
MessageStore returns source messages
  ↓
ToolCallRouter formats messages into a <recalled-context> tool result
  ↓
MessageRuntime appends the tool result to the normal message sequence
```

### 8.3 Compressor 类型

```text
RuleBasedCompressor      # 测试、fallback、确定性摘要
LLMCompressor            # 真实摘要
```

---

## 9. Capability Plane + Tool Routing

Capability Plane 统一声明 tools、skills、MCP；`ToolCallRouter` 负责把 provider tool calls 路由到 context tools、外部 tools、skills 或 MCP。

### 9.1 责任

- 注册工具。
- 暴露 provider tool schemas。
- 为 ContextRenderer 提供 capability registry 的 LLM 可见摘要投影；默认 prompt 只展示工具分组、MCP server 摘要和 skill frontmatter/when-to-use，不展示完整 input schema。
- 通过 `ToolCallRouter` 路由 tool calls。
- 通过 `ToolExecutor` 执行外部工具。
- 应用 tool policy 和 security policy。
- 将 tool results 写回 Message Runtime。
- 将 context tool calls 路由到 Context Runtime。

### 9.2 工具分类

```text
Context tools      # declare_schema / update_state / extend_schema / start_chapter / recall_context / load_image
Builtin tools      # read_file / edit_file / run_shell / ask_user 等
MCP tools          # 来自 MCP server
Skill tool         # 加载 skill 指令
Subagent tool      # 派发子 agent
```

### 9.3 Skills

Skills 属于 capability plane，不属于 context projection。

参考 Claude Code 的方向：

- system 中只列 skill 摘要。
- 通过 `Skill` tool 加载具体 skill。
- 加载后的 skill 内容作为 meta message 注入。

---

## 10. Provider Boundary

`Provider` 是模型后端边界，不知道 context 内部细节。

### 10.1 输入

```text
ProviderRequest
  system: rendered context
  messages: active messages
  tools: provider tool schemas
```

### 10.2 责任

- 适配 OpenAI / Anthropic 等 provider。
- 处理 tool call schema。
- 处理 streaming。
- 返回标准化 `ProviderResponse`。
- 上报 usage 给 Observability。

---

## 11. Observability

Observability 是 runtime metadata 的归宿，不污染 prompt。

### 11.1 记录内容

```text
session_id
turn_id
message_id
trace_id
span_id
tool_call_id
schema_id
projection_id
compression_id
recall events
budget events
provider usage
tool execution events
```

### 11.2 输出

```text
Internal event log
Langfuse adapter
OTel adapter
Debug projection
Eval traces
```

---

## 12. Persistence

Persistence 给 runtime 提供可替换存储。

### 12.1 第一批实现

```text
MemoryPersistence
SQLitePersistence
FileSystemPersistence
```

### 12.2 存储对象

```text
sessions
turns
messages
context state
compressed segments
compression indexes
memory facts
observability events
```

---

## 13. Multi-Agent

Multi-Agent 是独立能力，不共享主 agent 的 context state。

### 13.1 原则

- 每个 subagent 有独立 `ContextRuntime`、`MessageRuntime`、`ToolCallRouter`。
- 主 agent 只看到 subagent 的 tool result。
- 主 agent 想吸收 subagent 发现，必须显式 `update_state`。
- Subagent 权限不能超过父 agent。

### 13.2 不做什么

- 不允许 subagent 直接读写主 agent working state。
- 不默认共享 active messages。
- 不默认继承全部工具。

---

## 14. Agent Registry + Finetuning Extensions

这两个模块来自 ai-knowledge 的完整 L1 概念覆盖，但不进入早期上下文主链。

### 14.1 Agent Registry + Discovery

Agent Registry 让 agent 成为可寻址、可发现、可按能力匹配的实体。

早期实现保持轻量：

```text
AgentCard          # name / description / capabilities / version / endpoint
AgentRegistry      # register / unregister / resolve / list
InMemoryRegistry   # 单进程测试与本地开发
```

后续远程部署再扩展：

```text
StaticResolver     # 文件或 well-known URL
ServiceResolver    # k8s / Nacos / 自建 registry
A2AAdapter         # 跨进程 agent 调用协议适配
```

Registry 与 `multi/` 的边界：

- `registry/` 负责声明和发现 agent。
- `multi/` 负责调度 subagent、权限降级和结果回收。
- `channels/` 负责把远程 agent 暴露到 CLI / HTTP / 服务入口。

### 14.2 Finetuning System

Finetuning System 不改变 runtime loop 的第一阶段行为，它消费 eval、trace、tool trajectory 和人工标注数据，用于后期优化模型或 prompt。

早期只预留导出边界：

```text
TrainingExampleExporter
TrajectoryDataset
PromptOptimizationRun
FinetuningRun
```

规则：

- 训练数据导出只能读取 observability / eval / persistence 中的记录，不反向污染默认 prompt。
- prompt optimization 属于 `finetuning/` 与 `eval/` 的协作，不替代 `context/renderer.py` 的默认渲染规则。
- 微调模型选择和训练执行是后期扩展，不进入 Phase 1-7 的主链。

---

## 15. 旧 neoagent 代码复用规则

### 15.1 可以参考或搬运

- Provider API 调用细节。
- Tool calling schema 适配经验。
- MCP client 生命周期管理。
- Skill discovery 经验。
- 配置加载。
- 事件与观察者实现经验。
- 测试 fixtures。

### 15.2 不要搬运

- 旧 8 层 prompt renderer。
- 旧 working memory 字段模型。
- 旧 PromptBuilder 拼接抽象。
- 旧 compressed history schema。
- 旧 memory context 注入方式。
- 任何把 runtime metadata 直接渲染进 prompt 的逻辑。

---

## 16. 实施阶段

### 实现期验证风险

以下问题不再通过继续扩写设计文档解决，而是在对应 phase 用测试和最小实现验证：

| 风险点 | 验证时机 | 验收方式 |
|---|---|---|
| Compressor 不能切断 `tool_use` / `tool_result` 配对。 | Phase 2 写 `Evictor` 时 | 用 message window 测试覆盖配对保护，确认压缩只移除完整可压缩区间。 |
| M2 字段顺序必须稳定。 | Phase 1 写 renderer golden tests 时 | 默认 renderer 按 declared schema 字段顺序渲染，不按字母或 runtime metadata 重排。 |
| Recall 注入的临时消息不能破坏 provider message 序列约束。 | Phase 2 引入 recall runtime，Phase 3 接真实 provider 前 | 用 fake provider 和 provider adapter 测试覆盖临时消息插入与下一次 request 自动移除。 |
| Subagent 的 context 初始化策略要显式。 | Phase 7 做 multi-agent 时 | 明确是 fork 父配置还是独立初始化；默认不共享主 agent active messages 或 working state。 |
| Schema 模板库的分发路径要和 skill 延迟加载兼容。 | Phase 5 做 skills 时 | 将 schema template 作为内置 skill/cookbook 能力测试，不提前进入默认 prompt。 |
| 行为反馈 hint 通道不能污染默认 runtime metadata 边界。 | 后期增量 | 若需要反馈给 LLM，优先设计为显式 M3 投影或独立 debug/ops 通道，并增加 golden tests。 |

### ai-knowledge 覆盖表

| ai-knowledge 概念 | 落地 phase | 说明 |
|---|---:|---|
| `prompt-system` | Phase 1 | `ContextRenderer` 渲染默认 LLM-visible context。 |
| `query-loop` | Phase 1 | `QueryLoop` 形成最小 turn 调度。 |
| `runtime-state` | Phase 3 | session、turn、event bus 和生命周期状态独立出来。 |
| `context-management` | Phase 1-2 | Phase 1 做 context/messages 主链，Phase 2 做 compression/recall。 |
| `tool-system` | Phase 4 | Tool registry、executor、external tool result routing。 |
| `memory-system` | Phase 7 | Memory extractor/retriever/store 与 M3 memory projection。 |
| `multi-agent` | Phase 7 | Subagent 隔离调度。 |
| `agent-registry-discovery` | Phase 7 | AgentCard、registry、resolver 与 multi-agent/channels 协作。 |
| `finetuning-system` | Phase 8 | 训练数据导出、prompt optimization、finetuning/eval extensions。 |
| `hooks` | Phase 3 | typed `EventBus` 和 `HookManager` 先于 provider/tool loop 建立。 |
| `mcp-skills` | Phase 5 | Skills、MCP registry、schema template skill。 |
| `channel-remote` | Phase 8 | CLI / HTTP / 远程入口。 |
| `session-recovery` | Phase 6 | Persistence 与 session restore。 |
| `evaluation-observability` | Phase 6, Phase 8 | Phase 6 做 runtime events/traces，Phase 8 做 eval/finetuning 联动。 |
| `sandbox-isolation` | Phase 4 | SecurityPolicy、ToolPolicy 和 executor 隔离。 |

### Phase 1: Context + Messages 主链

- ContextState
- ContextRenderer
- Context tools
- MessageStore
- ActiveWindow
- ProviderRequestBuilder
- FakeProvider

验收：

- 能渲染 context-only 范文结构。
- 能通过 tools 更新 working state。
- 能生成 provider request。
- 默认 prompt 不暴露 runtime metadata。

### Phase 2: Compression + Recall

- BudgetPolicy
- Evictor
- RuleBasedCompressor
- CompressionIndex
- recall_context

验收：

- 旧 messages 从 active window 移除。
- 原文仍在 MessageStore。
- prompt 出现 `seg_1`。
- `recall_context("seg_1")` 能临时恢复原文。

### Phase 3: Runtime State + Hooks Foundation

- SessionState
- TurnState
- EventBus
- HookRegistry
- HookManager

验收：

- session / turn 生命周期不靠散落字段维护。
- runtime loop 关键节点能发出类型化事件。
- hook 默认只读；失败策略和执行顺序明确。
- 默认 prompt 不暴露 hook/runtime metadata。

### Phase 4: Providers + Tools

- OpenAI provider
- Anthropic provider
- ToolRegistry
- ToolExecutor
- SecurityPolicy

验收：

- 能完成真实 provider tool-call loop。
- 工具结果进入 MessageRuntime。
- context tools 和 external tools 路由清晰。

### Phase 5: Skills + MCP

- Skill registry
- Skill tool
- MCP registry
- MCP tool adapter
- Schema template skill

验收：

- system 只列 skill/MCP 摘要。
- 通过 tool 加载 skill。
- MCP tools 进入 capability plane。
- schema template 作为内置 skill/cookbook 能力分发，不提前进入默认 prompt。

### Phase 6: Persistence + Session Recovery + Observability

- SQLite/File persistence
- Event log
- Langfuse adapter
- OTel adapter
- debug projection

验收：

- session 可恢复。
- message/context/compression/recall 事件可追踪。
- 默认 prompt 仍不暴露 runtime metadata。

### Phase 7: Memory + Multi-Agent + Agent Registry

- Memory extractor/retriever/store
- subagent orchestration
- AgentCard
- AgentRegistry
- AgentResolver

验收：

- memory context 可召回并渲染。
- subagent 隔离运行。
- 主 agent 只通过 tool result 吸收 subagent 发现。
- agent registry 支持本地注册、按名称解析和按能力枚举。

### Phase 8: Channels + Remote + Finetuning/Eval Extensions

- CLI/HTTP channels
- remote channel adapter
- eval runner
- training example exporter
- prompt optimization runner

验收：

- agent 可通过不同 channel 暴露。
- eval cases 能复用 runtime traces。
- finetuning/export 只消费观测与评估数据，不反向修改默认 context protocol。

---

## 17. 一句话架构图

```text
AgentOs
  -> QueryLoop
      -> ContextRuntime      # agent cognition
      -> MessageRuntime      # truth source + active window
      -> ToolCallRouter      # context tools / external tools / skills / MCP
      -> Provider            # model backend
      -> Observability       # runtime metadata, not prompt
      -> Persistence         # recovery and storage
```
