# Phase B — Web 水平扩展：PersistentAgentSessionProvider 设计

## Status

**Draft — 待实现。**

现有代码盘点：

- `AgentSessionProvider` Protocol（`channels/session.py`）仅定义同步 `get_agent` / `release_agent`，无 async 变体。
- `InMemoryAgentSessionProvider` 是唯一内置实现，单进程、无持久化、无 TTL。
- `SessionSnapshot`（`persistence/base.py`）已完整定义四大字段（`session_state`, `context_state`, `message_runtime`, `compression_index`），`session_snapshot_from_dict` / `to_dict` 序列化已就绪。
- `RedisHotSessionStore`（`memory/redis_store.py`）已实现 `load_hot_state` / `save_hot_state`，但其 `HotSessionState` 不等于 `SessionSnapshot`——两者是不同的投影，**不能互换**。Redis 层目前没有直接存取 `SessionSnapshot` 的 API。
- `PostgresDurableSessionStore`（`persistence/postgres.py`）只存储 `SessionState` + 消息 + refs + compressed segments，无整体 snapshot 序列化入口。
- `MemoryPersistence` / `FileSystemPersistence` / `SqlitePersistence` 均实现 `SessionPersistence` Protocol（`save` / `load` / `list_ids` / `delete`），整体 snapshot 存取已就绪。
- `AsgiAgentApp._handle_lifespan` 已调用 `shutdown_handlers` + `sessions.shutdown()`（鸭子调用），shutdown 钩子点已存在，但无 drain 等待逻辑。

**本设计范围（经 review 拆为 B1–B4 四个独立可交付子阶段）**：B1 新增 `AsyncAgentSessionProvider` Protocol + `PersistentAgentSessionProvider`（基于抽象 `SessionPersistence`）；B2 添加 Redis snapshot 热缓存；B3 添加 Postgres snapshot 冷存储（OPT-IN）；B4 AsgiAgentApp lifespan shutdown 补入 in-flight turn drain。

**推荐实施顺序(经 review 调整):D → A → C → B(B1→B2→B3→B4)→ E。本 spec(B)经 review 由单一大阶段拆为 B1–B4 四个独立可交付子阶段,逐步落地以规避双写/一致性风险。**

---

## Design References

| 文件 | 关键点 |
|------|--------|
| `src/agentos/channels/session.py` | `AgentSessionProvider` Protocol，`InMemoryAgentSessionProvider` |
| `src/agentos/channels/asgi.py:32-69` | `AsgiAgentApp.__init__`，`shutdown_handlers`，`_handle_lifespan:601-627` |
| `src/agentos/persistence/base.py` | `SessionSnapshot`，`SessionPersistence` Protocol |
| `src/agentos/persistence/serializers.py` | `session_snapshot_to_dict` / `session_snapshot_from_dict` |
| `src/agentos/persistence/postgres.py` | `PostgresDurableSessionStore`：只存 SessionState + 消息 + refs + segments |
| `src/agentos/memory/redis_store.py` | `RedisHotSessionStore`：`load_hot_state` / `save_hot_state` |
| `src/agentos/memory/types.py` | `HotSessionState`：热点工作集，不等于 `SessionSnapshot` |
| `src/agentos/channels/rate_limit.py:57-64` | `_evict` 按 bucket 做 LRU 清理的参考模式 |
| `src/agentos/builder.py:176-184` | `AgentBuilder.build` / `build_async`，hydration 需对应 snapshot 恢复路径 |

---

## Sub-phase Dependency

```
B1（地基）→ B2（Redis 热缓存，依赖 B1 的 SessionPersistence seam）
         → B3（Postgres 冷存储，依赖 B1 的 SessionPersistence seam）
B4（ASGI drain，独立于 B1–B3，可任意顺序实施）
```

B2 和 B3 均通过 B1 暴露的 `SessionPersistence` 抽象注入，不直接耦合彼此。

---

## B1 — SessionPersistence-backed Provider（地基，先做）

**Goal**：新增 `AsyncAgentSessionProvider` Protocol 和 `PersistentAgentSessionProvider`，后者针对抽象 `SessionPersistence` 实现 get-miss 水合 + 本地 LRU/TTL 缓存 + release 写回，**不依赖任何 Redis / Postgres 具体实现**。

