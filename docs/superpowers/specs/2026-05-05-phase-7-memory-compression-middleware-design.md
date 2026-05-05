---
name: agentos phase 7 memory compression middleware design
description: 设计 Phase 7 第一块：memory recall、compression 副产物和生产级中间件存储边界。
type: design-spec
status: draft
date: 2026-05-05
relates_to:
  - AGENTS.md
  - docs/design/sdk-architecture.md
  - docs/design/llm-context-only-example.md
  - ../ai-knowledge/wiki/context-management.md
  - ../ai-knowledge/wiki/memory-system.md
  - ../ai-knowledge/wiki/session-recovery.md
  - ../ai-knowledge/wiki/runtime-state.md
---

# Phase 7 Memory Recall + Compression Middleware 设计

## 背景

Phase 2 已经实现了 context compression 和 handle recall 的最小闭环：

```text
CompressionRuntime.maybe_compress()
  -> CompressedSegment(seg_1)
  -> ContextState.compressed_history
  -> CompressionIndex(seg_1 -> message_ids)
  -> ActiveWindow 移除旧 refs

recall_context(handle="seg_1")
  -> CompressionIndex 查 source refs
  -> MessageStore 读取原文
  -> inject_temporary_recalled(...)
```

这个链路保证了被压缩原文没有丢失，但它只适合本地内存和精确 handle 召回。Web agent 运行在多 node 环境时，同一个 session 的下一次请求可能落到另一个节点，不能依赖进程内 dict。用户也不一定知道该召回 `seg_1` 还是 `seg_3`，因此需要基于 query 的 memory recall。

Phase 7 第一块要把现有 compression/recall 升级为生产级存储与检索链路：压缩不仅生成 LLM 可见摘要，还生成 source refs 和 recall document。Redis 保存活跃 session 的热点原文工作集，durable store 保存长期真值源，Qdrant 保存可检索 recall index。默认 prompt 仍只展示 compressed history 摘要，不展示 runtime metadata。

## Scope Contract

本设计属于 Phase 7 `memory-system` 的第一块，同时改造 Phase 2 compression/recall 的存储边界。

本设计要完成的验收项：

- 压缩产物从单个 `CompressedSegment` 升级为 `CompressedSegmentPackage`，包含 LLM 可见摘要、source refs 和 `SegmentRecallDocument`。
- `recall_context` 同时支持 `handle` 精确召回和 `query` 语义召回。
- Web/production profile 使用 Redis 保存活跃 session 热点原文和 active refs，支持多 node 读取同一 session 工作集。
- Web/production profile 使用 durable store 保存 sessions、turns、messages、compressed segments 和 segment refs 的长期真值源。
- Web/production profile 使用 Qdrant 保存 segment-level recall document embedding，用于 query -> candidate segment id。
- 存储与检索通过 ABC 注入，runtime 主链不直接依赖 Redis、Qdrant、Postgres SDK。
- JSON/FileSystem persistence 保留为 CLI/local profile，文档和命名明确不把它作为 Web/production 默认方案。
- 默认 LLM-visible prompt 仍不出现 session id、message id、segment refs、embedding score、storage backend、trace id 等 runtime metadata。

本设计明确不做：

- 不在第一版实现图片、截图或多模态 embedding。Phase 7 第一块只处理文本 recall。
- 不实现 subagent orchestration、AgentCard 或 AgentRegistry。它们属于 Phase 7 第二块。
- 不实现 HTTP channel、A2A、远程 resolver、eval runner 或 finetuning exporter。它们属于 Phase 8 或后续。
- 不把 Qdrant 作为原文消息真值源。Qdrant 只保存 embedding 和 pointer metadata。
- 不把 Redis 作为唯一真值源。Redis 可以保存热点原文，但长期恢复和审计依赖 durable store。
- 不在 core import 时强制安装 Redis、Qdrant 或 Postgres client。中间件实现通过 extras 提供。

不能被简化掉的规则：

