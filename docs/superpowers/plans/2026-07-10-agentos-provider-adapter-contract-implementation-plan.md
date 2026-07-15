# AgentOS Provider Adapter Contract Implementation Plan

> 状态：待用户批准；依赖 Phase 3 Contract Addendum 和 Artifact Plan Task 0

**Goal:** 为 OpenAI Responses、OpenAI Chat Completions、OpenAI-Compatible Chat 和
Anthropic 建立统一 Context Protocol Contract Matrix，并拆分超大 Compatible Adapter。

**Architecture:** ProviderRequest 保持 Provider 无关且不可变；Adapter 只在最终 wire
payload 做角色合并、多模态编码和响应解析。Provider 不读取 Context/Message/Artifact
Store，不拥有 retry，不反写逻辑输入。

## Scope Contract

- **Phase / Specs:** Phase 3B；两份 2026-07-10 Spec、Phase 3 Contract Addendum、
  Context-First SDK 总体计划。
- **Acceptance:** 四类 Adapter 独立矩阵、Authority 分离、Tool/Mount 顺序、strict-role
  merge、binary mapping、timeout/error、payload immutability、Compatible 拆分。
- **Allowed:** `src/agentos/providers/**` 和 `tests/providers/**`，但明确排除 Preflight 独占的
  `providers/input.py`、`providers/input_serialization.py`、`providers/__init__.py`、
  Binary Content input/import/signature contract tests。Provider Owner 只消费已合入的
  Preflight，不再次修改共享契约；可继续修改已经迁为 `.payload` 的 Adapter 消费者。
- **Forbidden:** `context/**`、`messages/**`、`artifacts/**`、Builder、
  ProviderRequestBuilder、QueryLoop、ProviderAttemptRunner、Public API Inventory/Root exports、
  module-size baseline 写入、Persistence/Distributed。
- **Dependencies:** Provider 层只依赖 Provider value types、标准库和注入 client/transport；
  不导入 `agentos.attachments` 或 `agentos.artifacts`。
- **Completion:** 完成 Adapter wire contract；不改变 Runtime retry/loop。
- **Deferral:** Provider hosted conversation、previous_response_id、真实网络 smoke 和
  分布式 File ID cache；Artifact 生命周期到 3A/4。
- **Verification:** Provider/Runtime contract tests、全量、static、module size、drift。

## Readiness Gate

- [ ] Phase 2 `2370 passed, 11 skipped` 证据有效；
- [ ] Phase 3 Contract Addendum 已批准；
- [ ] `ProviderBinaryPayload` 共享提交已合入；
- [ ] 当前 Provider characterization：`tests/providers = 126 passed`；
- [ ] Provider/RequestBuilder 交叉集 `47 passed`；架构集 `69 passed`。

## Contract Matrix

| Contract | Responses | Chat | Compatible | Anthropic |
|---|---:|---:|---:|---:|
| System/Snapshot 分离 | required | required | required | required |
| Tool call/result | required | required | required | required |
| Mount 顺序 | required | required | required | required |
| Image | required | required | required | required |
| File/PDF | required | reject with stable error | reject with stable error | PDF required |
| Strict user merge | n/a | n/a | capability-specific | required |
| Payload 不反写 | required | required | required | required |
| timeout 映射 | required | required | required | required |

## File Responsibility Map

| File | Responsibility |
|---|---|
| `openai.py` | Responses facade、调用生命周期和响应标准化。 |
| `openai_responses_wire.py` | Responses request/content/tool 纯映射。 |
| `openai_chat.py` | 官方 Chat Completions facade。 |
| `openai_chat_wire.py` | 官方/Compatible 共用的 Chat Completions 基础映射；唯一 shared Chat primitive owner。 |
| `openai_compatible.py` | Compatible facade，目标 `<300`。 |
| `openai_compatible_wire.py` | Compatible capability/extra_body 差异包装，单向依赖 `openai_chat_wire.py`，目标 `<500`。 |
| `openai_compatible_parsing.py` | response/stream 解析，目标 `<500`。 |
| `openai_compatible_transport.py` | sync/async HTTP、SSE、timeout，目标 `<500`。 |
| `anthropic.py` | Anthropic facade。 |
| `anthropic_wire.py` | content、tool 和 strict-role merge 纯函数。 |

---

### Task 0: Characterization 与拆分门禁

**Files:** 仅 tests 和计划记录，不改生产行为。

