# AgentOS Message / Provider Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把业务消息真值、临时 Provider 输入、前端 Read Model 和 Provider payload 彻底分离，并保证每一次物理 Provider attempt 都从权威状态重新构建不可变双平面请求。

**Architecture:** `MessageStore` 只保存 `StoredMessage`；`ProviderRequestBuilder` 是 `SystemEnvelope + ContextSnapshot + Active StoredMessage + Tool Result + ContextMount + Tool Schemas` 的唯一组装 Owner；`ProviderInputItem` 是不可持久化的 Provider 无关逻辑输入。同步和异步 Provider attempt 共享同一 Request Factory 契约，retry 不复用旧 Snapshot，具体 OpenAI/Anthropic payload 和严格角色合并仍留给 Phase 3B。

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

## Execution Waves And Ownership

Phase 2 使用“核心接口串行冻结、外围消费者受控并行、公共 API 串行收口”的执行拓扑。共享核心文件不得由多个 worktree 同时修改。

| Wave | Tasks | Execution | Merge gate |
|---|---:|---|---|
| Wave 0 | Task 0 | 主集成分支，只读基线和 Scope Contract | Gate 0 事实、迁移清单和规模基线确认 |
| Wave 1 | Tasks 1-4 | 主集成分支串行 | `StoredMessage`、`ProviderInputItem`、Read Model 接口冻结 |
| Wave 2 | Tasks 5-7 | 主集成分支串行 | Request Builder/Attempt Contract 冻结，两个 Loop 迁移完成 |
| Wave 3A | Task 8 | 独立 worktree/subagent | Persistence 定向测试、双层 Review、精确提交 |
| Wave 3B | Task 9 | 独立 worktree/subagent | Compression/Recall 定向测试、双层 Review、精确提交 |
| Wave 3C | Task 10 | 独立 worktree/subagent | Memory 定向测试、双层 Review、精确提交 |
| Wave 3D | Tasks 11-12 | 主 Agent；可与 3A-3C 并行 | Policy/Capability/Debug 定向测试、双层 Review |
| Wave 4 | Task 13 | 所有 Wave 3 合并后在主集成分支串行 | 迁移桥和旧 Public API 零残留 |
| Wave 5 | Task 14 | 主集成分支串行 | Contract Matrix、全量门禁和阶段双层 Review |

并行规则：

- Wave 1 和 Wave 2 不启动实现 subagent；Architecture/Integration Owner 独占共享核心文件；
- Wave 3 最多同时运行三个 worktree subagent，主 Agent 保留第四个并发槽负责 Task 11-12、diff 审查和集成；
- Wave 3 worktree 禁止修改 `messages/**`、`providers/input.py`、`providers/base.py`、`runtime/provider_request_builder.py`、`runtime/provider_attempt.py`、`runtime/async_provider_attempt.py`、两个 QueryLoop、Builder 和 Public API inventory；
- 每个 worktree 从 Wave 2 的同一冻结提交创建，提交后由主 Agent 逐一审查并以非交互 Git 命令集成；不允许在 worktree 内自行合并其他工作流；
- Task 13 前必须重新运行旧名称 drift scan；Task 14 前不得保留任何未声明迁移桥。

### Compatibility Budget

本项目尚未生产推广，Phase 2 优先选择清晰的 breaking migration，不为历史调用方式设计长期兼容架构。允许的临时兼容仅有：

1. `Message = StoredMessage` 同一类身份 alias，用于让分阶段提交保持可运行；不得增加 wrapper、subclass、双写、行为分支或第二套 serializer，并在 Task 13 删除；
2. 现有 Public Attachment 行为的私有 ProviderInput 投影桥，仅维持当前图片能力，并在 Phase 3A 由正式 ContextMount 替换。

除以上两项外，不得新增 deprecated facade、legacy DTO、旧新字段双读、自动猜测迁移、版本分支或 Adapter-specific 领域字段。任何新增兼容需求都必须先停止实现、更新 Spec 并获得批准；Phase 2 最终 Public API 只保留正式的 `StoredMessage` 和 `ProviderInputItem` 边界。

## Mandatory Execution Bootstrap And Review Gate

