# Async Runtime Completion 设计

## Status

新 spec，治理 Codex 实现。本 spec 描述的三处 `asyncio.to_thread` 降级路径（provider 调用、compression、retry sleep）在代码审查时均已确认存在；`_provider_stream_events` 对 `async_stream` / `async_complete` 的优先判断逻辑已存在于 `async_query_loop.py:468-497`，Anthropic provider 只需补充实现即可接入该路径，无需修改调度逻辑。

**推荐实施顺序(经 review 调整):D → A → C → B(B1→B2→B3→B4)→ E。本 spec(A)为第 2 位,是 async web 能力地基,依赖无,但建议在 D(执行后端 seam)之后做。**

## Design References

- `src/agentos/runtime/async_query_loop.py` — 主要改动目标，重点关注 `_execute_tool_call`（L382-391）、`_consume_provider_stream`（L393-461）、`_provider_stream_events`（L463-497）、`_run_provider_loop_stream`（L238-381 中 L247 的 `build_request` 调用）
- `src/agentos/providers/anthropic.py` — 当前只有 `complete`，需增加 `async_complete` + `async_stream`
- `src/agentos/providers/openai_compatible.py` L332-345（`async_complete`）、L478-620（`async_stream`）— 参考实现
- `src/agentos/providers/base.py` — `Provider` / `AsyncProvider` 协议定义
- `src/agentos/providers/stream.py` — `ProviderStreamEvent` 类型层级
- `src/agentos/compression/runtime.py` — `CompressionRuntime.maybe_compress`（L96-146）及 `build_request` 调用路径
- `src/agentos/compression/llm_compressor.py` — `LlmCompressor.compress` / `compress_package`（L46-91）
- `src/agentos/builder.py` L181-269 — `build_async` 组装路径
- `AGENTS.md` — 命名规范、边界规则、完成度 checklist
- `docs/design/sdk-architecture.md` — SDK 层次结构与 Protocol seam 原则

## Goal

**强目标（可验证）：** 通过 `AgentBuilder.build_async()` 构建的 Agent，其完整 turn 执行路径中，provider 调用和 LLM-based compression 均不使用 `asyncio.to_thread` 降级；所有持有 `async_complete` 或 `async_stream` 的 provider 均通过原生 `await` 调用。具体包含：

1. `AnthropicProvider` 实现 `async_complete(request) -> ProviderResponse` 和 `async_stream(request, options) -> AsyncIterator[ProviderStreamEvent]`，通过 Anthropic Python SDK 的 `async_anthropic` 客户端（注入方式与 `client` 字段一致）发起调用。
2. `AsyncQueryLoop._provider_stream_events` 的现有分发逻辑（L463-497）已正确优先使用 `async_stream` → `async_complete` → `stream` → `to_thread(complete)` 降级链；`AnthropicProvider` 实现后自动接入前两级，无需修改调度逻辑。
3. `CompressionRuntime` 增加 `async_maybe_compress() -> CompressedSegment | None`，内部 `await` LLM compressor（若实现了 `async_compress_package`）；`AsyncQueryLoop._run_provider_loop_stream` 在调用 `build_request` 前改为 `await self._async_maybe_compress()`。
4. `asyncio.to_thread(policy.sleep, delay)`（L461）替换为 `await asyncio.sleep(delay)`；retry delay 不再阻塞事件循环。
5. sync-only 工具的 `to_thread(execute, tool_call)`（L390）保留不变，本 spec 不要求所有工具原生异步。

## Contracts

### AnthropicProvider 新增方法

```python
# src/agentos/providers/anthropic.py

@dataclass(slots=True)
class AnthropicProvider:
    client: Any          # anthropic.Anthropic（sync）
    model: str
    max_tokens: int = 4096
    timeout_seconds: float | None = None
    async_client: Any = None    # anthropic.AsyncAnthropic（async），可选；调用 async 方法时必须注入

    async def async_complete(self, request: ProviderRequest) -> ProviderResponse:
        """异步调用 Anthropic Messages API，需注入 async_client。"""
        ...

    async def async_stream(
        self,
        request: ProviderRequest,
        options: ProviderStreamOptions | None = None,
    ) -> AsyncIterator[ProviderStreamEvent]:
        """异步 streaming 调用 Anthropic Messages API，需注入 async_client。"""
        ...
```

