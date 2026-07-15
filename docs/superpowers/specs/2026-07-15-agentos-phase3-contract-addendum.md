# AgentOS Phase 3 Contract Addendum

> 状态：待用户批准
>
> 日期：2026-07-15
>
> 上位规范：`2026-07-10-agentos-next-generation-sdk-architecture-design.md`、
> `2026-07-10-agentos-context-protocol-v1-design.md`

## 1. 目的

本文只冻结 Phase 3A、3B、3C 并行实施前缺失的共享契约，不改变已经批准的
Context Protocol v1、StoredMessage/ProviderInputItem 边界、单一异步 QueryLoop、
WAITING 或 Tool Scheduler 语义。

Phase 3 仍按以下拓扑执行：

```text
Shared Contract Preflight
        |
        +----------+-----------+
        |          |           |
  Artifact 3A  Provider 3B  Extension 3C
        |          |           |
        +----------+-----------+
                   |
              Phase 4 集成
```

共享 Preflight 由 Architecture Owner 串行完成。完成后 3A、3B、3C 才允许在互不
重叠的文件 Owner 边界内并行。

## 2. Provider-Neutral Binary Content

### 2.1 冻结类型

当前 `ImagePart/FilePart` 的 `attachment: object` 允许本地路径、URL、Provider
File ID 和旧 Attachment 对象穿透 Provider 边界，必须在并行开发前替换。

```python
@dataclass(frozen=True, slots=True)
class ProviderBinaryPayload:
    handle: str
    media_type: str
    data: bytes = field(repr=False)
    filename: str | None = None


@dataclass(frozen=True, slots=True)
class ImagePart:
    payload: ProviderBinaryPayload
    detail: Literal["auto", "low", "high"] = "auto"


@dataclass(frozen=True, slots=True)
class FilePart:
    payload: ProviderBinaryPayload
```

约束：

- `ProviderBinaryPayload` 只存在于单次请求组装及 Provider 调用期间，禁止持久化，
  调用结束后丢弃；
- `data` 必须复制为 `bytes`，不接受 path、URL、Signed URL 或 Provider File ID；
- `handle` 是 AgentOS 稳定 Artifact Handle，只用于关联和 Adapter 私有缓存键；
- 默认 serializer、Trace、Read Model 和 StoredMessage 只能输出元数据，不能输出 bytes；
- Phase 3A 从 `ArtifactStore.read()` 生成 Payload；
- Phase 3B 只把 Payload 映射为 Provider wire content；
- Provider File ID 只能位于具体 Adapter 的私有缓存，不能反写 Payload、ArtifactRecord
  或 StoredMessage。

### 2.2 所有权

Architecture Owner 必须在一个保持全量测试 Green 的原子 Preflight 中独占迁移：

- `providers/input.py`、`providers/input_serialization.py`、`providers/__init__.py`；
- `providers/_content_parts.py`、`providers/openai.py`、`providers/openai_compatible.py`、
  `providers/anthropic.py`；
- `attachments/runtime.py`；
- Binary Content 直接相关的 Provider、Attachment、Runtime、Observability、Public import/signature
  tests。

Preflight 必须公开导出 `ProviderBinaryPayload`，并证明公开 `ImagePart/FilePart` 构造签名只依赖
公开类型。不得提交已知 Red，不增加旧 `attachment` 与新 `payload` 的双形态兼容。Artifact Owner
和 Provider Owner 都不能各自定义第二个 Binary Payload。Preflight 完成后，Provider Owner 可继续
修改 Adapter 消费者，但不得修改 `input.py`、`input_serialization.py`、`providers/__init__.py`
和 Binary Content input/import/signature contract tests。

上述“独占”只适用于 Preflight 提交的迁移窗口。该提交完成后，`openai.py`、
`openai_compatible.py`、`anthropic.py` 等 Adapter 文件立即移交 Provider Owner；永久冻结的是
Binary Payload 共享类型、serializer/export 和对应 contract，而不是整个 Adapter 文件。

## 3. Artifact Contract

Phase 3A 冻结以下领域对象：

```text
ArtifactRecord
ArtifactRef
ArtifactPage
ArtifactToolPage
ContextMount
ArtifactStore
InMemoryArtifactStore
ArtifactRuntime
```

规则：

- Artifact ID 为 `art_` + UUID4；
- `created_at` 必须是 timezone-aware UTC；列表固定按
  `created_at DESC, artifact_id DESC` 排序；
