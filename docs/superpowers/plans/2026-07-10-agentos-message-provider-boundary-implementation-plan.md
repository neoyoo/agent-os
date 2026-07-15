# AgentOS Message / Provider Boundary Implementation Plan

> **SUPERSEDED FOR LOOP TOPOLOGY:** 本文关于双 Loop、双 Runner 和旧 Agent API
> 的实施条款已被 `2026-07-12-agentos-single-async-query-loop-design.md` 取代。
> Task 0-12 的历史正文仅作为当时实施记录保留；当前执行游标从 Task 13 开始。

> **For development agents:** 按 `AGENTS.md`、工程规范、Scope Contract、独立双层 Review
> 和精确提交规则逐任务执行。本文不依赖任何外部 Skill 才能生效。

**Goal:** 把业务消息真值、临时 Provider 输入、前端 Read Model 和 Provider payload 彻底分离，并保证每一次物理 Provider attempt 都从权威状态重新构建不可变双平面请求。

**Architecture:** `MessageStore` 只保存 `StoredMessage`；`ProviderRequestBuilder` 是 Agent Turn 的 `SystemEnvelope + ContextSnapshot + Active StoredMessage + Tool Result + ContextMount + Tool Schemas` 唯一组装 Owner；专用 Runtime 可以按批准的 internal model task 平面直接构建仅含一个 `model_task` 的 `ProviderRequest`，但不能进入 Turn projection 或形成第二套 Provider/Retry 控制流；`ProviderInputItem` 是不可持久化的 Provider 无关逻辑输入。唯一异步 `ProviderAttemptRunner` 适配 native async 与同步 Provider capability，所有 Agent Turn retry 都通过同一 Request Factory 重建请求且不复用旧 Snapshot；具体 OpenAI/Anthropic payload 和严格角色合并仍留给 Phase 3B。

**Tech Stack:** Python 3.11+、frozen/slotted dataclasses、Protocol、pytest Unit/Contract/Integration tests、现有 Hook/Retry/Token 边界、Ruff。

---

## Readiness Gate

实现前必须同时满足：

1. Phase 1 Context Protocol Kernel 已进入 Phase 2 基线分支并通过全量验证；
2. `2026-07-11-agentos-message-provider-boundary-contract-addendum.md` 和本计划已由用户批准；
3. 本计划 Scope Contract 已再次发布；
4. Phase 1 的 `ContextRenderer.render() -> SystemEnvelope` 和 `ContextSnapshotRenderer.render() -> ContextSnapshot` 签名没有漂移。

任一条件不满足时，本计划只能审阅，不能启动实现 subagent。

### 2026-07-12 Execution Re-baseline

Gate 0 当前事实：

- Phase 1 基线提交为 `1b9f385bfc414dbffdf17332aadbacdc3e30b756`；
- Phase 1 分支已推送，用户确认 Pull Request 已创建；
- Phase 2 集成分支为 `feature/agentos-sdk-phase2-message-provider-boundary`，直接从上述提交创建；
- Phase 1 全量验证证据为 `1945 passed, 15 skipped`，Ruff、compileall、模块规模/Public API 测试和两层 Review 均通过；
- 当前有 48 个源码/测试文件直接依赖 `Message`、`ProviderMessage` 或 `materialize_provider_messages`；
- 当前有 101 个 `ProviderRequestBuilder(` 或 `ProviderRequest(` 构造/引用位置；
- 当前规模为 `query_loop.py=884`、`async_query_loop.py=570`、`providers/messages.py=353`、`builder.py=287` 行。

`ai-knowledge/wiki` 当前不在本工作区 checkout 中。实施者必须读取仓库内已固化的对应设计输入 `docs/plans/2026-06-10-kb-refresh-agentos-sdk-iteration.md`，以及 `AGENTS.md` 中的 query-loop、context-management、memory-system 和 session-recovery 映射；若后续恢复 `ai-knowledge` checkout，再补读原始页面，但不得因此改变已批准 Phase 2 Spec。

### 2026-07-14 Post Single-Async-Loop Historical Re-baseline

当日执行基线为：

- 分支：`feature/agentos-sdk-single-async-loop-impl`；
- 提交：`88ae4fff0bd1432b75b846914efe2c4d60c4b491`；
- Task 0-7 的领域目标已经完成，当时 Task 8-14 仍待执行；
- Kernel 已收敛为唯一异步 `QueryLoop.execute(RunRequest) -> AgentStream`；
- `Agent` 只有 `await run(input, stream=...)`，同步适配仅位于 `agentos.sync`；
- Provider attempt 只有一个 canonical async `ProviderAttemptRunner`，同步 Provider 在 capability 边界适配；
- Tool 批次并发只由 `ToolCallScheduler` 管理；
- WAITING 在权威提交后退出当前执行切片，本 Phase 不实现 durable wakeup/resume；
- 全量基线为 `2309 passed, 15 skipped`，Ruff、compileall、Public API、模块规模和双层 Review 全部通过；
- 当前规模为 `query_loop.py=493`、`provider_attempt.py=145`、`agent.py=144`、`agent_stream.py=249`、`providers/messages.py=335`、`builder.py=280` 行。

本节取代 2026-07-12 基线中的双 Loop、双 Runner、旧 Agent API、旧模块路径和旧规模事实。它已被下一节 2026-07-15 Wave 3 集成基线继续取代，只解释迁移历史。

### 2026-07-15 Post Wave 3 Integration Re-baseline

当前执行基线为：

- 分支：`feature/agentos-sdk-single-async-loop-impl`；
- 提交：`5eca3b8`；
- Tasks 8-12 已全部完成并集成，Task 13-14 待执行；
- Wave 3 定向测试为 `214 passed`；全量基线为 `2354 passed, 11 skipped`；
- Ruff、compileall、module-size gate、`git diff --check` 和逐任务双层 Review 全部通过；
- `persistence/serializers.py` 为 308 行，经职责审查仍是单一 SessionSnapshot codec Owner，已登记 `responsibility_review`，本 Phase 不机械拆分；
- 当前只允许从 Task 13A 开始串行修改共享 Provider 核心，Task 14 最后完成阶段验收。

## Execution Waves And Ownership

Phase 2 使用“核心接口串行冻结、外围消费者受控并行、公共 API 串行收口”的执行拓扑。共享核心文件不得由多个 worktree 同时修改。

| Wave | Tasks | Execution | Merge gate |
|---|---:|---|---|
| Wave 0 | Task 0 | 已完成 | Gate 0 事实、迁移清单和规模基线确认 |
| Wave 1 | Tasks 1-4 | 已完成 | `StoredMessage`、`ProviderInputItem`、Read Model 接口冻结 |
| Wave 2 | Tasks 5-7 | 已完成 | Request Builder 与每-attempt 重建契约冻结 |
| Wave 2S | 单一异步内核重构 | 已完成，基线 `88ae4ff` | 单一 QueryLoop/Runner、AgentStream、`agentos.sync`、WAITING 与 Tool Scheduler 契约冻结 |
| Wave 3A | Task 8 | 已完成并集成 | Persistence 定向测试、双层 Review、精确提交 |
| Wave 3B | Task 9 | 已完成并集成 | Compression/Recall 定向测试、双层 Review、精确提交 |
| Wave 3C | Task 10 | 已完成并集成 | Memory 定向测试、双层 Review、精确提交 |
| Wave 3D | Tasks 11-12 | 已完成并集成 | Policy/Capability/Debug 定向测试、双层 Review |
| Wave 4 | Task 13 | 当前串行执行 | `model_task` 契约、迁移桥和旧 Public API 零残留 |
| Wave 5 | Task 14 | 串行，待执行 | Contract Matrix、全量门禁和阶段双层 Review |

并行规则：

- Wave 0-2S 已完成，不得重新执行或恢复双 Loop/双 Runner 路径；
- Wave 3 的 worktree/subagent 流程已经结束，不得重放或改写其已集成提交；
- Task 13 共享 `providers/input.py`、`providers/base.py`、Adapter 和 Public API 边界，只允许单一 Owner 串行实施；独立 Reviewer 可以并行只读审查；
- `88ae4ff` 只作为单一异步内核历史审计基线，当前实现与验证以 `5eca3b8` 及其后续精确提交为准；
- Task 13 前必须重新运行旧名称 drift scan；Task 14 前不得保留任何未声明迁移桥。

### Compatibility Budget

本项目尚未生产推广，Phase 2 优先选择清晰的 breaking migration，不为历史调用方式设计长期兼容架构。允许的临时兼容仅有：

1. `Message = StoredMessage` 同一类身份 alias，用于让分阶段提交保持可运行；不得增加 wrapper、subclass、双写、行为分支或第二套 serializer，并在 Task 13 删除；
2. 现有 Public Attachment 行为的私有 ProviderInput 投影桥，仅维持当前图片能力，并在 Phase 3A 由正式 ContextMount 替换。

除以上两项外，不得新增 deprecated facade、legacy DTO、旧新字段双读、自动猜测迁移、版本分支或 Adapter-specific 领域字段。任何新增兼容需求都必须先停止实现、更新 Spec 并获得批准；Phase 2 最终 Public API 只保留正式的 `StoredMessage` 和 `ProviderInputItem` 边界。

## Mandatory Execution Bootstrap And Review Gate

Task 13 前依次完整读取 `AGENTS.md`、工程规范、两份批准 Spec、单一异步 QueryLoop Spec、已批准 addendum、本计划、`5eca3b8` 实施结果、将修改的源码/相邻测试及对应 `ai-knowledge/wiki` 页面，并重新发布 Scope Contract。上下文压缩、交接或 Agent 替换后重复。

剩余 Task 13-14 每个任务都必须执行 Red -> 最小实现 -> 模块 Green -> 独立 Spec Compliance Review -> 修复 Critical/Important -> 独立 Code Quality Review -> 精确暂存提交。两层 Reviewer 均 `APPROVED` 前不得提交或进入下一任务；禁止 `git add .`。Task 14 的阶段 Review 是跨模块额外验收，不替代逐任务门禁。

## Scope Contract

- **Phase / Active Specs:** Phase 2；两份 2026-07-10 已批准 Spec、2026-07-11 Message/Provider Contract Addendum、2026-07-12 单一异步 QueryLoop Spec，以及当前实现基线 `5eca3b8`。
- **Acceptance Items:** `StoredMessage` 成为唯一业务消息 Public 名称；Persistence、Compression/Recall、Memory、Policy/Capability、Debug Projection 全部迁移到该真值类型；temporary recall 原子进入 temporary window；`model_task` 成为 Runtime 内部模型任务的唯一 Provider 输入；全部旧 Message/Provider DTO、serializer 和 Phase 2 迁移桥删除；stream/non-stream 共用同一 QueryLoop/AgentStream 事件源；Task 14 Contract Matrix 与全量门禁通过。
- **Allowed Files:** Task 13-14 各节明确列出的 builder、messages、providers、attachments、observability、compression、example、测试、Public API inventory、module-size baseline 和稳定性文档。Task 13 只为内部模型输入和旧边界收口修改其明确列出的消费者，不改变对应模块的其他业务语义。
- **Forbidden Files:** `runtime/query_loop.py`、`runtime/provider_attempt.py`、`runtime/agent.py`、`runtime/agent_stream.py`、`src/agentos/sync/**` 的新控制流；第二 Loop/Runner；Artifact Store/Runtime/Projection；Attachment 生命周期重写；Skill/Plan/Memory Projection；Provider-specific strict-role merge/File ID/cache 优化；Distributed/Transport 语义；Root API 既定范围之外的公共扩张。
- **Dependency Boundaries:** `messages` 不导入 `providers`；`ProviderRequest.messages` 只接受 `ProviderInputItem`；Provider Input Projection 位于 `providers/input.py` 或 Request Builder 的纯函数边界；`model_task` 只由专用 Runtime 直接构建，QueryLoop RequestBuilder/Turn projector 不生成；Provider Adapter 不读取 MessageStore/ContextRuntime；Read Model 不读取 ProviderRequest Transcript。
- **Completed In This Work Package:** Task 0-12、M2 Core Request Pipeline、业务/Provider/前端三类模型分离、每 attempt 重建、单一异步 QueryLoop/ProviderAttemptRunner、AgentStream 生命周期、`agentos.sync` 适配、WAITING、Tool Scheduler、Persistence、Compression/Recall、Memory、Policy/Capability 和 Debug Projection 边界。
- **Explicit Deferrals:** Artifact Catalog/Mount/Session Scope 到 Phase 3A；Phase 2 只保留现有 Public Attachment API 的私有 ProviderInput 兼容桥，不扩展新附件语义，并在 Phase 3A 由正式 ContextMount 替换；完整 Adapter Contract/严格角色合并到 Phase 3B；真实 Skill/Plan/Memory Projection 到 Phase 3C；Local Tool Scheduler/Public root 收敛到 Phase 4。`providers/openai_compatible.py` 已超过 800 行，Phase 2 禁止继续净增长；Phase 3B 必须先以现有 adapter tests 固化 payload、stream、timeout 和错误映射，再按顺序提取 `openai_compatible_wire.py`（request/tool/content 序列化纯函数）、`openai_compatible_parsing.py`（response/stream 解析纯函数）和 `openai_compatible_transport.py`（HTTP/timeout I/O），最后由 `openai_compatible.py` 只保留 Provider 门面与生命周期协调。拆分期间不得改变 Public API、retry 或 wire 语义，目标是门面文件 `<300` 且三个目标模块各 `<500`。
- **Verification Commands:** 每任务定向 pytest；Phase 1+2 Contract Matrix；全量 pytest；compileall；ruff；public inventory generator；单 Loop/旧 Message/旧 Provider 名称 drift scan；module-size baseline generator/gate；diff check。