Task 1 前依次完整读取 `AGENTS.md`、工程规范、两份批准 Spec、已批准 addendum、本计划、Phase 1 实施结果、将修改的源码/相邻测试及对应 `ai-knowledge/wiki` 页面，并重新发布 Scope Contract。上下文压缩、交接或 Agent 替换后重复。

Task 1–14 每个任务都必须执行 Red -> 最小实现 -> 模块 Green -> 独立 Spec Compliance Review -> 修复 Critical/Important -> 独立 Code Quality Review -> 精确暂存提交。两层 Reviewer 均 `APPROVED` 前不得提交或进入下一任务；禁止 `git add .`。Task 14 的阶段 Review 是跨模块额外验收，不替代逐任务门禁。

## Scope Contract

- **Phase / Active Specs:** Phase 2；两份 2026-07-10 已批准 Spec、总体实施计划、Phase 1 实施结果，以及 2026-07-11 Message/Provider Contract Addendum。
- **Acceptance Items:** `StoredMessage` 是唯一业务消息真值；所有 JSON-like 领域字段递归冻结；`ProviderInputItem`/`ProviderRequest` 深不可变；Snapshot 位于 Active Messages 前且不打断 Tool Pair；Read Model 只来自 StoredMessage 和显式 Event Projector；每次 build/physical retry 重新渲染 Snapshot；temporary recall 只在 after-hook 与 usability validation 均成功后按 build receipt 精确消费；同步/异步遵守同一 Attempt Contract；旧 `Message`/`ProviderMessage` Public 名称删除。
- **Allowed Files:** `src/agentos/_frozen_json.py`、`src/agentos/_internal_transcript.py`；`src/agentos/artifacts/types.py`、`artifacts/__init__.py`（仅 ArtifactRef）；`src/agentos/context/models.py`（仅 internal transcript marker）；`src/agentos/messages/**`；`src/agentos/providers/input.py`、`base.py`、`messages.py`、`__init__.py` 和具体 Adapter 的最小逻辑输入兼容；`src/agentos/attachments/runtime.py`（仅旧 Public Attachment 行为的 ProviderInput 兼容桥）；`src/agentos/runtime/message_projection.py`、`provider_request_builder.py`、`provider_attempt.py`、`async_provider_attempt.py`、`query_loop.py`、`async_query_loop.py`；`src/agentos/builder.py`；所有直接依赖旧 Message 名称的 persistence/compression/recall/memory/policy/capability 类型注解和 serializer；对应测试、API inventory 和稳定性文档。
- **Forbidden Files:** Artifact Store/Runtime/Projection、Attachment 生命周期重写、Skill/Plan/Memory Projection、Provider-specific strict-role merge/File ID/cache 优化、Tool Scheduler、Distributed/Transport 语义、Root API 五名称收敛之外的公共扩张。
- **Dependency Boundaries:** `messages` 不导入 `providers`；Provider Input Projection 位于 `providers/input.py` 或 Request Builder 的纯函数边界；Provider Adapter 不读取 MessageStore/ContextRuntime；Read Model 不读取 ProviderRequest Transcript。
- **Completed In This Work Package:** M2 Core Request Pipeline、业务/Provider/前端三类模型分离、每 attempt 重建、同步异步一致性和 breaking type migration。
- **Explicit Deferrals:** Artifact Catalog/Mount/Session Scope 到 Phase 3A；Phase 2 只保留现有 Public Attachment API 的私有 ProviderInput 兼容桥，不扩展新附件语义，并在 Phase 3A 由正式 ContextMount 替换；完整 Adapter Contract/严格角色合并到 Phase 3B；真实 Skill/Plan/Memory Projection 到 Phase 3C；Local Tool Scheduler/Public root 收敛到 Phase 4。
- **Verification Commands:** 每任务定向 pytest；Phase 1+2 contract matrix；全量 pytest；compileall；ruff；public inventory generator；协议 drift scan；module size scan；diff check。

## File Responsibility Map

