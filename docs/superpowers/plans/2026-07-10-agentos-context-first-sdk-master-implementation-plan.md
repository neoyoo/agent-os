# AgentOS Context-First SDK 总体实施计划

> **SUPERSEDED FOR LOOP TOPOLOGY:** 本文保留双 Loop API 的阶段性描述已被
> `2026-07-12-agentos-single-async-query-loop-design.md` 取代。历史正文保留，
> 其余 context-first 和依赖边界继续有效。

> **For development agents:** 按仓库 `AGENTS.md`、工程规范、当前阶段详细计划、
> Scope Contract、独立双层 Review 和精确提交规则逐任务执行。本文不依赖任何外部
> Skill 才能生效；步骤使用 checkbox（`- [ ]`）跟踪。

**Goal:** 在不保留旧错误边界的前提下，把现有 AgentOS RC 代码迁移为统一支持 Local、Durable 和 Distributed Profile 的 context-first SDK，并以 Context Protocol v1 作为所有 LLM 可见上下文的唯一协议锚点。

**Architecture:** 先串行冻结 Context Protocol Kernel、StoredMessage/ProviderInputItem 边界和 Public API，再把 Artifact、Provider Adapter、Skill/Plan/Memory Projection 分配为互不修改共享核心文件的并行工作流。所有阶段通过类型化 Port、Contract Test 和 Golden Test 集成，Distributed Runtime 只能组合 Kernel，不能重新定义 Agent、Run、Turn、Tool、Plan 或 Context 语义。

**Tech Stack:** Python 3.11+、标准库 dataclasses/typing/XML 工具、pytest、ruff、SQLite/Filesystem 可选 Durable Adapter、PostgreSQL/Redis 可选 Distributed Adapter。

---

## 1. 计划状态与范围契约

- 状态：已批准，进入分阶段实施；各工作包必须先生成详细 TDD 实施计划并通过 Scope Contract 门禁。
- 基线分支：`review/agentos-sdk-architecture-20260611`。
- 基线提交：`32bacab`。
- 协议基线：[AgentOS Context Protocol v1](../specs/2026-07-10-agentos-context-protocol-v1-design.md)。
- 架构基线：[AgentOS 下一代 SDK 架构设计](../specs/2026-07-10-agentos-next-generation-sdk-architecture-design.md)。

本计划完成以下规划工作：

1. 明确现有代码到目标架构的迁移边界；
2. 冻结目标包结构和核心类型关系；
3. 定义阶段依赖、验收标准和并行波次；
4. 定义团队角色、文件所有权和变更治理；
5. 定义 Public API、版本和 Breaking Change 策略；
6. 定义进入 subagent 并行开发前的 Definition of Ready。

本计划不直接实施 SDK 代码，也不立即分配 subagent。详细工作包必须在本计划批准后逐份生成并审查。

## 2. 已冻结的架构结论

以下内容不在实施阶段重新讨论：

```text
system   = SystemEnvelope
messages = ContextSnapshot + Active Messages + Tool Results + ContextMounts
tools    = Provider Tool Schemas
```

- `SystemEnvelope` 只包含可信指令；
- `ContextSnapshot` 是内部 synthetic user-role ProviderInputItem；
- `ContextSnapshot` 每次 Provider 调用重新生成；
- `ContextSnapshot` 不进入 MessageStore、Compression、Frontend Read Model；
- `StoredMessage` 是业务会话真值；
- Tool Call/Tool Result Pair 不能被 Snapshot 或 Compression 打断；
- Artifact 原始内容保存在 ArtifactStore，通过 ContextMount 临时投影；
- Local Profile 不依赖 PostgreSQL、Redis、Nacos 或外部对象存储；
- Distributed Profile 使用相同 Kernel 和领域协议；
- Event 只观察，Hook/Policy 才能拦截或修改执行；
- Provider Transcript 不是 Session 真值源。

任何实现任务若需要改变上述结论，必须先修改 Spec 并获得批准，不能在代码评审中静默改变。

## 3. 当前代码基线

### 3.1 可保留能力

现有代码已经具备以下可复用基础：

- `MessageStore` append-only 语义；
- `ActiveWindow` 对 Tool Call/Tool Result Pair 的保护；
- 每次 Provider 调用前通过 `ProviderRequestBuilder.build()` 重建请求；
- 单一异步 `QueryLoop`，同步调用只通过 `agentos.sync` 边界适配；
- OpenAI、OpenAI-Compatible、Anthropic 和 Fake Provider；
- 类型化 Runtime Event、HookManager 和 Observability；
- Compression/Recall、Memory、Planner、Team、Channel 和 Distributed Adapter；
- Optional Dependency 分组，基础安装没有强制 PostgreSQL/Redis 依赖。

这些能力只有在符合新协议边界时才保留。旧类型名称或旧序列化格式本身不构成兼容要求。

### 3.2 必须替换的旧边界