> 字段顺序：`async_client` 必须放在所有非默认字段之后,避免 dataclass 'non-default argument follows default argument' 报错。

`async_client` 字段为可选（默认 `None`）。若 `async_client` 为 `None` 但 `async_complete` / `async_stream` 被调用，raise `RuntimeError("AnthropicProvider requires async_client for async methods")`。

`async_stream` 与 OpenAI-compatible `async_stream` 保持相同的 `ProviderStreamEvent` 输出顺序：`ProviderStreamStarted` → 零个或多个 `ProviderContentDelta` / `ProviderThinkingDelta` / `ProviderToolCallDelta` → `ProviderStreamCompleted`。

注意 Anthropic SDK streaming 的 event 类型映射：

| Anthropic SDK event | ProviderStreamEvent |
|---------------------|---------------------|
| `message_start` | `ProviderStreamStarted` |
| `content_block_delta` (text) | `ProviderContentDelta` |
| `content_block_delta` (thinking) | `ProviderThinkingDelta` |
| `content_block_delta` (input_json) | `ProviderToolCallDelta` |
| `message_stop` / 流结束 | `ProviderStreamCompleted` |

### AsyncProvider 协议（无需改动，已在 base.py 定义）

```python
class AsyncProvider(Protocol):
    async def async_complete(self, request: ProviderRequest) -> ProviderResponse: ...
    async def async_stream(
        self,
        request: ProviderRequest,
        options: ProviderStreamOptions,
    ) -> AsyncIterator[ProviderStreamEvent]: ...
```

`AnthropicProvider` 实现上述两个方法后，`_provider_stream_events`（L468-497）的 `getattr` 检测逻辑自动将其接入 `async_stream` → `async_complete` 优先路径，不需要修改 `async_query_loop.py`。

### LlmCompressor 新增 async 方法

```python
# src/agentos/compression/llm_compressor.py

class LlmCompressor:
    provider: Provider  # 同步 provider，已有
    async_provider: Provider | None = None  # 支持 async_complete 的 provider，可选

    async def async_compress(
        self,
        segment_id: str,
        messages: Sequence[Message],
    ) -> CompressedSegment:
        """异步 LLM 压缩，优先使用 async_provider.async_complete；降级为 to_thread(self.compress)。"""
        ...

    async def async_compress_package(
        self,
        segment_id: str,
        session_id: str,
        messages: Sequence[Message],
    ) -> CompressedSegmentPackage:
        """异步生成 compression package。"""
        ...
```

若 `async_provider` 有 `async_complete`，直接 `await`；否则 `await asyncio.to_thread(self.compress, segment_id, messages)`。

### CompressionRuntime 新增 async 方法

```python
# src/agentos/compression/runtime.py

class CompressionRuntime:
    async def async_maybe_compress(self) -> CompressedSegment | None:
        """异步版 maybe_compress：若 compressor 支持 async_compress_package，则 await；否则 to_thread。"""
        ...
```

实现逻辑与 `maybe_compress`（L96-146）相同，唯一差异：`_compress_package` 调用替换为 `await self._async_compress_package(source_messages, active_compressor)`。内部 `_async_compress_package` 优先调用 `compressor.async_compress_package`（若存在），否则 `asyncio.to_thread(self._compress_package, ...)` 降级。

### AsyncQueryLoop 调整（最小 diff）

```python
# src/agentos/runtime/async_query_loop.py

# _run_provider_loop_stream 内 L247：
# 原：request = self.sync_loop.build_request()
# 改为：
request = await self._build_request_async()

# 新增私有方法：
async def _build_request_async(self) -> ProviderRequest:
    """构建 provider request，并异步执行压缩（避免阻塞事件循环）。"""
    if self.compression_runtime is not None:
        await self.compression_runtime.async_maybe_compress()
    return self.sync_loop.request_builder.build(self.sync_loop.context_runtime)

# _consume_provider_stream 内 L461：
# 原：await asyncio.to_thread(policy.sleep, delay)
# 改为：
await asyncio.sleep(delay)
```