| File | Single responsibility |
|---|---|
| `_frozen_json.py` | JSON-like 值的递归校验、冻结与 thaw，不含任何领域语义。 |
| `_internal_transcript.py` | Provider/Context 内部投影对象的中立 nominal marker，供 Read Model fail-closed。 |
| `artifacts/types.py` | 只冻结 `ArtifactRef` 轻量值类型。 |
| `messages/types.py` | `StoredMessage`、`MessageRef`、`ToolCall` 业务领域值。 |
| `messages/store.py` | append-only StoredMessage 真值。 |
| `messages/window.py` | Active refs、temporary refs 和 Tool Pair 保护。 |
| `messages/runtime.py` | Store/Window 门面，不做 Provider 投影。 |
| `messages/read_model.py` | StoredMessage + 显式 Event Projector 到 Conversation Read Model。 |
| `providers/input.py` | `ProviderInputItem`、ContentPart 和逻辑输入自身校验；不导入 StoredMessage。 |
| `providers/base.py` | 不可变 `ProviderRequest`/Response/Provider Protocol。 |
| `runtime/provider_request_builder.py` | 每次调用组装双平面逻辑请求并返回本次投影 receipt。 |
| `runtime/message_projection.py` | StoredMessage/MessageRef 到 ProviderInputItem 的纯映射。 |
| `attachments/runtime.py` | 保留现有附件行为；Phase 2 只增加私有 ProviderInput 兼容入口。 |
| `runtime/provider_attempt.py` | 同步 attempt 的重建、Hook、Retry 和成功消费边界。 |
| `runtime/async_provider_attempt.py` | 异步 attempt 的同构语义。 |

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
- Create: `tests/test_frozen_json.py`
- Test: `tests/messages/test_stored_message.py`

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

- [ ] **Step 4: Green 和提交**

```powershell
python -m pytest tests/test_frozen_json.py tests/messages/test_stored_message.py tests/messages -q
git add -- src/agentos/_frozen_json.py src/agentos/artifacts/types.py src/agentos/artifacts/__init__.py src/agentos/messages/types.py src/agentos/messages/_migration.py src/agentos/messages/__init__.py tests/test_frozen_json.py tests/messages/test_stored_message.py
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
- Modify: `tests/providers/test_provider_messages.py`
- Create: `tests/providers/test_provider_input_contract.py`

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

- [ ] **Step 4: Green 和提交**

```powershell
python -m pytest tests/providers/test_provider_input_contract.py tests/providers/test_provider_messages.py tests/context/test_context_protocol_models.py -q
git add -- src/agentos/_internal_transcript.py src/agentos/context/models.py src/agentos/providers/input.py src/agentos/providers/base.py src/agentos/providers/messages.py src/agentos/providers/__init__.py tests/providers/test_provider_input_contract.py tests/providers/test_provider_messages.py
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
git add -- src/agentos/attachments/runtime.py src/agentos/runtime/message_projection.py src/agentos/runtime/provider_request_builder.py tests/runtime/test_provider_request_builder.py tests/runtime/test_provider_request_rebuild.py tests/runtime/test_query_loop.py
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
Get-ChildItem src/agentos/runtime/query_loop.py,src/agentos/runtime/async_query_loop.py | ForEach-Object { "{0}: {1}" -f $_.Name,(Get-Content -Encoding utf8 $_).Count }
```

- [ ] **Step 5: 提交**

```powershell
git add -- src/agentos/runtime/provider_attempt.py src/agentos/runtime/async_provider_attempt.py src/agentos/runtime/provider_request_builder.py src/agentos/runtime/query_loop.py src/agentos/runtime/async_query_loop.py src/agentos/messages/runtime.py src/agentos/messages/_migration.py tests/messages/test_runtime.py tests/runtime/test_provider_attempt_rebuild.py tests/runtime/test_async_provider_attempt_rebuild.py
git commit -m "refactor: rebuild requests for every provider attempt"
```

---

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

新增断言 Memory store/runtime/serializer 的公开类型注解和返回实例都是 `StoredMessage`，源码不再从 `agentos.messages` 导入旧 `Message`。该断言在迁移前确定性失败；同时保留现有 serializer round-trip characterization tests。

- [ ] **Step 2: 机械迁移并验证**

只迁移 `StoredMessage` 类型、tuple 和 `FrozenJsonObject` 的只读访问，不改变 hot/durable store 算法或 Redis wire schema。

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

### Task 13: 收口 AgentBuilder、删除迁移桥与旧 Public 名称

**Files:**
- Modify: `src/agentos/builder.py`
- Modify: `src/agentos/messages/runtime.py`
- Delete: `src/agentos/messages/_migration.py`
- Modify: `src/agentos/messages/__init__.py`
- Modify: `src/agentos/providers/openai.py`
- Modify: `src/agentos/providers/openai_compatible.py`
- Modify: `src/agentos/providers/anthropic.py`
- Modify: `src/agentos/providers/messages.py`
- Modify: `src/agentos/providers/__init__.py`
- Modify: `tests/runtime/test_agent_builder.py`
- Modify: `tests/architecture/test_public_api.py`
- Modify: `docs/public-api-inventory.json`
- Modify: `docs/api-stability.md`

- [ ] **Step 1: 写桥删除与 Builder 组装 Red 测试**

```python
def test_legacy_message_names_are_not_public() -> None:
    import agentos.messages as messages
    import agentos.providers as providers
    assert not hasattr(messages, "Message")
    assert not hasattr(providers, "ProviderMessage")


