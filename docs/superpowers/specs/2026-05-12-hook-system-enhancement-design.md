# Hook 系统增强设计

## Scope

扩展现有 Hook 系统：增加 7 个新 hook 点、hook 优先级、async hook 支持。

保持向后兼容——现有 4 个 hook 和 HookHandler protocol 不变。

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

## 新 Hook 点设计

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

- **触发时机**：`Agent.close()` 显式调用，或 GC 时的 `__del__`
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

## Async Hook 支持

### AsyncHookHandler Protocol

```python
class AsyncHookHandler(Protocol):
    async def __call__(self, context: HookContext) -> HookResult | None: ...
```

### HookManager 检测与执行

```python
def dispatch(self, hook_name, payload):
    for registration in sorted_registrations:
        handler = registration.handler
        if asyncio.iscoroutinefunction(handler):
            # 在同步 QueryLoop 下用 asyncio.run 包装
            result = self._run_async_handler(handler, context)
        else:
            result = handler(context)
```

`_run_async_handler` 逻辑：

```python
def _run_async_handler(self, handler, context):
    try:
        loop = asyncio.get_running_loop()
        # 已在 async 环境：创建 task 并同步等待
        future = asyncio.ensure_future(handler(context))
        return asyncio.get_event_loop().run_until_complete(future)
    except RuntimeError:
        # 不在 async 环境：直接 asyncio.run
        return asyncio.run(handler(context))
```

### 向后兼容

- `HookHandler`（同步）protocol 不变
- 现有注册的同步 handler 正常运行，无感知
- HookRegistration 接受 `HookHandler | AsyncHookHandler`

## 集成点（需改动的文件）

| 文件 | 改动 |
|------|------|
| `hooks/base.py` | 扩展 HookName、新增 AsyncHookHandler、HookRegistration 加 priority |
| `hooks/registry.py` | register() 接受 priority |
| `hooks/manager.py` | dispatch() 加排序、async 检测；新增 `on()` decorator |
| `runtime/query_loop.py` | 在 build_request 前后加 context render hook 调用 |
| `messages/runtime.py` | append_* 方法前加 before_message_append hook 调用 |
| `compression/runtime.py` | maybe_compress() 中加 compression start/complete hook 调用 |
| `runtime/agent.py` | run/stream 首次调用时触发 on_session_start；新增 close() 触发 on_session_end |

## 测试计划

- 新 hook 注册和触发
- priority 排序：priority=50 先于 priority=100
- 同 priority 保持注册顺序
- before_context_render 可修改 context_state
- before_message_append deny 阻止消息写入
- on_session_start 只触发一次
- on_compression_start 可修改 selected_message_ids
- async handler 在同步 loop 下正常执行
- 现有 4 个 hook 的行为不变
- decorator 注册方式可用

## 验收标准

- HookName 扩展到 11 个
- 所有新 hook 在对应代码路径触发
- priority 排序正确
- async handler 支持
- 现有 hook 和 HookHandler protocol 向后兼容
- 无新外部依赖
