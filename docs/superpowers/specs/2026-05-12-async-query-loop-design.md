# AsyncQueryLoop 设计规范

**日期**：2026-05-12
**状态**：已定稿，待实现
**关联文件**：`src/agentos/runtime/query_loop.py`, `src/agentos/channels/asgi.py`

---

## 1. 问题描述

`QueryLoop` 是 agentos 的 agent turn 调度器，当前实现完全同步：

```python
def run_turn_stream(self, user_message: str, ...) -> Iterator[TurnStreamEvent]:
    ...
    response = yield from self._run_provider_loop_stream(turn, run_options)
```

`AsgiAgentApp._handle_sse_turn()` 在 ASGI 调用内用以下方式消费同步 stream：

```python
for chunk in stream:
    await asyncio.sleep(0)   # 假装让出控制权
    await send({"type": "http.response.body", "body": chunk.encode()})
```

`asyncio.sleep(0)` 只在两次 chunk 之间让出事件循环一次，但 provider HTTP 调用（`urllib` 或 `httpx` 同步模式）在此期间完全阻塞事件循环。在 uvicorn 下：

- 单个 provider 调用（通常 2-30 秒）期间，同一进程内所有其他 session 的请求均无法处理
- 多并发 session 互相阻塞，P99 延迟随并发数线性劣化
- `disconnect_task` 轮询靠 `asyncio.sleep(0)` 驱动，provider 调用期间客户端断开后无法及时响应

根本原因：阻塞 IO 操作（provider HTTP 调用、tool 执行中可能包含的 IO）占用了 event loop thread。

---

## 2. 现有同步 loop 控制流分析

### 2.1 核心调用链

```
Agent.stream()
  └─ QueryLoop.run_turn_stream()          # Iterator[TurnStreamEvent]
       ├─ _run_provider_loop_stream()     # while True loop
       │    ├─ build_request()            # 纯 CPU，可复用
       │    ├─ _consume_provider_stream() # 阻塞 IO: provider HTTP
       │    │    └─ provider.stream() / provider.complete()
       │    └─ tool_call_router.execute_tool_call()  # 可能含阻塞 IO
       └─ yield TurnStreamCompleted
```

### 2.2 阻塞点识别

| 位置 | 阻塞类型 | 持续时间 |
|------|---------|---------|
| `provider.complete()` | 网络 IO（HTTP） | 2-30 秒 |
| `provider.stream()` | 网络 IO（HTTP streaming） | 持续 token 期间 |
| `tool_call_router.execute_tool_call()` | 视工具而定，可能含网络/磁盘 IO | 0.1-10 秒 |
| `compression_runtime.maybe_compress()` | 纯 CPU，LLM embedding 可能含网络 | 通常 <100ms |

### 2.3 非 IO 可复用逻辑

以下逻辑纯 CPU，在 async 版本中可直接复用：

- `build_request()` — 构建 ProviderRequest
- `_clear_runtime_notices()` / `_set_runtime_notices()`
- `_consume_turn_notices()`
- `_ensure_provider_response_usable()`
- `_raise_if_interrupted()`（需改为检查 `asyncio.CancelledError`）
- `_event_context()` / `_emit()` — EventBus 本身同步
- `_start_turn()` / `turn.complete()` / `turn.fail()`
- message_runtime 写操作（纯内存）

---

## 3. AsyncQueryLoop 设计

### 3.1 设计原则

1. **并列，不替换**：`AsyncQueryLoop` 和 `QueryLoop` 共存于 `runtime/` 包，同步版永远保留
2. **不重复 shared helper**：通过 module-level helper 函数或 mixin 避免复制纯 CPU 逻辑
3. **最小 async surface**：只把确实需要 await 的操作 async 化，其余复用
4. **中断用 CancelledError**：async 版不使用 `_interrupted` 标志，依赖 `asyncio.CancelledError` 传播

### 3.2 新文件布局

```
src/agentos/runtime/
├── query_loop.py           # 现有同步版，不改
├── async_query_loop.py     # 新增
└── _loop_helpers.py        # 可选：提取 shared helpers（视复杂度决定）
```

### 3.3 AsyncQueryLoop 核心接口