### Contracts

#### AsyncAgentSessionProvider Protocol

```python
# src/agentos/channels/session.py

class AsyncAgentSessionProvider(Protocol):
    """支持 async 路径的 session provider 扩展协议。
    独立于现有同步 AgentSessionProvider，旧 provider 无需改动。
    """

    async def async_get_agent(self, session_id: str) -> Agent:
        """异步按 session_id 返回 Agent；未知 session 可自动创建。"""

    async def async_release_agent(self, session_id: str, agent: Agent) -> None:
        """异步标记本轮 channel 调用结束，触发 snapshot 写回。"""
```

`AsgiAgentApp` 在调用 `get_agent` / `release_agent` 前，通过
`getattr(self._sessions, "async_get_agent", None)` 检测是否走 async 路径；
无 async 路径则保持当前的 `asyncio.to_thread` 包装。

> **Python 正确性说明**：不使用 `isinstance(self._sessions, AsyncAgentSessionProvider)`，
> 因为普通 `typing.Protocol` 在运行时不支持 isinstance 检查（会抛 `TypeError`）。
> 改用 `getattr` 鸭子检测，与 codebase 现有惯例一致。若需 isinstance，须给 Protocol 加
> `@runtime_checkable`，但 getattr 方式更轻量，**推荐使用 getattr**。

> ⚠ 假设：现有 `AgentSessionProvider` Protocol 保持不变（不加 async 方法）；
> `AsyncAgentSessionProvider` 是独立 Protocol，旧 provider 无需改动，新 provider 实现两套 Protocol 即可。

#### PersistentAgentSessionProvider

```python
# src/agentos/channels/persistent_session.py（新文件）

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Callable
from threading import RLock

from agentos.channels.session import AgentSessionProvider, AsyncAgentSessionProvider
from agentos.persistence.base import SessionPersistence, SessionSnapshot
from agentos.runtime import Agent


class PersistentAgentSessionProvider:
    """跨节点无 sticky session 的 AgentSessionProvider。

    水合优先级：本节点 LRU 缓存 → 注入的 SessionPersistence → agent_factory 新建。
    release_agent 时通过 SessionPersistence 写回 snapshot。
    实现 AgentSessionProvider + AsyncAgentSessionProvider 两套 Protocol。
    B1 阶段仅依赖抽象 SessionPersistence，不感知 Redis / Postgres。
    """

    def __init__(
        self,
        *,
        agent_factory: Callable[[str], Agent],
        snapshot_to_agent: Callable[[str, SessionSnapshot], Agent],
        agent_to_snapshot: Callable[[str, Agent], SessionSnapshot],
        persistence: SessionPersistence,
        local_cache_max_size: int = 128,
        local_cache_ttl_seconds: float = 300.0,
    ) -> None: ...

    # --- AgentSessionProvider (sync) ---

    def get_agent(self, session_id: str) -> Agent: ...
    def release_agent(self, session_id: str, agent: Agent) -> None: ...

    # --- AsyncAgentSessionProvider (async) ---

    async def async_get_agent(self, session_id: str) -> Agent: ...
    async def async_release_agent(self, session_id: str, agent: Agent) -> None: ...

    # --- 内部 ---

    def _local_get(self, session_id: str) -> Agent | None: ...
    def _local_put(self, session_id: str, agent: Agent) -> None: ...
    def _evict_expired(self) -> None:
        """清理本地缓存中 TTL 过期的条目，参考 rate_limit._evict 模式。"""
        ...

    def shutdown(self) -> None:
        """lifespan shutdown 时被 AsgiAgentApp 鸭子调用，flush 所有缓存 agent 回 persistence。"""
        ...
```

**关键字段**（`__init__` 内部状态）：

| 字段 | 类型 | 说明 |
|------|------|------|
| `_cache` | `OrderedDict[str, tuple[Agent, float]]` | LRU：value = (agent, insert_timestamp) |
| `_lock` | `RLock` | 保护 `_cache` 的同步访问 |
| `_agent_factory` | `Callable[[str], Agent]` | 全新 session 时回退创建 |
| `_snapshot_to_agent` | `Callable[[str, SessionSnapshot], Agent]` | 从 snapshot 水合 Agent |
| `_agent_to_snapshot` | `Callable[[str, Agent], SessionSnapshot]` | 把 Agent 状态序列化为 snapshot |