- cursor 固定为 canonical JSON UTF-8（key 排序、紧凑 separators）后进行无 padding base64url
  编码；载荷只包含版本和已在 Catalog 可见的 `artifact_id`。Store 必须在调用方
  `session_id` 内解析该 anchor 并读取其 `created_at` 作为排序键；非法、已删除或跨 Session
  anchor 统一抛出
  `ArtifactValidationError("invalid artifact cursor")`；
- `limit` 范围为 1..100，默认 20；
- 默认媒体 allowlist 仅为 GIF、JPEG、PNG、WebP 和 PDF，单文件默认上限 25 MiB；
- filename 最长 255 个字符，禁止控制字符、`/` 和 `\\`；
- Store 的 `put/get/read/list/delete/delete_session` 全部显式携带 `session_id`；
- 未知 Handle 和跨 Session Handle 返回同类型、同消息的 not-found；
- `ArtifactRecord`、Catalog、Event 和前端投影不包含 bytes；
- `list_attachments` 的模型可见结果固定为 `ArtifactToolPage(items, next_cursor)`；每个 item
  只包含 `handle`、`filename`、`media_type`、`state=available|mounted`，不得直接序列化
  `ArtifactRecord`，不得包含 `session_id`、`created_at`、`size_bytes`、bytes 或内部路径；
- Runtime 只保存当前 Turn 的 `ContextMount`，不缓存第二份内容；
- 每次 ContextMount 投影重新从 Store 读取 bytes；
- `load_attachment` 固定返回：

```text
附件已挂载：{handle}。附件内容将在下一次模型请求中作为当前轮次的工具结果数据提供。
```

- ContextMount 的固定中文 TextPart 后紧跟一个 `ImagePart` 或 `FilePart`；
- Mount 清除不删除 Artifact；Artifact 生命周期由 Session/Application 管理；
- Phase 3A 不修改 Builder、QueryLoop 或 StoredMessage；首轮自动 Mount、最终消息排序、
  Turn 终态清理和旧 `attachments` 删除由 Phase 4 完成。

## 4. Provider Adapter Contract

### 4.1 OpenAI 类名

项目尚未生产推广，不保留含糊的旧命名：

- `OpenAIProvider` 使用现代 OpenAI Responses API；
- `OpenAIChatCompletionsProvider` 明确使用官方 Chat Completions API；
- `OpenAICompatibleProvider` 继续表示 OpenAI-compatible Chat Completions wire；
- 不增加 `api_mode`、`use_responses` 或运行时字符串模式分支。

### 4.2 Adapter 不变量

所有 Adapter 必须证明：

- SystemEnvelope 与 ContextSnapshot Authority 分离；
- 输入顺序由 `ProviderRequest.messages` 决定，Adapter 不重排 Tool Pair 或 Mount；
- 严格角色 Provider 只在最终 wire payload 合并相邻 user content；
- 合并不修改 `ProviderRequest`、`ProviderInputItem`、MessageStore 或 Read Model；
- Responses、Chat Completions、Compatible Chat 和 Anthropic 有独立 Contract Matrix；
- timeout 映射为稳定 `ProviderTimeoutError`；retry 仍只由
  `ProviderAttemptRunner` 拥有；
- Provider 托管历史、`previous_response_id` 和 File ID 都只是可选优化。

### 4.3 OpenAI-Compatible 拆分

`openai_compatible.py` 必须拆为：

```text
openai_chat_wire.py             shared Chat request/tool/content 基础映射，<500 行
openai_compatible.py            Provider facade / lifecycle，<300 行
openai_compatible_wire.py       capability/extra_body/服务差异包装，单向依赖 shared Chat wire，<500 行
openai_compatible_parsing.py    response/stream 解析纯函数，<500 行
openai_compatible_transport.py  sync/async HTTP、SSE、timeout I/O，<500 行
```

拆分前先用现有测试固化 wire、stream、timeout 和 error 行为。机械拆分提交不能同时
改变协议语义。

## 5. Skill Contract

Skill Metadata、正文和可信指令必须分离：

```text
SkillMetadata     -> available-skills ContextSnapshot
Skill body        -> 按需加载的数据
TrustedSkillInstruction -> SystemEnvelope
```

Skill 至少包含 `trust = trusted|untrusted` 和可验证来源状态。规则：