## File Responsibility Map

| File | Single responsibility |
|---|---|
| `providers/json_values.py` | JSON-like 值的递归校验、冻结与 thaw，不含任何领域语义。 |
| `_internal_transcript.py` | Provider/Context 内部投影对象的中立 nominal marker，供 Read Model fail-closed。 |
| `artifacts/types.py` | 只冻结 `ArtifactRef` 轻量值类型。 |
| `messages/types.py` | `StoredMessage`、`MessageRef`、`ToolCall` 业务领域值。 |
| `messages/store.py` | append-only StoredMessage 真值。 |
| `messages/window.py` | Active refs、temporary refs 和 Tool Pair 保护。 |
| `messages/runtime.py` | Store/Window 门面，不做 Provider 投影。 |
| `messages/read_model.py` | StoredMessage + 显式 Event Projector 到 Conversation Read Model。 |
| `providers/input.py` | `ProviderInputItem`、ContentPart 和逻辑输入自身校验；不导入 StoredMessage。 |
| `providers/tool_specs.py` | Provider tool schema 值类型及 JSON-safe serializer，不包含消息 DTO。 |
| `providers/base.py` | 不可变 `ProviderRequest`/Response/Provider Protocol。 |
| `runtime/provider_request_builder.py` | 每次调用组装双平面逻辑请求并返回本次投影 receipt。 |
| `runtime/message_projection.py` | StoredMessage/MessageRef 到 ProviderInputItem 的纯映射。 |
| `attachments/runtime.py` | 保留现有附件行为；Phase 2 只增加私有 ProviderInput 兼容入口。 |
| `runtime/provider_attempt.py` | 唯一异步 attempt 的重建、Provider capability 适配、Hook、Retry 和成功消费边界。 |

## Historical Execution Record: Tasks 0-7

以下任务已经完成。其双 Loop、双 Runner、旧 Agent API 和旧路径描述只用于解释当时迁移过程，不得重新执行，也不得覆盖 2026-07-14 re-baseline。

---

### Task 0: Gate 0 确认与 Breaking Migration 基线

**Files:**
- Read only: `docs/superpowers/specs/2026-07-11-agentos-message-provider-boundary-contract-addendum.md`
- Read only: `docs/api-stability.md`

- [x] **Step 1: 确认 Architecture Owner 批准和 Phase 1 远端固化**

用户已批准 addendum、本计划和 Phase 2 下一步，并确认 Phase 1 Pull Request 已创建。Phase 2 集成分支已从 Phase 1 提交 `1b9f385` 创建；该步骤不修改业务代码。

- [x] **Step 2: 固定只读迁移搜索和模块规模基线**

```powershell
rg -l "\bMessage\b|ProviderMessage|materialize_provider_messages" src/agentos tests | Sort-Object
rg -n "ProviderRequestBuilder\(|ProviderRequest\(" src/agentos tests
Get-ChildItem src/agentos/runtime/query_loop.py,src/agentos/runtime/async_query_loop.py,src/agentos/providers/messages.py,src/agentos/builder.py | ForEach-Object { "{0}: {1}" -f $_.Name,(Get-Content -Encoding utf8 $_).Count }
```

Expected: 48 个旧消息边界依赖文件、101 个 Provider Request 构造/引用位置；规模分别为 884、570、353、287 行。迁移期间只允许 `messages/_migration.py` 和 Adapter 私有兼容入口保留旧名，最终 Public API 收口任务必须删除这些桥。

- [ ] **Step 3: 发布实施 Scope Contract**

实施主 Agent 在 commentary 中重新发布本计划七项 Scope Contract。Task 0 不产生提交；批准后的 addendum 状态更新与 API stability 记录在最终 Public API 收口任务中精确提交。

---

### Task 1: 递归冻结 JSON、ArtifactRef 和 StoredMessage（加法迁移）

**Files:**
- Create: `src/agentos/_frozen_json.py`
- Create: `src/agentos/artifacts/types.py`
- Create: `src/agentos/artifacts/__init__.py`
- Modify: `src/agentos/messages/types.py`
- Create: `src/agentos/messages/_migration.py`
- Modify: `src/agentos/messages/__init__.py`
- Modify: `src/agentos/messages/runtime.py`（仅现有 Provider 输出边界 thaw）
- Modify: `src/agentos/persistence/serializers.py`（仅 ToolCall 输出边界 thaw）
- Modify: `src/agentos/memory/serializers.py`（仅 ToolCall 输出边界 thaw）
- Create: `tests/test_frozen_json.py`
- Test: `tests/messages/test_stored_message.py`
- Test: `tests/messages/test_runtime.py`
- Test: `tests/persistence/test_serializers.py`
- Test: `tests/memory/test_types.py`
- Test: `tests/recall/test_runtime.py`（仅 tuple 领域契约断言）

- [ ] **Step 1: 写不可变、复制和禁止 Provider metadata 的 Red 测试**

```python
from dataclasses import FrozenInstanceError, fields

import pytest

from agentos.artifacts import ArtifactRef
from agentos.messages import StoredMessage, ToolCall


def test_stored_message_is_frozen_and_uses_tuple_boundaries() -> None:
    artifact = ArtifactRef("art_1", "drawing.png", "image/png")
    message = StoredMessage(
        id="msg_1",
        role="user",
        content="分析图纸",
        artifact_refs=(artifact,),
        tool_calls=(ToolCall("call_1", "read_file", {"path": "README.md"}),),
    )
    with pytest.raises(FrozenInstanceError):
        message.content = "mutated"  # type: ignore[misc]
    assert isinstance(message.artifact_refs, tuple)
    assert isinstance(message.tool_calls, tuple)


def test_tool_call_arguments_are_recursively_immutable() -> None:
    call = ToolCall("call_1", "search", {"filters": {"tags": ["a"]}})
    with pytest.raises(TypeError):
        call.arguments["filters"]["tags"] += ("b",)  # type: ignore[index,operator]


def test_freeze_json_rejects_non_json_and_non_finite_numbers() -> None:
    with pytest.raises(TypeError, match="JSON-compatible"):
        freeze_json({"bad": object()})
    with pytest.raises(ValueError, match="finite"):
        freeze_json(float("nan"))


def test_stored_message_schema_excludes_provider_runtime_fields() -> None:
    names = {item.name for item in fields(StoredMessage)}
    assert not names & {
        "origin", "authority", "persistence", "visibility",
        "provider_file_id", "signed_url", "path", "base64",
    }
```

- [ ] **Step 2: 运行 Red**

```powershell
python -m pytest tests/test_frozen_json.py tests/messages/test_stored_message.py -q
```

- [ ] **Step 3: 实现值类型**

`_frozen_json.py` 冻结以下闭集，不接受任意 `Mapping` 实现泄漏到领域对象：

```python
JsonScalar: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonScalar | tuple["JsonValue", ...] | FrozenJsonObject


class FrozenJsonObject(Mapping[str, JsonValue]):
    """只读、保持插入顺序且可比较的 JSON object。"""


def freeze_json(value: object) -> JsonValue: ...
def thaw_json(value: JsonValue) -> object: ...
```

`freeze_json()` 递归把 list/tuple 转为 tuple、dict 转为 `FrozenJsonObject`，拒绝非字符串 key、非 JSON 值、NaN 和 Infinity。`thaw_json()` 只供 Provider wire adapter/serializer 在边界生成新 dict/list，绝不返回内部可变引用。

```python
@dataclass(frozen=True, slots=True)
class ArtifactRef:
    """StoredMessage 保存的附件轻量引用。"""

    artifact_id: str
    filename: str | None
    media_type: str


@dataclass(frozen=True, slots=True)
class StoredMessage:
    """MessageStore 中 append-only 保存的业务消息真值。"""

    id: str
    role: MessageRole
    content: str
    artifact_refs: tuple[ArtifactRef, ...] = ()
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "artifact_refs", tuple(self.artifact_refs))
        object.__setattr__(self, "tool_calls", tuple(self.tool_calls))
```

`ToolCall.arguments` 在 `__post_init__` 调用 `freeze_json()`，因此实例构造后不能直接修改任意嵌套值。此任务只增加新类型：由于当前 Store/Window/Runtime 和 persistence serializer 仍直接从 `messages.types` 导入旧名称，`messages/types.py` 暂时保留 **单一类身份 alias** `Message = StoredMessage`；`messages/_migration.py` 只集中重导出同一绑定，不能定义第二个类、包装器或第二份领域模型。`messages.__init__` 暂时继续导出该绑定，以保证当前消费者构造的对象天然就是 `StoredMessage`。测试断言 `Message is StoredMessage`、`agentos.messages.types.Message is StoredMessage`、Store 接受旧 import 构造值、相等性/序列化没有双模型分支。两个桥位置都必须带 `# Phase 2 migration bridge; remove in Task 13`，不得新增业务调用者；Task 2/8 迁移直接内部 import 后，Task 13 一次性删除旧名称。`StoredMessage.to_provider_dict()` 不存在，业务类型不能知道 Provider 形态。

深冻结会使现有直接输出边界中的浅层 `dict(tool_call.arguments)` 留下嵌套 `FrozenJsonObject`，并破坏 Provider 投影、JSON serializer 和 `MessageStore.put()` 的冲突判定。Task 1 因此必须在同一原子提交中完成以下稳定化，不得用 `__deepcopy__`、伪 dict 行为或第二套 serializer 掩盖边界错误：

- `FrozenJsonObject` 使用 JSON 类型敏感的递归等价，至少保证 `true`、`1` 和 `1.0` 不互相等价；
- JSON 闭集只接受精确内建类型，拒绝携带自定义行为或可变附加状态的内建类型子类；循环容器稳定抛出 `ValueError("circular JSON value")`，共享但无环的输入仍允许；
- `FrozenJsonObject` 禁止继承，并提供与类型敏感、对象键无序等价一致的稳定进程内哈希，使包含它的 frozen 领域值不会暴露虚假 Hashable 契约；
- 作为不可变值对象，`FrozenJsonObject` 的 `copy`/`deepcopy` 安全返回自身，pickle round-trip 重建同语义精确类型；该协议不能替代 Provider/serializer 输出边界的显式 `thaw_json()`；
- `MessageRuntime` 现有 Provider 投影在构造 `ProviderToolCall` 前调用 `thaw_json()`；
- persistence 和 memory 的现有 `tool_call_to_dict()` 在 JSON 输出边界调用 `thaw_json()`；
- 对应测试必须覆盖嵌套 object/list 可被标准 `json.dumps()` 序列化，以及同 ID 消息只因 bool/int/float 参数不同就产生冲突。