| 当前实现 | 问题 | 目标状态 |
|---|---|---|
| `ContextRenderer.render(ContextState) -> str` | 把 Runtime Contract、Capability、Working State、Memory 和附件规则全部写入 system | `ContextRenderer` 只生成 SystemEnvelope；`ContextSnapshotRenderer` 生成动态 XML |
| `ProviderRequest.messages: list[ProviderMessage]` | 缺少 origin、authority、persistence、visibility 等内部语义 | 使用不可变 `ProviderInputItem` 序列 |
| `Message` | 名称不能明确它是业务持久化真值，且没有 ArtifactRef | 改为 `StoredMessage` |
| `MessageRuntime.materialize_provider_messages()` | Message Store 直接决定 Provider 形态 | 改为显式的 Provider Input Projection |
| `AttachmentLifecycle = Literal["ephemeral"]` | 把存储生命周期、Provider 投影生命周期和来源混为一个字段 | 使用 ArtifactRecord、ArtifactRef、ContextMount 分离职责 |
| `AttachmentRuntime._next_index` | 进程内递增 ID 无法可靠恢复 | 使用 `art_` + UUID4 稳定 ID |
| `agentos.attachments` | 包名和模型只覆盖临时附件 | 迁移为 `agentos.artifacts` |
| `CapabilityPlane` 写入 system | 动态能力数据被提升为指令 | Metadata 进入 ContextSnapshot；Trusted Skill Instructions 才进入 SystemEnvelope |
| Root `agentos` 暴露大量稳定对象 | Public API 面过宽，模块边界不清晰 | Root 只保留 Level 1 高频入口，其他能力从责任包导入 |

### 3.3 测试基线

在提交 `32bacab`、Python 3.13.14 上运行：

```text
1492 passed, 10 skipped, 5 failed
```

五个失败来自三个既有根因：

1. Public API Inventory 把 `pathlib.Path` 的运行时内部限定名写入签名比较，在 Python 3.13 上表现为 `pathlib._local.Path`；
2. Live Backend Probe 测试的子进程没有继承 pytest 的 `pythonpath = ["src"]`，未安装 editable package 时无法导入 `agentos`；
3. `docs/release-evidence.json` 固定记录旧提交 `378db4a`，默认测试却要求它等于当前 HEAD。

Phase 0 必须先消除这三个基线问题。架构迁移不能建立在红色主测试套件上。

## 4. 目标包结构

```text
src/agentos/
  runtime/          QueryLoop、Run/Session/Turn、ProviderRequestBuilder
  context/          SystemEnvelope、ContextSnapshot、Slot Registry、渲染与预算
  messages/         StoredMessage、MessageStore、ActiveWindow、Read Model
  providers/        Provider Port、ProviderInputItem、Provider Adapter
  capabilities/     Tool、Skill、MCP Registry、Router、Executor、Policy
  artifacts/        ArtifactRecord、ArtifactStore、ArtifactRef、ContextMount
  compression/      ActiveWindow 压缩与 SourceRef
  recall/           原始消息临时召回
  memory/           Episodic/Semantic Memory Port 和 Adapter
  planning/         Plan、Step、Claim、Planner Runtime 和 Store Port
  multi/            Team、Subagent、Remote Agent 协作
  persistence/      Memory、SQLite、Filesystem、PostgreSQL Adapter
  distributed/      Queue、Inbox、Lease、Worker、Wakeup、Recovery
  transports/       HTTP、SSE、WebSocket、CLI、A2A 协议转换
  channels/         面向应用的会话托管和传输组合
  events/           观察型领域事件
  hooks/            显式拦截和修改策略
  observability/    Trace、Metric、Log、Snapshot Metadata
```

包边界规则：

- `runtime` 可以依赖 Port，不能依赖 PostgreSQL、Redis、HTTP 或具体 Provider SDK；
- `context` 不保存原始业务消息，不执行外部 Tool；
- `messages` 不渲染 Context，不压缩内容；
- `providers` 不读取 ContextRuntime、MessageStore 或 ArtifactStore；
- `artifacts` 不决定前端消息形态；
- `planning` 是可选 Extension，基础 QueryLoop 不依赖 Plan；
- `distributed` 只能组合领域 Port，不能重新定义领域对象；
- `transports` 只做 wire protocol 与 Command/Event 的转换；
- `channels` 可以组合 Transport 和 Session Provider，但不拥有 Run 真值。

`agentos.planning`、`agentos.artifacts`、`agentos.distributed` 和 `agentos.transports` 是目标结构。旧模块迁移必须在对应阶段一次完成，不能长期维护双包写入。

## 5. 核心类型冻结点

详细字段校验由子计划给出，但以下对象关系在任务分配前冻结。

### 5.1 Provider 输入

```python
@dataclass(frozen=True, slots=True)
class ProviderInputItem:
    role: ProviderRole
    kind: ProviderInputKind
    origin: InputOrigin
    authority: InputAuthority
    persistence: PersistencePolicy
    visibility: VisibilityPolicy
    content: tuple[ProviderContentPart, ...]
    tool_calls: tuple[ProviderToolCall, ...] = ()
    tool_call_id: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderRequest:
    system: str
    messages: tuple[ProviderInputItem, ...]
    tools: tuple[ProviderToolSpec, ...] = ()
```