- 被压缩原文必须可以通过 `segment_id -> message refs -> original messages` 恢复。
- query recall 必须先命中 segment，再通过 segment refs 拉回原文，不能把 Qdrant payload 当成恢复原文。
- compression 写入必须保证可召回性先建立，再移除 active refs。
- `runtime/query_loop.py` 仍只负责 turn 调度，不直接写 Redis/Qdrant/Postgres。
- `context/renderer.py` 只渲染 LLM 可见上下文，不读取 memory index 或中间件状态。

## 当前实现评估

当前实现的真值源和索引关系是：

```text
MessageStore
  append-only 原始消息，进程内 list

ActiveWindow
  当前 provider request 可见 refs，进程内 list

ContextState.compressed_history
  LLM 可见 compressed segment 摘要

CompressionIndex
  seg_id -> message_ids，进程内 dict

SessionSnapshot
  message_runtime + compression_index + context_state 的完整快照
```

这适合单进程测试和 CLI demo，但不适合 Web agent：

- 进程内 `MessageStore` 和 `CompressionIndex` 不能跨 node 共享。
- JSON `FileSystemPersistence` 是本地 CLI 友好的恢复格式，不适合多用户 Web 服务。
- 现有 `recall_context` 只能按 handle 找 segment，不能按自然语言 query 找相关压缩片段。
- compression 只生成 prompt summary，没有生成专门面向检索的 recall document。

因此，Phase 7 不直接删除这些实现，而是将它们降级为 `local/cli` 和 unit test profile，并新增生产级 ABC 与默认中间件 profile。

## 核心概念

### CompressedSegmentPackage

一次 compression 的完整产物：

```python
@dataclass(frozen=True, slots=True)
class CompressedSegmentPackage:
    segment: CompressedSegment
    source_refs: tuple[str, ...]
    recall_document: SegmentRecallDocument
```

三类字段分别服务不同对象：

- `segment`：LLM 可见摘要，进入 `# Compressed History`。
- `source_refs`：SDK 内部恢复原文使用，不能进入默认 prompt。
- `recall_document`：给 Qdrant embedding 和 query recall 使用，不能进入默认 prompt。

### SegmentRecallDocument

`SegmentRecallDocument` 是 compression 的检索副产物，不是原文存储：

```python
@dataclass(frozen=True, slots=True)
class SegmentRecallDocument:
    session_id: str
    segment_id: str
    topic: str
    summary: str
    keywords: tuple[str, ...]
    tool_hints: tuple[str, ...]
    searchable_text: str
```

`searchable_text` 由 topic、summary、关键词、文件名、工具名、关键参数摘要和短实体组成。它可以比 LLM 可见摘要更适合检索，但不能包含需要作为真值源保存的完整原文。

第一版不做 LLM 关键词抽取依赖。默认实现使用规则提取：

- 文件路径和扩展名，例如 `pyproject.toml`、`src/agentos/...`。
- tool name，例如 `read_file`、`run_shell`。
- 常见业务结构化 key，例如 `project.name`、`build-system`、`requires-python`。
- 源消息中的短 token、代码标识符和中英文关键词。

后续可以替换为 LLM 或 embedding-model-assisted extractor，但 extractor 仍在 compression/memory 边界内，不进入 runtime 主链。

### Recall Candidate

query recall 不直接返回消息，而是返回候选 segment：

```python
@dataclass(frozen=True, slots=True)
class RecallCandidate:
    session_id: str
    segment_id: str
    score: float | None
    reason: str | None = None
```

`score` 和 `reason` 只用于 debug projection、observability 或排序，不进入默认 prompt。

## 存储 Profile

### Local / CLI Profile

适用范围：

- 本地 CLI agent。
- 单用户 demo。
- 单元测试和集成测试。

默认组件：

```text
FileSystemSessionStore(JSON)
InMemoryHotSessionStore
InMemoryRecallIndex
```

约束：