- [ ] 固化现有 Compatible payload、extra_body 优先级、sync/async stream event、fallback
  tool ID、SSE metadata、timeout 和 error body。
- [ ] 将 970/773 行测试按 wire、transport、parsing/stream 拆成职责文件；测试移动不能
  改断言语义。
- [ ] 运行：

```powershell
python -m pytest tests/providers/test_openai_compatible.py tests/providers/test_openai_compatible_streaming.py tests/providers/test_provider_timeout.py -q
```

Expected: `51 passed`；`126 passed` 是完整 `tests/providers` 基线，不作为本子集计数。

- [ ] 提交：`test: characterize provider adapter wire behavior`。

### Task 1: 机械拆分 OpenAI-Compatible

**Files:** `openai_compatible.py`、新增 `openai_chat_wire.py` 和
wire/parsing/transport 模块、相关 tests。

- [ ] 先写架构 Red：facade `<300`，三个目标模块 `<500`，旧
  `_openai_compatible_payload.py` 不形成第二 payload owner。
- [ ] 按顺序移动：transport -> parsing -> wire；facade 只保留配置、调用和生命周期。
- [ ] Task 1 即建立 `openai_chat_wire.py` 的公共 Chat 基础映射；
  `openai_compatible_wire.py -> openai_chat_wire.py` 是唯一允许方向，Compatible 模块只保留
  capability、extra_body 和服务差异，不复制 message/tool/content 基础编码。
- [ ] sync/async stream 必须调用同一个 parser/state builder；不能复制 delta 规则。
- [ ] 此提交不得改变 wire payload、retry、timeout 或 event 序列。
- [ ] 运行全部 Provider characterization。
- [ ] 提交：`refactor: split openai compatible adapter`。

### Task 2: 建立统一 Context Protocol Contract Helper

**Files:** `tests/providers/test_context_protocol_contract.py` 和 adapter fakes。

- [ ] 先为当前已经可构造的 Adapter 写参数化 helper 和 characterization tests：

```python
@pytest.mark.parametrize("adapter_factory", PROVIDER_ADAPTER_FACTORIES)
def test_adapter_preserves_context_protocol_boundaries(adapter_factory): ...
```

- [ ] 构造固定逻辑序列：ContextSnapshot、business user、assistant tool call、tool result、
  ContextMount；断言 System 独立、Mount 在 Tool Result 后、原 request hash/内容不变。
- [ ] Adapter-specific 断言不塞进公共 helper；本 Task 只提交当前实现可满足的 Green
  helper/fixtures，不预先提交 Responses 缺失或 Anthropic merge 失败的已知 Red。
- [ ] Responses、Chat、Compatible、Anthropic 的新增 Red 与对应 Adapter 实现在各自 Task
  内完成并转 Green，再把该 Adapter factory 加入矩阵。
- [ ] 提交：`test: define provider context protocol matrix`。

### Task 3: OpenAI Responses Adapter

**Files:** `openai.py`、`openai_responses_wire.py`、
`tests/providers/test_openai_responses.py` 和该 Adapter 的 matrix fixture。

- [ ] `OpenAIProvider` 改为调用 `client.responses.create`；不保留运行时 mode 分支。
- [ ] 在同一 Task 先写 Adapter-specific Red，再实现并转 Green。映射：SystemEnvelope ->
  instructions；Snapshot/business -> input message；Tool call -> function_call；Tool result ->
  function_call_output；Image/File -> input_image/input_file。
- [ ] `ProviderRequest.tools` 映射为 Responses function tool schema；仅当 tools 非空时映射
  `parallel_tool_calls`，并用 request-wire tests 覆盖 schema、顺序、缺省和显式 true/false。
- [ ] fake client 测试 complete、tool calls、usage、timeout、malformed response、binary payload。
- [ ] 若实现 native stream，必须复用 Provider stream event 契约；若 SDK client 不提供该能力，
  明确依赖 Runtime 的 complete fallback，不伪造实时 delta。
- [ ] 提交：`feat: add openai responses adapter`。

### Task 4: 显式 OpenAI Chat Completions Adapter

**Files:** `openai_chat.py`、`openai_chat_wire.py`、
`tests/providers/test_openai_chat.py` 和该 Adapter 的 matrix fixture。