`ContextSnapshot` 对应的 Item 属性固定为：

```python
role="user"
kind="context_snapshot"
origin="runtime"
authority="context_data"
persistence="ephemeral"
visibility="internal"
```

### 5.2 Context 输出

```python
@dataclass(frozen=True, slots=True)
class SystemEnvelope:
    text: str


@dataclass(frozen=True, slots=True)
class ContextSnapshot:
    protocol: Literal["agentos.context"]
    version: Literal["1.0"]
    xml: str
```

- `ContextRenderer.render(...) -> SystemEnvelope`；
- `ContextSnapshotRenderer.render(...) -> ContextSnapshot`；
- `ProviderRequestBuilder` 是唯一把两者、Active Messages、Tool Results、ContextMounts 和 Tool Schemas 排序为 ProviderRequest 的 Owner。

### 5.3 业务消息

```python
@dataclass(frozen=True, slots=True)
class StoredMessage:
    id: str
    role: MessageRole
    content: str
    artifact_refs: tuple[ArtifactRef, ...] = ()
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None
```

StoredMessage 不包含 ProviderInputItem 元数据、ContextSnapshot XML、Base64、Provider File ID、Signed URL、本地路径或 Runtime 合成提示。

### 5.4 Artifact

```python
@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    id: str
    session_id: str
    filename: str | None
    media_type: str
    size_bytes: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    artifact_id: str
    filename: str | None
    media_type: str


@dataclass(frozen=True, slots=True)
class ContextMount:
    artifact_id: str
    reason: Literal["user_upload", "tool_result"]
    scope: Literal["current_turn"] = "current_turn"
```

ArtifactStore 管理内容和元数据；StoredMessage 只保存 ArtifactRef；ContextMount 只控制当前 Turn 的 Provider 投影。

## 6. Public API 与版本策略

项目尚未生产推广，本次采用一次协调完成的 Breaking Migration：

- 新架构版本从 `0.2.0a1` 开始，不继续把旧结构标为 `0.1.0rc1` 的兼容实现；
- 不为内部旧类型建立长期双写或双读；
- Wave 1 可以暂时保留尚未接线的旧包，但新旧实现不能双写，且 M3 Local Agent 合并前必须删除旧包；
- `agentos.attachments` 在 Phase 4 Local Loop 集成完成前删除；
- `Message` 在 Message Boundary 工作包完成后删除，公开名称统一为 `StoredMessage`；
- `ProviderMessage` 在 Provider Input 工作包完成后删除，公开名称统一为 `ProviderInputItem`；
- Public API Inventory 在每个对外边界阶段重新生成，不手工掩盖签名漂移。

目标 Root API 只保留：

```text
Agent
AgentBuilder
AgentResult
RunOptions
__version__
```

Provider、Context、Artifact、Planner、Transport 和 Distributed 类型从各自责任包显式导入。Root 不再聚合企业级全部对象。

## 7. 阶段依赖与产品里程碑

```text
Phase 0  基线与治理同步
   |
Phase 1  Context Protocol Kernel
   |
Phase 2  StoredMessage / ProviderInput Boundary
   |
   +----------------+------------------+
   |                |                  |
Phase 3A         Phase 3B           Phase 3C
Artifact         Provider Adapter    Skill/Plan/Memory Projection
   |                |                  |
   +----------------+------------------+
                    |
Phase 4  Local Loop 集成与 Public API
                    |
Phase 5  Durable Profile
                    |
Phase 6  Distributed Runtime / Transport
```

产品里程碑：

| 里程碑 | 完成阶段 | 可交付能力 |
|---|---:|---|
| M1 Context Kernel | Phase 1 | 确定性生成 SystemEnvelope 和 ContextSnapshot |
| M2 Core Request Pipeline | Phase 2 | 每次调用重建双平面 ProviderRequest，消息边界正确 |
| M3 Local Agent | Phase 4 | 无外部服务依赖的基础 Loop、Tool、附件重新加载 |
| M4 Durable Agent | Phase 5 | SQLite/Filesystem 恢复、Skill/Plan/Memory/HITL 组合 |
| M5 Distributed Runtime | Phase 6 | PostgreSQL/Redis、Worker、Transport、Team 和可靠恢复 |

## 8. 分阶段工作包

### Phase 0：基线与治理同步

**串行执行。禁止 subagent 并行修改。**

主要文件：

- `AGENTS.md`
- `docs/governance/agentos-engineering-standard.md`
- `docs/design/sdk-architecture.md`
- `docs/design/llm-context-only-example.md`
- `docs/api-stability.md`
- `docs/public-api-inventory.json`
- `pyproject.toml`
- `tests/architecture/test_public_api.py`
- `tests/deployment/test_live_backend_probe_pack.py`
- `tests/test_release_evidence.py`

完成条件：