`_clear_runtime_notices` 的调用保留在 `sync_loop.build_request` 内部（由 `request_builder.build` 完成），`_build_request_async` 跳过 `sync_loop.build_request`，直接调用 `request_builder.build`——需确认 `_clear_runtime_notices` 仍被调用，若它在 `build_request` 末尾 `finally` 中，需在 `_build_request_async` 内显式调用 `self.sync_loop._clear_runtime_notices()`。

## File Change Map

- **`src/agentos/providers/anthropic.py`**
  - 新增字段 `async_client: Any = None`（dataclass field，L38-39 处）
  - 新增 `async_complete(self, request: ProviderRequest) -> ProviderResponse`（L41 之后，完整实现）
  - 新增 `async def async_stream(self, request, options) -> AsyncIterator[ProviderStreamEvent]`（仿 `openai_compatible.py` L478-620 结构，适配 Anthropic SDK streaming event 类型）
  - 新增 `from collections.abc import AsyncIterator` import（若未存在）
  - 新增 Anthropic streaming event 到 `ProviderStreamEvent` 的 mapping 逻辑（私有 `_stream_events_from_async_response` 方法）

- **`src/agentos/compression/llm_compressor.py`**
  - 新增字段 `async_provider: Any = None`（dataclass，L37-44 处）
  - 新增 `async def async_compress(self, segment_id, messages) -> CompressedSegment`
  - 新增 `async def async_compress_package(self, segment_id, session_id, messages) -> CompressedSegmentPackage`
  - 新增 `import asyncio` 及 `from collections.abc import AsyncIterator`（按需）

- **`src/agentos/compression/runtime.py`**
  - 新增 `async def async_maybe_compress(self) -> CompressedSegment | None`（L96 的 `maybe_compress` 之后）
  - 新增私有 `async def _async_compress_package(self, source_messages, compressor) -> CompressedSegmentPackage`
  - 新增 `import asyncio`（若未存在）

- **`src/agentos/runtime/async_query_loop.py`**
  - 新增 `async def _build_request_async(self) -> ProviderRequest`（L382 之前）
  - `_run_provider_loop_stream` L247：`self.sync_loop.build_request()` → `await self._build_request_async()`
  - `_consume_provider_stream` L461：`asyncio.to_thread(policy.sleep, delay)` → `asyncio.sleep(delay)`
  - **不修改** `_provider_stream_events`（L463-497）——现有分发逻辑已完备
  - **不修改** `_execute_tool_call`（L382-391）——sync 工具的 `to_thread` 保留

- **`src/agentos/builder.py`**（视 `LlmCompressor` 构造方式决定）
  - 若 builder 直接构造 `LlmCompressor`，在 `_query_loop_kwargs` 或压缩段传入 `async_provider`；若 compressor 由用户注入，此文件无需改动。

## Acceptance Criteria

测试文件位置遵循 `tests/` 下对应模块目录，文件名以描述性名称新建或追加到现有文件。

1. **`AnthropicProvider.async_complete` 不走 to_thread**
   描述：创建一个 mock `async_client`，其 `messages.create` 是 async coroutine；构造 `AnthropicProvider(client=None, async_client=mock, model="claude-test")`；调用 `await provider.async_complete(request)`；断言 mock async `messages.create` 被调用一次、sync `client` 未被访问。

2. **`AnthropicProvider.async_stream` 产出正确事件序列**
   描述：mock `async_client.messages.stream` 返回 async context manager，依次产出 `content_block_delta` text events；调用 `async_stream`；收集事件；断言序列包含 `ProviderStreamStarted` → 一个或多个 `ProviderContentDelta` → `ProviderStreamCompleted`，且内容拼接等于预期。

3. **`AnthropicProvider` 缺少 `async_client` 时抛出明确错误**
   描述：构造 `AnthropicProvider(client=mock_sync, async_client=None, model="claude-test")`；`await provider.async_complete(request)` 应 raise `RuntimeError` 含 `"async_client"` 字样。

4. **`AsyncQueryLoop` provider 路径无 to_thread（端到端）**
   描述：参考现有 `test_async_provider_stream_is_awaited_without_executor_bridge`（`tests/runtime/test_async_query_loop_native.py:135`）；额外增加：注入实现了 `async_complete` 的 mock provider，其 `complete` 方法 raise `AssertionError`；运行 `loop.run_turn("hello")`；断言 `complete` 未被调用（即无 `to_thread` 降级）。此测试已有类似形式（`_AsyncCompleteProvider`，L281-288），可扩展为集成压缩场景。