- builtin 不等于自动可信；只有 Trust Policy 验证通过的 Skill 才可激活；
- trusted Skill 加载后由 `SkillRuntime` 激活，下一次 Provider 请求通过
  `TrustedSkillInstructionProvider.items()` 进入 SystemEnvelope；
- trusted Skill 的 Tool Result 只返回有界确认，不复制完整正文；
- untrusted Skill 永远不能进入 SystemEnvelope，正文只能作为有界 Tool Result 数据；
- Active Skill 不写入 StoredMessage，Runtime/Application 可以显式停用；
- `available-skills` 只包含 name、description、loadable、trust，不包含正文、路径或资源内容。

Trust 与激活契约：

```text
SkillVerificationSubject = source_id + skill_name + source_revision + content_digest
SkillTrustPolicy.verify(metadata, subject) -> SkillTrustDecision
SkillTrustDecision = verified + policy_id + subject
ActivationKey = (session_id, skill_name)
```

- Skill 激活固定为 Session Scope；共享 `SkillRuntime` 的激活表必须按 `session_id` 隔离；
- `load/disable/items/projections` 都显式接收 `session_id`，禁止隐式“当前 Session”全局变量；
- builtin、filesystem 或其他来源在没有 verified decision 时一律不能进入 SystemEnvelope；
- 来源 revision 变化、Policy 撤销/拒绝、显式 disable 或 Session 结束都会使激活失效；
- Trust Decision 只保存验证证据元数据，不保存 Skill 正文；跨 Session 激活必须测试为失败。
- Decision 必须逐字段绑定同一个 `SkillVerificationSubject`，不得在同一 Source/revision 下跨
  Skill 复用；正文变化必须改变 `content_digest` 并使旧 Decision 失效。
- Phase 2 的 `TrustedSkillInstructionProvider.items()` 和
  `ContextProjectionProvider.projections()` 无参签名保持不变。Phase 3C 使用请求绑定 Adapter：

```text
BoundSkillInstructionProvider(runtime, session_id).items()
BoundSkillProjectionProvider(runtime, session_id).projections()
BoundSkillTools(runtime, session_id).registered_tools()
```

- Adapter 在构造时冻结 Session Scope，内部再调用带显式 Scope 的 Runtime 方法；
  `BoundSkillTools` 注册的 handler 闭包捕获该 Session，不从 Tool arguments 或全局变量读取 Scope。

## 6. Plan Contract

`agentos.planning` 成为 Plan 领域 Owner，`planning` 禁止导入 `multi`。`multi` 只通过
明确的 dispatch/coordinator Adapter 与 Planner 协作。

Active Plan Projection 每次从 `PlanStore` 读取真值，不保存 XML 或自由文本副本。
Phase 3C 的 Projection Provider 使用显式 `plan_id` 和 `owner_agent_id`，并只通过
`AuthorizedPlanSource.get_for_projection(plan_id, owner_agent_id)` 读取。不存在与 Owner 不匹配
统一返回 not-found，不得把其他 Agent 的 Plan 投影到 Context。Run/Session 与 Active Plan 的
持久绑定由 Phase 4/5 完成，接线后只能进一步收紧此授权边界。

`BoundPlanProjectionProvider(source, plan_id, owner_agent_id).projections()` 在请求组装前冻结
两个标识，以无参方法满足既有 `ContextProjectionProvider`；禁止修改 Kernel Port 或读取可变
“当前 Plan”全局状态。

状态映射：

| Plan 真值 | Context Protocol |
|---|---|
| `draft` | `pending` |
| `running` 且至少一个 Step 为 `pending`、`assigned` 或 `running` | `in-progress` |
| `running` 且无上述 Step、但至少一个 Step 为 `blocked` 或 `failed` | `blocked` |
| `running` 且零 Step，或全部 Step 仅为 `completed/cancelled` | 抛出 `PlanProjectionError("invalid active plan state")`，不输出半成品投影 |
| `completed`、`failed`、`cancelled` | 不作为 Active Plan 投影 |

Step 状态映射：

| Step 真值 | Context Protocol |
|---|---|
| `pending` | `pending` |
| `assigned`、`running` | `in-progress` |
| `blocked`、`failed` | `blocked` |
| `completed` | `completed` |
| `cancelled` | `cancelled` |