**水合顺序**（`get_agent` / `async_get_agent`）：

```text
1. _local_get(session_id)              → hit: 返回
2. persistence.load(session_id)        → hit: snapshot_to_agent → _local_put → 返回
3. agent_factory(session_id)           → 新建 → _local_put → 返回
```

序列化使用现有 `persistence/serializers.py` 的 `session_snapshot_to_dict` / `session_snapshot_from_dict`，不新增序列化逻辑。

**写回顺序**（`release_agent` / `async_release_agent`）：

```text
1. agent_to_snapshot(session_id, agent)
2. persistence.save(snapshot)              # 写入注入的 SessionPersistence
3. _local_put(session_id, agent)            # 更新 LRU 时间戳
```

### File Change Map（B1）

| 文件 | 改动类型 | 说明 |
|------|----------|------|
| `src/agentos/channels/session.py` | 新增 | 末尾追加 `AsyncAgentSessionProvider` Protocol（约 +12 行） |
| `src/agentos/channels/persistent_session.py` | 新建 | `PersistentAgentSessionProvider` 完整实现（~100 行） |
| `src/agentos/channels/__init__.py` | 新增导出 | 暴露 `PersistentAgentSessionProvider`, `AsyncAgentSessionProvider` |
| `src/agentos/channels/asgi.py:get_agent` | 修改 | 将 `isinstance` 检测改为 `getattr(self._sessions, "async_get_agent", None)` |

> 不动：`persistence/base.py`、`persistence/serializers.py`、`memory/types.py`、Redis/Postgres 具体实现。

### Acceptance Criteria（B1）

**AC-B1-1：本地缓存 + persistence 水合**

```
- 构造 PersistentAgentSessionProvider，注入 MemoryPersistence（或 FileSystemPersistence）。
- provider.get_agent("s1") → 发送若干轮消息 → provider.release_agent("s1", agent)。
- 清空本地缓存后再次 provider.get_agent("s1")：
    - 断言 persistence.load 被调用。
    - 断言恢复的 agent message_runtime 历史与 release 前一致。
    - 断言恢复的 agent compression_index segments 完整。
```

**AC-B1-2：LRU/TTL 本地缓存驱逐**

```
- local_cache_max_size=2 的 provider，依次 get_agent("a"), "b", "c"：
    - 断言 "a" 被驱逐（LRU 最旧）。
- local_cache_ttl_seconds=0.1，get_agent("x") → 等待 0.2s → 再次 get_agent("x")：
    - 断言第二次触发 persistence.load（TTL 过期）。
```

**AC-B1-3：async 路径检测（getattr）**

```
- InMemoryAgentSessionProvider 实例：
    - getattr(provider, "async_get_agent", None) 为 None。
    - AsgiAgentApp 走 asyncio.to_thread 回退路径（不抛 TypeError）。
- PersistentAgentSessionProvider 实例：
    - getattr(provider, "async_get_agent", None) 不为 None。
    - AsgiAgentApp 直接调用 async_get_agent。
```

**AC-B1-4：回归**

```
- InMemoryAgentSessionProvider 行为不变。
- JSON turn、health、/ready 路径行为不变。
```

---

## B2 — Redis Snapshot 热缓存（依赖 B1）

**Goal**：为 `RedisHotSessionStore` 新增 `{prefix}:snapshot:{id}` 命名空间的 snapshot load/save/delete，作为 B1 `SessionPersistence` seam 的 Redis 实现，可注入 `PersistentAgentSessionProvider`。

> **依赖**：B1 的 `SessionPersistence` 抽象 seam 必须先完成。B2 不依赖 B3。

### Contracts