```python
# src/agentos/runtime/async_query_loop.py

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol

from agentos.runtime.stream_events import RunOptions, TurnStreamEvent


class AsyncProviderBoundary(Protocol):
    """AsyncQueryLoop 依赖的 provider 边界（异步版）。"""

    async def async_stream(
        self,
        request: ProviderRequest,
        options: ProviderStreamOptions,
    ) -> AsyncIterator[ProviderStreamEvent]:
        """异步 stream provider 响应。"""


class AsyncToolCallRouterBoundary(Protocol):
    """AsyncQueryLoop 依赖的 tool router 边界（异步版）。"""

    async def async_execute_tool_call(self, tool_call: object) -> object:
        """异步执行 provider tool call。"""


@dataclass(slots=True)
class AsyncQueryLoop:
    """异步 agent turn 调度器，适用于 asyncio 事件循环。"""

    context_runtime: ContextRuntimeBoundary
    message_runtime: MessageRuntime
    request_builder: ProviderRequestBuilder
    provider: object  # 同时接受 Provider 和 AsyncProvider
    compression_runtime: CompressionRuntime | None = None
    tool_call_router: object | None = None  # 同时接受同步和异步 router
    event_bus: EventBus | None = None
    session_state: SessionState | None = None
    turn_notice_provider: TurnNoticeProvider | None = None
    max_tool_iterations: int = 8
    _provider_stream_counter: int = field(default=0, init=False, repr=False)

    async def run_turn(self, user_message: str) -> str:
        """异步运行完整 turn，返回最终内容。"""

        final_content = ""
        async for event in self.run_turn_stream(user_message):
            if isinstance(event, TurnStreamCompleted):
                final_content = event.content
        return final_content

    async def run_turn_stream(
        self,
        user_message: str,
        options: RunOptions | None = None,
    ) -> AsyncIterator[TurnStreamEvent]:
        """异步运行 turn，产出 typed stream events。"""
        ...

    async def run_continuation_stream(
        self,
        options: RunOptions | None = None,
    ) -> AsyncIterator[TurnStreamEvent]:
        """异步运行 continuation turn。"""
        ...
```

### 3.4 内部 async provider loop

```python
async def _run_provider_loop_stream(
    self,
    turn: TurnState | None,
    options: RunOptions,
) -> AsyncIterator[TurnStreamEvent]:
    iterations = 0
    while True:
        # 中断检查：依赖 CancelledError，不用 _interrupted 标志
        request = self.build_request()           # 同步，纯 CPU
        self._emit(ProviderRequestBuiltEvent(...))

        # 关键：await provider IO
        response = await self._consume_provider_stream_async(request, options)

        self._emit(ProviderResponseReceivedEvent(...))
        self._ensure_provider_response_usable(response)  # 同步复用

        # append message：同步
        assistant = self.message_runtime.append_assistant(...)
        yield AssistantCompleted(response=response)

        if not response.tool_calls:
            return response.content

        iterations += 1
        if iterations > self.max_tool_iterations:
            raise RuntimeError("...")

        for tool_call in response.tool_calls:
            yield ToolStreamStarted(...)
            result = await self._execute_tool_call_async(tool_call)  # await
            yield ToolStreamCompleted(...)
```

### 3.5 provider stream 消费

```python
async def _consume_provider_stream_async(
    self,
    request: ProviderRequest,
    options: RunOptions,
) -> ProviderResponse:
    """优先使用 async_stream，fallback 到 run_in_executor。"""

    async_stream = getattr(self.provider, "async_stream", None)
    if callable(async_stream):
        async for event in async_stream(request, options):
            ...
        return response

    # fallback：把同步 provider 放进线程池，不阻塞事件循环
    loop = asyncio.get_running_loop()
    response = await loop.run_in_executor(
        None,
        lambda: self._sync_provider_complete(request, options),
    )
    return response
```

---

## 4. Provider async 扩展

### 4.1 AsyncProvider protocol

```python
# src/agentos/providers/base.py（追加，不修改现有 Provider）

class AsyncProvider(Protocol):
    """支持 async streaming 的 provider 可选 protocol。"""

    async def async_complete(
        self,
        request: ProviderRequest,
    ) -> ProviderResponse:
        """异步返回 complete response。"""

    async def async_stream(
        self,
        request: ProviderRequest,
        options: ProviderStreamOptions,
    ) -> AsyncIterator[ProviderStreamEvent]:
        """异步 stream provider events。"""
```

`AsyncProvider` 是**可选扩展**，不修改现有 `Provider` protocol。现有 provider 不需要改动即可继续被 `QueryLoop` 使用。

`AsyncQueryLoop` 优先 duck-type 检测 `async_stream` 方法，fallback 到 `run_in_executor`。

### 4.2 AnthropicProvider async 实现策略

```python
class AnthropicProvider:
    # 现有同步方法保留
    def complete(self, request): ...
    def stream(self, request, options): ...

    # 新增 async 方法
    async def async_stream(
        self,
        request: ProviderRequest,
        options: ProviderStreamOptions,
    ) -> AsyncIterator[ProviderStreamEvent]:
        # 使用 anthropic SDK 的 async client
        async with self._async_client.messages.stream(...) as s:
            async for event in s:
                yield self._map_event(event)
```

关键：`async_client` 用 `httpx.AsyncClient` 底层，完全不阻塞事件循环。

---

## 5. ToolCallRouter async 扩展

