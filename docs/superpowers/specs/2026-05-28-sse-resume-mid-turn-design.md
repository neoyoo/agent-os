# SSE Last-Event-ID Mid-Turn Resume 设计

## Scope Contract

本设计给 agentos 的 ASGI SSE 通道补上断线续传：客户端断线重连后，凭 `Last-Event-ID` 拿回漏掉的事件，并在 turn 仍在运行时无缝继续（Level B 完整 mid-turn 重接）。

所属阶段：

- Phase 8 channels 之后的 production harness 能力补强。
- 与 tool-result budget / compression 两份 spec **完全独立**，可并行实现。
- KB 摩擦记录：对比的 5 个源（Claude Code / OpenHarness / DeerFlow / Hermes / AgentScope）**全部没做好 SSE resume**，本设计无现成参考实现，属 channel-remote 真实盲区。

本设计完成：

- 定义 `SseEventBuffer` 协议 + `InMemorySseEventBuffer` + `RedisSseEventBuffer` 两实现。
- 把 SSE turn 从"连接驱动"解耦为"后台 turn-runner 写 buffer，连接是 reader"。
- 定义 per-event 稳定 `id:`、`Last-Event-ID` 重连 replay + tail。
- 定义断线 grace 期保活：grace 内重连无缝续传，超时才 interrupt + GC。
- 定义 turn 完成后的 buffer 保留窗口（让晚到的重连仍能取最终事件）。

本设计暂不完成：

- 不支持单 session 并发多 turn；本期约束**每 session 同时只有一个 in-flight turn**（见下）。
- 不做客户端 SDK；只定义服务端协议（标准 SSE `Last-Event-ID` header）。注意当前 agentos streaming endpoint 是 `POST` + JSON body，浏览器原生 `EventSource` 只能 `GET`，不会覆盖此路径；web 前端应使用 fetch streaming 或自有 client 显式带上 `Last-Event-ID`。若未来要原生 EventSource，需要另加 GET resume/read endpoint。
- 不改 JSON（非流式）turn 端点。
- 不做跨节点的 grace 协调；多节点下 grace 保活是单节点行为，Redis buffer 只保证重连到**任意**节点都能 replay 已产出事件（mid-turn live tail 仍需重连回原节点或走 Redis Stream 跟随，见实现注记）。

必须遵守的架构规则：

- turn-runner 是**事件的唯一生产者**，写进 `SseEventBuffer`；SSE 连接是纯 reader，不直接驱动 turn。
- 断线**不再立即 interrupt**（现状 `asgi.py:235` 的行为要改）；改为 grace 期保活，超时才 interrupt。
- 心跳帧是 SSE 注释（`: heartbeat`），**不带 id**，不污染 `Last-Event-ID`——现有心跳保留。
- agent 生命周期从"per 连接"挪到"per turn"：turn 运行期间持有 agent，turn 终结 + buffer 保留期过后才 `release_agent`。

## 背景问题

当前 `_handle_sse`（`asgi.py:194-256`）把 turn 和连接绑死：

```python
stream_task = asyncio.create_task(
    self._send_agent_sse_stream(agent, request, send, send_lock),  # 直接写连接的 send
)
done, _ = await asyncio.wait({stream_task, disconnect_task}, ...)
if disconnect_task in done:               # 客户端一断
    agent.interrupt()                     # ★ 立即杀 turn
    stream_task.cancel()
```

后果：

1. 事件直接写进**这一条** TCP 连接，没有任何缓冲——连接断了，已产出但未确认的事件永久丢失。
2. 断线即 `interrupt()`——网络闪断（手机切网、代理超时、刷新页面）会把一个跑了 30 秒、烧了真金白银的 turn 直接杀掉，用户重连后只能从头再来。
3. SSE 事件没有 `id:`，客户端无法告诉服务端"我看到哪了"。

生产级 web agent 必须能扛住网络抖动。这是 SDK 评分 channel 维度最后一块扣分。

## 核心架构：生产者/读者解耦

```text
                    ┌─────────────────────────────────┐
   POST .../stream  │  TurnRunner (后台 asyncio task)   │
   ────────────────▶│  消费 agent.async_stream(...)     │
                    │  每个 event → buffer.append(id++) │
                    └───────────────┬─────────────────┘
                                    │ 写
                                    ▼
                         ┌──────────────────────┐
                         │   SseEventBuffer      │  ← 事件唯一来源
                         │  (stream_key 维度)     │
                         └──────────┬───────────┘
                                    │ replay_since + follow(tail)
              ┌─────────────────────┼─────────────────────┐
              ▼                                            ▼
       SSE 连接 #1 (reader)                          SSE 连接 #2 (重连 reader)
       Last-Event-ID: 0                              Last-Event-ID: 42
       replay 0.. + tail                             replay 43.. + tail
```