- JSON 文件可读、易调试、易迁移，但不作为 Web/production 推荐。
- In-memory 实现只用于测试或本地单进程 profile。
- local profile 仍必须遵守 prompt 不泄露 runtime metadata 的规则。

### Web / Production Profile

适用范围：

- Web agent。
- 多用户、多 node、横向扩展部署。
- 需要恢复、审计和长期 recall 的生产服务。

默认组件：

```text
RedisHotSessionStore
PostgresDurableSessionStore
QdrantRecallIndex
```

职责划分：

```text
Redis
  活跃 session 热点原文
  active window refs
  temporary recalled refs
  最近 N 轮 messages
  segment refs 热数据
  sliding TTL 和多 node session 工作集共享

Postgres
  sessions
  turns
  messages
  active refs checkpoint
  compressed segments
  segment refs
  durable event/checkpoint metadata

Qdrant
  segment recall document embedding
  session_id / segment_id pointer payload
  query -> candidate segment_id
```

Redis 可以保存热点原文。它的定位不是“只缓存 refs”，而是活跃 session 的共享工作集。区别在于 Redis 数据有 TTL，且不承担长期真值源；Redis miss 后必须能从 Postgres 恢复。

## ABC 边界

### HotSessionStore

`HotSessionStore` 面向低延迟活跃 session 工作集，默认生产实现是 Redis：

```python
class HotSessionStore(Protocol):
    def load_hot_state(self, session_id: str) -> HotSessionState | None: ...
    def save_hot_state(self, state: HotSessionState) -> None: ...
    def append_hot_message(self, session_id: str, message: Message) -> None: ...
    def get_hot_messages(self, session_id: str, message_ids: Sequence[str]) -> list[Message] | None: ...
    def save_segment_refs(self, session_id: str, segment_id: str, message_ids: Sequence[str]) -> None: ...
    def get_segment_refs(self, session_id: str, segment_id: str) -> tuple[str, ...] | None: ...
    def set_temporary_recalled_refs(self, session_id: str, message_ids: Sequence[str]) -> None: ...
    def consume_temporary_recalled_refs(self, session_id: str) -> tuple[str, ...]: ...
```

`HotSessionState` 包含：

- `session_id`
- `active_refs`
- `recent_messages`
- `temporary_recalled_refs`
- `segment_refs`
- `expires_at` 或 TTL policy metadata

Redis 实现要求：

- 用 session-scoped key，避免跨用户污染。
- 使用 TTL 或 sliding TTL。
- 保存消息时保持 role/tool_call_id/tool_calls 结构完整。
- temporary recalled refs 必须一次性消费，消费后删除。

### DurableSessionStore

`DurableSessionStore` 是长期真值源，默认生产实现是 Postgres：

```python
class DurableSessionStore(Protocol):
    def save_session(self, session: SessionState) -> None: ...
    def load_session(self, session_id: str) -> SessionState: ...
    def append_message(self, session_id: str, message: Message) -> None: ...
    def get_messages(self, session_id: str, message_ids: Sequence[str]) -> list[Message]: ...
    def save_active_refs(self, session_id: str, refs: Sequence[MessageRef]) -> None: ...
    def load_active_refs(self, session_id: str) -> tuple[MessageRef, ...]: ...
    def save_compressed_segment(
        self,
        session_id: str,
        package: CompressedSegmentPackage,
    ) -> None: ...
    def get_segment_refs(self, session_id: str, segment_id: str) -> tuple[str, ...]: ...
    def list_compressed_segments(self, session_id: str) -> tuple[CompressedSegment, ...]: ...
```

Postgres 实现要求：

- messages append-only，不因 compression 删除原文。
- compressed segment 和 segment refs 在一个事务中保存。
- active refs checkpoint 可以覆盖保存，但原始 messages 不覆盖。
- schema 版本显式记录，后续 migration 不破坏旧 session。

### RecallIndex

`RecallIndex` 是 query recall 的检索目录，默认生产实现是 Qdrant：