5. **`LlmCompressor.async_compress_package` 异步路径**
   描述：注入实现了 `async_complete` 的 mock `async_provider`；`await compressor.async_compress_package(segment_id, session_id, messages)`；断言 mock `async_complete` 被调用、`provider.complete`（sync）未被调用。

6. **`CompressionRuntime.async_maybe_compress` 不阻塞事件循环（LLM compressor 路径）**
   描述：构造 `CompressionRuntime` 使用 `LlmCompressor`（含 `async_provider`），注入超预算消息；`await runtime.async_maybe_compress()`；断言返回 `CompressedSegment` 且事件循环未被 `to_thread` 之外的同步调用阻塞（可用 `asyncio.get_event_loop().call_soon` 抢占验证，或断言 `async_complete` mock 被 `await`）。

7. **`RuleBasedCompressor` 路径的 async_maybe_compress 降级正常**
   描述：使用 `RuleBasedCompressor`（不支持 `async_compress_package`）；`await runtime.async_maybe_compress()` 仍能正常完成、返回 `CompressedSegment`（通过 `to_thread` 降级路径）。

8. **retry delay 使用 `asyncio.sleep`**
   描述：构造会 retry 一次的 provider（首次失败，次次成功）；在 async loop 中运行；测量 `asyncio.sleep` 被调用（可通过 mock `asyncio.sleep` 并断言被调用），`threading.sleep` 未被调用。

9. **sync build 路径回归（`build()` 不变）**
   描述：`AgentBuilder.build()`（sync）路径在引入以上变更后，使用 `_TwoStepProvider`（sync `complete`）运行 `loop.run_turn` 仍然通过；参考 `tests/runtime/test_async_query_loop_native.py:72`（`test_sync_handler_still_works_in_async_loop`）及 `tests/runtime/test_agent_builder.py` 中现有 sync builder 测试。

## Risks & Non-Goals

**风险**

- `async_client` 字段默认 `None` 意味着 `AnthropicProvider` 在不注入 async client 时退化到 `to_thread(complete)`（通过 `_provider_stream_events` 降级链 L491-497）——这是可接受的向后兼容行为，但需要在 `async_complete` / `async_stream` 内明确 raise 而非静默降级，否则 mock 测试难以验证。
- Anthropic Python SDK 的 `AsyncAnthropic.messages.stream()` API 与 `create(stream=True)` 有差异；实现者需查阅 SDK 文档确认正确的 async streaming context manager 用法。
- `_build_request_async` 绕过 `sync_loop.build_request` 时，需手动保证 `_clear_runtime_notices` 在 `build` 之后调用——漏掉会导致 runtime notice 堆积，影响 continuation turn。
- `asyncio.to_thread(policy.sleep, delay)` 替换为 `asyncio.sleep(delay)` 是正确做法，但 `RetryPolicy.delay_for_attempt` 返回值是 wall-clock seconds；若单位不一致需核实。

**Non-Goals**

- **Sync build 路径不变**：`AgentBuilder.build()` + `QueryLoop` 的同步 `build_request()` → `maybe_compress()` 路径一律不改。
- **Sync-only 工具的 `to_thread` 保留**：`_execute_tool_call` L390 的 `to_thread(execute, tool_call)` 不在本 phase 范围内。
- **httpx 依赖为 optional extra**：`async_stream` / `async_complete` 使用 Anthropic Python SDK 自带的异步客户端，不额外依赖 `httpx` 直接调用；若 SDK 内部使用 httpx，不需要在 pyproject.toml 新增 extra。
- **不新增 Provider 协议字段**：`AsyncProvider` 协议（`base.py`）已完备，不改动。
- **不改变任何对外 Agent API**：`Agent.run`、`Agent.stream`、`Agent.async_stream`、`Agent.async_run` 签名不变。
- **不涉及 SSE channel 层**：本 spec 只到 `AsyncQueryLoop`，不修改 `channels/asgi.py`。
- **不涉及 `FallbackCompressor`**：`FallbackCompressor` 不强制要求 async 路径，运行时降级到 `to_thread` 可接受。