def test_builder_assembles_projection_owners_without_adapter_dependency() -> None:
    source = inspect.getsource(AgentBuilder)
    assert "OpenAIProvider" not in source
    assert "AnthropicProvider" not in source
    assert "SkillProjection(" not in source
```

- [ ] **Step 2: 删除全部临时桥并完成 Builder 接线**

确认 Task 7 已删除 `materialize_provider_messages()`/`_to_provider_message()`；本任务删除剩余的 `Message = StoredMessage` alias、Adapter `LegacyProviderMessage` union 和旧 Public exports。保留并明确标记 Phase 3A 删除的 Attachment ProviderInput 私有兼容桥，因为它承载现有 Public Attachment 行为，不属于旧 Message Public API。`AgentBuilder` 只组装 Phase 1 renderer、snapshot renderer、projection providers、MessageRuntime 与 Request Builder，不硬编码 Skill/Plan/Memory/Artifact 类型，不新增业务职责。

- [ ] **Step 3: 更新兼容声明、inventory、验证并提交**

`docs/api-stability.md` 记录 `0.2.0a1` 删除旧名，说明迁移期间桥从未作为新 Public API 承诺。生成 inventory 后要求 `providers/messages.py < 300`、`builder.py` 不净增职责且行数不超过实施前基线；否则继续拆分。

```powershell
python scripts/generate_public_api_inventory.py --output docs/public-api-inventory.json
python -m pytest tests/messages tests/providers tests/runtime/test_agent_builder.py tests/architecture/test_public_api.py -q
Get-ChildItem src/agentos/providers/messages.py,src/agentos/builder.py | ForEach-Object { "{0}: {1}" -f $_.Name,(Get-Content -Encoding utf8 $_).Count }
git add -- src/agentos/builder.py src/agentos/messages src/agentos/providers tests/runtime/test_agent_builder.py tests/architecture/test_public_api.py docs/public-api-inventory.json docs/api-stability.md
git commit -m "refactor: remove message provider migration bridges"
```

---

### Task 14: Phase 2 Contract Matrix、双层 Review 和阶段验收

**Files:**
- Create: `tests/runtime/test_message_provider_boundary_contract.py`
- Modify: `docs/api-stability.md`

- [ ] **Step 1: 增加跨边界契约矩阵**

Contract Matrix 必须覆盖：五种 ProviderInput kind 的全部合法矩阵与代表性非法交叉组合；Snapshot 固定元数据与首位顺序；Tool Pair 邻接；Read Model 明确拒绝 ProviderRequest/ProviderInputItem/internal transcript；每 physical retry fresh request；before-hook 替换清空 receipt；after-hook/usability validation 前不消费；按 receipt ID 精确消费；visible delta 后禁止 retry；sync/async parity；StoredMessage 无 Provider metadata；Tool arguments/schema parameters 深不可变；Builder 不依赖具体 Adapter。

- [ ] **Step 2: 运行目标和全量验证**

```powershell
python -m pytest tests/context tests/messages tests/providers/test_provider_input_contract.py tests/runtime/test_provider_request_builder.py tests/runtime/test_provider_request_rebuild.py tests/runtime/test_provider_attempt_rebuild.py tests/runtime/test_async_provider_attempt_rebuild.py tests/runtime/test_message_provider_boundary_contract.py -q
python -m pytest -q
python -m compileall -q src tests
python -m ruff check src tests
python scripts/generate_public_api_inventory.py --check
rg -n "system: rendered context|AttachmentLifecycle|ProviderMessage|class Message\b|materialize_provider_messages|<task_goal>|<constraints>" src tests
rg -n "ProviderMessage|class Message\b|materialize_provider_messages" docs --glob "!superpowers/plans/2026-07-10-agentos-message-provider-boundary-implementation-plan.md" --glob "!superpowers/specs/2026-07-11-agentos-message-provider-boundary-contract-addendum.md"
Get-ChildItem src/agentos/runtime/query_loop.py,src/agentos/runtime/async_query_loop.py,src/agentos/providers/messages.py,src/agentos/builder.py | ForEach-Object { "{0}: {1}" -f $_.Name,(Get-Content -Encoding utf8 $_).Count }
git diff --check
```

Expected: 全部 PASS；`src tests` 的严格 drift scan 零命中。Docs scan 的命中必须逐条列入人工允许清单，仅允许历史迁移说明；不得用一个宽泛 glob 跳过全部活跃文档。最终规模：`query_loop.py < 800`、`async_query_loop.py < 500`、`providers/messages.py < 300`；`builder.py` 不净增职责且不超过实施前基线。

- [ ] **Step 3: Spec Compliance Review**

Reviewer 独立检查：Authority/Persistence/Visibility 矩阵；ArtifactRef ownership；Snapshot/Tool Pair/ContextMount 预留顺序；每 attempt 重建；temporary 消费；Read Model 默认拒绝；Phase 3B 未偷跑；无 silent deferral。

- [ ] **Step 4: Code Quality Review**

Reviewer 独立检查：Store/Runtime/Projection 单一职责；无可变 list 穿过 frozen 边界；无 provider import 反向进入 messages；Attempt Runner 无重复 sync/async 语义；大文件净缩减；测试不依赖 sleep；错误不泄露敏感内容。

- [ ] **Step 5: 阶段提交**

```powershell
git add -- tests/runtime/test_message_provider_boundary_contract.py docs/api-stability.md
git commit -m "test: freeze message provider boundary contract"
```

---

## Self-Review Result

- **Spec coverage:** StoredMessage、ProviderInputItem、Read Model、Snapshot 顺序、Tool Pair、每 build/attempt 重建、temporary recall、sync/async parity、Public name removal 均有独立任务和测试。
- **Resolved ambiguities:** ProviderInput 枚举、ArtifactRef ownership/`media_type`、Read Model event projector 和 retry consumption 均由已批准 addendum 冻结，不交给实现猜测。
- **Plan completeness audit:** 已逐步检查，所有行为步骤均含具体输入、实现边界、命令和预期结果。
- **Type consistency:** `StoredMessage` 只在 MessageStore；`ProviderInputItem` 只在 ProviderRequest；`ArtifactRef` 单一定义；`ProviderRequest.messages/tools` 始终 tuple；`ContextSnapshot` 固定工厂元数据。
- **File-size review:** `query_loop.py` 目标 `<800`、`async_query_loop.py` 目标 `<500`、`providers/messages.py` 目标 `<300`；`builder.py` 仅保留组装且不得超过实施前基线。任何目标未满足都不能通过 Phase 2 DoD。
- **Execution topology review:** Tasks 1-7 独占共享核心文件；Tasks 8-10 只在 Wave 2 接口冻结后进入三个独立 worktree；Tasks 11-12 由主 Agent 持有；Tasks 13-14 等待所有迁移工作流合并后串行执行。
- **Re-baseline review:** 48 个旧边界依赖文件、101 个 Request 构造/引用位置和四个规模基线已经写入 Task 0；测试通配符已替换为实际文件路径。
- **Rollback boundary:** 新类型先以受限内部桥加法引入；Adapter 双读先于 Request Builder 切换；Persistence、Compression/Recall、Memory、Policy/Capability、Debug Projection、Attempt Runner、Public cleanup 分别提交。每个任务命令都包含受影响模块回归；Task 13 删除全部 Phase 2 Message/Provider 迁移桥，唯独承载既有 Public Attachment 行为的私有兼容桥按声明保留到 Phase 3A，因此每一提交保持 Green 且可精确回滚。