这里不提前迁移 Store、Memory、Persistence 的类型所有权，不改变 wire schema，也不新增兼容入口。Task 2、8、10 仍负责正式类型迁移和模块 Owner 收口。

- [ ] **Step 4: Green 和提交**

```powershell
python -m pytest tests/test_frozen_json.py tests/messages/test_stored_message.py tests/messages/test_runtime.py tests/persistence/test_serializers.py tests/memory/test_types.py tests/recall/test_runtime.py tests/messages -q
git add -- docs/superpowers/plans/2026-07-10-agentos-message-provider-boundary-implementation-plan.md src/agentos/_frozen_json.py src/agentos/artifacts/types.py src/agentos/artifacts/__init__.py src/agentos/messages/types.py src/agentos/messages/_migration.py src/agentos/messages/__init__.py src/agentos/messages/runtime.py src/agentos/persistence/serializers.py src/agentos/memory/serializers.py tests/test_frozen_json.py tests/messages/test_stored_message.py tests/messages/test_runtime.py tests/persistence/test_serializers.py tests/memory/test_types.py tests/recall/test_runtime.py
git commit -m "feat: define stored message truth model"
```

---

### Task 2: 迁移 MessageStore/ActiveWindow 并增加精确 temporary 消费

**Files:**
- Modify: `src/agentos/messages/store.py`
- Modify: `src/agentos/messages/window.py`
- Modify: `src/agentos/messages/runtime.py`
- Modify: `src/agentos/messages/_migration.py`
- Modify: `src/agentos/messages/__init__.py`
- Modify: `tests/messages/test_runtime.py`
- Create: `tests/messages/test_temporary_recall_lifecycle.py`

- [ ] **Step 1: 写 Provider 隔离和显式 temporary 消费 Red 测试**

```python
import inspect

from agentos.messages import MessageRuntime, StoredMessage


def test_materialize_active_does_not_consume_temporary_refs() -> None:
    runtime = MessageRuntime()
    recalled = StoredMessage(id="msg_99", role="user", content="old fact")
    runtime.hydrate_messages([recalled])
    runtime.active_window.prepend_temporary([recalled.id])

    assert runtime.materialize_active() == [recalled]
    assert runtime.materialize_active() == [recalled]
    runtime.consume_temporary_refs((recalled.id,))
    assert runtime.materialize_active() == []


def test_consumption_does_not_clear_temporary_refs_added_after_build() -> None:
    runtime = runtime_with_temporary("msg_1")
    receipt_ids = runtime.snapshot_active_with_refs()[0][0].message_id,
    runtime.add_temporary("msg_2")
    runtime.consume_temporary_refs(receipt_ids)
    assert runtime.temporary_message_ids() == ("msg_2",)
```

- [ ] **Step 2: 运行 Red**

```powershell
python -m pytest tests/messages/test_runtime.py tests/messages/test_temporary_recall_lifecycle.py -q
```

- [ ] **Step 3: 一次性迁移 Store 和 Runtime**

Store、Window 和 Runtime 内部真值改为 `StoredMessage`；Store 保留 append-only、idempotent `put` 和 `msg_N` 恢复语义。增加：

```python
def snapshot_active_with_refs(
    self,
) -> tuple[tuple[MessageRef, StoredMessage], ...]: ...

def consume_temporary_refs(self, message_ids: tuple[str, ...]) -> None: ...
```

`consume_temporary_refs()` 只删除 `temporary=True` 且 ID 位于参数闭集中的 ref，不得清除构建 receipt 之后加入的 ref。`materialize_active()` 永远无副作用。

为了使该提交全仓 Green，`materialize_provider_messages()` 与 `_to_provider_message()` 暂时移动到明确标记的 `_migration.py` 私有函数，Runtime 方法只委托给它们，继续服务尚未迁移的 QueryLoop；它们不得消费 temporary refs，也不得新增调用者。Task 7 完成 Request Builder 接线后删除 Provider 投影委托；类型 alias 到 Task 13 Public 收口时删除。architecture test 最终证明 `messages` 不再导入 `providers`。

- [ ] **Step 4: Green 和提交**

```powershell
python -m pytest tests/messages -q
git add -- src/agentos/messages/store.py src/agentos/messages/window.py src/agentos/messages/runtime.py src/agentos/messages/_migration.py src/agentos/messages/__init__.py tests/messages/test_runtime.py tests/messages/test_temporary_recall_lifecycle.py
git commit -m "refactor: isolate stored message runtime"
```

---

### Task 3: 引入 ProviderInputItem 和不可变 ProviderRequest

**Files:**
- Create: `src/agentos/_internal_transcript.py`
- Modify: `src/agentos/context/models.py`
- Create: `src/agentos/providers/input.py`
- Modify: `src/agentos/providers/base.py`
- Modify: `src/agentos/providers/messages.py`
- Modify: `src/agentos/providers/__init__.py`
- Modify: `src/agentos/providers/openai.py`（仅 Frozen JSON wire thaw）
- Modify: `src/agentos/providers/openai_compatible.py`（仅 Frozen JSON wire thaw）
- Modify: `src/agentos/providers/anthropic.py`（仅 Frozen JSON wire thaw）
- Modify: `src/agentos/runtime/query_loop.py`（仅 ProviderToolCall -> Stored ToolCall 冻结值传递和 signature thaw）
- Modify: `src/agentos/runtime/async_query_loop.py`（仅 ProviderToolCall -> Stored ToolCall 冻结值传递）
- Modify: `src/agentos/observability/snapshots.py`（仅 Frozen JSON capture/hash thaw）
- Modify: `src/agentos/capabilities/executor.py`（仅单次 Tool 执行参数 thaw）
- Modify: `src/agentos/capabilities/router.py`（仅 Context/MCP Tool 参数 thaw）
- Modify: `src/agentos/capabilities/mcp.py`（typed ProviderToolSpec 与 MCP call 参数 thaw）
- Modify: `tests/providers/test_provider_messages.py`
- Create: `tests/providers/test_provider_input_contract.py`
- Modify: 直接构造 `ProviderRequest`、断言 tuple 边界或验证上述 serializer 的现有 tests
- Modify: `docs/governance/agentos-module-size-baseline.json`

- [ ] **Step 1: 写元数据矩阵、不可变和 legacy dict 拒绝测试**

```python
from dataclasses import FrozenInstanceError

import pytest

from agentos.providers import ProviderInputItem, ProviderRequest, TextPart


def test_context_snapshot_item_has_sdk_fixed_metadata() -> None:
    item = ProviderInputItem.context_snapshot("<context-snapshot/>\n")
    assert (
        item.role, item.kind, item.origin, item.authority,
        item.persistence, item.visibility,
    ) == (
        "user", "context_snapshot", "runtime", "context_data",
        "ephemeral", "internal",
    )
    assert item.content == (TextPart("<context-snapshot/>\n"),)


def test_provider_request_is_deeply_immutable() -> None:
    request = ProviderRequest(
        system="system",
        messages=(ProviderInputItem.business_user("hello"),),
        tools=(
            ProviderToolSpec(
                function=ProviderFunctionSpec(
                    name="search",
                    description="search",
                    parameters={"type": "object", "properties": {}},
                ),
            ),
        ),
    )
    with pytest.raises(FrozenInstanceError):
        request.system = "mutated"  # type: ignore[misc]
    with pytest.raises(TypeError):
        request.tools[0].function.parameters["properties"]["q"] = {}  # type: ignore[index]


@pytest.mark.parametrize(
    "factory, expected",
    [
        (lambda: ProviderInputItem.context_snapshot("<context-snapshot/>"), ("user", "context_snapshot", "runtime", "context_data", "ephemeral", "internal")),
        (lambda: ProviderInputItem.business_user("hello"), ("user", "business_message", "message_store", "conversation_data", "stored", "conversation")),
        (lambda: ProviderInputItem.business_assistant("hello"), ("assistant", "business_message", "message_store", "conversation_data", "stored", "conversation")),
        (lambda: ProviderInputItem.tool_result("call_1", "ok"), ("tool", "tool_result", "message_store", "tool_data", "stored", "internal")),
        (lambda: ProviderInputItem.recalled_user("old"), ("user", "recalled_message", "recall_runtime", "conversation_data", "ephemeral", "internal")),
        (lambda: ProviderInputItem.recalled_assistant("old"), ("assistant", "recalled_message", "recall_runtime", "conversation_data", "ephemeral", "internal")),
        (lambda: ProviderInputItem.recalled_tool("call_1", "old"), ("tool", "recalled_message", "recall_runtime", "tool_data", "ephemeral", "internal")),
        (lambda: ProviderInputItem.context_mount((ImagePart("art_1"),)), ("user", "context_mount", "artifact_runtime", "artifact_data", "ephemeral", "internal")),
    ],
)
def test_provider_input_factories_freeze_the_metadata_matrix(factory, expected) -> None:
    item = factory()
    assert (item.role, item.kind, item.origin, item.authority, item.persistence, item.visibility) == expected


@pytest.mark.parametrize(
    "overrides",
    [
        {"role": "assistant", "kind": "context_snapshot"},
        {"kind": "tool_result", "tool_call_id": None},
        {"kind": "business_message", "origin": "recall_runtime"},
        {"kind": "recalled_message", "persistence": "stored"},
        {"kind": "context_mount", "authority": "conversation_data"},
    ],
)
def test_provider_input_rejects_cross_matrix_combinations(overrides) -> None:
    with pytest.raises(ValueError, match="metadata matrix"):
        raw_provider_input(**overrides)


def test_provider_request_rejects_legacy_dict_messages() -> None:
    with pytest.raises(TypeError, match="ProviderInputItem"):
        ProviderRequest(system="system", messages=({"role": "user"},))  # type: ignore[arg-type]
```

- [ ] **Step 2: 运行 Red**

```powershell
python -m pytest tests/providers/test_provider_input_contract.py -q
```

- [ ] **Step 3: 实现逻辑输入闭集**

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

    def __post_init__(self) -> None:
        object.__setattr__(self, "content", tuple(self.content))
        object.__setattr__(self, "tool_calls", tuple(self.tool_calls))
        _validate_provider_input_item(self)

    @classmethod
    def context_snapshot(cls, xml: str) -> "ProviderInputItem":
        return cls(
            role="user", kind="context_snapshot", origin="runtime",
            authority="context_data", persistence="ephemeral",
            visibility="internal", content=(TextPart(xml),),
        )
