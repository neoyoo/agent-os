# AgentBuilder 设计

## Scope

本设计为 agent-os SDK 新增 `AgentBuilder` fluent API，将 Agent 创建从 7+ 组件手动拼装降到 3 行代码。

这是 SDK 可用性的最高优先级改进——AgentBuilder 不只是开发者便利工具，更是 AI skill 系统创建 agent 的标准入口。Skill 写 `AgentBuilder().provider(p).build()` 比写 7 行组件拼装的成功率高一个数量级。

本设计不改变 Agent、QueryLoop 或任何已有组件的实现。

v1 只覆盖已经稳定的装配边界：

- provider
- registered tools
- compression runtime / compressor 注入
- context/message/renderer/router/event/compression 等组件 override

v1 明确不提供 `system_prompt()`、`with_memory()`、`with_observability()`、`with_hooks()` 或 `hook_manager()` 便捷方法。`system_prompt()` 容易绕过 context-first 的 `ContextRenderer` 投影模型；memory hydration / recall runtime 自动装配、observability preset 和 HookManager runtime 接入还没有稳定入口，不能先暴露半成品 API。

## 问题

当前创建一个最基本的 Agent 需要：

```python
messages = MessageRuntime()
context = ContextRuntime()
renderer = ContextRenderer()
request_builder = ProviderRequestBuilder(
    context_renderer=renderer,
    message_runtime=messages,
    tools=[],
)
provider = AnthropicProvider(api_key="...")
agent = Agent(query_loop_kwargs={
    "context_runtime": context,
    "message_runtime": messages,
    "request_builder": request_builder,
    "provider": provider,
})
```

问题：

- 用户要知道 MessageRuntime、ContextRuntime、ContextRenderer、ProviderRequestBuilder 之间的依赖关系
- AI assistant 通过 skill 创建 agent 时，组件越多出错概率越大
- 无法通过 API 表达 "我只想要一个能用的 agent"

## 设计

### 新增文件

`src/agentos/builder.py`

### AgentBuilder API

```python
class AgentBuilder:
    """Fluent builder，将 Agent 组件拼装降到一行。"""

    def provider(self, provider: Provider) -> AgentBuilder: ...
    def tools(self, tools: list[RegisteredTool]) -> AgentBuilder: ...

    # 可选组件 override
    def message_runtime(self, runtime: MessageRuntime) -> AgentBuilder: ...
    def context_runtime(self, runtime: ContextRuntimeBoundary) -> AgentBuilder: ...
    def context_renderer(self, renderer: ContextRenderer) -> AgentBuilder: ...
    def compression_runtime(self, runtime: CompressionRuntime) -> AgentBuilder: ...
    def event_bus(self, bus: EventBus) -> AgentBuilder: ...
    def tool_call_router(self, router: ToolCallRouterBoundary) -> AgentBuilder: ...

    # 便捷预设（内部自动创建所需组件）
    def with_compression(self, compressor: Compressor | None = None) -> AgentBuilder: ...

    def build(self) -> Agent: ...
```

### build() 内部逻辑

```
1. 校验 provider 已设置（否则 raise ValueError）
2. message_runtime ← 用户传入 or 新建 MessageRuntime()
3. context_runtime ← 用户传入 or 新建 ContextRuntime()
4. context_renderer ← 用户传入 or 新建 ContextRenderer()
5. tools ← 用户传入 or 空列表
6. request_builder ← 新建 ProviderRequestBuilder(context_renderer, message_runtime, tools)
7. 组装 query_loop_kwargs dict
8. 如果调用了 with_compression() → 创建 CompressionRuntime 并注入
9. 如果传入 compression_runtime override → 直接使用 override，且不能再调用 with_compression()
10. 如果有 event_bus → 注入
11. 返回 Agent(query_loop_kwargs=kwargs)
```

### 使用示例

**最简（3 行）**：