- Mandatory Context Bootstrap、Scope Contract、文件规模门禁和双层 Review 成为所有工作包的共同约束；
- 活跃设计文档不再声明 `system = rendered context`；
- 旧七段式范文明确标记为历史输入，不再是规范；
- Public API 签名比较跨 Python 3.11-3.13 稳定；
- 子进程测试显式建立 import 环境；
- Release Evidence 的静态 Fixture 与“当前候选提交验证”职责分离；
- `python -m pytest -q` 在无 Live Backend 环境下全绿；
- 生成 `0.2.0a1` Public API 基线策略，但此阶段不提前导出未实现类型。
- 生成当前 300/500/800 行模块规模基线；后续工作包不得继续向 800 行以上项目文件追加新的独立职责。

### Phase 1：Context Protocol Kernel

**串行执行，由 Architecture/Context Owner 持有共享核心文件。**

目标文件：

- 修改 `src/agentos/context/renderer.py`
- 创建 `src/agentos/context/models.py`
- 创建 `src/agentos/context/registry.py`
- 创建 `src/agentos/context/xml.py`
- 创建 `src/agentos/context/snapshot.py`
- 修改 `src/agentos/context/projection.py`
- 修改 `src/agentos/context/__init__.py`
- 创建 `tests/context/test_system_envelope_renderer.py`
- 创建 `tests/context/test_context_snapshot_renderer.py`
- 创建 `tests/context/test_context_protocol_security.py`
- 创建 `tests/context/goldens/system-envelope-v1.md`
- 创建 `tests/context/goldens/context-snapshot-v1-full.xml`
- 创建 `tests/context/goldens/context-snapshot-v1-minimal.xml`

完成条件：

- ContextRenderer 只输出固定顺序的可信章节；
- ContextSnapshotRenderer 按九个 Slot 的固定顺序输出 XML；
- 所有动态字段使用固定标签和 XML Escape；
- 相同输入产生字节级一致输出；
- Budget 只裁剪完整类型元素；
- 未注册 Slot、重复 Owner、未知 Major Version 和非法 XML 字符确定性失败；
- Golden、安全和确定性测试通过；
- 不修改 QueryLoop、Artifact 或具体 Provider Adapter。

### Phase 2：StoredMessage / ProviderInput Boundary

**核心接口已经冻结；剩余消费者迁移按详细计划 Wave 3 受控并行，Task 13-14 串行收口。**

> **2026-07-14 状态同步：** Phase 2 正在执行。Task 0-7 与单一异步 Kernel
> 重构已经在 `88ae4ff` 完成；当前详细计划执行游标为 Task 8。下方旧双 Loop
> 文件列表只保留为最初规划记录，Loop/Runner 拓扑以
> `2026-07-12-agentos-single-async-query-loop-design.md` 和 2026-07-14 re-baseline 为准。

目标文件：

- 修改 `src/agentos/messages/types.py`
- 修改 `src/agentos/messages/store.py`
- 修改 `src/agentos/messages/window.py`
- 修改 `src/agentos/messages/runtime.py`
- 创建 `src/agentos/messages/read_model.py`
- 修改 `src/agentos/messages/__init__.py`
- 创建 `src/agentos/providers/input.py`
- 修改 `src/agentos/providers/base.py`
- 修改 `src/agentos/providers/messages.py`
- 修改 `src/agentos/providers/__init__.py`
- 修改 `src/agentos/runtime/provider_request_builder.py`
- 修改 `src/agentos/runtime/query_loop.py`
- 修改 `src/agentos/runtime/provider_attempt.py`
- 修改 `src/agentos/runtime/agent_stream.py`
- 修改 `src/agentos/runtime/agent.py`
- 修改 `src/agentos/builder.py`
- 修改 `tests/messages/test_runtime.py`
- 修改 `tests/runtime/test_provider_request_builder.py`
- 修改 `tests/runtime/test_query_loop.py`
- 修改 `tests/runtime/test_async_query_loop_native.py`

完成条件：

- ProviderRequest 使用不可变 ProviderInputItem 序列；
- Snapshot 位于 Active Messages 之前且不打断 Tool Pair；
- StoredMessage 与 ProviderInputItem 没有继承或持久化关系；
- Frontend Read Model 只从 StoredMessage 和 user-visible Event 生成；
- stream/non-stream 以及同步/异步 Provider capability 使用同一个 QueryLoop、ProviderAttemptRunner 和 ProviderRequestBuilder 路径；
- 每次 Provider 调用重新渲染 Snapshot；
- 旧 `Message` 和 `ProviderMessage` 名称从 Public API 删除；
- ProviderRequestBuilder 接受已注册的类型化 Context Projection Provider，后续工作包不需要修改 ContextSnapshotRenderer；
- Phase 1 和 Phase 2 全量契约测试通过。

### Phase 3A：Artifact Domain 与 Projection

Phase 2 冻结后可与 Phase 3B、3C 并行。

目标文件：