```python
class RecallIndex(Protocol):
    def index_segment(self, document: SegmentRecallDocument) -> None: ...
    def search_segments(
        self,
        session_id: str,
        query: str,
        limit: int,
    ) -> tuple[RecallCandidate, ...]: ...
    def delete_session(self, session_id: str) -> None: ...
```

Qdrant point 设计：

```text
point_id = "{session_id}:{segment_id}:text"
vector = embed(recall_document_text)
payload = {
  "session_id": "...",
  "segment_id": "seg_3",
  "kind": "compressed_segment",
  "modality": "text",
  "topic": "...",
}
```

`recall_document_text` 由以下部分拼接：

```text
topic: ...
summary: ...
keywords: ...
tool_hints: ...
searchable_text: ...
```

Qdrant payload 不保存完整原文消息。payload 只保存 pointer metadata 和少量便于过滤的字段。

### EmbeddingProvider

第一版只定义文本 embedding：

```python
class TextEmbeddingProvider(Protocol):
    def embed_text(self, text: str) -> list[float]: ...
```

Qdrant adapter 依赖 `TextEmbeddingProvider`，不在 core 中绑定具体 embedding 服务。默认 production profile 可以提供一个 OpenAI-compatible embedding adapter，但它属于 optional dependency 或示例配置，不是 core 必需依赖。

## Runtime 组件调整

### CompressionRuntime

`CompressionRuntime` 仍负责压缩调度，但不直接依赖 Redis/Qdrant/Postgres SDK。它新增可选的 memory sink 边界：

```python
class CompressionMemorySink(Protocol):
    def record_compressed_segment(
        self,
        package: CompressedSegmentPackage,
    ) -> None: ...
```

压缩流程调整为：

```text
select active message refs
read original messages
compress to package
append visible segment to ContextRuntime
record package through CompressionMemorySink
remove selected refs from ActiveWindow
emit CompressionCompletedEvent
```

顺序要求：

- `append visible segment` 和 `record package` 都成功后，才能移除 active refs。
- 如果 sink 写入失败，compression 本次失败或跳过，不移除 active refs。
- 如果 context append 失败，不写 sink，不移除 active refs。
- 如果 active refs 移除失败，sink 里可能已写入 segment；下一次压缩遇到同 segment id 必须幂等处理或保持 segment cursor 不前进。实现计划中需要用测试固定具体行为。

### Compressor

`Compressor` 协议从返回 `CompressedSegment` 升级为返回 `CompressedSegmentPackage`，或者新增过渡协议 `PackageCompressor`。为了减少破坏面，第一版建议：

```python
class PackageCompressor(Protocol):
    def compress_package(
        self,
        segment_id: str,
        session_id: str,
        messages: Sequence[Message],
    ) -> CompressedSegmentPackage: ...
```

`RuleBasedCompressor` 可以保留旧 `compress()`，同时实现 `compress_package()`。这样现有测试可以渐进迁移。

### MemoryRuntime

`MemoryRuntime` 是 Phase 7 memory recall 的协调器：

```python
@dataclass(slots=True)
class MemoryRuntime:
    hot_store: HotSessionStore
    durable_store: DurableSessionStore
    recall_index: RecallIndex

    def record_compressed_segment(self, package: CompressedSegmentPackage) -> None: ...
    def recall_by_handle(self, session_id: str, handle: str) -> list[Message]: ...
    def recall_by_query(self, session_id: str, query: str, limit: int) -> list[Message]: ...
```

`MemoryRuntime` 不渲染 prompt，不调用 provider，不执行工具。它只负责：

- 保存 compression 副产物。
- 根据 handle/query 找 segment。
- 根据 segment refs 拉回原文。
- 将必要的 temporary recalled refs 写回 hot state 或交给 `MessageRuntime` 注入。

### RecallRuntime