```python
from agentos import AgentBuilder, AnthropicProvider

agent = AgentBuilder().provider(AnthropicProvider(api_key="sk-...")).build()
result = agent.run("你好")
```

**带工具**：

```python
agent = (
    AgentBuilder()
    .provider(AnthropicProvider(api_key="sk-..."))
    .tools([search_tool, code_tool])
    .build()
)
```

**带压缩**：

```python
agent = (
    AgentBuilder()
    .provider(provider)
    .tools(tools)
    .with_compression()
    .build()
)
```

**全配置**：

```python
agent = (
    AgentBuilder()
    .provider(provider)
    .tools(tools)
    .message_runtime(custom_messages)
    .context_runtime(custom_context)
    .with_compression(LlmCompressor(provider))
    .build()
)
```

### Deferred API

以下入口不进入 v1：

```python
def system_prompt(self, prompt: str) -> AgentBuilder: ...
def with_memory(self, hot_store, durable_store, recall_index) -> AgentBuilder: ...
def with_observability(self, tracer) -> AgentBuilder: ...
def hook_manager(self, manager: HookManager) -> AgentBuilder: ...
def with_hooks(self, manager: HookManager) -> AgentBuilder: ...
```

`system_prompt()` 如果直接覆盖 provider `system` 字符串，会破坏 context-first 架构。后续若需要可配置身份或 guardrails，应通过 `RuntimeContract`、`CapabilityPlane` 或自定义 `ContextRenderer` 进入，而不是把静态 prompt 拼接进 builder。

`with_memory()` 需要先稳定 session hydration、recall runtime、memory sink 和 persistence snapshot 的统一装配路径。否则 builder 会暴露以后必然 breaking 的半成品 API。

`with_observability()` 需要等待 tracing / metrics / eval 的公共配置面稳定后再加入。

`hook_manager()` / `with_hooks()` 需要等待 HookManager 接入 `QueryLoop` 和 `ToolCallRouter` 后再加入。v1 不能暴露“可调用但不生效”的假能力。

### 错误设计

错误消息面向 AI 可执行性——告诉调用者下一步该做什么：

| 场景 | 错误消息 |
|------|---------|
| 没设 provider | `AgentBuilder requires .provider() before .build(). Pass a Provider instance, e.g. AnthropicProvider(api_key="...")` |
| 重复设置同一组件 | `AgentBuilder.provider() called twice. Remove one call.` |
| compression runtime 与 with_compression 同时设置 | `AgentBuilder cannot use both .compression_runtime() and .with_compression(). Choose one compression setup.` |

### 与 Skill 系统集成

`create-agent-skill` 和 `neoagent` skill 应将 AgentBuilder 作为唯一的 agent 创建入口：

```
Skill 输出的代码模板：
  AgentBuilder()
    .provider(...)
    .tools([...])
    .build()

而不是：
  手动拼装 MessageRuntime + ContextRuntime + ...
```

### 公共 API

从 `agentos` 顶层导出 `AgentBuilder`。

## 测试计划

- `AgentBuilder().provider(FakeProvider(...)).build()` 返回可运行 Agent
- `.tools()` 注入的工具在 agent.run 中可用
- `.with_compression()` 创建带压缩的 agent
- `.compression_runtime()` override 与 `.with_compression()` 互斥
- 无 provider 调 build() 抛 ValueError
- 每个 override 方法能替换默认组件
- build() 返回的 Agent 类型和手动构建的完全一致
- 多次调用 build() 返回独立 Agent 实例

## 验收标准

- 最简创建路径：`AgentBuilder().provider(p).build()` 可运行 turn
- v1 override 都可工作
- v1 不提供 `system_prompt()`、`with_memory()`、`with_observability()`、`hook_manager()`、`with_hooks()` 便捷方法
- build() 返回标准 Agent，无包装层
- 从 `import agentos` 可直接导入 AgentBuilder
- 无新外部依赖
