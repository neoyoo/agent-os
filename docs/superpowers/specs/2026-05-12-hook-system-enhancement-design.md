# Hook 系统增强设计

## Scope

扩展现有 Hook 系统，但按阶段落地，避免把没有稳定调用点的 hook 名先暴露成公共 API。

保持向后兼容——现有 4 个 hook 和 HookHandler protocol 不变。

v1 只完成：

- 现有 4 个 hook 点端到端接入 runtime/tool 路径：
  - `before_provider_call`
  - `after_provider_call`
  - `before_tool_call`
  - `after_tool_call`
- hook priority
- decorator 注册 API

v1 不做：

- 新增 7 个 hook 点
- async hook handler
- `on_session_end` 的 `__del__` 兜底

新 hook 点必须等对应代码路径和修改语义稳定后逐个加入。async hook 必须等 `AsyncQueryLoop` 存在后，通过 async runtime 原生 await，不在同步 loop 中用 `run_until_complete()` 包装。

## 问题

当前 Hook 系统（`hooks/base.py`）：

```python
HookName = Literal[
    "before_provider_call",
    "after_provider_call",
    "before_tool_call",
    "after_tool_call",
]
```

缺失的关键拦截点：

- **Context render**：无法在 system prompt 渲染前修改 ContextState，或渲染后修改最终 prompt
- **Message append**：无法过滤或改写即将写入 MessageStore 的消息
- **Session lifecycle**：无法在 session 开始/结束时做初始化/清理
- **Compression**：无法控制哪些消息被压缩，或修改压缩结果

其他问题：

- 无优先级——执行顺序是注册顺序，多个 hook 时无法控制先后
- 纯同步——如果 hook 需要调外部 API（webhook、日志服务），会阻塞 agent loop

## 后续 Hook 点设计（Deferred）

以下 hook 点是后续候选，不进入 v1 验收。原因是每个 hook 都需要明确调用点、payload schema、modify/deny 语义和测试，否则会出现“声明了 hook 名但从未触发”的占位 API。

### 扩展 HookName

```python
HookName = Literal[
    # 现有（不变）
    "before_provider_call",
    "after_provider_call",
    "before_tool_call",
    "after_tool_call",
    # 新增
    "before_context_render",
    "after_context_render",
    "before_message_append",
    "on_session_start",
    "on_session_end",
    "on_compression_start",
    "on_compression_complete",
]
```

### 每个新 Hook 的定义

#### `before_context_render`

- **触发时机**：`ProviderRequestBuilder.build()` 调用 `ContextRenderer.render()` 之前
- **Payload**：`{"context_state": ContextState}`
- **可修改**：`context_state`（可注入 runtime notice、修改 working state）
- **用途**：动态注入 system prompt 段落、根据 turn 状态调整 context

#### `after_context_render`

- **触发时机**：`ContextRenderer.render()` 返回后
- **Payload**：`{"system_prompt": str}`
- **可修改**：`system_prompt`（可追加/替换最终 system prompt 字符串）
- **用途**：追加 guardrail 指令、日志记录完整 prompt

#### `before_message_append`

- **触发时机**：`MessageRuntime.append_user()` / `append_assistant()` / `append_tool_result()` 调用前
- **Payload**：`{"role": str, "content": str, "tool_calls": list, "tool_call_id": str | None}`
- **可修改**：`content`（可过滤敏感信息、改写内容）
- **deny 效果**：消息不写入 MessageStore（跳过本条）
- **用途**：PII 过滤、内容审计、消息拦截

#### `on_session_start`

- **触发时机**：`Agent.run()` 或 `Agent.stream()` 首次调用时（通过 `SessionState.is_first_turn` 判断）
- **Payload**：`{"session_id": str}`
- **不可修改**（观察型 hook，deny 无效）
- **用途**：初始化日志、加载用户偏好、预热缓存

#### `on_session_end`

- **触发时机**：`Agent.close()` 显式调用，或 session provider 显式释放/关闭 session
- **Payload**：`{"session_id": str, "turn_count": int}`
- **不可修改**（观察型 hook）
- **用途**：持久化 session 状态、清理资源、发送统计

#### `on_compression_start`

- **触发时机**：`CompressionRuntime.maybe_compress()` 选出候选消息后、调用 Compressor 之前
- **Payload**：`{"selected_message_ids": list[str], "message_count": int}`
- **可修改**：`selected_message_ids`（可添加/移除候选消息）
- **用途**：保护关键消息不被压缩、强制压缩特定消息

#### `on_compression_complete`