`RecallRuntime` 继续执行 `recall_context`，但底层从只依赖 `CompressionIndex` 扩展为可选依赖 `MemoryRuntime`：

```text
handle recall
  local profile: CompressionIndex -> MessageRuntime
  production profile: MemoryRuntime.recall_by_handle(...)

query recall
  requires MemoryRuntime
  RecallIndex.search_segments(...)
  HotSessionStore / DurableSessionStore get messages
```

第一版兼容规则：

- 如果只有 `CompressionIndex`，仍支持旧的 `handle` recall。
- 如果调用 `query` 但未配置 `MemoryRuntime`，抛出清晰错误。
- 如果 `handle` 和 `query` 同时提供，优先拒绝，要求二选一。

### Context Protocol Tool Schema

`recall_context` schema 从只接受 `handle` 扩展为二选一：

```json
{
  "handle": "seg_3"
}
```

或：

```json
{
  "query": "之前讨论 pyproject 项目名的上下文",
  "limit": 2
}
```

tool description 调整为：

```text
当压缩摘要不够时，按 handle 恢复指定压缩片段，或按 query 检索并恢复相关压缩片段。
```

默认 LLM-visible context rules 仍强调：

- compressed history 是有损摘要。
- 需要原文细节时调用 `recall_context`。
- 不要在 assistant 文本中手写内部 refs 或 metadata。

## 数据流

### 压缩写入链路

```text
QueryLoop.build_request()
  -> CompressionRuntime.maybe_compress()
     -> MessageRuntime.materialize_active()
     -> Evictor.select_message_ids()
     -> MessageStore / HotSessionStore 读取原文
     -> PackageCompressor.compress_package()
     -> ContextRuntime.append_compressed_segment(package.segment)
     -> MemoryRuntime.record_compressed_segment(package)
        -> HotSessionStore.save_segment_refs()
        -> DurableSessionStore.save_compressed_segment()
        -> RecallIndex.index_segment()
     -> ActiveWindow.remove_refs()
     -> HotSessionStore.save_hot_state()
     -> DurableSessionStore.save_active_refs()
```

实现时可以先做同步写入。异步 write-behind 可以作为后续性能优化，但第一版不得牺牲可召回性。

### handle 召回链路

```text
Provider tool call: recall_context(handle="seg_3")
  -> ToolCallRouter
  -> RecallRuntime
  -> MemoryRuntime.recall_by_handle(session_id, "seg_3")
     -> HotSessionStore.get_segment_refs()
     -> HotSessionStore.get_hot_messages()
     -> miss: DurableSessionStore.get_segment_refs()
     -> miss: DurableSessionStore.get_messages()
  -> MessageRuntime.inject_temporary_recalled(message_ids)
  -> next ProviderRequest includes recalled original messages once
```

### query 召回链路

```text
Provider tool call: recall_context(query="之前 pyproject 项目名")
  -> ToolCallRouter
  -> RecallRuntime
  -> MemoryRuntime.recall_by_query(session_id, query, limit)
     -> RecallIndex.search_segments(session_id, query, limit)
     -> for each candidate segment:
          HotSessionStore / DurableSessionStore get refs + messages
  -> MessageRuntime.inject_temporary_recalled(message_ids)
  -> next ProviderRequest includes recalled original messages once
```

query recall 返回给 tool result 的文本只说明召回了多少消息或哪些 segment handle，不把完整原文复制进 tool result。原文通过 temporary recalled messages 进入下一次 provider request。

## 一致性与失败策略

### 写入顺序

压缩成功的最低标准是：

```text
visible segment written
segment refs durable
recall index written or explicitly degraded
active refs removed
```

推荐第一版采用强一致写入：`MemoryRuntime.record_compressed_segment()` 失败则本次 compression 失败，不移除 active refs。这样最安全，代价是 Qdrant 短暂故障会阻止压缩。

可选降级策略：