默认投影只包含 goal、step handle、step status 和 instruction。Claim、Lease、Worker、
Task ID、owner、模板、路径、Retry Token、Evidence URI 和内部时间戳不得进入默认 Context。

`multi/planner.py` 必须按领域职责迁入 `planning/` 并删除，禁止复制两份 Plan 类型。
全部仓库内消费者在同一迁移波次改为从 `agentos.planning` 导入；`multi.__init__` 不保留
旧 Planner re-export、兼容包装器或第二套序列化入口。

## 7. Memory Contract

`RecallRuntime` 是唯一的 recall command owner，继续负责 `recall_context` 的参数校验、事件、
消息注入和命令编排。当前负责压缩片段持久化、索引和原文读取的旧 `MemoryRuntime` 拆为
`recall/segment_repository.py::SegmentRepository`，作为 `RecallRuntime` 的 Store/Index 协作者，
不形成第二个 Recall Runtime。压缩片段类型、RecallIndex 和索引 Adapter 同步迁入 `recall/`；
热点/持久 Session Store 类型与 Adapter 迁入 `persistence/`。`recall/` 不得导入 `memory/`。
旧 `memory` 包中的上述类型和导出在同一原子迁移中删除，不保留 alias。新的 `MemoryRuntime`
只负责 Episodic/Semantic Memory 的选择、权限/过期过滤和 `memory-context` 投影。

领域迁移固定为：

```text
memory/{types,recall_index,embeddings,qdrant_index}.py
  -> recall/{types,index,embeddings,qdrant_index}.py
memory/runtime.py
  -> recall/segment_repository.py + 新 memory/runtime.py
memory/{store,serializers,redis_store}.py
  -> persistence/{session_store,session_serializers,redis_session}.py
memory/in_memory.py
  -> recall/in_memory_index.py + persistence/in_memory_session.py
```

```text
MemoryKind     = episodic | semantic
SemanticCategory = preference | reference | fact | procedure
EpisodicCategory = interaction | outcome
MemorySelectionContext = session_id + principal_id + permissions + query + now
```

规则：

- Kind 与 category 组合必须严格校验；
- `instructional` 在 v1 固定输出 `false`；
- Artifact 内容只保存 Handle，不复制到 Memory 文本；
- `MemoryStore` 是真值源；`search(context, candidate_limit)` 必须至少按
  `MemorySelectionContext.session_id` 隔离候选；
- `MemoryRuntime.projections(context)` 每次重新选择 Top-K，并再次校验 record session、
  注入的 `MemoryAccessPolicy`、过期时间、相关性和确定性 tie-break；
- `principal_id`、`permissions` 和 `now` 禁止从进程全局状态或墙钟隐式获取；
- `BoundMemoryProjectionProvider(runtime, selection_context).projections()` 在请求组装前冻结
  `MemorySelectionContext`，以无参方法满足既有 `ContextProjectionProvider`；不得修改 Kernel Port；
- score、reason 和内部排序元数据不进入默认 Context；
- Memory 不修改 Working State，也不能成为 System Instruction。

## 8. 并行文件边界

| Owner | 可修改 | 禁止修改 |
|---|---|---|
| Architecture Owner | Binary Content 原子 Preflight、Phase 3 Spec/Plan | QueryLoop、Phase 3 业务实现 |
| Artifact Owner | `artifacts/**`、`events/artifacts.py` 和 Artifact tests | Provider Adapter、Builder、Messages、全局 Tool 发布、Public API 导出 |
| Provider Owner | `providers/**` 和 Provider tests，但明确排除 Preflight 的 input、serializer、`__init__` 与 Binary Content input/import/signature contract tests | ArtifactStore、Context Renderer、Messages、QueryLoop |
| Extension Owner | Skill、Memory、`planning/**`、下列 Planner 迁移消费者和相关 tests | Provider Adapter、QueryLoop、Artifact |

Extension Owner 为完成 Planner 单一真值迁移，可修改且只能修改以下现有消费者：