```

`ProviderToolCall` 与 `ProviderContentPart` 一并迁入 `input.py`，避免 `input.py -> base.py -> input.py` 循环依赖；`base.py` 只 re-export/使用这些逻辑类型。保留现有 `ProviderToolSpec(function=ProviderFunctionSpec(...))` 结构，不做 breaking schema redesign；深冻结发生在 `ProviderFunctionSpec.parameters`。`input.py` 不导入 `StoredMessage`，StoredMessage 映射由 Task 5 的 runtime 纯投影模块拥有。

`ProviderInputItem` 的公开创建入口按五种 kind 提供受控工厂；直接构造仍执行同一完整矩阵校验。Temporary recalled tool result 使用 `role="tool"`、`kind="recalled_message"`、`authority="tool_data"`，与 addendum 一致。

`_internal_transcript.py` 只定义无方法的 nominal marker `InternalTranscriptValue`。`ContextSnapshot`、`ProviderInputItem`、`ProviderRequest` 和 `ProviderResponse` 显式继承它；该中立模块不导入 context/messages/providers。Read Model 因而可以 fail-closed 拒绝内部投影对象，而无需 `messages -> providers` 反向依赖或按模块名猜测。

`ProviderToolCall.arguments`、`ProviderToolSpec.function.parameters` 和任何 JSON-like ContentPart metadata 均调用 Task 1 的 `freeze_json()`；Adapter 只在 wire 边界调用 `thaw_json()`。`ProviderRequest.__post_init__` tuple-normalize 已类型化输入并拒绝 dict，因此“深不可变”包括直接修改嵌套 Tool arguments/schema parameters 的失败测试。旧 `ProviderMessage` 暂时保留为 Adapter 私有迁移入口，不从新模块引用；Task 13 删除 Public export 和桥。

该 breaking type flip 不能只通过 Task 3 的新测试后提交。Task 3 必须在同一原子提交中完成所有直接消费者的稳定化，保证提交结束时全仓 Green：

- 现有 `ProviderRequest` fixture 和 Hook replacement 改为强类型 `ProviderMessage`/`ProviderToolSpec` 与 tuple 断言；不得恢复 dict 自动转换；
- 当前 Capability/Router 生产路径已经提供 `ProviderToolSpec`；直接传 dict 的 Builder 测试 fixture 改为强类型，不给 Builder 增加转换分支；Task 5 仍负责最终双平面 Builder 接口；
- OpenAI、OpenAI-compatible 和 Anthropic 只在最终 wire payload 中对 tool arguments/schema parameters 调用 `thaw_json()`；不得提前实现 `ProviderInputItem` 双读、严格角色合并或 File ID 优化；
- QueryLoop/AsyncQueryLoop 把 Provider tool call 转回 Stored `ToolCall` 时直接传递不可变 `FrozenJsonObject`，不再做会残留嵌套冻结值的浅层 `dict()`；同步 Loop 只在生成 JSON 去重 signature 时显式 `thaw_json()`；不得改变 Loop 控制流、retry 或 scheduler 语义；
- Observability snapshot 在 capture/hash 序列化边界显式 thaw，不修改 capture policy、脱敏或 Trace schema；
- ToolExecutor、Context Tool 和 MCP 在单次执行入口把 `FrozenJsonObject` thaw 为新的 dict/list 副本，再执行 schema 校验、Sandbox 和用户 handler；MCP Registry 直接生成 `ProviderToolSpec`，不保留与返回注解冲突的 dict schema；
- `providers/messages.py` 移动类型后同步重新生成 module-size baseline；Public export 变化同步重新生成 inventory；
- Python 运行时新增的类内部元数据不得使 nominal marker 测试依赖精确 `vars()` 闭集，测试只约束 marker 无公开行为和无实例状态。

这些修改是深冻结契约的直接输出边界稳定化，不是 Task 5-7 的行为偷跑，也不能通过 facade、wrapper、旧新双写、第二 serializer 或可变 dict 回退替代。

- [ ] **Step 4: Green 和提交**

```powershell
$python = Resolve-Path '.\.venv\Scripts\python.exe'
& $python -m pytest tests/providers tests/messages/test_runtime.py tests/runtime/test_provider_request_builder.py tests/runtime/test_query_loop.py tests/runtime/test_async_query_loop_native.py tests/runtime/test_query_loop_hooks.py tests/runtime/test_agent_builder.py tests/runtime/test_agent_stream_api.py tests/runtime/test_skill_mcp_tool_loop.py tests/runtime/test_streaming_tool_loop.py tests/runtime/test_tool_loop.py tests/capabilities tests/multi/test_continuation.py tests/examples/test_small_openai_agent.py tests/observability tests/context/test_context_protocol_models.py tests/architecture/test_module_size_baseline.py tests/architecture/test_public_api.py tests/architecture/test_public_api_inventory.py tests/architecture/test_public_api_inventory_cli.py -q
& $python -m pytest -q
& $python -m compileall -q src tests
& $python -m ruff check src tests
& $python scripts/generate_public_api_inventory.py --policy docs/public-api-stability.json --output docs/public-api-inventory.json
& $python scripts/generate_module_size_baseline.py --root src/agentos --output docs/governance/agentos-module-size-baseline.json
& $python -m pytest tests/architecture/test_module_size_baseline.py tests/architecture/test_public_api.py tests/architecture/test_public_api_inventory.py tests/architecture/test_public_api_inventory_cli.py -q
git diff --check
git add -- docs/superpowers/plans/2026-07-10-agentos-message-provider-boundary-implementation-plan.md docs/governance/agentos-module-size-baseline.json src/agentos/_internal_transcript.py src/agentos/context/models.py src/agentos/providers/input.py src/agentos/providers/base.py src/agentos/providers/messages.py src/agentos/providers/__init__.py src/agentos/providers/openai.py src/agentos/providers/openai_compatible.py src/agentos/providers/anthropic.py src/agentos/runtime/query_loop.py src/agentos/runtime/async_query_loop.py src/agentos/observability/snapshots.py src/agentos/capabilities/executor.py src/agentos/capabilities/router.py src/agentos/capabilities/mcp.py tests/providers tests/messages/test_runtime.py tests/runtime/test_provider_request_builder.py tests/runtime/test_query_loop.py tests/runtime/test_async_query_loop_native.py tests/runtime/test_query_loop_hooks.py tests/runtime/test_agent_builder.py tests/runtime/test_agent_stream_api.py tests/runtime/test_skill_mcp_tool_loop.py tests/runtime/test_streaming_tool_loop.py tests/runtime/test_tool_loop.py tests/capabilities tests/multi/test_continuation.py tests/examples/test_small_openai_agent.py tests/observability tests/architecture/test_module_size_baseline.py tests/architecture/test_public_api.py tests/architecture/test_public_api_inventory.py tests/architecture/test_public_api_inventory_cli.py
git commit -m "feat: define immutable provider input items"
```

---

### Task 4: 用显式 Projector 构建 Frontend Conversation Read Model

**Files:**
- Create: `src/agentos/messages/read_model.py`
- Modify: `src/agentos/messages/__init__.py`
- Create: `tests/messages/test_read_model.py`

- [ ] **Step 1: 写排除内部输入和显式事件投影 Red 测试**

```python
from agentos.messages import (
    ConversationEventItem,
    ConversationReadModel,
    StoredMessage,
)


class ApprovalProjector:
    event_type = ApprovalGranted

    def project(self, event: ApprovalGranted) -> ConversationEventItem | None:
        return ConversationEventItem(event.id, "approval", "已批准")


def test_read_model_uses_business_messages_and_registered_event_projectors() -> None:
    model = ConversationReadModel(projectors=(ApprovalProjector(),))
    result = model.build(
        messages=(StoredMessage("msg_1", "user", "你好"),),
        events=(InternalTrace(), ApprovalGranted("evt_1")),
    )
    assert [item.content for item in result] == ["你好", "已批准"]


@pytest.mark.parametrize(
    "internal",
    [
        ProviderRequest(system="s", messages=(), tools=()),
        ProviderInputItem.context_snapshot("<context-snapshot/>"),
        ProviderResponse(content="internal"),
    ],
)
def test_read_model_rejects_provider_transcript_objects(internal: object) -> None:
    with pytest.raises(TypeError, match="domain event"):
        ConversationReadModel().build(messages=(), events=(internal,))
```

- [ ] **Step 2: 运行 Red**

```powershell
python -m pytest tests/messages/test_read_model.py -q
```

- [ ] **Step 3: 实现默认拒绝的 Read Model**

Read Model 默认只输出 user/assistant StoredMessage；tool StoredMessage 不作为用户消息显示。`UserVisibleConversationEvent` 是显式 marker base，projector 注册时验证 `event_type` 是其子类；未继承 marker 或未注册的领域事件默认跳过。实现只导入中立的 `InternalTranscriptValue`，先对其抛 `TypeError`，再做领域事件 projector 分派；因此 ProviderRequest、ProviderInputItem、ProviderResponse、ContextSnapshot 和其他 internal transcript 即使遇到宽泛 projector 也不能绕过，同时保持 `messages` 不导入 `providers`。

```python
@dataclass(frozen=True, slots=True)
class ConversationMessageItem:
    message_id: str
    role: Literal["user", "assistant"]
    content: str
    artifact_refs: tuple[ArtifactRef, ...] = ()


ConversationItem: TypeAlias = ConversationMessageItem | ConversationEventItem
```

- [ ] **Step 4: Green 和提交**

```powershell
python -m pytest tests/messages/test_read_model.py -q
git add -- src/agentos/messages/read_model.py src/agentos/messages/__init__.py tests/messages/test_read_model.py
git commit -m "feat: add explicit conversation read model"
```

---

### Task 5: 让 ProviderRequestBuilder 每次构建双平面请求

**Files:**
- Modify: `docs/superpowers/plans/2026-07-10-agentos-message-provider-boundary-implementation-plan.md`
- Modify: `docs/public-api-inventory.json`
- Modify: `src/agentos/attachments/runtime.py`
- Create: `src/agentos/runtime/message_projection.py`
- Modify: `src/agentos/runtime/provider_request_builder.py`
- Modify: `tests/runtime/test_provider_request_builder.py`
- Create: `tests/runtime/test_provider_request_rebuild.py`
- Modify: `tests/runtime/test_query_loop.py`

- [ ] **Step 1: 写顺序、重建、Tool Pair 和无副作用 Red 测试**

```python
def test_builder_places_fresh_snapshot_before_active_messages() -> None:
    context = MutableProjectionProvider(goal="first")
    builder = configured_builder(context)
    first = builder.build_with_receipt()
    context.goal = "second"
    second = builder.build_with_receipt()

    assert first is not second
    assert first.request.messages[0].kind == "context_snapshot"
    assert second.request.messages[0].kind == "context_snapshot"
    assert "first" in first.request.messages[0].content[0].text
    assert "second" in second.request.messages[0].content[0].text


def test_snapshot_never_interrupts_tool_pair() -> None:
    build = build_request_with_tool_pair()
    kinds = [item.kind for item in build.request.messages]
    assert kinds == ["context_snapshot", "business_message", "tool_result"]


def test_build_does_not_consume_temporary_recall() -> None:
    builder, messages = build_with_temporary_recall()
    first = builder.build_with_receipt()
    second = builder.build_with_receipt()
    assert first.receipt.temporary_message_ids == ("msg_recalled",)
    assert second.receipt.temporary_message_ids == ("msg_recalled",)
    assert messages.has_temporary_recalled()


def test_existing_attachment_projection_survives_new_builder() -> None:
    build = build_with_uploaded_image_and_load_attachment()
    assert any(item.kind == "context_mount" for item in build.request.messages)
    assert image_bytes_are_present_in_provider_content(build.request)
```

- [ ] **Step 2: 运行 Red**

```powershell
python -m pytest tests/runtime/test_provider_request_builder.py tests/runtime/test_provider_request_rebuild.py -q
```

- [ ] **Step 3: 实现唯一组装 Owner**

本任务先以加法方式新增 `ProviderRequestBuilder.build_with_receipt()`；现有 `build(...)` 迁移桥继续返回当前 QueryLoop 所需的裸 Request，直到 Task 7 原子迁移同步/异步 QueryLoop 后，`build()` 才成为无参数、返回 `ProviderRequestBuild` 的最终入口并删除 `build_with_receipt()` 临时名。构造时注入 Phase 1 `ContextRenderer`（它在内部拥有 `SystemSectionRegistry`）、Context Slot Projection Provider、MessageRuntime 和 tool schema provider。最终返回值不是裸 Request，而是内部构建结果：

```python
@dataclass(frozen=True, slots=True)
class ProviderRequestReceipt:
    temporary_message_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProviderRequestBuild:
    request: ProviderRequest
    receipt: ProviderRequestReceipt


class ProviderRequestFactory(Protocol):
    def __call__(self) -> ProviderRequestBuild: ...
```

receipt 只记录本次不可变 active snapshot 中实际投影进 Request 的 temporary IDs；不是当前 Window 的动态视图。每次调用顺序固定：

```python
class ContextProjectionProvider(Protocol):
    def projections(self) -> tuple[ContextSlotProjection, ...]: ...
```

`runtime/message_projection.py` 固定 temporary 和 tool result 的元数据映射：

```python
def project_stored_message(
    ref: MessageRef,
    message: StoredMessage,
) -> ProviderInputItem:
    temporary = ref.temporary
    kind: ProviderInputKind = (
        "recalled_message" if temporary else
        "tool_result" if message.role == "tool" else
        "business_message"
    )
    return ProviderInputItem(
        role=message.role,
        kind=kind,
        origin="recall_runtime" if temporary else "message_store",
        authority=(
            "tool_data" if message.role == "tool" else "conversation_data"
        ),
        persistence="ephemeral" if temporary else "stored",
        visibility="internal" if temporary or message.role == "tool" else "conversation",
        content=(TextPart(message.content),),
        tool_calls=tuple(project_tool_call(item) for item in message.tool_calls),
        tool_call_id=message.tool_call_id,
    )