- **触发时机**：Compressor 返回 CompressedSegment 后、写入 context 之前
- **Payload**：`{"segment_id": str, "topic": str, "summary": str}`
- **可修改**：`topic`, `summary`（可增强或修正压缩结果）
- **用途**：后处理摘要、追加元数据

## 优先级机制

### HookRegistration 扩展

```python
@dataclass(frozen=True, slots=True)
class HookRegistration:
    name: HookName
    handler: HookHandler
    failure_policy: HookFailurePolicy = "continue"
    priority: int = 100  # 新增，数字小优先级高
```

### 执行顺序

`HookManager.dispatch()` 按 `priority` 升序排列后执行（数字小先执行）。同 priority 保持注册顺序。

```python
def dispatch(self, hook_name, payload):
    registrations = sorted(
        self.registry.hooks_for(hook_name),
        key=lambda r: r.priority,
    )
    # ... 其余逻辑不变
```

### Decorator 注册

```python
@hook_manager.on("before_tool_call", priority=50)
def audit_tool_call(context: HookContext) -> HookResult | None:
    log_tool_call(context.payload)
    return None
```

`HookManager.on()` 方法：

```python
def on(self, hook_name: HookName, *, priority: int = 100):
    def decorator(handler):
        self.registry.register(HookRegistration(
            name=hook_name,
            handler=handler,
            priority=priority,
        ))
        return handler
    return decorator
```

## Async Hook 支持（Deferred）

async hook 不进入同步 `QueryLoop` 阶段。

不能采用以下方案：

```python
loop = asyncio.get_running_loop()
future = asyncio.ensure_future(handler(context))
return asyncio.get_event_loop().run_until_complete(future)
```

如果当前线程已经有 running event loop（例如 ASGI handler），`run_until_complete()` 会抛出 `RuntimeError("This event loop is already running")`。在同步 loop 里强行等待 async handler 也会把 hook 的 IO 延迟引入主 turn 热路径。

后续 async hook 的正确方向是：

- 保留同步 `HookManager.dispatch()` 只执行同步 handler。
- 在 `AsyncQueryLoop` 阶段新增 `HookManager.dispatch_async()`。
- `dispatch_async()` 原生 `await` async handler，并可兼容同步 handler。
- ASGI/channel 层只调用 async agent API，不在同步 hook manager 中桥接 event loop。

### AsyncHookHandler Protocol（后续）

```python
class AsyncHookHandler(Protocol):
    async def __call__(self, context: HookContext) -> HookResult | None: ...
```

### 向后兼容

- `HookHandler`（同步）protocol 不变
- 现有注册的同步 handler 正常运行，无感知
- 后续 `HookRegistration` 可以接受 `HookHandler | AsyncHookHandler`，但同步 dispatch 遇到 async handler 应明确拒绝或要求使用 `dispatch_async()`

## 集成点（需改动的文件）

| 文件 | v1 改动 | 后续改动 |
|------|---------|----------|
| `hooks/base.py` | HookRegistration 加 priority 所需类型 | 扩展 HookName、新增 AsyncHookHandler |
| `hooks/registry.py` | register() 接受 priority | 无 |
| `hooks/manager.py` | dispatch() 加 priority 排序；新增 `on()` decorator | 新增 `dispatch_async()` |
| `runtime/query_loop.py` | 接入 `before_provider_call` / `after_provider_call` | context render hook 另开 spec |
| `capabilities/router.py` 或 `runtime/query_loop.py` | 接入 `before_tool_call` / `after_tool_call` | async tool hook 另开 spec |
| `messages/runtime.py` | 不改 | `before_message_append` 另开 spec |
| `compression/runtime.py` | 不改 | compression hook 另开 spec |
| `runtime/agent.py` | 不改 | session lifecycle hook 另开 spec，且只支持显式 close/session provider 生命周期，不依赖 `__del__` |

## 测试计划

- 现有 4 个 hook 注册和触发
- priority 排序：priority=50 先于 priority=100
- 同 priority 保持注册顺序
- before_provider_call deny 阻止 provider 调用并返回明确错误
- before_provider_call modify 可修改 provider request payload（如果 v1 选择支持 modify）
- after_provider_call 可观察 provider response
- before_tool_call deny 阻止工具执行并写入明确 tool error result
- after_tool_call 可观察工具执行结果
- 现有 4 个 hook 的行为不变
- decorator 注册方式可用

## 验收标准

- 现有 4 个 HookName 不变且全部有真实调用点
- priority 排序正确
- decorator 注册 API 可用
- async handler 明确 deferred；同步 dispatch 不使用 `run_until_complete()` 桥接
- 新增 7 个 hook 点明确 deferred，不能作为 v1 公共 API 暴露
- 现有 hook 和 HookHandler protocol 向后兼容
- 无新外部依赖