### 5.1 AsyncToolCallRouterBoundary

```python
class AsyncToolCallRouterBoundary(Protocol):
    """QueryLoop 依赖的 async tool router 边界。"""

    async def async_execute_tool_call(
        self,
        tool_call: ProviderToolCall,
    ) -> ToolExecutionResult:
        """异步执行 provider tool call。"""
```

### 5.2 ToolCallRouter async 实现

`ToolCallRouter` 新增 `async_execute_tool_call` 方法：

- context tools（`declare_schema`, `update_state` 等）：纯同步，直接调用，包成 `asyncio.to_thread` 或直接返回
- MCP tool：视 MCP adapter 是否支持 async；fallback 到 `run_in_executor`
- 外部工具：`ToolExecutor.async_execute()` 新增，IO-heavy 工具用 `asyncio.to_thread`

```python
async def async_execute_tool_call(
    self,
    tool_call: ProviderToolCall,
) -> ToolExecutionResult:
    self.security_policy.ensure_tool_allowed(tool_call.name)
    if tool_call.name in CONTEXT_PROTOCOL_TOOL_NAMES:
        # context tools 纯 CPU，直接调用
        return self._execute_context_tool(tool_call)
    if tool_call.name.startswith("mcp__"):
        return await self._async_execute_mcp(tool_call)
    return await asyncio.to_thread(
        self._tool_executor().execute, tool_call
    )
```

---

## 6. Agent async API

```python
# src/agentos/runtime/agent.py（追加方法）

class Agent:
    # 现有同步方法不变

    async def async_run(
        self,
        user_message: str,
        *,
        thinking: bool = False,
        show_thinking: bool = False,
    ) -> AgentResult:
        """异步运行完整 turn。"""

        final_content = ""
        async for event in self.async_stream(user_message, thinking=thinking):
            if isinstance(event, TurnStreamCompleted):
                final_content = event.content
        return AgentResult(content=final_content)

    async def async_stream(
        self,
        user_message: str,
        *,
        thinking: bool = False,
        show_thinking: bool = False,
    ) -> AsyncIterator[TurnStreamEvent]:
        """异步运行 turn，产出 typed stream events。"""

        async_loop = getattr(self.query_loop, "_async_loop", None)
        if async_loop is not None:
            async for event in async_loop.run_turn_stream(user_message, ...):
                yield event
            return
        # fallback：同步 QueryLoop 放进线程池
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[TurnStreamEvent | None] = asyncio.Queue()

        def _worker() -> None:
            for event in self.stream(user_message, thinking=thinking):
                asyncio.run_coroutine_threadsafe(queue.put(event), loop)
            asyncio.run_coroutine_threadsafe(queue.put(None), loop)

        executor_future = loop.run_in_executor(None, _worker)
        while True:
            event = await queue.get()
            if event is None:
                break
            yield event
        await executor_future
```

`Agent` 内部保持 `AsyncQueryLoop` 的可选引用：
```python
@dataclass(slots=True)
class Agent:
    query_loop: QueryLoop
    _turn_lock: RLock
    _async_loop: AsyncQueryLoop | None = field(default=None, init=False)
```

---

## 7. ASGI 集成改进

### 7.1 当前问题

```python
# asgi.py 现状（问题代码）
for chunk in stream:
    await asyncio.sleep(0)   # 只在 chunk 边界让出，provider IO 仍阻塞
```

### 7.2 目标：真正非阻塞的 SSE handler

```python
async def _handle_sse_turn(self, session_id, body, receive, send):
    await send({"type": "http.response.start", "status": 200, ...})
    disconnect_task = asyncio.create_task(receive())

    agent = await self._sessions.get_agent(session_id)

    # 优先走 async_stream（真正非阻塞）
    if hasattr(agent, "async_stream"):
        async_stream = agent.async_stream(user_message)
        try:
            async for event in async_stream:
                if disconnect_task.done():
                    if disconnect_task.result().get("type") == "http.disconnect":
                        agent.interrupt()
                        break
                chunk = event_to_sse(event)
                if chunk:
                    await send({"type": "http.response.body", "body": chunk.encode()})
        except asyncio.CancelledError:
            agent.interrupt()
            raise
        finally:
            disconnect_task.cancel()
    else:
        # fallback：同步 stream 放进线程池，通过 queue 桥接
        await self._handle_sse_turn_sync_fallback(agent, user_message, send, disconnect_task)
```

### 7.3 断开检测改进

async 版中，`disconnect_task` 和 `async_stream` 可以真正并发：

```python
stream_task = asyncio.create_task(self._consume_stream(agent, user_message, send))
disconnect_task = asyncio.create_task(receive())

done, pending = await asyncio.wait(
    [stream_task, disconnect_task],
    return_when=asyncio.FIRST_COMPLETED,
)
if disconnect_task in done:
    agent.interrupt()
    stream_task.cancel()
```