```

```python
envelope = self.context_renderer.render()
snapshot = self.snapshot_renderer.render(self.context_projections.projections())
active_snapshot = self.messages.snapshot_active_with_refs()
active = tuple(project_stored_message(ref, message) for ref, message in active_snapshot)
return ProviderRequestBuild(
    request=ProviderRequest(
        system=envelope.text,
        messages=(ProviderInputItem.context_snapshot(snapshot.xml), *active),
        tools=tuple(self.tools),
    ),
    receipt=ProviderRequestReceipt(
        temporary_message_ids=tuple(
            ref.message_id for ref, _ in active_snapshot if ref.temporary
        ),
    ),
)
```

实际实现不得通过伪代码中的二次 materialize 丢失 ref metadata；MessageRuntime 应提供不可变 `(MessageRef, StoredMessage)` 快照。Snapshot 永远在完整 ActiveWindow 前，因此不会插入 assistant tool call 与 tool result 之间。

现有 Public Attachment API 必须保留。`AttachmentRuntime` 增加私有 `_project_provider_inputs_compat(items)`：复用现有 user handle/turn-loaded 状态，把当前已支持的图片 ContentPart 投影为 `context_mount` 或扩展对应 business user item，行为与原 `project_provider_messages()` 字节等价；不新增 ArtifactStore、Catalog、Session Scope、file id 或生命周期语义。Builder 只在注入 AttachmentRuntime 时调用该兼容入口。该桥在代码和文档标记 `remove in Phase 3A`，Phase 3A 由正式 ContextMount producer 替换，而不是 Task 13 删除。

Task 5 提交必须运行现有 QueryLoop 测试，证明加法迁移桥保持 Green；Task 7 的同一原子提交负责切换两个 Loop、删除裸 Request 构建桥并更新全部调用者。

- [ ] **Step 4: Green 和提交**

```powershell
python -m pytest tests/runtime/test_provider_request_builder.py tests/runtime/test_provider_request_rebuild.py tests/runtime/test_query_loop.py tests/runtime/test_async_query_loop_native.py -q
python scripts/generate_public_api_inventory.py --policy docs/public-api-stability.json --output docs/public-api-inventory.json
python -m pytest tests/architecture/test_public_api.py tests/architecture/test_public_api_inventory.py tests/architecture/test_public_api_inventory_cli.py -q
git add -- docs/superpowers/plans/2026-07-10-agentos-message-provider-boundary-implementation-plan.md docs/public-api-inventory.json src/agentos/attachments/runtime.py src/agentos/runtime/message_projection.py src/agentos/runtime/provider_request_builder.py tests/runtime/test_provider_request_builder.py tests/runtime/test_provider_request_rebuild.py tests/runtime/test_query_loop.py
git commit -m "feat: rebuild dual plane provider requests"
```

---

### Task 6: 具体 Provider Adapter 增加 ProviderInputItem 机械兼容

**Files:**
- Modify: `src/agentos/providers/openai.py`
- Modify: `src/agentos/providers/openai_compatible.py`
- Modify: `src/agentos/providers/anthropic.py`
- Modify: `tests/providers/test_adapters.py`
- Modify: `tests/providers/test_openai_compatible.py`

- [ ] **Step 1: 写新旧逻辑输入 wire 等价 Red 测试**

对 user、assistant tool call、tool result 三类现有 payload fixture，各构造一个 `ProviderInputItem` 和一个旧私有 `ProviderMessage`，断言 Adapter 生成的 wire dict 完全相等。新增 recalled user/tool 的断言，证明 authority 元数据不泄漏到 wire payload。

- [ ] **Step 2: 运行 Red**

```powershell
python -m pytest tests/providers/test_adapters.py tests/providers/test_openai_compatible.py -q
```

- [ ] **Step 3: 实现受限双读迁移桥**

每个 Adapter 的私有 `_message_payload()` 接受 `ProviderInputItem | LegacyProviderMessage`。新分支只读取 `role/content/tool_calls/tool_call_id`，对冻结 JSON 调用 `thaw_json()`；旧分支保持现有 payload。不得读取 `origin/authority/persistence/visibility`，不得实现 strict alternation 或 Provider File ID。ContextMount 的图片 ContentPart 只维持 Task 5 已存在 Attachment 行为，不扩展新媒体类型。该 union 只存在具体 Adapter 私有函数，不能进入 `Provider` Protocol 或 Public export；Task 13 删除旧分支。

- [ ] **Step 4: Green 和提交**

```powershell
python -m pytest tests/providers -q
git add -- src/agentos/providers/openai.py src/agentos/providers/openai_compatible.py src/agentos/providers/anthropic.py tests/providers/test_adapters.py tests/providers/test_openai_compatible.py
git commit -m "refactor: accept provider input items in adapters"
```

---

### Task 7: 每个同步/异步 Provider Attempt 重新构建请求

**Files:**
- Create: `src/agentos/runtime/provider_attempt.py`
- Create: `src/agentos/runtime/async_provider_attempt.py`
- Modify: `src/agentos/runtime/provider_request_builder.py`
- Modify: `src/agentos/runtime/query_loop.py`
- Modify: `src/agentos/runtime/async_query_loop.py`
- Modify: `src/agentos/messages/runtime.py`
- Modify: `src/agentos/messages/_migration.py`
- Modify: `tests/messages/test_runtime.py`
- Create: `tests/runtime/test_provider_attempt_rebuild.py`
- Create: `tests/runtime/test_async_provider_attempt_rebuild.py`

#### 2026-07-12 Execution Amendment: build consumers and resource ownership

Task 7 的原子切换要求现有 Request Builder 消费者保持 Green，并在 async Loop 使用同步 Provider fallback 时建立确定的 iterator 资源所有权。除上述核心文件外，本任务扩展为以下精确范围：

- Create: `src/agentos/runtime/provider_attempt_state.py`
- Create: `src/agentos/runtime/query_loop_support.py`
- Modify: `src/agentos/runtime/_async_bridge.py`
- Create: `src/agentos/providers/input_serialization.py`
- Modify: `src/agentos/observability/instrumented.py`
- Modify: `src/agentos/observability/snapshots.py`
- Modify: `src/agentos/examples/small_openai_agent.py`
- Modify: `docs/governance/agentos-module-size-baseline.json`
- Modify: `tests/attachments/test_turn_scoped_image_lifecycle.py`
- Modify: `tests/capabilities/test_tools.py`
- Modify: `tests/examples/test_small_openai_agent.py`
- Modify: `tests/messages/test_temporary_recall_lifecycle.py`
- Modify: `tests/multi/test_continuation.py`
- Modify: `tests/observability/test_query_loop_instrumentation.py`
- Modify: `tests/providers/test_provider_messages.py`
- Modify: `tests/recall/test_query_recall.py`
- Modify: `tests/recall/test_runtime.py`
- Modify: `tests/runtime/test_agent_builder.py`
- Modify: `tests/runtime/test_agent_stream_api.py`
- Modify: `tests/runtime/test_async_agent_api.py`
- Modify: `tests/runtime/test_async_bridge_nested_cancel.py`
- Modify: `tests/runtime/test_async_query_loop_native.py`
- Modify: `tests/runtime/test_provider_request_builder.py`
- Modify: `tests/runtime/test_provider_request_rebuild.py`
- Modify: `tests/runtime/test_query_loop.py`
- Modify: `tests/runtime/test_query_loop_boundaries.py`
- Modify: `tests/runtime/test_query_loop_hooks.py`
- Modify: `tests/runtime/test_session_recovery.py`
- Modify: `tests/runtime/test_skill_mcp_tool_loop.py`
- Modify: `tests/runtime/test_streaming_query_loop.py`
- Modify: `tests/runtime/test_streaming_tool_loop.py`
- Modify: `tests/runtime/test_tool_loop.py`
- Modify: `tests/runtime/test_tool_result_budget.py`

`input_serialization.py` 只承接 `ProviderInputItem` 的 JSON-safe 观测序列化，Observability 和 example 只做新 build contract 的消费适配；不得把 capture policy、业务消息真值或 Provider payload 组装迁入该模块。治理基线只记录本任务经批准的模块拆分结果。外围测试只允许机械适配不可变 Request、ProviderInputItem 和 attempt 时序，不改变 Attachment、Capability、Continuation、Recall 或 Tool 的业务语义。

`_async_bridge.py` 是同步 iterator factory 返回值的唯一资源 Owner：early exit、late-event error 和 cancellation 都必须等待 worker 在 `next()`/`close()` 安全点收口并执行 iterator `close()`/generator `finally`。同步阻塞 Provider 使用 cooperative cancellation；Runtime 不阻塞 event loop，但在 Provider 到达安全点前也不得让 worker 后台逃逸。同步 bridge cleanup 与 native async stream `aclose()` 共享同一个 cancellation-preserving tracked cleanup 原语；重复 cancellation 必须由 cleanup task 吸收，cleanup 完成后再恢复原 cancellation count。

本 amendment 不授权新增领域能力或 Public API，只补齐原子 build contract 迁移所需的消费者适配、模块规模治理和资源生命周期责任。任何超出上述路径或责任的修改必须拆分到后续任务。

- [ ] **Step 1: 写 retry fresh request 和 temporary 成功消费 Red 测试**

```python
def test_sync_retry_builds_and_hooks_each_attempt() -> None:
    builder = CountingRequestBuilder()
    provider = FailOnceProvider()
    runner = configured_attempt_runner(builder=builder, provider=provider)
    list(runner.run_stream(options=None))
    assert builder.calls == 2
    assert provider.requests[0] is not provider.requests[1]


def test_temporary_recall_is_consumed_only_after_success() -> None:
    messages, runner = runner_with_temporary_recall_and_fail_once()
    list(runner.run_stream(options=None))
    assert runner.second_request_contains_recall
    assert not messages.has_temporary_recalled()


def test_hook_replacement_clears_receipt() -> None:
    messages, runner = runner_with_replacing_before_hook()
    list(runner.run_stream(options=None))
    assert messages.has_temporary_recalled()


def test_retry_is_forbidden_after_first_visible_delta() -> None:
    runner = runner_with_delta_then_error()
    events = []
    with pytest.raises(ProviderStreamError):
        events.extend(runner.run_stream(options=None))
    assert runner.request_build_count == 1
```

异步文件写同构断言，使用 `asyncio.Event` 或确定性 Fake，不使用 `sleep`。

- [ ] **Step 2: 运行 Red**

```powershell
python -m pytest tests/runtime/test_provider_attempt_rebuild.py tests/runtime/test_async_provider_attempt_rebuild.py -q
```

- [ ] **Step 3: 抽出 Attempt 协调责任**

同步接口冻结为：

```python
@dataclass(slots=True)
class ProviderAttemptRunner:
    request_factory: ProviderRequestFactory
    stream_provider: Callable[[ProviderRequest, ProviderStreamOptions | None], Iterator[ProviderStreamEvent]]
    before_call: Callable[[ProviderRequest], ProviderRequest]
    after_call: Callable[[ProviderRequest, ProviderResponse], ProviderResponse]
    ensure_usable: Callable[[ProviderResponse], None]
    consume_temporary: Callable[[tuple[str, ...]], None]
    retry_policy: RetryPolicy | None
    on_retry: Callable[[int, Exception], None]

    def run_stream(
        self,
        options: ProviderStreamOptions | None,
    ) -> Iterator[ProviderStreamEvent]: ...
```

异步接口明确为：

```python
@dataclass(slots=True)
class AsyncProviderAttemptRunner:
    request_factory: ProviderRequestFactory
    stream_provider: Callable[
        [ProviderRequest, ProviderStreamOptions | None],
        AsyncIterator[ProviderStreamEvent],
    ]
    before_call: Callable[[ProviderRequest], ProviderRequest]
    after_call: Callable[[ProviderRequest, ProviderResponse], ProviderResponse]
    ensure_usable: Callable[[ProviderResponse], None]
    consume_temporary: Callable[[tuple[str, ...]], None]
    retry_policy: RetryPolicy | None
    on_retry: Callable[[int, Exception], Awaitable[None]]

    def run_stream(
        self,
        options: ProviderStreamOptions | None,
    ) -> AsyncIterator[ProviderStreamEvent]: ...