```python
# src/agentos/memory/redis_store.py

class RedisHotSessionStore:
    # 现有方法保持不变 ...

    def load_session_snapshot(self, session_id: str) -> bytes | None:
        """读取序列化的 SessionSnapshot bytes；未命中返回 None。"""
        # key: {prefix}:snapshot:{session_id}
        ...

    def save_session_snapshot(self, session_id: str, data: bytes, ttl_seconds: int | None = None) -> None:
        """保存序列化的 SessionSnapshot bytes。"""
        # key: {prefix}:snapshot:{session_id}，可独立 TTL
        ...

    def delete_session_snapshot(self, session_id: str) -> None:
        """删除热缓存中的 SessionSnapshot。"""
        ...
```

snapshot bytes = `json.dumps(session_snapshot_to_dict(snapshot)).encode("utf-8")`，使用现有 `persistence/serializers.py` 序列化，**不新增序列化逻辑**。

`RedisHotSessionStore` 同时实现 `SessionPersistence` Protocol（`save` / `load` / `list_ids` / `delete`），以便直接注入 `PersistentAgentSessionProvider`。

### File Change Map（B2）

| 文件 | 改动类型 | 说明 |
|------|----------|------|
| `src/agentos/memory/redis_store.py` | 新增方法 | `load_session_snapshot`, `save_session_snapshot`, `delete_session_snapshot`（约 +40 行） |
| `src/agentos/memory/redis_store.py` | 新增 | 实现 `SessionPersistence` Protocol 的 `save`/`load`/`list_ids`/`delete` 方法 |
| `pyproject.toml` | 确认 | `redis` extra 已有，确认覆盖范围 |

> 不动：`memory/types.py`（`HotSessionState` 不变）、已有 hot_state 方法、Postgres 实现。

### Acceptance Criteria（B2）

**AC-B2-1：跨节点 Redis 水合**

```
- 构造两个 PersistentAgentSessionProvider（node_a, node_b），
  均注入同一 RedisHotSessionStore 实例（fake 或真 Redis）。
- node_a.get_agent("s1") → 发送若干轮消息 → node_a.release_agent("s1", agent)。
- node_b.get_agent("s1")：
    - 断言本节点本地缓存 miss。
    - 断言 load_session_snapshot 被调用并命中。
    - 断言恢复的 agent message_runtime 历史与 node_a 完成轮次后一致。
    - 断言恢复的 agent compression_index segments 完整。
```

**AC-B2-2：key 命名空间隔离**

```
- save_session_snapshot("s1", data) 写入的 key 为 {prefix}:snapshot:s1。
- 断言不影响 {prefix}:hot:{s1} 等已有 key（HotSessionState 路径不变）。
```

---

## B3 — Postgres Snapshot 冷存储（依赖 B1，OPT-IN）

**Goal**：为 `PostgresDurableSessionStore` 新增 `agentos_snapshots` 表及 save/load 方法，作为 B1 `SessionPersistence` seam 的 Postgres 实现。**此路径为 OPT-IN，默认不启用**，由调用方显式选择注入。

> **依赖**：B1 的 `SessionPersistence` 抽象 seam 必须先完成。B3 不依赖 B2，可与 B2 并行开发。

> ⚠ **Risk — 双写/一致性**：`agentos_snapshots` 整体 snapshot 与现有分散表（`agentos_sessions`、`agentos_messages`、refs、compressed segments）**并列存在，两条路径互相不更新**。若调用方同时写两条路径，可能产生数据不一致。此 snapshot 路径应视为"粗粒度快照通道"，与"细粒度增量通道"互斥选用，不混用。此风险是 OPT-IN 设计的主要原因——只有显式注入 `PostgresDurableSessionStore` 作为 `persistence` 时，snapshot 表才会被写入。

### Contracts

```python
# src/agentos/persistence/postgres.py

class PostgresDurableSessionStore:
    # 现有方法保持不变 ...

    def save_snapshot(self, snapshot: SessionSnapshot) -> None:
        """将整体 SessionSnapshot 序列化后保存到 agentos_snapshots 表。"""
        # UPSERT agentos_snapshots(session_id, payload, updated_at)

    def load_snapshot(self, session_id: str) -> SessionSnapshot:
        """从 agentos_snapshots 表恢复 SessionSnapshot；未找到 raise KeyError。"""
        # SELECT payload FROM agentos_snapshots WHERE session_id = %s
```

新表 DDL（迁移文件新增）：