`stream_key = f"{session_id}:{turn_stream_id}"`。本期约束**每 session 同时只有一个 in-flight turn**，但 `turn_stream_id` 仍然必须存在：否则 terminal retention 窗口内如果同一 session 发起下一轮，新 turn 会和上一轮保留的 buffer 共用 `session_id`，导致 replay 错发旧事件。

SSE `id:` 使用不透明字符串：`<turn_stream_id>:<seq>`。服务端从 `Last-Event-ID` 解析出 `turn_stream_id` 和 `seq`，再定位对应 buffer。客户端不需要理解其内部结构，只需原样回传。

若一个 session 已有非 terminal in-flight turn 时再来**不带 matching `Last-Event-ID` 的新 turn 请求** → 返回 409（实现可选排队，本期 409）。若请求带 matching `Last-Event-ID`，按重连 reader 处理。

## SseEventBuffer

```python
class SseEventBuffer(Protocol):
    async def append(self, stream_key: str, sequence: int, chunk: str) -> None: ...
    async def replay_since(self, stream_key: str, last_sequence: int) -> list[tuple[int, str]]: ...
    async def follow(self, stream_key: str, since_sequence: int) -> AsyncIterator[tuple[int, str]]:
        """tail：yield > since 的事件，turn 终结后正常结束。"""
    async def mark_terminal(self, stream_key: str) -> None: ...     # turn 结束信号
    async def drop(self, stream_key: str) -> None: ...              # GC
```

`sequence` 是每个 `stream_key` 内的单调整数，由 TurnRunner 维护（每 append 前 ++）。写给客户端的 SSE `id:` 是 `turn_stream_id:sequence`，不是裸整数。

### InMemorySseEventBuffer（默认）

- 每 `stream_key` 一个 bounded `deque[(event_id, chunk)]`（默认上限如 512 事件）+ 一个 `asyncio.Condition` 用于 `follow` 的通知。
- `append` 入队 + `notify_all`；`follow` 在 Condition 上等新事件。
- 单进程；进程重启即丢（可接受，重连若打到重启后的进程，replay 返回空→客户端从头）。
- `mark_terminal` 唤醒所有 follower 让其正常收尾。

### RedisSseEventBuffer（生产多节点）

- 用 **Redis Stream**（`XADD` / `XREAD BLOCK`）按 `stream_key` 存事件——天然支持 replay（按 id 范围）+ 阻塞 tail + `MAXLEN` 截断 + TTL。
- 复用现有 `RedisHotSessionStore` 的连接配置。
- `event_id` 用整数序号映射到 Redis Stream id（或直接用 Stream 自增 id，TurnRunner 维护整数序号写进 field）。
- 扛进程重启 + 多节点：重连到任意节点都能 `replay_since`；live tail 通过 `XREAD BLOCK` 跟随同一 stream。

> ⚠ 实现注记（多节点 live tail）：InMemory 下 TurnRunner 和 reader 必在同进程；Redis 下 reader 可在别的节点通过 `XREAD BLOCK` 跟 TurnRunner 所在节点写入的 stream。但 grace 期保活（不 interrupt）是 TurnRunner 所在节点的本地状态——多节点下若重连打到非原节点，能 replay 已产出事件并通过 Redis Stream 跟随后续，但"是否还在 grace 保活"由原节点决定。Scope Contract 已声明不做跨节点 grace 协调。

## SseTurnRegistry（生命周期）

AsgiAgentApp 持有一个 registry：`stream_key -> TurnEntry`。

```python
@dataclass
class TurnEntry:
    runner_task: asyncio.Task
    agent: Agent
    turn_stream_id: str
    next_sequence: int
    terminal: bool
    active_readers: int
    grace_task: asyncio.Task | None
```

### 新 turn 请求

```text
1. registry 已有非 terminal entry? → 409 (session busy)
2. 生成 `turn_stream_id`；get_agent(session_id)；创建 buffer stream
3. 启动 TurnRunner 后台 task：
     async for event in agent.async_stream(...):
         sequence = entry.next_sequence++; await buffer.append(stream_key, sequence, event_to_sse(event))
     await buffer.mark_terminal(stream_key); entry.terminal = True
     schedule GC after retention_window
4. 当前连接作为 reader：replay_since(0) → tail follow → 随 terminal 结束
```

### 重连请求（带 Last-Event-ID）

```text
1. 读 Last-Event-ID header（`<turn_stream_id>:<seq>`）
2. 解析 stream_key 与 last_sequence；registry 有 entry?
     有 → 取消其 grace_task（若在 grace 中），active_readers++
          replay_since(last_sequence) 立即补发漏掉的 → 继续 follow tail
     无（turn 已 GC 或本就没有）→ buffer.replay_since 若仍有保留事件则补发后结束；
          否则返回空流（客户端据此决定重发 turn）
```

### 连接断开（reader 侧）