---

## 8. 中断机制

### 8.1 同步版（现状保留）

`QueryLoop._interrupted` 标志 + `_raise_if_interrupted()` 在各 loop 安全点检查。`Agent.interrupt()` 写标志，适合跨线程通知。

### 8.2 async 版

async 版不使用 `_interrupted` 标志，依赖标准 asyncio 取消机制：

- `asyncio.Task.cancel()` → 下一个 await 点抛出 `CancelledError`
- `AsyncQueryLoop` 每个 `await` 都是潜在中断点，无需手动轮询标志
- `Agent.interrupt()` 在 async 上下文中同时设置 `_interrupted` 标志 **并** cancel 正在运行的 async task（通过 `_current_task` 引用）

```python
class Agent:
    _current_async_task: asyncio.Task | None = None

    def interrupt(self) -> None:
        self.query_loop.request_interrupt()       # 同步版标志
        if self._current_async_task is not None:  # async 版 cancel
            self._current_async_task.cancel()
```

- `CancelledError` 在 `async_stream` 中传播，SSE handler 捕获后清理连接
- tool 执行中的 `run_in_executor` 任务：cancel 会在下一次 await 生效，正在运行的线程任务不会强制终止（Python 限制）；需在 tool 层面支持 cooperative cancellation

---

## 9. 代码复用策略

避免在 `AsyncQueryLoop` 中重复 `QueryLoop` 的逻辑，采用以下策略：

### 9.1 纯函数提取（推荐）

把无状态 helper 提取为 module-level 函数：

```python
# runtime/_loop_helpers.py
def ensure_provider_response_usable(response: ProviderResponse) -> None: ...
def map_provider_tool_calls(response: ProviderResponse) -> list[ToolCall]: ...
def build_event_context(session_state, turn) -> dict[str, str | None]: ...
```

`QueryLoop` 和 `AsyncQueryLoop` 都 import 这些函数。

### 9.2 不用继承

不使用 `AsyncQueryLoop(QueryLoop)` 继承，因为同步 `Iterator` 和 `AsyncIterator` 的 `yield from` / `async for` 语义不兼容，强行继承会产生隐患。

### 9.3 stream_events 类型共用

`TurnStreamEvent` 类型层级完全复用，无需改动。

---

## 10. 测试策略

### 10.1 unit tests

```
tests/runtime/test_async_query_loop.py
```

- 使用 `FakeAsyncProvider`（`AsyncIterator` 直接 yield 预置 events）
- 测试 `run_turn_stream` 产出正确 event 序列
- 测试 tool call loop（多轮 iteration）
- 测试 `CancelledError` 传播（中断测试）
- 测试 fallback 到 sync provider 的 `run_in_executor` 路径

### 10.2 integration tests

```
tests/channels/test_asgi_app_async.py
```

- 使用 `httpx.AsyncClient` + `ASGITransport`
- 测试 SSE endpoint 并发两个 session 不互阻塞
- 测试客户端断开后 agent 收到中断信号

### 10.3 向后兼容测试

现有 `tests/runtime/test_query_loop.py` 全部保持通过，不改动同步版行为。

---

## 11. 迁移路径

| 阶段 | 内容 | 影响范围 |
|------|------|---------|
| Phase A | 新增 `AsyncQueryLoop`，`AsyncProvider` protocol | 新文件，不破坏现有 |
| Phase B | `AnthropicProvider.async_stream()` 实现 | providers/anthropic.py |
| Phase C | `ToolCallRouter.async_execute_tool_call()` | capabilities/router.py |
| Phase D | `Agent.async_run()` / `Agent.async_stream()` | runtime/agent.py |
| Phase E | `AsgiAgentApp` SSE handler 切换为 async 路径 | channels/asgi.py |
| Phase F | `AgentSessionProvider.get_agent()` 改为 async | channels/session.py |

每个 phase 独立 PR，有明确 rollback 点。

---

## 12. 验收标准

- [ ] `AsyncQueryLoop.run_turn_stream()` 签名为 `AsyncIterator[TurnStreamEvent]`
- [ ] `AsyncProvider.async_stream()` protocol 定义在 `providers/base.py`
- [ ] `ToolCallRouter.async_execute_tool_call()` 实现，context tools 同步执行，外部工具 `asyncio.to_thread`
- [ ] `Agent.async_run()` 和 `Agent.async_stream()` 可用
- [ ] `AsgiAgentApp` SSE handler 不再使用 `asyncio.sleep(0)`
- [ ] 两个并发 SSE session 不互相阻塞（集成测试通过）
- [ ] 现有同步 `QueryLoop` / `Agent.run()` / `Agent.stream()` 全部测试继续通过
- [ ] `CancelledError` 测试：cancel async task 后 SSE 连接正确关闭