```text
src/agentos/testing/contracts/plan_store.py
src/agentos/testing/contracts/plan_claim_store.py
src/agentos/multi/postgres_plan.py
src/agentos/multi/serializers.py
src/agentos/multi/__init__.py
src/agentos/multi/planning_dispatch.py
src/agentos/__init__.py
src/agentos/examples/planner_patterns.py
src/agentos/examples/production_reference_web_agent.py
tests/multi/test_planner_claimed_scheduler_daemon.py
tests/multi/test_planner_projection.py
tests/multi/test_planner_runtime.py
tests/multi/test_planner_tools.py
tests/multi/test_planner_scheduler_governance_profile.py
tests/multi/test_plan_claim_store_contract.py
tests/multi/test_plan_store_contract.py
tests/multi/test_planner_scheduler_daemon.py
tests/multi/test_postgres_plan_claim_store.py
tests/multi/test_postgres_plan_store.py
tests/integration/test_distributed_planner_worker_flow.py
tests/architecture/test_public_api.py
tests/architecture/test_public_api_inventory.py
docs/api-stability.md
docs/public-api-inventory.json
docs/public-api-stability.json
```

Extension Owner 的 Memory/Recall 原子迁移还独占以下文件：

```text
src/agentos/memory/__init__.py
src/agentos/memory/embeddings.py
src/agentos/memory/in_memory.py
src/agentos/memory/qdrant_index.py
src/agentos/memory/recall_index.py
src/agentos/memory/redis_store.py
src/agentos/memory/runtime.py
src/agentos/memory/serializers.py
src/agentos/memory/store.py
src/agentos/memory/types.py
src/agentos/recall/__init__.py
src/agentos/recall/runtime.py
src/agentos/recall/segment_repository.py
src/agentos/recall/types.py
src/agentos/recall/index.py
src/agentos/recall/embeddings.py
src/agentos/recall/qdrant_index.py
src/agentos/recall/in_memory_index.py
src/agentos/recall/store.py
src/agentos/persistence/session_store.py
src/agentos/persistence/session_serializers.py
src/agentos/persistence/redis_session.py
src/agentos/persistence/in_memory_session.py
src/agentos/persistence/__init__.py
src/agentos/persistence/postgres.py
src/agentos/compression/compressor.py
src/agentos/compression/llm_compressor.py
src/agentos/compression/runtime.py
src/agentos/builder.py
tests/memory/**
tests/compression/test_memory_sink.py
tests/compression/test_package_compressor.py
tests/recall/test_query_recall.py
tests/recall/test_runtime.py
tests/capabilities/test_tools.py
tests/observability/test_event_log.py
tests/runtime/test_query_loop.py
tests/runtime/test_session_recovery.py
tests/architecture/test_phase7_memory_boundaries.py
tests/architecture/test_public_api.py
tests/architecture/test_public_api_inventory.py
docs/README-OUTLINE.md
docs/readme-online.md
docs/api-stability.md
docs/public-api-inventory.json
docs/public-api-stability.json
```

`docs/governance/agentos-module-size-baseline.json` 由 Integration/Quality Owner 在三个 Workstream
合并后串行更新；并行 Workstream 只运行只读规模检查。

除 Preflight 必须完成的 `ProviderBinaryPayload` module export/import test、Phase 3C
Memory/Recall/Persistence cutover 必须完成的 module exports/stability、`RecallRuntime` 构造消费者
和窄幅 Builder 签名迁移，以及删除 `multi` Planner re-export 必须完成的 Planner Root/module
export、Examples 和 Public API Inventory 原子迁移外，Phase 4 才拥有 Builder、ProviderRequestBuilder、
ToolCallRouter、QueryLoop、Root API、Public API Inventory/导出、Examples 和旧包删除的集成
修改权。Phase 3 可以冻结 Artifact Tool schema/handler contract，但不得加入全局 Context Tool
列表或形成模型可见入口。

## 9. 明确延期

- Phase 4：Builder/Registry 聚合、首轮 Artifact 自动 Mount、Tool Result 后最终排序、
  Turn 终态清理、Active Plan/Memory Query Source 接线、删除 `agentos.attachments`；
- Phase 5：SQLite/Filesystem Artifact、Plan、Memory 持久化和 Restart/Resume；
- Phase 6：PostgreSQL/Redis Adapter、分布式 Claim/Worker、Transport、Provider File
  分布式缓存和故障注入；
- Artifact OCR、摘要、向量检索、Workspace/Tenant Scope 仍按上位 Spec 延期。

## 10. 验收

本补充批准后，Phase 3 的 Definition of Ready 还要求三份详细实施计划、Scope Contract、
模块规模审查、失败测试和精确提交边界全部完成。任何 Workstream 不得以本补充代替其
独立 TDD 实施计划。