```

异步 fallback 顺序固定为 `provider.async_stream` -> `provider.async_complete` -> 在线程执行 `provider.stream` -> 在线程执行 `provider.complete`，保留现有 native async capability；同步 Runner 保持现有 `async_stream` bridge -> `stream` -> `complete` 顺序。同步和异步都不使用 generator return value 交付响应：Runner 截获 Provider 原始 `ProviderStreamCompleted`，执行 after hook、usability validation 和 receipt consumption 后，再向上游 yield 一个携带**最终已校验 response** 的 `ProviderStreamCompleted`；它必须是成功流的最后一个事件。无 completion 时抛错，不 yield 伪完成事件。

每一物理 attempt 的严格时序是：`request_factory()` -> 保存原 request 身份与 receipt -> `before_call()` -> 若返回对象不是原 request，则把本 attempt receipt 替换为空 receipt -> Provider stream/fallback -> 收集 completion response -> `after_call()` -> `ensure_usable()` -> 按 receipt IDs 调用 `consume_temporary()` -> 返回成功。after-hook deny/raise、无 completion、cancel、timeout、截断/不可用响应均发生在消费前。

`visible_delta_emitted` 在 `ProviderContentDelta`，以及 `show_thinking=True` 时的 `ProviderThinkingDelta`，**交给 QueryLoop 调用方之前**置为 true。Tool call/usage/internal started event 不视为用户可见。发生异常时，只有 `visible_delta_emitted is False` 且 RetryPolicy 允许时才能 retry；一旦已发出可见 delta，原异常直接向上传播，不得构建下一 request。retry 时从 request factory 重新构建并重新执行 before hook，不能复用 hook 修改后的对象。

`query_loop.py` 和 `async_query_loop.py` 只注入现有 event/log/hook/response validation/provider fallback callbacks，不复制 attempt retry policy。验收目标：`query_loop.py < 800`、`async_query_loop.py < 500`；若提取后仍超限，本任务不得提交，必须继续把 provider stream fallback helper 移到 attempt 模块。两个 Runner 可共享纯状态判定 helper，但不得用 sync-over-async 隐藏取消语义。

同一原子提交把 `ProviderRequestBuilder.build_with_receipt()` 重命名为最终无参 `build()`，删除旧裸 Request build 桥；两个 Loop 全部改用 `ProviderRequestBuild`。随后删除 MessageRuntime 的 `materialize_provider_messages()` 委托和 `_migration.py` 中的 Provider 投影函数，保留的只有 `Message = StoredMessage` 类型 alias。这样提交结束后 `messages` 已不导入 `providers`，而 QueryLoop/Adapter 都走新逻辑输入。

- [ ] **Step 4: Green、现有 retry/stream 回归和规模检查**

```powershell
python -m pytest tests/runtime/test_provider_attempt_rebuild.py tests/runtime/test_async_provider_attempt_rebuild.py tests/runtime/test_provider_retry.py tests/runtime/test_streaming_query_loop.py tests/runtime/test_async_query_loop_native.py tests/runtime/test_query_loop.py -q
python -m pytest tests/runtime/test_async_bridge_nested_cancel.py tests/runtime/test_async_agent_api.py tests/observability/test_instrumented_provider.py tests/examples/test_small_openai_agent.py tests/architecture/test_module_size_baseline.py -q
Get-ChildItem src/agentos/runtime/query_loop.py,src/agentos/runtime/async_query_loop.py | ForEach-Object { "{0}: {1}" -f $_.Name,(Get-Content -Encoding utf8 $_).Count }
```

- [ ] **Step 5: 提交**

```powershell
git add -- docs/superpowers/plans/2026-07-10-agentos-message-provider-boundary-implementation-plan.md docs/governance/agentos-module-size-baseline.json src/agentos/runtime/_async_bridge.py src/agentos/runtime/provider_attempt.py src/agentos/runtime/async_provider_attempt.py src/agentos/runtime/provider_attempt_state.py src/agentos/runtime/provider_request_builder.py src/agentos/runtime/query_loop_support.py src/agentos/runtime/query_loop.py src/agentos/runtime/async_query_loop.py src/agentos/providers/input_serialization.py src/agentos/observability/instrumented.py src/agentos/observability/snapshots.py src/agentos/examples/small_openai_agent.py src/agentos/messages/runtime.py src/agentos/messages/_migration.py tests/attachments/test_turn_scoped_image_lifecycle.py tests/capabilities/test_tools.py tests/examples/test_small_openai_agent.py tests/messages/test_runtime.py tests/messages/test_temporary_recall_lifecycle.py tests/multi/test_continuation.py tests/observability/test_query_loop_instrumentation.py tests/providers/test_provider_messages.py tests/recall/test_query_recall.py tests/recall/test_runtime.py tests/runtime/test_agent_builder.py tests/runtime/test_agent_stream_api.py tests/runtime/test_async_agent_api.py tests/runtime/test_async_bridge_nested_cancel.py tests/runtime/test_async_query_loop_native.py tests/runtime/test_provider_attempt_rebuild.py tests/runtime/test_async_provider_attempt_rebuild.py tests/runtime/test_provider_request_builder.py tests/runtime/test_provider_request_rebuild.py tests/runtime/test_query_loop.py tests/runtime/test_query_loop_boundaries.py tests/runtime/test_query_loop_hooks.py tests/runtime/test_session_recovery.py tests/runtime/test_skill_mcp_tool_loop.py tests/runtime/test_streaming_query_loop.py tests/runtime/test_streaming_tool_loop.py tests/runtime/test_tool_loop.py tests/runtime/test_tool_result_budget.py
git commit -m "refactor: rebuild requests for every provider attempt"
```

---

## Active Execution Cursor: Tasks 8-14

### Task 8: 迁移 Persistence 真值与序列化

**Files:**
- Modify: `src/agentos/persistence/serializers.py`
- Modify: `src/agentos/persistence/postgres.py`
- Modify: `tests/persistence/test_serializers.py`
- Modify: `tests/persistence/test_postgres_session_snapshot_persistence.py`

- [ ] **Step 1: 写 StoredMessage/ArtifactRef round-trip Red 测试**

断言 `StoredMessage -> dict -> StoredMessage` 保留 tool calls、递归冻结 arguments 与 `ArtifactRef(artifact_id, filename, media_type)`；序列化结果不得包含 ContextSnapshot 或 Provider 元数据字段。未知字段按现有兼容策略处理，不在本任务发明新版本协议。

- [ ] **Step 2: Red、最小迁移、Green**

```powershell
python -m pytest tests/persistence/test_serializers.py tests/persistence/test_postgres_session_snapshot_persistence.py -q
```

只把 persistence 类型和构造迁移到 `StoredMessage`，serializer 在写出边界调用 `thaw_json()`，读取时通过构造器重新冻结。不得修改 compression、recall 或 Provider 行为。

```powershell
python -m pytest tests/persistence -q
git add -- src/agentos/persistence/serializers.py src/agentos/persistence/postgres.py tests/persistence/test_serializers.py tests/persistence/test_postgres_session_snapshot_persistence.py
git commit -m "refactor: persist stored message truth"
```

---

### Task 9: 迁移 Compression 与 Recall

**Files:**
- Modify: `src/agentos/compression/_helpers.py`
- Modify: `src/agentos/compression/compressor.py`
- Modify: `src/agentos/compression/evictor.py`
- Modify: `src/agentos/compression/llm_compressor.py`
- Modify: `src/agentos/compression/runtime.py`
- Modify: `src/agentos/recall/runtime.py`
- Modify: `tests/compression/test_llm_compressor.py`
- Modify: `tests/compression/test_memory_sink.py`
- Modify: `tests/compression/test_package_compressor.py`
- Modify: `tests/compression/test_runtime.py`
- Modify: `tests/recall/test_query_recall.py`
- Modify: `tests/recall/test_runtime.py`

- [ ] **Step 1: 写算法等价与 temporary recall Red 测试**

使用现有 fixtures 的相同输入，断言迁移前后压缩选择、summary、Tool Pair eviction 和 recall 排序不变。新增 Red：`recall_context()` 成功返回前，所有 recalled message 已 hydrate 到 Store，并通过 `MessageRuntime.active_window.prepend_temporary(message_ids)` 原子加入 temporary window；重复 recall 仍按 Window 去重；未知 handle、Memory 查询失败或 hydrate 前失败不修改 Window。Router 仍返回标准 `ToolExecutionResult`，QueryLoop 仍把它 append 为 Stored tool result，temporary recalled 原文不能替代 Tool Result。

- [ ] **Step 2: 机械迁移并验证**

Compression 只替换领域类型名、tuple 适配和冻结 arguments 的只读访问，不改变阈值、排序或摘要 prompt。Recall 在 `_recall_by_handle/_recall_by_query` 完成全部读取/水合后、发出 `RecallContextInjectedEvent` 前调用同一个 `prepend_temporary(tuple(message.id ...))` API；若前序读取/水合失败则不调用，Window 不变。Window prepend 本身是无 I/O 的确定性操作；成功后返回 recalled messages，Router 按现有 `_format_recalled_context()` 生成标准 tool result。测试覆盖 handle/query、重复、失败不变和 tool result 仍存在。

```powershell
python -m pytest tests/compression tests/recall -q
git add -- src/agentos/compression/_helpers.py src/agentos/compression/compressor.py src/agentos/compression/evictor.py src/agentos/compression/llm_compressor.py src/agentos/compression/runtime.py src/agentos/recall/runtime.py tests/compression tests/recall
git commit -m "refactor: migrate compression and recall messages"
```

---

### Task 10: 迁移 Memory 真值消费者

**Files:**
- Modify: `src/agentos/memory/in_memory.py`
- Modify: `src/agentos/memory/redis_store.py`
- Modify: `src/agentos/memory/runtime.py`
- Modify: `src/agentos/memory/serializers.py`
- Modify: `src/agentos/memory/store.py`
- Modify: `src/agentos/memory/types.py`
- Modify: `tests/memory/test_in_memory.py`
- Modify: `tests/memory/test_optional_adapters.py`
- Modify: `tests/memory/test_production_adapters.py`
- Modify: `tests/memory/test_runtime.py`
- Modify: `tests/memory/test_types.py`

- [ ] **Step 1: 写新类型边界 Red 测试**

新增断言 Memory store/runtime/serializer 的公开类型注解和返回实例都是 `StoredMessage`，源码不再从 `agentos.messages` 导入旧 `Message`。该断言在迁移前确定性失败；同时保留现有 serializer round-trip characterization tests，并增加带 `ArtifactRef`、嵌套 `ToolCall.arguments` 的 Memory message payload、Redis hot message/state 和 durable recall round-trip，证明恢复结果没有丢失业务消息真值。

- [ ] **Step 2: 机械迁移并验证**

迁移 `StoredMessage` 类型、tuple 和 `FrozenJsonObject` 的只读访问，不改变 hot/durable store 算法、Redis key/hash 结构、TTL、原子消费或 recall 排序。Memory message payload 必须增加 `artifact_refs` 业务字段，并与 canonical persistence serializer 使用同一 `ArtifactRef(artifact_id, filename, media_type)` 结构；读取既有 payload 时字段缺省为空。该加法字段是保存完整 `StoredMessage` 真值所必需的格式修正，不允许引入版本分支、双写或第二套 serializer。

```powershell
python -m pytest tests/memory -q
git add -- src/agentos/memory tests/memory
git commit -m "refactor: migrate memory message consumers"
```

---

### Task 11: 迁移 Policy 与 Capability 消费者

**Files:**
- Modify: `src/agentos/policies/budget.py`
- Modify: `src/agentos/capabilities/router.py`
- Modify: `tests/policies/test_token_budget_policy.py`
- Modify: `tests/capabilities/test_tools.py`

- [ ] **Step 1: 写旧类型消除 Red 测试**

断言 Budget policy 接收 `tuple[StoredMessage, ...]`，Router 追加的 tool result 是 `StoredMessage`，且两个模块源码不再导入旧 `Message`。迁移前至少旧 import/type assertion 失败，不把已通过的 characterization test 冒充 Red。

- [ ] **Step 2: 最小迁移、模块验证和提交**

只替换领域类型与冻结 JSON 只读访问；Router 仍只 dispatch 一个 call，不增加 Scheduler、并发或 DAG。

```powershell
python -m pytest tests/policies tests/capabilities -q
git add -- src/agentos/policies/budget.py src/agentos/capabilities/router.py tests/policies tests/capabilities
git commit -m "refactor: migrate policy and capability messages"
```

---

### Task 12: 迁移 Debug Projection

**Files:**
- Modify: `src/agentos/context/debug_projection.py`
- Modify: `tests/context/test_debug_projection.py`

- [ ] **Step 1: 写 StoredMessage 输入和 Provider metadata 排除 Red 测试**

断言 debug projector 的输入实例为 `StoredMessage`、输出不包含 `origin/authority/persistence/visibility`，并且源码无旧 `Message` import；旧实现的类型/import 断言确定性失败。

- [ ] **Step 2: 最小迁移、验证和提交**

Debug Projection 只用于调试，不成为 Provider context 或 Read Model 真值源。

```powershell
python -m pytest tests/context/test_debug_projection.py -q
git add -- src/agentos/context/debug_projection.py tests/context/test_debug_projection.py
git commit -m "refactor: migrate debug message projection"
```

---

### Task 13: 冻结内部模型输入并删除全部 Message / Provider 迁移桥

Task 13 按 13A/13B 串行执行。13A 是加法契约，可独立 Green；13B 是旧 Provider message 体系、Public API 和 inventory 的原子删除。Public 导出与其 inventory 不能跨提交失配，因此不得把 13B 再拆成互相不可运行的 DTO 删除和 API 收口提交。

#### Task 13A: 增加 Runtime 内部模型任务输入

**Files:**
- Modify: `src/agentos/providers/input.py`
- Modify: `src/agentos/providers/base.py`
- Modify: `src/agentos/compression/llm_compressor.py`
- Modify: `tests/providers/test_provider_input_contract.py`
- Modify: `tests/compression/test_llm_compressor.py`
- Inspect/Run: `tests/runtime/test_provider_request_builder.py`
- Inspect/Run: `tests/runtime/test_provider_request_rebuild.py`

- [ ] **Step 1: 写 `model_task` 元数据与生产者限制 Red 测试**

断言 `ProviderInputKind` 增加且只增加 `model_task`；`ProviderInputItem.model_task(text)` 固定生成 `user/runtime/context_data/ephemeral/internal`，只接受文本，不允许 tool calls/tool_call_id；直接通过公开字段构造同矩阵 `model_task` 被拒绝；internal model task `ProviderRequest` 必须且只能包含一个 `model_task`、`tools=()`、`parallel_tool_calls=None`，混合 Turn item、多任务 item 或 Tool Schema 均被拒绝；`LlmCompressor` 的请求使用该平面，不再构造 `UserMessage`。Compressor task instruction 先构造 `SystemEnvelope` 再把 `.text` 写入 request.system，待压缩正文只存在于 `model_task`，不能进入 trusted template；现有 ProviderRequestBuilder/rebuild 测试同时证明普通 Turn 路径只产生 ContextSnapshot、business/tool/recall/context-mount，不产生 `model_task`。

- [ ] **Step 2: 最小实现、验证、双层 Review 和提交**

`model_task` 工厂使用不导出的内部构造凭证，避免公开 dataclass 字段入口绕过受控 producer。`LlmCompressor.prompt_template` 是 SDK 默认值或应用开发者显式配置的 trusted task template；实现不得把 StoredMessage 内容、摘要候选或其他 Context Data 拼入 SystemEnvelope，只允许加入类型化计算出的输出预算。该平面复用统一 `ProviderRequest`/Provider Adapter/错误/超时/观测边界，但不进入 QueryLoop ProviderAttemptRunner、Hook、temporary receipt 或 retry 生命周期；本任务不新增 `ModelTaskProvider`、第二套 Adapter 协议或第二个 retry 控制流。当前只有 Compression 一个真实调用点；只有未来出现至少两个需要不同结构化输出、批处理、路由或生命周期语义的内部模型任务后，才允许通过新 Spec 设计 `ModelTaskRunner`。

```powershell
python -m pytest tests/providers tests/compression/test_llm_compressor.py tests/runtime/test_provider_request_builder.py tests/runtime/test_provider_request_rebuild.py tests/runtime/test_async_provider_attempt_rebuild.py tests/runtime/test_provider_attempt_candidate.py tests/runtime/test_query_loop_hooks.py -q
python -m ruff check src/agentos/providers/input.py src/agentos/providers/base.py src/agentos/compression/llm_compressor.py tests/providers/test_provider_input_contract.py tests/compression/test_llm_compressor.py
python -m compileall -q src/agentos/providers/input.py src/agentos/providers/base.py src/agentos/compression/llm_compressor.py tests/providers/test_provider_input_contract.py tests/compression/test_llm_compressor.py
git diff --check
git add -- src/agentos/providers/input.py src/agentos/providers/base.py src/agentos/compression/llm_compressor.py tests/providers/test_provider_input_contract.py tests/compression/test_llm_compressor.py
git commit -m "feat: add model task provider input"
```

#### Task 13B: 原子删除 legacy Provider message 体系并收口 Public API

**Production files:**
- Modify: `src/agentos/messages/types.py`
- Delete: `src/agentos/messages/_migration.py`
- Modify: `src/agentos/messages/__init__.py`
- Modify: `src/agentos/providers/base.py`
- Delete: `src/agentos/providers/messages.py`
- Create: `src/agentos/providers/tool_specs.py`
- Modify: `src/agentos/providers/__init__.py`
- Modify: `src/agentos/providers/openai.py`
- Modify: `src/agentos/providers/openai_compatible.py`
- Modify: `src/agentos/providers/anthropic.py`
- Modify: `src/agentos/providers/_openai_compatible_payload.py`
- Modify: `src/agentos/providers/_content_parts.py`
- Modify: `src/agentos/attachments/runtime.py`
- Modify: `src/agentos/attachments/__init__.py`
- Modify: `src/agentos/observability/snapshots.py`
- Modify: `src/agentos/examples/small_openai_agent.py`
- Inspect/Modify only if required: `src/agentos/builder.py`
- Modify: `docs/governance/agentos-module-size-baseline.json`
- Modify: `docs/public-api-inventory.json`
- Modify: `docs/api-stability.md`

**Test files:**
- Modify: `tests/messages/test_stored_message.py`
- Modify: `tests/tokens/test_counter.py`
- Modify: `tests/providers/test_provider_messages.py`
- Modify: `tests/providers/test_adapters.py`
- Modify: `tests/providers/test_openai_compatible.py`
- Modify: `tests/providers/test_openai_compatible_streaming.py`
- Modify: `tests/compression/test_llm_compressor.py`
- Modify: `tests/attachments/test_attachment_runtime.py`
- Modify: `tests/attachments/test_turn_scoped_image_lifecycle.py`
- Modify: `tests/observability/test_snapshots.py`
- Modify: `tests/observability/test_instrumented_provider.py`
- Modify: `tests/runtime/test_query_loop_hooks.py`
- Modify: `tests/runtime/test_turn_lifecycle.py`
- Inspect/Run: `tests/runtime/test_agent_builder.py`
- Modify: `tests/examples/test_small_openai_agent.py`
- Modify: `tests/architecture/test_public_api.py`
- Inspect/Run: `tests/architecture/test_public_api_inventory.py`
- Inspect/Run: `tests/architecture/test_public_api_inventory_cli.py`

- [ ] **Step 1: 写唯一 ProviderInput 边界 Red 测试**

断言 `ProviderRequest.messages` 只接受 `ProviderInputItem`；`Message`、`ProviderMessage`、`UserMessage`、`AssistantMessage`、`ToolResultMessage`、`ProviderMessageContent` 和 `provider_message_*` 均不再可导入；Adapter 源码不再包含 legacy DTO；Observability/example 只调用 `provider_input_to_dict()`；附件旧 `project_provider_messages()` 不存在，现有图片行为仍通过 ProviderRequestBuilder 调用 `_project_provider_inputs_compat()`；Public API inventory 与新模块路径、删除名称完全一致。

- [ ] **Step 2: 原子删除 DTO、迁移 Adapter 与消费者**

删除 `Message = StoredMessage`、`messages/_migration.py` 和全部 Provider message DTO/serializer。把 `ProviderFunctionSpec`、`ProviderToolSpec` 及 tool schema serializer 移到 `providers/tool_specs.py`；`providers/messages.py` 整体删除，避免留下名不副实模块。三个 Adapter 直接把 `ProviderInputItem` 转为 wire payload，不得先构造临时 DTO；`ProviderRequest.__post_init__` 只接受 `ProviderInputItem`。Observability 和 example 统一使用 `provider_input_to_dict()`。

附件只保留 Phase 3A 前的 `_project_provider_inputs_compat()`，删除 `project_provider_messages()` 与 `_project_user_handles()`；附件测试通过 ProviderRequestBuilder/Agent 公共路径验证首轮展开、当前 Turn 持续挂载和 Turn 结束清理，不把私有桥当成新的 Public API。

同一原子提交更新 `docs/api-stability.md`、生成 Public API inventory，并更新 Public API tests。`AgentBuilder` 不依赖具体 Adapter，不硬编码 Skill/Plan/Memory/Artifact projection；若已满足契约，不为制造 diff 修改生产文件。

- [ ] **Step 3: 模块 Green、双层 Review 和原子提交**

```powershell
python -m pytest tests/messages tests/tokens/test_counter.py tests/providers tests/compression/test_llm_compressor.py tests/attachments tests/observability tests/runtime/test_query_loop_hooks.py tests/runtime/test_turn_lifecycle.py tests/runtime/test_agent_builder.py tests/examples/test_small_openai_agent.py -q
python -m ruff check src tests
python -m compileall -q src tests
python scripts/generate_public_api_inventory.py --policy docs/public-api-stability.json --output docs/public-api-inventory.json
$limits = @{
    "src/agentos/providers/openai_compatible.py" = 884
    "src/agentos/providers/tool_specs.py" = 199
    "src/agentos/builder.py" = 280
}
foreach ($entry in $limits.GetEnumerator()) {
    $lines = (Get-Content -Encoding utf8 $entry.Key).Count
    if ($lines -gt $entry.Value) {
        throw "$($entry.Key) has $lines lines; limit is $($entry.Value)"
    }
}
if (Test-Path "src/agentos/providers/messages.py") {
    throw "src/agentos/providers/messages.py must be deleted"
}
python scripts/generate_module_size_baseline.py --root src/agentos --output docs/governance/agentos-module-size-baseline.json
python -m pytest tests/architecture/test_module_size_baseline.py tests/architecture/test_public_api.py tests/architecture/test_public_api_inventory.py tests/architecture/test_public_api_inventory_cli.py -q
$legacyPattern = '\b(ProviderMessage|UserMessage|AssistantMessage|ToolResultMessage|ProviderMessageContent)\b|provider_message_(to|from)_dict|class Message\b|Message = StoredMessage|project_provider_messages'
$legacyMatches = rg -n $legacyPattern src
if ($LASTEXITCODE -eq 0) {
    $legacyMatches
    throw "legacy Message/Provider symbols remain in src"
}
if ($LASTEXITCODE -ne 1) {
    throw "legacy Message/Provider drift scan failed"
}
$legacyTestAllowlist = @(
    "tests/architecture/test_public_api.py"
    "tests/messages/test_stored_message.py"
    "tests/providers/test_provider_messages.py"
    "tests/runtime/test_message_provider_boundary_contract.py"
)
$legacyTestMatches = rg -n $legacyPattern tests
if ($LASTEXITCODE -gt 1) {
    throw "legacy Message/Provider test scan failed"
}
$unexpectedLegacyTests = @(
    $legacyTestMatches | Where-Object {
        $path = (($_ -split ':', 2)[0] -replace '\\', '/')
        $path -notin $legacyTestAllowlist
    }
)
if ($unexpectedLegacyTests.Count -gt 0) {
    $unexpectedLegacyTests
    throw "legacy Message/Provider usage remains outside negative-test allowlist"
}
git diff --check
git add -- src/agentos/builder.py src/agentos/messages src/agentos/providers src/agentos/attachments src/agentos/compression/llm_compressor.py src/agentos/observability/snapshots.py src/agentos/examples/small_openai_agent.py tests/messages tests/tokens/test_counter.py tests/providers tests/compression/test_llm_compressor.py tests/attachments tests/observability tests/runtime/test_query_loop_hooks.py tests/runtime/test_turn_lifecycle.py tests/runtime/test_agent_builder.py tests/examples/test_small_openai_agent.py tests/architecture/test_public_api.py docs/governance/agentos-module-size-baseline.json docs/public-api-inventory.json docs/api-stability.md
git commit -m "refactor: remove legacy provider messages"
```

---

### Task 14: Phase 2 Contract Matrix、双层 Review 和阶段验收

**Files:**
- Create: `tests/runtime/test_message_provider_boundary_contract.py`
- Modify: `docs/api-stability.md`

- [ ] **Step 1: 增加跨边界契约矩阵**

Contract Matrix 必须覆盖：六种 ProviderInput kind 的全部合法矩阵与代表性非法交叉组合；Snapshot 固定元数据与首位顺序；Tool Pair 邻接；`model_task` 固定元数据、文本限制、唯一受控工厂和公开字段构造拒绝；Agent Turn 与 internal model task 两种 ProviderRequest 平面互斥；QueryLoop RequestBuilder/Turn projector 不生成 `model_task`；`model_task` 不进入 MessageStore、ContextSnapshot、Conversation Read Model 或 temporary receipt；Read Model 明确拒绝 ProviderRequest/ProviderInputItem/internal transcript；每 physical retry fresh request；before-hook 替换清空 receipt；after-hook/usability validation 前不消费；按 receipt ID 精确消费；visible delta 后禁止 retry；`stream=False`/`stream=True` 共用同一事件源且最终结果一致；native async、async complete、同步 stream、同步 complete Provider capability 均进入同一 `ProviderAttemptRunner`；StoredMessage 无 Provider metadata；Tool arguments/schema parameters 深不可变；Builder 不依赖具体 Adapter。

- [ ] **Step 2: 运行目标和全量验证**

```powershell
python -m pytest tests/context tests/messages tests/providers/test_provider_input_contract.py tests/runtime/test_provider_request_builder.py tests/runtime/test_provider_request_rebuild.py tests/runtime/test_async_provider_attempt_rebuild.py tests/runtime/test_provider_attempt_candidate.py tests/runtime/test_query_loop_contract.py tests/runtime/test_agent_api.py tests/runtime/test_agent_stream_api.py tests/runtime/test_message_provider_boundary_contract.py -q
python -m pytest -q
python -m compileall -q src tests
python -m ruff check src tests
python scripts/generate_public_api_inventory.py --policy docs/public-api-stability.json --output docs/public-api-inventory.json
$limits = @{
    "src/agentos/runtime/query_loop.py" = 493
    "src/agentos/runtime/provider_attempt.py" = 145
    "src/agentos/runtime/agent.py" = 144
    "src/agentos/runtime/agent_stream.py" = 249
    "src/agentos/providers/openai_compatible.py" = 884
    "src/agentos/providers/tool_specs.py" = 199
    "src/agentos/builder.py" = 280
}
foreach ($entry in $limits.GetEnumerator()) {
    $lines = (Get-Content -Encoding utf8 $entry.Key).Count
    if ($lines -gt $entry.Value) {
        throw "$($entry.Key) has $lines lines; limit is $($entry.Value)"
    }
}
if (Test-Path "src/agentos/providers/messages.py") {
    throw "src/agentos/providers/messages.py must be deleted"
}
python scripts/generate_module_size_baseline.py --root src/agentos --output docs/governance/agentos-module-size-baseline.json
python -m pytest tests/architecture/test_module_size_baseline.py tests/architecture/test_public_api.py tests/architecture/test_public_api_inventory.py -q
$legacyPattern = '\b(ProviderMessage|UserMessage|AssistantMessage|ToolResultMessage|ProviderMessageContent)\b|provider_message_(to|from)_dict|class Message\b|Message = StoredMessage|project_provider_messages|materialize_provider_messages'
$legacyMatches = rg -n $legacyPattern src
if ($LASTEXITCODE -eq 0) {
    $legacyMatches
    throw "legacy Message/Provider symbols remain in src"
}
if ($LASTEXITCODE -ne 1) {
    throw "legacy Message/Provider drift scan failed"
}
$legacyTestAllowlist = @(
    "tests/architecture/test_public_api.py"
    "tests/messages/test_stored_message.py"
    "tests/providers/test_provider_messages.py"
    "tests/runtime/test_message_provider_boundary_contract.py"
)
$legacyTestMatches = rg -n $legacyPattern tests
if ($LASTEXITCODE -gt 1) {
    throw "legacy Message/Provider test scan failed"
}
$unexpectedLegacyTests = @(
    $legacyTestMatches | Where-Object {
        $path = (($_ -split ':', 2)[0] -replace '\\', '/')
        $path -notin $legacyTestAllowlist
    }
)
if ($unexpectedLegacyTests.Count -gt 0) {
    $unexpectedLegacyTests
    throw "legacy Message/Provider usage remains outside negative-test allowlist"
}
$architectureDrift = rg -n 'system: rendered context|AttachmentLifecycle|<task_goal>|<constraints>' src tests
if ($LASTEXITCODE -eq 0) {
    $architectureDrift
    throw "legacy context architecture symbols remain in src/tests"
}
if ($LASTEXITCODE -ne 1) {
    throw "context architecture drift scan failed"
}
$docLegacy = rg -n $legacyPattern docs --glob "!docs/superpowers/plans/2026-07-10-agentos-message-provider-boundary-implementation-plan.md" --glob "!docs/superpowers/specs/2026-07-11-agentos-message-provider-boundary-contract-addendum.md"
if ($LASTEXITCODE -gt 1) {
    throw "documentation legacy-name scan failed"
}
$docLegacy
$loopDrift = rg -n 'class\s+(AsyncQueryLoop|AsyncProviderAttemptRunner)|def\s+(build_async|run_turn_stream|run_continuation_stream|clear_interrupt)\b|sync_loop\s*[:=]' src tests
if ($LASTEXITCODE -eq 0) {
    $loopDrift
    throw "legacy Loop/Runner symbols remain in src/tests"
}
if ($LASTEXITCODE -ne 1) {
    throw "Loop/Runner drift scan failed"
}
$modelTaskProducers = @(rg -n '\.model_task\(' src)
if ($LASTEXITCODE -ne 0) {
    throw "model_task producer scan failed or found no producer"
}
if ($modelTaskProducers.Count -ne 1) {
    $modelTaskProducers
    throw "model_task must have exactly one Phase 2 producer"
}
$modelTaskProducerPath = (($modelTaskProducers[0] -split ':', 2)[0] -replace '\\', '/')
if ($modelTaskProducerPath -ne "src/agentos/compression/llm_compressor.py") {
    $modelTaskProducers
    throw "unexpected model_task producer"
}
git diff --check
```

Expected: 全部 PASS；`src` 的旧 Message/Provider DTO、serializer 和投影桥严格零命中；`tests` 只允许四个明确文件中的负向断言，其他测试不得继续消费旧 DTO；旧 Loop/Runner 在 `src tests` 零命中；Phase 2 的 `.model_task()` 源码 producer 只有 `LlmCompressor`。Docs scan 的命中必须逐条列入人工允许清单，仅允许历史迁移说明；不得用一个宽泛 glob 跳过全部活跃文档。最终规模：`query_loop.py <= 493`、`provider_attempt.py <= 145`、`agent.py <= 144`、`agent_stream.py <= 249`、`providers/tool_specs.py < 200`；`providers/messages.py` 不存在；`builder.py` 不净增职责且不超过 `280` 行。自动生成的 module-size baseline 和门禁是最终规模证据。

- [ ] **Step 3: Spec Compliance Review**

Reviewer 独立检查：六种 ProviderInput 的 Authority/Persistence/Visibility 矩阵；`model_task` 受控生产者和隔离边界；ArtifactRef ownership；Snapshot/Tool Pair/ContextMount 预留顺序；每 attempt 重建；temporary 消费；Read Model 默认拒绝；Phase 3B 未偷跑；无 silent deferral。

- [ ] **Step 4: Code Quality Review**

Reviewer 独立检查：Store/Runtime/Projection 单一职责；`model_task` 不形成第二套 Provider/Retry 控制流；无可变 list 穿过 frozen 边界；无 provider import 反向进入 messages；只有一个 ProviderAttemptRunner 和一个 QueryLoop 控制流；`agentos.sync` 不复制 Kernel 语义；大文件不增长、`providers/messages.py` 已删除且 `providers/tool_specs.py` 只承载 tool schema；测试不依赖 sleep；错误不泄露敏感内容。

- [ ] **Step 5: 阶段提交**

```powershell
git add -- tests/runtime/test_message_provider_boundary_contract.py docs/api-stability.md docs/governance/agentos-module-size-baseline.json
git commit -m "test: freeze message provider boundary contract"
```

---

## Self-Review Result

- **Spec coverage:** StoredMessage、六种 ProviderInputItem（含 Runtime `model_task`）、Read Model、Snapshot 顺序、Tool Pair、每 build/attempt 重建、temporary recall、stream/non-stream parity、Provider capability adapter parity、Public name removal 均有独立任务和测试。
- **Resolved ambiguities:** ProviderInput 枚举、`model_task` 生产者/隔离边界、internal task 的 trusted SystemEnvelope 约束、ArtifactRef ownership/`media_type`、Read Model event projector 和 retry consumption 均由已批准 addendum 冻结，不交给实现猜测。
- **Plan completeness audit:** 已逐步检查，所有行为步骤均含具体输入、实现边界、命令和预期结果。
- **Type consistency:** `StoredMessage` 只在 MessageStore；`ProviderInputItem` 只在 ProviderRequest；`model_task` 只由专用 Runtime 直接构建且不进入 Turn projection；`ArtifactRef` 单一定义；`ProviderRequest.messages/tools` 始终 tuple；`ContextSnapshot` 固定工厂元数据。
- **File-size review:** `query_loop.py <= 493`、`provider_attempt.py <= 145`、`agent.py <= 144`、`agent_stream.py <= 249`、`providers/tool_specs.py < 200` 且 `providers/messages.py` 已删除；`builder.py` 仅保留组装且不得超过 `280` 行。任何目标未满足都不能通过 Phase 2 DoD。
- **Execution topology review:** Tasks 0-7 和单一异步内核重构已完成，`88ae4ff` 仅作为历史代码审计基线；Tasks 8-10 的独立工作流与 Tasks 11-12 已全部集成到 `5eca3b8` 并通过 Wave 3 门禁；Tasks 13A/13B 在当前集成分支串行执行，Task 14 最后完成阶段验收。
- **Re-baseline review:** 48 个旧边界依赖文件、101 个 Request 构造/引用位置和四个规模基线已经写入 Task 0；测试通配符已替换为实际文件路径。
- **Rollback boundary:** `model_task` 先作为受控工厂加法引入并单独迁移 LlmCompressor；随后在同一原子提交删除 legacy Provider DTO/serializer、更新 Public API tests/stability/inventory。Persistence、Compression/Recall、Memory、Policy/Capability、Debug Projection、Attempt Runner、Public cleanup 分别提交。每个任务命令都包含受影响模块回归；Task 13 删除全部 Phase 2 Message/Provider 迁移桥，唯独承载既有 Public Attachment 行为的私有兼容桥按声明保留到 Phase 3A，因此每一提交保持 Green 且可精确回滚。