- 创建 `src/agentos/artifacts/types.py`
- 创建 `src/agentos/artifacts/store.py`
- 创建 `src/agentos/artifacts/runtime.py`
- 创建 `src/agentos/artifacts/projection.py`
- 创建 `src/agentos/artifacts/__init__.py`
- 修改 `src/agentos/context_protocol.py`
- 创建 `tests/artifacts/test_store_contract.py`
- 创建 `tests/artifacts/test_runtime.py`
- 创建 `tests/artifacts/test_context_mount.py`
- 创建 `tests/artifacts/test_session_scope_security.py`

完成条件：

- ID 使用 `art_` + UUID4；
- Store API 强制 Session Scope；
- 未知和跨 Session Handle 返回相同 not-found；
- Catalog 只投影元数据；
- `load_attachment` 返回固定中文 Tool Result；
- Projection 生成固定 TextPart 和 canonical ImagePart/FilePart，不直接操作 Provider Adapter；
- Runtime API 可以清除 Mount 而不删除 Artifact；
- MessageStore、Trace 和 Frontend 不包含 Bytes、Base64、本地路径、Signed URL 或 Provider File ID。

ContextMount 在 Tool Result 后的最终排序、首轮自动 Mount 和 Turn 完成/失败/取消接线由 Phase 4 集成测试验收。

### Phase 3B：Provider Adapter Contract

Phase 2 冻结后可独立执行，不修改 Context 或 Message Store。

目标文件：

- 修改 `src/agentos/providers/openai.py`
- 修改 `src/agentos/providers/openai_compatible.py`
- 修改 `src/agentos/providers/anthropic.py`
- 修改 `src/agentos/providers/_content_parts.py`
- 修改 `src/agentos/providers/fake.py`
- 创建 `tests/providers/test_context_protocol_contract.py`
- 修改 `tests/providers/test_adapters.py`
- 修改 `tests/providers/test_openai_compatible.py`
- 修改 `tests/providers/test_provider_messages.py`

完成条件：

- SystemEnvelope 与 ContextSnapshot 在所有 Adapter 中保持 Authority 分离；
- OpenAI Responses、Chat Completions 和 Anthropic 映射有独立 Contract Test；
- 严格角色交替 Provider 只在最终 Payload 合并相邻 user content；
- Payload 合并不反写 ProviderRequest、MessageStore 或 Read Model；
- Tool Pair 和 Attachment Mount 顺序不被 Adapter 改变；
- Provider File ID 只作为 Adapter 缓存优化，不成为业务真值。

### Phase 3C：Skill / Plan / Memory Projection

Phase 2 冻结后可独立执行，不修改 Provider Adapter。

目标文件：

- 修改 `src/agentos/capabilities/skills.py`
- 修改 `src/agentos/capabilities/registry.py`
- 创建 `src/agentos/planning/`
- 迁移 `src/agentos/multi/planner.py` 的 Planner 领域对象
- 修改 `src/agentos/memory/types.py`
- 修改 `src/agentos/memory/runtime.py`
- 创建 `src/agentos/capabilities/skill_projection.py`
- 创建 `src/agentos/planning/projection.py`
- 创建 `src/agentos/memory/projection.py`
- 创建 `tests/context/test_skill_projection.py`
- 创建 `tests/context/test_plan_projection.py`
- 创建 `tests/context/test_memory_projection.py`
- 迁移相关 `tests/multi/test_planner_*.py`

完成条件：

- Available Skill 只投影 Metadata；
- Trusted Skill Instructions 通过 System Section Registry 进入 SystemEnvelope；
- untrusted Skill 永远不能提升为 System Instruction；
- Plan Store 是真值源，模型文本不能直接改变状态；
- `memory-context` 一级 Kind 只有 episodic/semantic；
- preference/reference/fact/procedure 只作为 category；
- 三类 Projection 只实现 Phase 2 冻结的 Port，不修改 ContextSnapshotRenderer；
- Planner 从 `multi` 的基础包职责中分离，基础 Loop 不依赖 Planner。

### Phase 4：Local Loop 集成与 Public API

目标文件：

- 修改 `src/agentos/builder.py`
- 修改 `src/agentos/runtime/agent.py`
- 修改 `src/agentos/runtime/profile.py`
- 修改 `src/agentos/runtime/provider_request_builder.py`
- 修改 `src/agentos/runtime/query_loop.py`
- 修改 `src/agentos/runtime/provider_attempt.py`
- 修改 `src/agentos/runtime/agent_stream.py`
- 修改 `src/agentos/sync/`
- 创建 `src/agentos/capabilities/scheduler.py`
- 修改 `src/agentos/context/registry.py`
- 修改 `src/agentos/__init__.py`
- 修改 `docs/public-api-inventory.json`
- 修改 `docs/api-stability.md`
- 修改 `src/agentos/examples/small_openai_agent.py`
- 创建 `src/agentos/examples/context_protocol_agent.py`
- 修改 `tests/runtime/test_agent_builder.py`
- 修改 `tests/runtime/test_agent_stream_api.py`
- 修改 `tests/examples/test_small_openai_agent.py`
- 创建 `tests/examples/test_context_protocol_agent.py`
- 删除 `src/agentos/attachments/`
- 迁移 `tests/attachments/` 到 `tests/artifacts/`