```text
active_readers--
若 active_readers == 0 且 not terminal:
    启动 grace_task：asyncio.sleep(grace_seconds) 后若仍无 reader →
        agent.interrupt(); runner_task.cancel(); release_agent; buffer.drop
若 terminal: 不动（buffer 在 retention_window 后自然 GC）
```

**关键差异**：现状"断线即 `agent.interrupt()`"删除；新设计断线进 grace 期（默认如 30s，可配 `sse_resume_grace_seconds`）。grace 内重连无缝续传；grace 超时才 interrupt + GC。

### 配置（AsgiAgentApp.__init__ 新增）

```python
sse_event_buffer: SseEventBuffer | None = None,     # 默认 InMemorySseEventBuffer()
sse_resume_grace_seconds: float = 30.0,
sse_terminal_retention_seconds: float = 60.0,       # turn 完成后 buffer 保留多久
```

## SSE 线格式

每个事件帧加 `id:` 行（在现有 `event_to_sse` 输出前）：

```
id: turn_abc:42
data: {"type":"assistant_content_delta","text":"..."}

```

心跳帧不变、不带 id：`: heartbeat\n\n`。当前 POST streaming path 需要 fetch/custom client 显式保存最后一个 `id:` 并在重连请求中设置 `Last-Event-ID` header；如果未来新增 GET EventSource endpoint，浏览器可自动回传。

## 测试策略

- buffer（两实现同一套契约测试）：append 后 `replay_since(n)` 只返回 > n；`follow` tail 收到新事件、terminal 后结束；bounded 上限驱逐最旧。
- event id：SSE 帧带单调 `id:`；心跳不带 id。
- 重连 replay：模拟断线（reader 任务取消）→ 新 reader 带 Last-Event-ID 重连 → 断言只补发漏掉的事件、无重复、无丢失。
- grace 保活：断线后 grace 内重连 → turn 未被 interrupt、续传成功；grace 超时无重连 → interrupt + release_agent + buffer GC（断言 `agent.interrupt` 被调用、`release_agent` 被调用）。
- 并发约束：session 已有 in-flight turn 时再来 turn 请求 → 409。
- terminal 保留：turn 完成后 retention 窗口内重连仍能取最终事件；窗口后取空。
- 回归：无断线的正常流式（含心跳）行为不变；JSON turn 端点不受影响。

## 组件边界小结

| 组件 | 职责 | 实现 |
|------|------|------|
| `SseEventBuffer` 协议 | 事件 replay + tail 抽象 | 新增 |
| `InMemorySseEventBuffer` | 单进程默认 | deque + Condition |
| `RedisSseEventBuffer` | 多节点/抗重启 | Redis Stream，复用 RedisHotSessionStore |
| `SseTurnRegistry` / `TurnEntry` | per-turn 生命周期 + grace | 新增，AsgiAgentApp 持有 |
| `TurnRunner` | 唯一事件生产者，写 buffer | 由 `_send_agent_sse_stream` 改造 |
| `_handle_sse` | reader：replay + tail + grace 触发 | 重构 |
| `event_to_sse` | 加 `id:` 行 | 小改 |
| AsgiAgentApp.__init__ | buffer / grace / retention 配置 | 加参数 |

## 实现交接须知（给实现者）

先读 `AGENTS.md`，命名/边界/typed event/test-first/完成度 checklist 一律遵守。本节只列 spec 特有点：

- **独立于另两份 spec**：不依赖 TokenCounter，可单独实现。全部落在 `channels/`（AGENTS.md：`channel-remote -> channels/`）。
- **这是三份里唯一的真重构**：要改 `_handle_sse` 的核心控制流（生产者/读者解耦 + 取消"断线即 interrupt"）。改之前**先把现有 SSE 测试跑绿留作回归基线**，重构后必须仍绿（含心跳、正常流式、disconnect 行为）。AGENTS.md：架构级改动要 targeted tests + 全套 + compileall + git diff --check。
- **若要改 `AgentSessionProvider` 的 get/release 调用时机**（从 per-连接 挪到 per-turn），这是跨 `channels/session.py` 边界的改动——AGENTS.md 要求「需要破边界先停下说明」。在实现笔记里写清为什么必须挪。
- **不碰 `persistence/` / `SNAPSHOT_VERSION`**：SSE buffer 是通道层瞬态（InMemory）或 Redis 独立 key，不进 session snapshot。
- **新增的 turn 生命周期事件**（若有）按 typed `*Event` 命名，进 observability。
- **两个 buffer 实现共用一套契约测试**（同一组 append/replay/follow/terminal 断言跑两遍），保证可互换。
- **Redis 实现用 Redis Stream（XADD/XREAD BLOCK）**，复用现有 `RedisHotSessionStore` 的连接配置，不要新起一套连接管理。
- **diff 要 surgical**：JSON（非流式）turn 端点、health/ready、rate-limit 路径一律不动。