- 如果 durable store 成功但 Qdrant 失败，可以保留 handle recall，标记 query recall index pending。
- pending index 必须有后台补偿或下次启动重建机制。
- 第一版若没有后台 worker，不启用这个降级；直接失败更可控。

### Redis Miss

Redis miss 是正常路径：

```text
Redis miss
  -> load from durable store
  -> hydrate hot state
  -> continue request
```

如果 durable store 也 miss，说明 session 或 segment 不存在，`recall_context` 应返回明确错误并发出 typed event。

### Qdrant Miss

Qdrant miss 不代表历史不存在，只代表 query 没找到候选 segment。处理方式：

- tool result 告知没有找到相关 compressed segment。
- 不注入 temporary messages。
- observability 记录 search miss。

### Tool Pair 完整性

所有 recall 注入必须保留 assistant tool call 与 tool result 配对。候选 segment 的 source refs 来自 compression 阶段，compression evictor 已保护 pair 边界；恢复时仍要用测试覆盖：

- segment refs 中包含 assistant tool call 时，必须包含对应 tool result。
- 注入顺序必须保持原始消息顺序。

## 安全与隐私

- 默认 prompt 只展示 `CompressedSegment` 的 `id/topic/summary`。
- `source_refs`、`message_ids`、`session_id`、Redis key、Qdrant score 不进入默认 prompt。
- Qdrant payload 不保存完整原文，避免向量库成为第二套明文消息库。
- Redis 保存热点原文时必须被视为敏感数据存储，生产部署需要 ACL、TLS、加密和 TTL。
- Postgres 是长期真值源，生产部署需要按用户/租户隔离和备份策略。
- debug projection 可以显式展示 segment refs 和 recall scores，但必须由用户主动调用。
- observability 捕获 recall query、candidate count、hit/miss 和 latency；是否捕获原文仍由 capture policy 控制。

## Optional Dependencies

建议 extras：

```toml
[project.optional-dependencies]
redis = ["redis>=5.0"]
qdrant = ["qdrant-client>=1.9"]
postgres = ["psycopg[binary]>=3.1"]
production-memory = [
  "redis>=5.0",
  "qdrant-client>=1.9",
  "psycopg[binary]>=3.1",
]
```

core `agentos` 不依赖这些包。`agentos.memory` 可以导出 ABC 和 in-memory fake；具体中间件 adapter 放在不影响 core import 的模块中，并在缺依赖时给出清晰错误。

## 模块结构

新增或调整：

```text
agentos/
  memory/
    __init__.py
    types.py                 # CompressedSegmentPackage, SegmentRecallDocument, RecallCandidate
    runtime.py               # MemoryRuntime
    store.py                 # HotSessionStore, DurableSessionStore protocols
    recall_index.py          # RecallIndex protocol
    embeddings.py            # TextEmbeddingProvider protocol
    in_memory.py             # unit test/local fake
    redis_store.py           # RedisHotSessionStore optional adapter
    qdrant_index.py          # QdrantRecallIndex optional adapter

  persistence/
    postgres.py              # PostgresDurableSessionStore optional adapter
    filesystem.py            # 标注为 local/cli profile
    memory.py                # 标注为 test/local fake

  compression/
    compressor.py            # PackageCompressor
    runtime.py               # CompressionMemorySink integration

  recall/
    runtime.py               # handle/query recall

  context_protocol.py        # recall_context schema 扩展
```

命名原则：

- `MemoryRuntime` 可以使用 `Runtime`，因为它长期持有 memory/recall 存储边界并协调该领域生命周期。
- `HotSessionStore` 和 `DurableSessionStore` 使用 `Store`，因为它们是存储边界，不是业务 orchestrator。
- `RecallIndex` 使用 `Index`，因为它负责 query 到 segment candidate 的检索目录。

## 测试策略

### 单元测试