完成条件：

- `AgentBuilder().provider(provider).tools(tools).build()` 保持 Level 1 简单入口；
- 基础安装无数据库和远程服务依赖；
- Run 状态机拥有统一 Kernel 语义；短时 I/O 保持 `RUNNING`，持久等待退出当前 Loop，唤醒按 `WAITING -> QUEUED -> RUNNING` 创建 Continuation Turn；
- 恢复执行从权威状态重新组装 ProviderRequest，不复用暂停前的内存 Provider Transcript；
- `ToolCallScheduler` 统一同步和异步批次语义，Tool 默认 `EXCLUSIVE`，显式 `PARALLEL_SAFE` 才并发；
- `max_parallel_calls` 默认 `8`，超限 FIFO 排队，独占调用形成屏障，Tool Result 按 Provider 原始顺序写回；
- Root API 收敛到五个约定名称；
- Local 垂直切片覆盖用户消息、双平面 Context、Tool、附件挂载和最终回复；
- ContextMount 位于 `load_attachment` Tool Result 后，首轮上传自动 Mount，Turn 终态清除 Mount 但保留 Artifact；
- Skill、Plan、Memory 和 Artifact Projection 通过同一 Registry 接入，不在 Builder 中硬编码组件类型；
- Examples 不直接操作 ContextRuntime、MessageRuntime 或 ProviderRequestBuilder；
- M3 Local Agent 验收通过。

### Phase 5：Durable Profile

目标范围：

- SQLite 保存 Session、Run、StoredMessage、Working State、Plan 和 Artifact Metadata；
- Filesystem 保存 Artifact Content；
- 进程重启后恢复，不依赖 Provider Transcript；
- 持久化 WaitReason、Checkpoint、Resume Command 和 Continuation Turn 所需权威状态；
- Skill、Plan、Memory、HITL 和定时 Wakeup 可组合；
- 引入 `agentos[durable]` 可选依赖组；
- 不导入 Redis/PostgreSQL Client。

完成条件：

- Restart、Cancel、Wait、Resume 和 Artifact Reload 集成测试通过；
- Local Profile 测试在未安装 Durable Extra 时仍通过；
- M4 Durable Agent 验收通过。

### Phase 6：Distributed Runtime / Transport

目标范围：

- 把 Queue、Inbox、Lease、Worker、Wakeup 和 Recovery 从混合模块迁移到 `agentos.distributed`；
- 新增 `agentos.transports`，承接 HTTP、SSE、WebSocket、CLI 和 A2A wire mapping；
- PostgreSQL 是持久真值，Redis 承担 Lease、Queue、Inbox、Wakeup 和 Stream Replay；
- at-least-once 交付结合 Command ID、CAS 和 Fencing；
- `WAITING -> QUEUED` 使用幂等 Wakeup Command 和 CAS，Worker Claim 后才进入 `RUNNING`；
- Side Effect Policy 决定 Retry；
- `agentos[distributed]` 才安装 PostgreSQL/Redis Client。

完成条件：

- Local、Durable、Distributed 运行同一套 Kernel Contract；
- Transport 不拥有 Run/Session 真值；
- Duplicate Delivery、Stale Lease、Worker Drain、Cancellation、Recovery 和 Stream Replay 故障注入通过；
- M5 Distributed Runtime 验收通过。

## 9. 团队所有权模型

任务分配时使用以下角色，不按“一个人负责一个技术层”拆分共享文件：

| 角色 | 主要所有权 | 禁止直接修改 |
|---|---|---|
| Architecture/Integration Owner | Spec、核心类型、阶段接口、主分支集成 | 不代替各 Workstream 实现全部业务 |
| Context Protocol Owner | `context/`、Golden、安全和预算 | Provider Adapter、Artifact Store |
| Runtime/Message Owner | `runtime/`、`messages/`、Read Model | 具体 Provider HTTP Payload |
| Artifact Owner | `artifacts/`、Catalog、Mount、Session Scope | Context 核心 Registry、Provider Adapter |
| Provider Owner | `providers/` 和 Adapter Contract | MessageStore、ArtifactStore |
| Extension Owner | Skill、Planning、Memory Projection | QueryLoop 核心状态机 |
| Quality/Release Owner | Contract Matrix、全量测试、Public API、Release Evidence | 业务实现，除测试基础设施外 |
| Distributed Owner | `distributed/`、Transport、State Plane Adapter | Kernel 领域语义 |

一个共享文件在同一波次只能有一个 Owner。需要修改共享接口时，先由 Architecture Owner 合入接口提交，再由其他 Workstream rebase，禁止多个分支各自定义同名类型。

## 10. 并行开发波次

### Wave 0：只允许串行

- Phase 0 基线治理；
- Phase 1 Context Protocol Kernel；
- Phase 2 ProviderInput/StoredMessage 边界。