- [ ] 新建 `OpenAIChatCompletionsProvider`，迁移旧 `OpenAIProvider` Chat 行为。
- [ ] 在同一 Task 先写 Adapter-specific Red，再实现并转 Green。
- [ ] 复用 Task 1 已冻结的 `openai_chat_wire.py`；若必须扩展 shared primitive，同一提交先证明
  Compatible characterization 全绿，不在官方与 Compatible 之间建立反向依赖。
- [ ] Image 映射为 image_url；FilePart 使用稳定不支持错误；parallel flag 只在存在 tools 时发送。
- [ ] 不提供旧 `OpenAIProvider` Chat facade 或自动猜测；迁移说明进入 `docs/api-stability.md`
  的工作、`providers/__init__.py` 导出和 Public API Inventory 由 Phase 4 Public API Owner
  统一完成。
- [ ] 提交：`feat: expose explicit openai chat adapter`。

### Task 5: Anthropic Strict-Role Wire

**Files:** `anthropic.py`、`anthropic_wire.py`、`tests/providers/test_anthropic.py`。

- [ ] Red：ContextSnapshot text + business user text 必须合并为单个 user message；多个
  Tool Result 和 Mount 合并后 block 顺序保持；原逻辑对象不变。
- [ ] Red 与 strict-role 实现在本 Task 内完成并转 Green，不提交跨 Task 的已知失败。
- [ ] merge 只操作新建 wire list，不对输入 dict/list 原地扩展。
- [ ] Image 和 PDF 使用 `ProviderBinaryPayload.data`；不导入旧 Attachment Source。
- [ ] 提交：`refactor: enforce anthropic role alternation`。

### Task 6: Provider Binary Mapping 与私有缓存边界

**Files:** 各 wire 模块、`tests/providers/test_provider_binary_wire.py`；不修改 Preflight 的
input/import/signature contract tests。

- [ ] Red：Provider 源码零 `agentos.attachments` import；Payload 不接受 path/URL/File ID；
  Adapter 编码后 ProviderRequest 仍持有原 bytes 值且未变异。
- [ ] Responses 可直接 inline 或使用注入的私有 file cache；cache key 至少包含 provider、
  handle 和内容 hash，cache value 不进入领域对象。
- [ ] 第一版可以没有 cache；不得为了占位引入无调用者的通用缓存框架。
- [ ] 提交：`refactor: isolate provider binary wire mapping`。

### Task 7: Timeout、错误与 Stream 一致性

**Files:** Provider facade/transport/parsing tests。

- [ ] Red：sync/async timeout 都是 `ProviderTimeoutError`；相同 chunks 的 sync/async parser
  产生字节级等价事件；cancel 不产生 completed；malformed tool arguments 确定性失败。
- [ ] Adapter 不增加 retry 或 backoff；只映射单次调用错误。
- [ ] 提交：`fix: align provider transport contracts`。

### Task 8: Phase 3B 收口

- [ ] 运行：

```powershell
python -m pytest tests/providers -q
python -m pytest tests/runtime/test_provider_request_builder.py tests/runtime/test_message_provider_boundary_contract.py tests/runtime/test_async_provider_attempt_rebuild.py tests/runtime/test_provider_request_rebuild.py -q
python -m pytest -q
python -m compileall -q src tests
python -m ruff check src tests
python -m pytest tests/architecture/test_module_size_baseline.py tests/architecture/test_public_api.py tests/architecture/test_public_api_inventory.py -q
rg -n "agentos\.attachments|ProviderFileSource|LocalFileSource|InlineBase64Source|UrlSource" src/agentos/providers tests/providers
git diff --check
```

`docs/governance/agentos-module-size-baseline.json` 由 Integration/Quality Owner 在三路合并后
串行更新；本 Workstream 只运行只读规模门禁。Phase 3B 新类型只验证模块级导入，Root export
和 Public API Inventory 更新由 Phase 4 统一完成。

- [ ] Spec Review：Authority、顺序、Provider 状态优化、retry owner、错误。
- [ ] Quality Review：拆分职责、sync/async 共享、测试稳定性、无 request mutation。
- [ ] 提交：`docs: close phase3b provider adapter contract`。

## Rollback And Integration

Task 1 是纯拆分，可独立回滚；Tasks 3/4 是协调 breaking API，必须一起进入 Phase 4 的
Public API inventory。Phase 3 集成分支先合并 Provider，再合并 Artifact，使 Adapter
在接入真实 Artifact Projection 前已经支持冻结的 Binary Payload。