- `CompressedSegmentPackage` 能从规则 compressor 生成。
- `SegmentRecallDocument` 不包含完整原文，但包含 topic、summary、keywords 和 tool hints。
- `MemoryRuntime.record_compressed_segment()` 写 hot store、durable store 和 recall index。
- `MemoryRuntime.recall_by_handle()` 优先读 hot store，miss 后读 durable store。
- `MemoryRuntime.recall_by_query()` 通过 recall index 找 candidate，再拉原文。
- `RecallRuntime` 支持 `handle` 和 `query`，且拒绝二者同时出现。
- `recall_context` tool schema 支持 `handle` 或 `query`。
- Redis/Qdrant/Postgres adapter 缺依赖时不影响 core import。

### 集成测试

- 压缩后 active window 移除旧 refs，但 hot/durable store 可恢复原文。
- query recall 恢复原文后，下一次 provider request 只注入一次 recalled messages。
- session recovery 后，compressed history、segment refs 和 recall index 仍能支撑 handle recall。
- Redis miss 后从 durable store hydrate hot state。
- Qdrant miss 返回空召回，不破坏当前 active window。
- tool use/tool result pair 压缩后仍能完整召回。

### 架构测试

- `runtime/` 不 import Redis、Qdrant、Postgres SDK。
- `context/renderer.py` 不 import `agentos.memory`。
- 默认 prompt 不包含 `message_id`、`session_id`、`source_refs`、`score`、`qdrant`、`redis`、`postgres`。
- core import 在未安装 optional dependencies 时通过。
- public API 导出 Phase 7 必需 ABC 和类型名。

## 验收清单

| 设计要求 | 实现文件 | 测试或验证 | 状态 |
|---|---|---|---|
| Compression 生成 package | `compression/compressor.py`, `memory/types.py` | package compressor unit tests | 待实现 |
| 压缩后写 memory sink 再移除 active refs | `compression/runtime.py`, `memory/runtime.py` | compression-memory integration tests | 待实现 |
| Redis 保存活跃 session 热点原文 | `memory/redis_store.py` | adapter contract tests 或 fake tests | 待实现 |
| Durable store 保存长期真值源 | `persistence/postgres.py` | repository contract tests | 待实现 |
| Qdrant 保存 recall document embedding | `memory/qdrant_index.py` | recall index contract tests | 待实现 |
| `recall_context(query=...)` 支持语义召回 | `recall/runtime.py`, `context_protocol.py` | recall query tests | 待实现 |
| 默认 prompt 不泄露 runtime metadata | `context/renderer.py` | golden tests / forbidden term search | 待实现 |
| local/cli profile 继续可用 | `persistence/filesystem.py`, `memory/in_memory.py` | existing session recovery tests | 待实现 |

## 迁移策略

第一步：新增类型和 ABC，不改变现有 public API。

第二步：让 `RuleBasedCompressor` 支持 `compress_package()`，并让 `CompressionRuntime` 在配置了 memory sink 时写入 package。未配置 sink 时保留当前 handle recall 行为。

第三步：扩展 `RecallRuntime` 和 `recall_context` schema，支持 query recall。旧的 `handle` recall 测试必须继续通过。

第四步：新增 in-memory fake、Redis hot store、Qdrant recall index 和 Postgres durable store。生产 adapter 测试优先用 contract fake；真实中间件可以放 integration smoke tests。

第五步：更新文档，明确：

- JSON/FileSystem 是 local/cli profile。
- Redis 是 Web 活跃 session 热点原文工作集。
- Postgres 是 durable truth。
- Qdrant 是 recall index。

## 后续 Phase

Phase 7 第二块继续做：

- AgentCard。
- AgentRegistry。
- AgentResolver。
- Subagent orchestration。

这些能力可以复用本设计的 session/memory 存储边界，但不得让 subagent 直接读写主 agent working state。主 agent 只能通过 tool result 吸收 subagent 发现。

Phase 8 或后续再做：

- HTTP/SSE channel 的 session hydration。
- 远程 agent resolver 和 A2A。
- 多模态 recall。
- recall 质量 eval。
- background reindex 和 failed index compensation worker。