原因：这三个阶段共同决定所有后续 Workstream 的类型和调用方向。

### Wave 1：最多三个并行 Workstream

- Workstream A：Artifact Vertical Slice；
- Workstream B：Provider Adapter Contract；
- Workstream C：Skill/Plan/Memory Projection。

三个 Workstream 使用独立分支或明确的文件隔离边界；是否创建 git worktree 由用户和工作区条件决定。Architecture Owner 保留第四个并发位负责接口答疑、Review 和集成，不同时实现另一个大工作包。

### Wave 2：集成优先

- Local Loop 和 AgentBuilder；
- Public API 收敛；
- Examples 和完整 Contract Matrix。

Wave 2 不再并行修改 QueryLoop。主集成分支先合并 Provider，再合并 Artifact，最后合并 Extension Projection，每次合并后运行全量测试。

### Wave 3：可再次并行

- Durable Persistence；
- Distributed State Plane；
- Transport/Channel 边界；
- Failure Injection 和 Release Evidence。

每个 Workstream 依赖同一个 M3 Kernel，不允许复制或 fork QueryLoop。

## 11. 软件工程协作流程

### 11.1 Definition of Ready

一个工作包只有满足以下条件才允许分配给人或 subagent：

- 对应 Spec 和上游接口已经批准；
- 有独立的详细实施计划文档；
- 已完成 Mandatory Context Bootstrap 和七项 Scope Contract；
- 文件 Owner 和禁止修改范围明确；
- 已完成 300/500/800 行文件规模审查；触碰 500 行以上文件时已有拆分方案或有效例外记录；
- 测试文件、测试命令和预期失败原因明确；
- 上游阶段全量测试通过；
- 工作包不与同波次其他任务修改同一个共享核心文件；
- 回滚边界和提交粒度明确。

### 11.2 分支与隔离

- 是否使用 worktree 由用户、任务风险和当前工作区状态决定，不作为强制要求；
- 不使用 worktree 时必须在当前分支明确允许文件，并使用精确路径暂存，禁止 `git add .`；
- 分支命名：`feature/<phase>-<workstream>`；
- 一个提交只完成一个可验证行为；
- 禁止在 Workstream 分支修改未声明文件；
- 合并前必须 rebase 到最新集成分支并重新运行测试；
- 不在多个分支复制核心类型作为临时解决方案。

### 11.3 TDD 与 Review

每个行为使用以下顺序：

```text
写失败测试
-> 运行并确认失败原因正确
-> 最小实现
-> 运行目标测试
-> 运行模块测试
-> Spec Compliance Review
-> Code Quality Review
-> 提交
```

Review 分两层：

1. Spec Compliance：检查协议、Authority、Persistence、Visibility、生命周期和模块边界；
2. Code Quality：检查类型、错误处理、测试质量、可维护性和性能。

同一 Reviewer 不用一次 Review 同时替代两层结论。

### 11.4 变更控制

以下变化必须先修改 Spec：

- 改变 Context Slot 顺序、Owner、Authority 或版本；
- 改变 SystemEnvelope 允许内容；
- 改变 StoredMessage、ProviderInputItem 或 ContextMount 的持久化边界；
- 改变 Local/Durable/Distributed 的基础设施要求；
- 改变 Tool Pair、Retry、Cancellation 或 Session Scope 语义。

其他实现细节由详细计划和代码评审决定。

## 12. 质量门禁

所有工作包必须遵守 `docs/governance/agentos-engineering-standard.md`。文件规模门禁为：

```text
300 行：职责审查
500 行：默认拆分或登记例外
800 行：项目代码禁止继续扩张，修改前必须有批准的拆分计划
```

实现前和 Review 时运行模块规模扫描：

```powershell
Get-ChildItem -Recurse -File src/agentos -Filter '*.py' |
  ForEach-Object {
    [pscustomobject]@{
      Path = $_.FullName
      Lines = (Get-Content -Encoding utf8 $_.FullName).Count
    }
  } |
  Where-Object { $_.Lines -ge 300 } |
  Sort-Object Lines -Descending
```

扫描结果不是机械失败条件，但本工作包触碰的 500 行以上文件必须拆分或提供规范要求的例外记录。不得在 800 行以上项目文件中追加新的独立子系统。

每个工作包至少运行：

```powershell
python -m pytest <target-tests> -q
python -m compileall -q src tests
python -m ruff check src tests
git diff --check
```

每个阶段集成必须运行：

```powershell
python -m pytest -q
python -m compileall -q src tests
python -m ruff check src tests
git diff --check
```

Phase 1 之后增加协议漂移检查：

```powershell
rg -n "system: rendered context|AttachmentLifecycle|ProviderMessage|class Message\b|<task_goal>|<constraints>" src tests docs
```

搜索结果只允许出现在明确标记的历史文档或迁移测试中。

阶段完成报告必须列出：