```sql
CREATE TABLE IF NOT EXISTS agentos_snapshots (
    session_id  TEXT PRIMARY KEY,
    payload     JSONB NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

`PostgresDurableSessionStore` 同时实现 `SessionPersistence` Protocol，以便直接注入 `PersistentAgentSessionProvider`。

> ⚠ 假设：整体 snapshot 和现有分散存储（`agentos_sessions`, `agentos_messages` 等）并列存在，互不干扰。`save_snapshot` 不调用现有分散存储方法——两条路径由调用方选择使用哪一条。

### File Change Map（B3）

| 文件 | 改动类型 | 说明 |
|------|----------|------|
| `src/agentos/persistence/postgres.py` | 新增方法 | `save_snapshot`, `load_snapshot`（约 +45 行） |
| `src/agentos/persistence/postgres.py` | 新增 | 实现 `SessionPersistence` Protocol 的 `save`/`load`/`list_ids`/`delete` 方法 |
| `src/agentos/migrations/` | 新增迁移文件 | `agentos_snapshots` 表 DDL |
| `pyproject.toml` | 确认 | `postgres` extra 确认覆盖 `psycopg` |

> 不动：现有分散存储方法（`SessionState`、消息、refs、segments 路径）、Redis 实现。

### Acceptance Criteria（B3）

**AC-B3-1：Postgres 冷恢复**

```
- 构造 PersistentAgentSessionProvider，注入 PostgresDurableSessionStore（fake connection）。
- provider.get_agent("s2") → 发送若干轮消息 → provider.release_agent("s2", agent)。
- 清空本地缓存后再次 provider.get_agent("s2")：
    - 断言 postgres_store.load_snapshot 被调用。
    - 断言水合后历史完整。
```

**AC-B3-2：OPT-IN 隔离**

```
- 不注入 PostgresDurableSessionStore 时，agentos_snapshots 表从不被访问。
- 断言现有分散存储方法（agentos_messages 等）不受影响。
```

---

## B4 — ASGI Graceful Drain（独立，可任意顺序实施）

**Goal**：AsgiAgentApp 在 k8s 滚动重启时，阻塞 `lifespan.shutdown.complete` 直至所有 in-flight turns drain 完毕；drain 期间 `/ready` 返回 503。

> **依赖**：B4 独立于 B1–B3，可在任意阶段实施。

### Contracts

```python
# src/agentos/channels/asgi.py

class AsgiAgentApp:
    def __init__(self, ...) -> None:
        # 新增字段
        self._inflight_turns: int = 0
        self._inflight_lock: asyncio.Lock = asyncio.Lock()
        self._drain_event: asyncio.Event = asyncio.Event()
        self._draining: bool = False
        ...

    async def _handle_lifespan(self, receive, send) -> None:
        # lifespan.shutdown 改造：
        # 1. self._draining = True → /ready 开始返回 503
        # 2. 等待 self._drain_event（超时可配，默认 30s）
        # 3. 调用现有 shutdown_handlers + sessions.shutdown()
        # 4. send lifespan.shutdown.complete
        ...

    # _run_sse_turn / handle_turn 路径：
    # turn 开始时 _inflight_turns += 1，
    # 结束时 _inflight_turns -= 1，若 == 0 且 _draining → _drain_event.set()