| 设计要求 | 实现文件 | 测试或验证命令 | 状态 |
|---|---|---|---|
| 对应 Spec 条目 | 精确路径 | 精确命令 | complete / deferred / not applicable |

存在 deferred 或 incomplete 时只能报告“部分完成”。

## 13. 风险与控制

| 风险 | 当前证据 | 控制措施 |
|---|---|---|
| Renderer 改动面过大 | 约 30 个文件直接构造 ContextRenderer | Phase 1 先冻结 Renderer API，Phase 2 再迁移调用方 |
| Message 边界改动面过大 | 约 53 个文件依赖 MessageRuntime | 统一迁移 StoredMessage，不维护双写 |
| Sync/Async Loop 漂移 | 两套 Loop 都直接调用 RequestBuilder | RequestBuilder 保持唯一组装 Owner，并运行共同 Contract |
| Provider 顺序差异 | 各 Adapter 有独立序列化逻辑 | 使用同一 Provider Contract Matrix |
| Artifact 泄露敏感内容 | 旧 Attachment 持有 Path/Base64/Provider File ID | Store 与投影分离，默认 Trace 只记录 Handle |
| Public API 过宽 | Root 当前导入大量企业对象 | `0.2.0a1` 收敛 Root，模块级导入替代聚合导出 |
| 旧文档继续误导实现 | 多份旧 Spec 声明 system=all context | Phase 0 标记取代关系，测试主动检查漂移 |
| 并行分支互相覆盖 | Context、QueryLoop、Builder 是共享热点 | Wave 0 串行，Wave 1 单 Owner 文件矩阵 |
| Distributed 反向污染 Core | 当前 multi/channels 包含多种基础设施实现 | Phase 6 按 Port/Adapter 迁移，Core Import Test 禁止泄漏 |
| 超大模块继续吸收职责 | `multi/planner.py`、`channels/a2a_operations.py` 等文件已远超 800 行 | Phase 0 建立规模基线；后续触碰时按责任拆分，不再向超大文件追加独立子系统 |

## 14. Spec 覆盖矩阵

| Spec 能力 | 负责阶段 |
|---|---:|
| SystemEnvelope 与固定 System Slot | Phase 1 |
| ContextSnapshot、Slot Registry、XML Escape、Budget | Phase 1 |
| StoredMessage、ProviderInputItem、Frontend Read Model | Phase 2 |
| Tool Pair 和每次调用重组装 | Phase 2 |
| Artifact Catalog、Tool Result、ContextMount、Session Scope | Phase 3A |
| OpenAI/Anthropic/严格角色 Adapter | Phase 3B |
| Skill、Plan、Memory Projection | Phase 3C |
| Local Loop、渐进式 AgentBuilder、ToolCallScheduler | Phase 4 |
| Run 状态机与 WAITING/Continuation Kernel 语义 | Phase 4 |
| SQLite/Filesystem Durable Runtime、Wait Checkpoint | Phase 5 |
| PostgreSQL/Redis、Worker、Transport、Team、分布式 Wakeup/Claim | Phase 6 |
| Observability、Security、Public API、Release Evidence | 所有阶段的共同门禁 |

## 15. 详细计划生成顺序

本总体计划批准后，按以下顺序生成可直接执行的详细计划：

1. `2026-07-11-agentos-phase0-baseline-remediation-implementation-plan.md`
2. `2026-07-10-agentos-context-protocol-kernel-implementation-plan.md`
3. `2026-07-10-agentos-message-provider-boundary-implementation-plan.md`
4. `2026-07-10-agentos-artifact-vertical-slice-implementation-plan.md`
5. `2026-07-10-agentos-provider-adapter-contract-implementation-plan.md`
6. `2026-07-10-agentos-extension-projection-implementation-plan.md`
7. `2026-07-10-agentos-local-loop-integration-implementation-plan.md`
8. `2026-07-10-agentos-durable-profile-implementation-plan.md`
9. `2026-07-10-agentos-distributed-runtime-transport-implementation-plan.md`

每份详细计划必须包含精确代码片段、失败测试、运行命令、预期输出、提交边界和自审结果。前三份详细计划批准前，不启动任何实现 subagent。

## 16. 总体 Definition of Done

只有全部满足时，下一代 AgentOS SDK 架构迁移才算完成：

- Local Agent 零外部服务依赖；
- Context Protocol v1 是唯一默认 LLM 上下文协议；
- SystemEnvelope 和 ContextSnapshot Authority 分离；
- StoredMessage、ProviderInputItem、Read Model 和 Trace 分离；
- Artifact 原始内容不进入 MessageStore 或默认 Trace；
- Skill、Plan、Memory 是可组合 Extension；
- Local、Durable、Distributed 使用同一个 Kernel；
- PostgreSQL/Redis 只由 Distributed Extra 引入；
- Provider Adapter Contract Matrix 全部通过；
- 全量测试、compileall、ruff 和 drift scan 全部通过；
- Public API Inventory 与 `0.2.0a1` 实现一致；
- 所有阶段完成报告没有未声明的 deferred 项。