```

**`/ready` 在 drain 期间返回 503**：`_handle_ready` 增加对 `self._draining` 的检查，返回 `{"status": "not_ready", "reason": "draining"}`。

**新增 `AsgiAgentApp.__init__` 参数**：

```python
shutdown_drain_timeout_seconds: float = 30.0,
```

### File Change Map（B4）

| 文件 | 改动类型 | 说明 |
|------|----------|------|
| `src/agentos/channels/asgi.py:32-69` | 修改 `__init__` | 新增 `_inflight_turns`, `_draining`, `_drain_event`, `shutdown_drain_timeout_seconds` 参数 |
| `src/agentos/channels/asgi.py:601-627` | 修改 `_handle_lifespan` | 增加 drain 等待逻辑 |
| `src/agentos/channels/asgi.py:629-648` | 修改 `_handle_ready` | drain 期间返回 503 |
| `src/agentos/channels/asgi.py:_run_sse_turn` | 修改 | turn 开始/结束各 +/- `_inflight_turns`，触发 `_drain_event` |

> 不动：`session.py`、`persistent_session.py`、Redis/Postgres 实现、JSON turn 端点、rate-limit。

### Acceptance Criteria（B4）

**AC-B4-1：滚动重启 drain**

```
- 启动一个 in-flight SSE turn（runner_task 未完成）。
- 触发 lifespan.shutdown。
- 断言 /ready 立即返回 503（draining=True）。
- 断言 lifespan.shutdown.complete 尚未发出（turn 仍在运行）。
- 完成 turn（runner_task 完成）。
- 断言 lifespan.shutdown.complete 在 drain_timeout 内发出。
```

**AC-B4-2：drain timeout 保护**

```
- in-flight turn 故意不结束，shutdown_drain_timeout_seconds=0.1。
- 触发 lifespan.shutdown → 断言 0.2s 内仍发出 lifespan.shutdown.complete（不阻塞无限）。
```

---

## Risks & Non-Goals

### Non-Goals（本 phase 不做）

- **auth / tenant 隔离**：session 可见性策略留在 `ChannelAuthPolicy` seam，`PersistentAgentSessionProvider` 不做多租户过滤。
- **分布式锁**：多节点同时水合同一 session_id，可能产生写写冲突。本 phase 依赖 persistence 实现的 last-write-wins 语义，不加分布式悲观锁。高一致性场景留给上层策略。
- **Postgres 异步驱动**：`async_get_agent` 内部对 Postgres 的调用仍走 `asyncio.to_thread`，不引入 `asyncpg`。
- **SSE buffer 跨节点 live tail**：已由 SSE Resume spec 声明为"不做跨节点 grace 协调"；本 phase 不改 SSE buffer 路径。
- **agent_factory 的 snapshot 恢复契约**：`snapshot_to_agent` / `agent_to_snapshot` 回调由 SDK 使用方实现，SDK 不强制规范其内部 `AgentBuilder` 组装细节——示例文档给出参考实现即可。

### Risks

- **`session_snapshot_to_dict` 序列化完整性**：`SessionSnapshot` 含 `CompressionIndex` 和 `MessageRuntime` 两个大结构，现有 `serializers.py` 已实现序列化，但未经跨进程 round-trip 测试。AC-B1-1 要求显式验证历史和 segments 完整性。
- **Postgres `agentos_snapshots` vs 现有分散表重复（B3 OPT-IN 原因）**：两套存储并列，可能导致数据不一致。此为 B3 设计为 OPT-IN 的核心原因；在实现注记中标明这是"粗粒度快照通道"，与"细粒度增量通道"并列，不相互更新。
- **本地缓存和 persistence 双写的缓存失效**：节点 A 写回后，节点 B 本地缓存仍存旧 agent。TTL 驱逐是唯一一致性机制，无主动失效通知。适合读多写少、TTL 内容忍短暂不一致的场景。

---

## 实现交接须知（给实现者）

- **先读 `AGENTS.md`**，命名/边界/typed event/test-first/完成度 checklist 一律遵守。
- **按 B1→B2→B3→B4 顺序交付**，每个子阶段独立可测试、可合并。
- **fake 实现**：B1 用 `MemoryPersistence`；B2 扩展 fake Redis client 的 `load/save_session_snapshot`；B3 扩展 fake Postgres connection 同理。
- **`persistent_session.py` 属于 `channel-remote` 边界**（AGENTS.md 分类），落在 `channels/` 下。
- **Keep diff surgical**：只改直接相关的；不顺手改相邻注释/格式；发现无关 dead code 在 report 里提一句，不自作主张删。
- **两个 Protocol 共用同一套契约测试**：`InMemory` 和 `Persistent` 都应能跑 AC-B1-4 回归断言。
- **extras 声明**：`persistent_session.py` 顶部用 `TYPE_CHECKING` guard，运行时懒加载，缺失 extra 给出清晰 `RuntimeError`（参考 `redis_store.py:35-44` 模式）。
- **async 路径检测统一用 `getattr`**：`getattr(self._sessions, "async_get_agent", None)` 而非 `isinstance`。
