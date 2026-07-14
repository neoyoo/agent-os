# Skill ContentSource Async 重构 & PR 整合计划

> **SUPERSEDED FOR LOOP TOPOLOGY:** 本归档计划中的旧双 Loop 与 Agent API
> 描述已被 `docs/superpowers/specs/2026-07-12-agentos-single-async-query-loop-design.md`
> 取代；历史正文保留。
>
> Status: archived / 已收口
>
> Closed on: 2026-06-10
>
> 当前 `master` 已完成本计划的 SDK 主干实现并合并推送：
> native `AsyncQueryLoop`、async `SkillContentSource`、streaming `extra_body`、
> fallback `tool_call_id`、duplicate tool-call suppression、turn-scoped
> `load_attachment`、image-only attachment 边界和相关文档均已落地。旧远端分支已统一清理。
>
> 外部 webagent 端 RedisSkillSource drop-in 验证不属于本仓库实现范围，后续如需继续，应另开新计划。

> 给 Codex 直接执行。每个 Phase 完成后停下来报告，等 Neo 确认 checkpoint 通过再进下一个 Phase。

## 背景

当前 master 上 6 个 open PR（#2-#7）围绕 issue #1（Qwen 多模态附件）和 web agent runtime 重构。Review 结论：
- PR #2 是源分支，内容已通过其它分支拆分处置——`_content_parts.py` 已进 master（commit `41d94e6`）；live streaming 由 PR #4；`extra_body` 由 PR #5；`load_attachment` tool surface 由 PR #6。PR #2 独有部分**部分采纳，部分丢弃**：
  - ✅ 采纳：fallback `tool_call_id`（issue #1 第 4 项需求，Phase 2.3 摘入）、duplicate tool-call suppression（Phase 2.4 摘入）、turn-scoped image persistent state（Phase 4 融合进 PR #6 的 `load_attachment` tool）
  - ❌ 丢弃：image-first 顺序（与 text-first 标准相反）、反向 `extra_body` 覆盖（与 core-fields-protected 原则相反）、`request_payload()` provider public 方法（nice-to-have，master 已有内部 `_request_payload`）、PR #2 的 budget 计数机制（被 turn-scoped 替代）
- PR #3 `SkillContentSource` ABC 是 sync，webagent 端要用 `redis.asyncio` + 连接池 + pipeline，**必须改 async**
- PR #4 / #5 小修后可合
- PR #6 整合分支基本 OK，2 个 follow-up
- PR #7 docs 跟随 #6

合并纪律：每个 Phase 独立分支 → 自测过 → `integration/staging` 集成 review → 通过 checkpoint 后 fast-forward 到 master。

---

## 分支处置总览表（Codex Quick Reference）

| 当前分支 / PR | 远端 ref | 最终处置 | 关键问题 | Codex 要做的具体改动 | Review 验收点 |
|---|---|---|---|---|---|
| **PR #2** `fix-persistent-recalled-attachments` | `origin/fix-persistent-recalled-attachments` | 🗑️ **拆分迁移 + 部分摘入后关闭** | PR #2 是源分支，内容已被拆分：`_content_parts.py` / 部分 lifecycle 已进 master（commit 41d94e6）；live streaming → PR #4；`extra_body` → PR #5；`load_attachment` tool surface → PR #6。PR #2 独有的 fallback `tool_call_id`（issue #1 第 4 项需求）、duplicate tool-call suppression（防 LLM 同参数重复调用）、turn-scoped image persistent state **必须迁移**；image-first 顺序、反向 `extra_body` 覆盖、PR #2 budget 计数机制 **丢弃** | Phase 0：归属表 walkthrough；不立即关闭，**等 Phase 2.3 / 2.4 / Phase 4 摘入完成后再关闭** | 归属表所有"采纳"项都在后续 Phase 落实；所有"丢弃"项在关闭评论里说明 |
| **PR #3** `skill-progressive-disclosure` | `origin/skill-progressive-disclosure` | ❌ 废弃，先补 native async runtime，再重做 async skills（Phase 3A `refactor/native-async-query-loop` + Phase 3B `refactor/skill-async-abc`） | sync ABC 不能用 `redis.asyncio`；当前 `AsyncQueryLoop` 只是 sync loop + executor，不能真正 await async skill handler；L2→L3 桥结构性风险；builtin skill 静默无 resources；单数/复数 API 混乱 | Phase 3A：原生 `AsyncQueryLoop`，tool dispatch 走 `await async_execute_tool_call`；Phase 3B：ABC 改 async、RegisteredTool 支持 async handler、新增 `BuiltinSkillSource` 统一路径、`render_tool_result` 附 manifest、单数 API 私有化、新增 `docs/skills.md` Redis 示例 | native async loop 测试覆盖 async provider + async tool；全测过；ruff 干净；webagent 端 drop-in `RedisSkillSource` 端到端验证通过 |
| **PR #4** `fix-provider-live-streaming` | `origin/fix-provider-live-streaming` | ⚠️ 补测试后合，**依赖 Phase 1 前置** | streaming live yield 设计正确。master 自身 `test_async_agent_api.py` 9 passed；**PR #4 的 streaming 改动暴露了 master 中潜在的 async bridge cancel cleanup 缺口**，导致该 PR CI 红（`test_agent_async_stream_cancellation_waits_for_worker_to_finish` 等）。`ProviderStreamCancelled` 路径无专属测试 | Phase 2.1（在 Phase 1 合入后）：rebase 到带 async bridge 修复的 master，CI 红自动消失；再新增两个 `ProviderStreamCancelled` 测试；force-push 到 PR #4 头分支 | CI 绿；两个 cancel 测试通过；emitted_visible_delta 守卫所有 event 类型行为正确 |
| **PR #5** `feat-openai-compatible-extra-body` | `origin/feat-openai-compatible-extra-body` | ⚠️ 小修后合，**依赖 Phase 1 前置** | streaming 路径生效但测试零覆盖；浅拷贝在嵌套场景理论不安全；**PR #5 的改动同样暴露 async bridge cancel cleanup 缺口**（master 自身 9 passed），导致 CI 红 | Phase 2.2（在 Phase 1 合入后）：rebase 到带 async bridge 修复的 master，CI 红自动消失；`dict(self.extra_body or {})` → `copy.deepcopy(...)`；新增 streaming + async_stream 的 extra_body 测试 | CI 绿；streaming 测试断言 payload 含 extra_body 字段；core fields 防覆盖测试仍过 |
| **PR #6** `dev/web-agent-runtime` | `origin/dev/web-agent-runtime` | ✅ 整合分支基本可用，需 rebase + 2 个 follow-up（Phase 4 新分支 `feat/web-agent-runtime-v2`） | `MessageRuntime.inject_temporary_recalled` 死 API 未清；嵌套 TaskGroup cancel 无测试；PDF 删除未在 PR body 声明；**注意**：本 PR 已含 `_async_bridge.py` 修复，但该修复会在 Phase 1 单独前置合入，所以 rebase 时会有冲突，按"master 已有"解决 | Phase 4：基于 Phase 3B 后的 master rebase，丢弃旧 skill 代码 + 接受 master 的 `_async_bridge.py`；删 `inject_temporary_recalled` + 测试；新增 `test_async_bridge_nested_cancel.py`；PR body 显式声明 BREAKING | 全测过；`load_attachment` vs `recall_context` 边界清晰；嵌套 cancel 测试覆盖 |
| **PR #7** `docs/web-agent-runtime-context-sync` | `origin/docs/web-agent-runtime-context-sync` | ✅ 跟随 #6 后合 | PDF 删除文档轻度不足；缺 SDK async 行为说明 | Phase 5：rebase 到 Phase 4 后的 master；`readme-online.md` 补 MIME 限制段；`sdk-architecture.md` 补 SkillContentSource async 段 | 文档对得上代码实际行为 |

**全局约束**：
- 任一 Phase 测试红灯立即停，不绕过、不 skip、不 `--no-verify`
- 每个 Phase 独立分支，最终通过 `integration/staging` 集成 review 才合 master
- 每个 Checkpoint 由 Neo 人工 review，Codex 不擅自 fast-forward 到 master
- 所有新增测试不许用 mock 代替真实行为（mock 仅用于外部 I/O 边界如 Redis / HTTP transport）

---

## Phase 0 — PR #2 归属确认（不关闭，等迁移完成后再关）

**已验证事实**（grep src/ + 远端分支 `git show`，2026-05-19）：
- `src/agentos/providers/_content_parts.py` 已经在 master（commit `41d94e6`），与 PR #2 版本 `git diff` 无差异
- `src/agentos/providers/openai_compatible.py:15` 已 `from agentos.providers._content_parts import openai_chat_user_content`
- master 的 `_project_user_handles` (`attachments/runtime.py:181`) 和 `project_provider_messages` recall 路径 (`attachments/runtime.py:149`) 均为 **text-first**（`TextPart` 在前），符合多模态 API 标准
- PR #2 当前 CI 5 fail：3 个 image-first/text-first 断言冲突来源是 PR #2 把生产代码改成 image-first（与标准相反），断言反而保留了 text-first 期望；2 个 async stream cancellation 与 PR #4/#5 同源（Phase 1 解决）
- **核验过的独有项**：master / PR #5 / PR #6 全都 **没有** fallback `tool_call_id`、duplicate tool-call suppression、`request_payload()` provider public method、`recalled_attachment_request_budget`，这些是 PR #2 真独有
- master 的 `_request_payload` 在 `observability/instrumented.py:471` 是内部方法，不是 provider 公开接口
- PR #6 当前 `load_attachment_handle` 通过 `_pending_image_handles` 实现 **one-shot**（投影一次即清），**不满足 turn-scoped 持续语义**；PR #2 的 `_turn_loaded_attachment_handles` 是 turn-scoped 状态，必须迁移

**PR #2 内容归属表**：

| PR #2 变更点 | 归属 | 落实在哪个 Phase |
|---|---|---|
| `_content_parts.py` 模块抽取 | ✅ 已在 master (commit `41d94e6`) | — |
| 附件 lifecycle（`upload` / `placeholder_text` / `prepare_user_message`） | ✅ 已在 master (commit `41d94e6`) | — |
| live streaming（去 buffer 逐 event yield） | ✅ 由 PR #4 提供 | Phase 2.1 |
| `extra_body` 字段（**core 覆盖 extra_body** 方向） | ✅ 由 PR #5 提供 | Phase 2.2 |
| `load_attachment` tool surface / XML recall tool result | ✅ 由 PR #6 提供 | Phase 4 |
| **fallback `tool_call_id`**（流式无 id 时生成本地兜底 id） | ✅ **从 PR #2 摘入需求**（issue #1 第 4 项需求），实现改为 timestamp-based id，不沿用 PR #2 的自增编号 | **Phase 2.3** |
| **duplicate tool-call suppression**（`applied_tool_signatures` / `_duplicate_tool_call_result` / `_tool_call_signature`） | ✅ **从 PR #2 摘入**（防 LLM 同参数重复调用） | **Phase 2.4** |
| **turn-scoped image persistent state**（`_turn_loaded_attachment_handles` + `clear_turn_loaded_attachments` + sync/async `run_turn_stream`、`run_continuation_stream` finally 清理） | ✅ **从 PR #2 摘入语义**，融合进 PR #6 的 `load_attachment` tool surface | **Phase 4 子任务** |
| image-first 顺序（`_project_user_handles`、`project_provider_messages`） | ❌ **错误方向**——与 text-first 多模态 API 标准相反，丢弃 | — |
| 反向 `extra_body` 覆盖（extra_body 覆盖 core fields） | ❌ **错误方向**——core 字段防御更安全，丢弃 | — |
| `recalled_attachment_request_budget` budget 计数机制 | ❌ **被 turn-scoped 替代**——简化为"整个 turn 内持续"，无需 budget 计数 | — |
| `request_payload()` provider public 方法 | ❌ **nice-to-have，丢弃**——master 已有内部 `_request_payload`，需要时另开 PR | — |

**任务**：
1. **不立即关闭 PR #2**，把上面归属表用 `gh pr comment 2` 贴到 PR 评论里，向团队声明状态："内容已拆分迁移，等 Phase 2.3 / 2.4 / Phase 4 完成后关闭"
2. 把"需要从 PR #2 摘入"的三项放入后续 Phase 的 task tracking：
   - **Phase 2.3**：fallback `tool_call_id`（独立分支 `feat/fallback-tool-call-id`，新 PR）
   - **Phase 2.4**：duplicate tool-call suppression（独立分支 `feat/duplicate-tool-call-suppression`，新 PR）
   - **Phase 4 子任务**：turn-scoped image state 融合到 PR #6 重做分支
3. 等 Phase 2 + Phase 4 全部合入 master 后，在 Phase 5 末尾真正 `gh pr close 2`，关闭评论里贴归属表 + 每项对应的 commit / PR 号

**验收**：归属表 PR #2 comment 已发；后续 Phase task tracking 已明确。预计耗时 10 min。

---

## Phase 1 — 前置 async bridge cancel 修复（必须）

**分支**：`fix/async-bridge-cancel-cleanup`（base = master）

**背景**：master 自己跑 `uv run pytest tests/runtime/test_async_agent_api.py -q` 是 **9 passed**。PR #4 / #5 当前 CI 红灯的两个测试 `test_agent_async_stream_cancellation_waits_for_worker_to_finish` 和 `test_agent_async_stream_close_waits_for_worker_to_finish` 是被 PR #4/#5 的 live streaming 改动**暴露出来**的 async bridge cancel cleanup 缺口——master 当前测试覆盖不够触发不了，PR #4/#5 一改 streaming 路径就 trigger。修复在 PR #6 的 `src/agentos/runtime/_async_bridge.py`（`_aclose_from_cancelled_task` 用 `task.uncancel()` 暂时压制 cancel 计数）。必须把这个修复抽到独立小分支前置合入，**否则 Phase 2 永远绿不了**。

**任务**：
1. `git checkout master && git checkout -b fix/async-bridge-cancel-cleanup`
2. `git checkout origin/dev/web-agent-runtime -- src/agentos/runtime/_async_bridge.py` 摘文件
3. 检查 `_async_bridge.py` 是否引入了 master 没有的依赖（应该没有，纯 stdlib `asyncio`），如有补齐
4. 不要摘 PR #6 其他文件——只要 `_async_bridge.py`
5. 跑 `uv run pytest tests/runtime/test_async_agent_api.py -q`：master 自身 9 passed 应保持，本分支也必须 9 passed（这一步只是确认 `_async_bridge.py` 摘过来后没破坏 master 现有绿测试；红灯消失是 Phase 2 PR #4/#5 rebase 后才能观察到的效果）
6. 跑全测 `uv run pytest -q`：必须 0 fail
7. `uv run ruff check src/ tests/` + `uv run python -m compileall -q src tests`：必须干净
8. 新 PR 标题 `fix: cancel-safe aclose for async stream bridge`，PR body 说明：摘自 PR #6 的 `_async_bridge.py`，**解决 PR #4 / #5 streaming 改动会暴露的 async bridge cancel cleanup 缺口**（master 自身 `test_async_agent_api.py` 9 passed，不存在已有红测试），不引入其他变更

**验收 Checkpoint 1（Neo review）**：CI 绿；diff 只动 `_async_bridge.py` 一个文件（可能 +30 / -10 量级）。通过后合 master。

---

## Phase 2 — 补完 PR #4 + PR #5，整合 review

> **前置条件**：Phase 1 已合入 master。

### 2.1 `fix/streaming-cancel-test`（base = `origin/fix-provider-live-streaming`）

新增测试 `tests/runtime/test_streaming_query_loop.py`：

```python
async def test_provider_stream_cancelled_no_delta_no_retry():
    # provider 在零 delta 时 yield ProviderStreamCancelled
    # 断言：转 RuntimeError("provider stream was cancelled")，按 retry policy 处理
    # 若默认 retry policy 不重试 RuntimeError，断言 retry_count == 0

async def test_provider_stream_cancelled_after_delta_no_retry():
    # provider yield ContentDelta 后再 yield ProviderStreamCancelled
    # 断言：emitted_visible_delta=True → 绝不重试
```

**先 rebase** 到 Phase 1 合入后的 master（让 async bridge cancel 修复带进来，红测试自动转绿），再加测试。测试过后 force-push 到 PR #4 头分支。

### 2.2 `fix/extra-body-streaming-coverage`（base = `origin/feat-openai-compatible-extra-body`）

- `OpenAICompatibleProvider._payload`：`dict(self.extra_body or {})` → `copy.deepcopy(self.extra_body) if self.extra_body else {}`，加注释说明防嵌套 mutation
- **`extra_body` 合并方向决策（D3）**：维持 PR #5 现有语义——`payload = dict(extra_body); payload.update(core_fields)`，**core fields（model/messages/tools/thinking）覆盖 extra_body，extra_body 不可覆盖 core**。理由：用户想换 model 应改 provider 配置，不该用 `extra_body` 旁路绕过。不要改成 PR #2 的反向语义
- 新增 `tests/providers/test_openai_compatible_streaming.py::test_streaming_payload_includes_extra_body`：mock transport 抓 streaming 路径 payload，断言含 `vl_high_resolution_images: True` 之类 extra_body 字段
- 同理为 `async_stream()` 加一条
- 保留现有 `test_openai_compatible_provider_core_payload_overrides_extra_body` 测试不动
- 同样**先 rebase** 到 Phase 1 后的 master
- force-push 到 PR #5 头分支

### 2.3 `feat/fallback-tool-call-id`（base = master after Phase 1）

**来源**：从 PR #2 `origin/fix-persistent-recalled-attachments` 摘入需求，但不照搬自增编号实现。issue #1 第 4 项需求是：Qwen 等非标 OpenAI-compatible provider 偶尔流式不返回 `tool_call.id`，当前 master `require_tool_call_id` 直接 raise，整个 tool loop 崩。fallback 用本地生成的 timestamp id 兜底。

**Codex 任务**：
1. `git show origin/fix-persistent-recalled-attachments:src/agentos/providers/openai_compatible.py` 看 PR #2 需求落点（流式 tool_call 构造时 `item["id"] or ...`），但**不要照搬** PR #2 的 `_fallback_tool_call_counter` 自增编号实现
2. 在新分支上给 master 的 `OpenAICompatibleProvider` 增加 timestamp fallback：流式路径 id 缺失时生成 `call_ts_{time.time_ns()}` 形态的本地 id；若极端情况下与本 provider 已生成的 fallback id 重复，重新取 timestamp 或追加短 suffix，保证同一 provider 实例内唯一；**保留** `require_tool_call_id` 用于非流式路径（流式路径仅在 id 缺失时 fallback；非流式仍严格校验，因为非流式 provider 不返回 id 是真 bug）
3. 测试不要断言自增编号。新增 / 调整 `tests/providers/test_openai_compatible_streaming.py`：
   - monkeypatch `time.time_ns()` 或 provider 内部 id factory，让缺 id 的流式 tool_call 生成确定的 `call_ts_1700000000000000000`
   - 断言多个缺 id tool_call 的 fallback id 彼此不同
   - 断言显式 provider id 仍原样保留，不被 timestamp fallback 覆盖
   - 断言非流式路径仍严格校验缺失 id
4. 跑 `uv run pytest -q` + `uv run ruff check src/ tests/` + `uv run python -m compileall -q src tests`
5. 新 PR 标题 `feat: fallback tool_call_id for non-standard OpenAI-compatible streams`，PR body 引用 issue #1 第 4 项和 PR #2 来源

**验收**：流式 tool_call 缺 id 场景的测试通过；非流式路径行为不变（仍严格校验）。

### 2.4 `feat/duplicate-tool-call-suppression`（base = master after 2.3）

**来源**：从 PR #2 `origin/fix-persistent-recalled-attachments` 摘入。Neo 真实业务痛点：LLM 偶尔会用完全相同的 arguments 重复调用同一 tool，没有抑制就会重复执行相同副作用 / 浪费 token。

**Codex 任务**：
1. `git show origin/fix-persistent-recalled-attachments:src/agentos/runtime/query_loop.py` 看 PR #2 实现（关键行：`:322 applied_tool_signatures: set[str]` / `:394 duplicate_result = self._duplicate_tool_call_result(...)` / `:407 applied_tool_signatures.add(self._tool_call_signature(tool_call))` / `:447 def _duplicate_tool_call_result(...)` / `:464 def _tool_call_signature(...)`)
2. 把这套机制加到 master 的 `QueryLoop`（同步路径）。`_tool_call_signature` 通常是 `name + json.dumps(arguments, sort_keys=True)` 之类的稳定哈希——核对 PR #2 实现细节
3. 当前 master 的 `AsyncQueryLoop` 仍是 sync `QueryLoop` wrapper，所以 Phase 2.4 先只改 `QueryLoop`；Phase 3A 原生 async loop 重写时必须复用同一套 `_tool_call_signature` / duplicate suppression 逻辑，避免 async path 行为漂移
4. 新增测试 `tests/runtime/test_query_loop.py::test_duplicate_tool_call_returns_suppression_result`：mock provider 让它连续两次 yield 同 name + 同 args 的 tool_call，断言第二次返回"already executed"提示而非真正执行
5. 跑 `uv run pytest -q` + `uv run ruff check src/ tests/` + `uv run python -m compileall -q src tests`
6. 新 PR 标题 `feat: suppress duplicate tool-call execution within one turn`

**验收**：duplicate suppression 测试通过；正常路径（不同 args）的双 tool_call 仍正常执行；行为只在同 turn 内生效（跨 turn 不受影响——因为 `applied_tool_signatures` 是 turn-scoped 局部变量）。

### 2.5 集成 review

- 创建 `integration/fixes`（base = master after Phase 1），按顺序 merge：PR #4 → PR #5 → Phase 2.3 → Phase 2.4
- 跑 `uv run pytest -q --maxfail=1` + `uv run ruff check src/ tests/` + `uv run python -m compileall -q src tests`
- 输出 diff 摘要：变更文件清单、新增测试名、行数统计

**Checkpoint 2（Neo 人工 review）**：通过后按顺序 fast-forward 到 master，删临时分支。

---

## Phase 3A — Native `AsyncQueryLoop`（必须先做）

**分支**：`refactor/native-async-query-loop`（base = master after Phase 2）

**背景**：当前 `src/agentos/runtime/async_query_loop.py` 只是 `sync_loop.run_turn_stream(...)` + `iterate_sync_in_executor(...)` 的过渡实现。它能避免 ASGI event loop 被同步 provider 阻塞，但 tool dispatch 仍走 `QueryLoop` 的同步 `execute_tool_call()`，无法真正 `await` async skill handler。既然 webagent 端要用 `redis.asyncio` / async HTTP transport，必须先把 async runtime 补成原生 async loop，再做 async `SkillContentSource`。

**设计契约（Neo 已确认）**：

1. **`AsyncQueryLoop` 原生 async**：`run_turn_stream` / `run_continuation_stream` 改为真正 async generator，不再依赖 `sync_loop.run_turn_stream`。
2. **provider 路径**：
   - 优先 `provider.async_stream(request, options)`
   - 其次 `provider.async_complete(request)`，包装成 stream events
   - sync-only provider fallback 到 `asyncio.to_thread(...)`，保持兼容
3. **tool 路径**：tool dispatch 必须走 `await tool_call_router.async_execute_tool_call(tool_call)`；sync handler 在 async router 内用 `await asyncio.to_thread(handler, args)`，async handler 直接 `await handler(args)`。
4. **sync `QueryLoop` 保留**：`Agent.run()` / `Agent.stream()` 继续走同步 loop，不破坏 CLI 和现有同步 SDK 用户。sync loop 遇到 async handler 要 fail-fast，报清晰错误 `RuntimeError("async handler requires AsyncQueryLoop")`，不要用 `asyncio.run()` 伪桥接。
5. **duplicate suppression 要迁移**：Phase 2.4 已把重复 tool-call 抑制加到 sync `QueryLoop`；Phase 3A 原生 async loop 必须复用同样签名逻辑，确保 async path 行为一致。
6. **cancel / finally 语义**：原生 async loop 以 `asyncio.CancelledError` 作为主要中断机制；runtime notices 和后续 Phase 4 的 turn-scoped image state 都必须在 async `finally` 中清理。

**Codex 任务**：
1. 改 `src/agentos/runtime/async_query_loop.py`：删除 `sync_loop: QueryLoop` 作为执行核心，保留必要 shared dependency fields；实现原生 async `run_turn_stream` / `run_continuation_stream`。
2. 如同步 `QueryLoop` 内部 helper 需要复用，提取到 `src/agentos/runtime/_loop_helpers.py`，只放纯 CPU / 无 I/O 的共享函数，避免把 sync generator 逻辑复制一整份。
3. 改 `src/agentos/capabilities/router.py`：确保 `async_execute_tool_call()` 对 context tools / MCP / external tools 都有清晰 async 路径；context tools 若仍同步，直接调用即可；外部 sync handler 用 `asyncio.to_thread`。
4. 改 `src/agentos/capabilities/executor.py` / `tools.py`：为 Phase 3B 的 async handler 做好边界，sync `execute()` 遇到 async handler fail-fast，async execute path 才能 await。
5. 改 `src/agentos/runtime/agent.py`：`Agent.async_stream()` 优先使用 native `AsyncQueryLoop`，不再只把 `Agent.stream()` 包进 executor。同步 `Agent.stream()` 保持不变。
6. 新增 `tests/runtime/test_async_query_loop_native.py`：
   - `test_async_handler_awaited_not_returned_as_coroutine`
   - `test_sync_handler_still_works_in_async_loop`
   - `test_sync_loop_rejects_async_handler`
   - `test_async_provider_stream_is_awaited_without_executor_bridge`
   - `test_async_continuation_stream_uses_async_tool_dispatch`
   - `test_duplicate_tool_call_suppression_matches_sync_loop`
7. 跑 `uv run pytest tests/runtime/test_async_query_loop_native.py -q` + `uv run pytest tests/runtime/test_async_agent_api.py -q`
8. 跑全测 `uv run pytest -q` + `uv run ruff check src/ tests/` + `uv run python -m compileall -q src tests`

**Checkpoint 3A（Neo review）**：native async loop 独立 PR 绿；同步 `QueryLoop` 行为不回归；async tool handler 已能被真正 await。通过后合 master，再进入 Phase 3B。

---

## Phase 3B — `SkillContentSource` 改 async ABC

**分支**：`refactor/skill-async-abc`（base = master after Phase 3A）

### 设计契约（Neo 已确认）

**A. ABC 改 async**

```python
# src/agentos/capabilities/skills.py
class SkillContentSource(ABC):
    @abstractmethod
    async def list_skills(self) -> list[SkillDefinition]: ...

    @abstractmethod
    async def load_skill(self, name: str) -> SkillLoadResult: ...

    @abstractmethod
    async def list_resources(self, name: str) -> tuple[SkillResourceRef, ...]: ...

    @abstractmethod
    async def load_resource(self, name: str, path: str) -> SkillResourceLoadResult: ...

    # 可选 batch：默认并发单调用，Redis 实现重写为 pipeline
    async def load_resources(
        self, name: str, paths: Iterable[str]
    ) -> list[SkillResourceLoadResult]:
        return await asyncio.gather(*(self.load_resource(name, p) for p in paths))
```

**B. `RegisteredTool` 同时支持 sync + async handler**

```python
# src/agentos/capabilities/tools.py
ToolHandler = Callable[[dict[str, object]], str]
AsyncToolHandler = Callable[[dict[str, object]], Awaitable[str]]

@dataclass(frozen=True)
class RegisteredTool:
    name: str
    description: str
    parameters: dict[str, object]
    handler: ToolHandler | AsyncToolHandler
    kind: str = "tool"
```

`ToolExecutor` / `ToolCallRouter.async_execute_tool_call` 用 `asyncio.iscoroutinefunction(handler)` 检测：
- async handler → 直接 `await handler(args)`
- sync handler → `await asyncio.to_thread(handler, args)`（保持向后兼容旧 sync 工具）

**Phase 3B 前置条件**：Phase 3A 已合入，native `AsyncQueryLoop` 已经真实调用 `await tool_call_router.async_execute_tool_call(...)`。不要在 Phase 3B 里用 `asyncio.run()` / nested event loop 桥接 async handler。

**C. `SkillRegistry` 调用链 async 化**

- `load(name)` → `async def load(name)`
- `load_resource(name, path)` → `async def load_resource(name, path)`
- `available_skill_names()` / `capability_declarations()` 保持 sync（无 I/O，纯内存读）
- `__init__` 不再触发 I/O；新增 `async classmethod aload(source, builtin_skills=...)` 工厂方法，启动时调一次 `await source.list_skills()` 缓存元数据

**D. `register_skill_loader_tools` 注册 async handler**

```python
async def load_skill(args):
    name = str(args.get("skill_name", ""))
    try:
        result = await skill_registry.load(name)
        resources = await skill_registry.list_resources(name)
        return result.render_tool_result(resource_manifest=resources)
    except KeyError:
        return json.dumps({...})
```

`register_skill_loader_tool`（单数）改为模块私有 `_register_load_skill_tool`，从 `__init__.py` 移除导出。

**E. Builtin skill 统一路径——新增 `BuiltinSkillSource`**

```python
class BuiltinSkillSource(SkillContentSource):
    def __init__(self, skills: Iterable[SkillDefinition]):
        self._skills = {s.name: s for s in skills}

    async def list_skills(self): return list(self._skills.values())
    async def load_skill(self, name): return SkillLoadResult(...)
    async def list_resources(self, name): return ()
    async def load_resource(self, name, path): raise KeyError(path)
```

`SkillRegistry` 启动时同时接受 `source: SkillContentSource` 和 `builtin_source: BuiltinSkillSource | None`，合并 list_skills 输出。**删除 `_source_skill_names` 特判**——所有 skill 走 source 抽象。

如果需要支持「filesystem + builtin」组合，引入 `ChainedSkillSource(sources: list[SkillContentSource])`：`list_skills` concat，`load_skill` 第一个匹配的 source 返回。

**F. `SkillLoadResult.render_tool_result` 附 manifest（修 L2→L3 桥结构性风险）**

```python
def render_tool_result(
    self, resource_manifest: tuple[SkillResourceRef, ...] = ()
) -> str:
    body = f"# Skill: {self.name}\n\n{self.content}"
    if resource_manifest:
        body += "\n\n## Available resources\n"
        body += "\n".join(
            f"- `{r.path}` ({r.mime_type})" for r in resource_manifest
        )
        body += "\n\nUse `load_skill_resource` to load any of the above."
    return body
```

调用端 `load_skill` handler 同时 await `list_resources()` 一并传入。

**G. `FileSystemSkillSource` 全 async**

所有方法 `async def`，I/O 走 `await asyncio.to_thread(path.read_bytes)`。**注意 `rglob` / `iterdir` 等返回迭代器**——直接 `asyncio.to_thread(path.rglob, ...)` 只把生成器构造放到 thread，真正遍历仍在 event loop 线程发生。正确写法：

```python
files = await asyncio.to_thread(lambda: list(path.rglob("*.md")))
```

把 `list(...)` 包进 lambda，整个遍历在 thread 内执行完才返回。`iterdir` 同理。

**H. 文档新增 Redis 实现示例**

新文件 `docs/skills.md`（如已存在则追加章节 "Implementing a custom SkillContentSource"）：

````markdown
## Redis-backed SkillContentSource (webagent example)

Redis key naming:
- `skill:<name>` → skill metadata + content
- `skill:<name>:resource:<path>` → individual resource bytes

```python
import asyncio
import json
import redis.asyncio as redis
from agentos.capabilities import (
    SkillContentSource, SkillDefinition, SkillLoadResult,
    SkillResourceLoadResult, SkillResourceRef,
)

class RedisSkillSource(SkillContentSource):
    def __init__(self, pool: redis.ConnectionPool):
        self._redis = redis.Redis(connection_pool=pool)

    async def list_skills(self) -> list[SkillDefinition]:
        names = await self._redis.smembers("skills:index")
        async with self._redis.pipeline(transaction=False) as pipe:
            for name in names:
                pipe.hgetall(f"skill:{name.decode()}")
            raw_list = await pipe.execute()
        return [self._decode_skill(raw) for raw in raw_list]

    async def load_skill(self, name: str) -> SkillLoadResult:
        raw = await self._redis.hgetall(f"skill:{name}")
        if not raw:
            raise KeyError(name)
        return SkillLoadResult(
            name=name,
            content=raw[b"content"].decode(),
            content_hash=raw[b"content_hash"].decode(),
        )

    async def list_resources(self, name: str) -> tuple[SkillResourceRef, ...]:
        index = await self._redis.smembers(f"skill:{name}:resources")
        if not index:
            return ()
        async with self._redis.pipeline(transaction=False) as pipe:
            for path in index:
                pipe.hgetall(f"skill:{name}:resource:{path.decode()}")
            raw_list = await pipe.execute()
        return tuple(self._decode_resource(p.decode(), r) for p, r in zip(index, raw_list))

    async def load_resource(self, name: str, path: str) -> SkillResourceLoadResult:
        raw = await self._redis.hgetall(f"skill:{name}:resource:{path}")
        if not raw:
            raise KeyError(path)
        return SkillResourceLoadResult(
            skill_name=name, path=path, content=raw[b"content"].decode(),
        )

    async def load_resources(self, name, paths):
        async with self._redis.pipeline(transaction=False) as pipe:
            for p in paths:
                pipe.hgetall(f"skill:{name}:resource:{p}")
            raw_list = await pipe.execute()
        return [
            SkillResourceLoadResult(skill_name=name, path=p, content=r[b"content"].decode())
            for p, r in zip(paths, raw_list)
        ]
```
````

### Codex 执行清单

1. 改 `src/agentos/capabilities/skills.py`（按契约 A、C、E、F、G 全面改造）
2. 改 `src/agentos/capabilities/tools.py`（契约 B：新增 `AsyncToolHandler`，`RegisteredTool.handler` union 类型）
3. 改 `src/agentos/capabilities/executor.py`：`ToolExecutor` 检测 coroutine function 分发；sync `execute_tool_call` 遇到 async handler 抛 `RuntimeError("async handler requires AsyncQueryLoop")`，不要默默 `asyncio.run` 桥接
4. 改 `src/agentos/capabilities/router.py`：`async_execute_tool_call` 检测 coroutine function 分发
5. 改 `src/agentos/capabilities/__init__.py`：移除单数 `register_skill_loader_tool` 导出，新增 `BuiltinSkillSource` / `ChainedSkillSource` 导出
6. 改 `src/agentos/runtime/`：所有调用 `SkillRegistry.load` / `load_resource` 的地方加 `await`；async 路径用 Phase 3A 后的 native `AsyncQueryLoop`，sync 路径用 `QueryLoop`，handler 类型不匹配 fail-fast
7. 改测试：
   - `tests/capabilities/test_skills*.py` 全部加 `@pytest.mark.asyncio`、调用加 `await`
   - 新增 `tests/capabilities/test_skills_async_abc.py`：mock async source 验证 happy path、L2 返回带 manifest、并发 load 不串扰
   - 新增 `tests/capabilities/test_builtin_skill_source.py`：验证 builtin 走 BuiltinSkillSource，与 filesystem source 行为一致
   - 新增 `tests/capabilities/test_chained_skill_source.py`：验证 builtin + filesystem chained 行为
   - 新增 `tests/capabilities/test_tool_handler_async_dispatch.py`：验证 RegisteredTool 同时支持 sync + async handler，executor 正确分发
8. 新增 `docs/skills.md`（按契约 H 范例）
9. 跑 `uv run pytest -q` + `uv run ruff check src/ tests/` + `uv run python -m compileall -q src tests`
10. 自评：本分支 merge 进 `integration/staging` 跑全测确认无回归

**Checkpoint 3B（Neo 人工 review + webagent 端 drop-in 验证）**：
- Codex 出 review summary
- Neo 在 webagent 项目里用本分支 build SDK，写一个 `RedisSkillSource` 跑通端到端 → 确认能 drop-in
- 通过后 fast-forward `refactor/skill-async-abc` 到 master

---

## Phase 4 — PR #6 follow-up

**分支**：`feat/web-agent-runtime-v2`（base = `master` after Phase 3B，从 `origin/dev/web-agent-runtime` rebase）

**任务**：
1. Rebase 解决冲突：
   - **Phase 3B 重写 skills 路径** → #6 里集成的旧 skill-progressive-disclosure 全部丢弃，使用 master 上 Phase 3B 后的 async 版本
   - **`_async_bridge.py` 已在 Phase 1 单独前置合入 master** → rebase 时接受 master 版本，**不要**再作为 #6 独有改动处理
   - **`_project_user_handles` 和 `project_provider_messages` 顺序** → master 已是 text-first（符合多模态 API 标准），PR #6 同方向，直接接受；若 rebase 中误抓回 image-first 立即纠正
   - 保留 #6 独有的：
     - `attachments/runtime.py`（`load_attachment` 拆分相关的工具入口，**不动顺序**）
     - `capabilities/router.py`（`_format_recalled_context` XML 化、`_execute_load_attachment`）
     - `context_protocol.py`（`load_attachment` tool spec）
     - `recall/runtime.py`（XML 路径，去掉 inject）
2. **D8 — turn-scoped image persistent state 融合**（从 PR #2 摘语义，不沿用 PR #6 的 one-shot）：
   - 当前 PR #6 `AttachmentRuntime` 用 `_pending_image_handles` 实现 one-shot（投影一次即清），**满足不了 turn-scoped 持续语义**——一个 turn 内 LLM 多次 provider request 时只有第一次能看到 image
   - **改为**：参考 PR #2 `_turn_loaded_attachment_handles` 的设计
     ```python
     # AttachmentRuntime 字段
     - _pending_image_handles: list[str]    # 删掉
     + _turn_loaded_attachment_handles: list[str] # 加上,turn-scoped 持续

     # load_attachment_handle
     def load_attachment_handle(self, handle):
         ...
         if attachment.handle not in self._turn_loaded_attachment_handles:
             self._turn_loaded_attachment_handles.append(attachment.handle)
         return attachment

     # project_provider_messages
     def project_provider_messages(self, messages):
         user_handles, user_text = self._consume_user_handles()
         # 注意:不再 _consume,而是直接读 list,turn 内每次 project 都会重新投影
         projected = list(messages)
         if user_handles:
             projected = self._project_user_handles(projected, user_handles, user_text)
         if self._turn_loaded_attachment_handles:
             projected.append(UserMessage(content=(
                 TextPart("Loaded attachment " + ", ".join(self._turn_loaded_attachment_handles) + " for inspection."),
                 *[self._content_part_for_attachment(self.store.get(h)) for h in self._turn_loaded_attachment_handles],
             )))
         return projected

     # turn 边界清理
     def clear_turn_loaded_attachments(self):
         self._turn_loaded_attachment_handles.clear()
     ```
   - **清理点必须覆盖 sync + async 的 turn 边界**：
     1. `QueryLoop.run_turn_stream` 的 `finally` 块（master `query_loop.py:284`）
     2. **`QueryLoop.run_continuation_stream` 的 `finally` 块**（master `query_loop.py:247` 起，continuation turn 也必须清——否则 continuation 跨 turn 时 image 会残留）
     3. **异常 / cancel 路径**：finally 块本身已经覆盖普通异常退出；async cancel 通过 `AsyncQueryLoop`（Phase 3A 原生 async 重写后）的 async finally 同样覆盖。**`AsyncQueryLoop` 的 `run_turn_stream` 和 `run_continuation_stream` 也都要加 `clear_turn_loaded_attachments()`**
   - **不要**引入 PR #2 的 `recalled_attachment_request_budget` / `_turn_recall_remaining_by_handle`——turn-scoped 已经简单清晰，budget 是多余复杂度
3. **清掉死 API**：删 `src/agentos/messages/runtime.py:49` 的 `inject_temporary_recalled` 方法 + 所有引用测试（`tests/messages/test_runtime.py:49,111`、`tests/persistence/test_serializers.py:68`）
4. **新增 D8 测试** `tests/attachments/test_turn_scoped_image_lifecycle.py`：
   ```python
   def test_load_attachment_persists_across_provider_requests_in_same_turn():
       # 一个 turn 内 LLM 多次 provider request,确认 _turn_loaded_attachment_handles 持续投影,不被 consume 清空
   def test_clear_turn_loaded_attachments_resets_state_at_turn_boundary():
       # turn 结束(run_turn_stream finally) → image 不再在下个 turn 的 project 中出现
   def test_next_turn_requires_explicit_load_attachment():
       # 下一个 turn LLM 必须再 call load_attachment 才能让 image 重新出现
   def test_continuation_turn_also_clears_loaded_images():
       # run_continuation_stream 完成后 _turn_loaded_attachment_handles 也被清空
   def test_async_cancel_still_clears_loaded_images():
       # AsyncQueryLoop 中途 cancel 不影响 finally 块的清理
   ```
5. **补嵌套 cancel 测试** `tests/runtime/test_async_bridge_nested_cancel.py`：
   ```python
   async def test_aclose_preserves_pending_cancels_under_nested_taskgroup():
       # 用 asyncio.TaskGroup 触发嵌套 cancel
       # 断言 _aclose_from_cancelled_task 执行完后 task.cancelling() 计数还原
   ```
6. 更新 PR body：显式声明 `BREAKING: application/pdf removed from AttachmentRuntime.DEFAULT_ALLOWED_MIME_TYPES; FilePart direct construction unaffected.` + 说明 D8 行为：`load_attachment` 在 turn 内持续，turn 结束自动清空
7. 跑 `uv run pytest -q` + `uv run ruff check src/ tests/` + `uv run python -m compileall -q src tests`
8. 推到新 PR `feat/web-agent-runtime-v2`，**关闭旧 PR #6**

**Checkpoint 4（Neo review）**：通过后 fast-forward 到 master。

---

## Phase 5 — PR #7 docs sync

**分支**：`docs/web-agent-runtime-sync-v2`（base = `master` after Phase 4）

**任务**：
1. 从 `origin/docs/web-agent-runtime-context-sync` rebase
2. 在 `docs/readme-online.md` 附件段落新增：
   > **MIME types**: `AttachmentRuntime` 仅接受 image/* (gif/jpeg/png/webp)。上传 PDF 或其他类型将抛 `AttachmentError("unsupported attachment MIME type")`。`FilePart` 类仍保留，直接构造 provider message 不受影响。
3. 如果 Phase 3A / 3B 没在 `docs/skills.md` 体现的 SDK 行为变更，在 `docs/sdk-architecture.md` 补段说明 native `AsyncQueryLoop` 和 `SkillContentSource` 全 async
4. 在 `docs/readme-online.md` 或新 `docs/attachments.md` 描述当前 one-shot 语义：
   > **`load_attachment` 生命周期**：LLM call `load_attachment(handle="att:X")` 后，附件只投影到下一次 provider request，构建请求后立即折叠回 placeholder。后续 provider request 或下一个 turn 如需引用同一附件，LLM 必须显式重新 call `load_attachment`。
5. 推到新 PR `docs/web-agent-runtime-sync-v2`，**关闭旧 PR #7**

### Phase 5 末尾任务：关闭 PR #2

所有迁移项（Phase 2.3 fallback id / Phase 2.4 dup suppression / Phase 4 D8 turn-scoped image）合入 master 后：

```
gh pr close 2 --comment "$(cat <<'EOF'
关闭：PR #2 内容已通过其它分支拆分迁移到 master，每项对应：

✅ 采纳（已合入）:
- _content_parts.py / 附件 lifecycle: master commit 41d94e6
- live streaming (去 buffer): PR #4 (Phase 2.1)
- extra_body (core 覆盖方向): PR #5 (Phase 2.2)
- fallback tool_call_id: feat/fallback-tool-call-id (Phase 2.3)
- duplicate tool-call suppression: feat/duplicate-tool-call-suppression (Phase 2.4)
- load_attachment tool surface + XML recall: feat/web-agent-runtime-v2 (Phase 4)
- turn-scoped image persistent state: feat/web-agent-runtime-v2 (Phase 4 D8)

❌ 不采纳（明确丢弃）:
- image-first 顺序: 与 text-first 多模态 API 标准相反
- 反向 extra_body 覆盖: 与 core-fields-protected 原则相反
- recalled_attachment_request_budget budget 计数: 被 turn-scoped 方案替代
- request_payload() provider public 方法: nice-to-have,master 已有内部 _request_payload

详见 docs/plans/skill-async-refactor-plan.md
EOF
)"
```

**Checkpoint 5**：通过后合 master + 关闭 PR #2 + 删除所有临时分支。Phase 完整结束。

---

## 时间预算

| Phase | 预计 | 风险点 |
|---|---|---|
| 0 PR #2 归属确认（不关闭） | 10 min | 低 |
| 1 async bridge cancel 前置修复 | 30 min | 低（已验证过的修复，文件单一） |
| 2.1+2.2 PR #4 / #5 rebase + 补测 | 1 h | 低（前置修了 CI 自动绿） |
| 2.3 fallback tool_call_id 摘入 | 30 min | 低（采用 PR #2 需求落点，但 id 生成改为 timestamp-based） |
| 2.4 duplicate tool-call suppression 摘入 | 1 h | 中（先覆盖 sync `QueryLoop`，Phase 3A 重写 native async loop 时再复用同一逻辑） |
| 2.5 集成 review | 30 min | 低 |
| 3A native AsyncQueryLoop | 3-5 h | **高** — 原生 async provider/tool loop、cancel/finally、Agent.async_stream 路由都要独立测试 |
| 3B async ABC 重做 | 4-6 h | **高** — `SkillContentSource` / executor/router 多文件联动；webagent 端 drop-in 验证可能暴露契约不完整 |
| 4 PR #6 rebase + D8 融合 + 清理 | 1.5 h | 中（D8 要核 sync + async query_loop finally 钩子、continuation/cancel 测试） |
| 5 PR #7 docs + 关闭 PR #2 | 30 min | 低 |

## 合并纪律重申

- **每个 Phase 独立分支**，不混
- **集成到 `integration/staging` 分支跑全测**，不直接合 master
- **每个 Checkpoint Neo 人工 review**，Codex 不擅自 fast-forward
- **任一测试红灯立即停**，不绕过

## Codex 报告模板

每个 Phase 结束时输出：

```
## Phase X 完成报告
**分支**: <branch-name>
**测试**: pass X / fail Y（fail 必须为 0）
**diff 统计**: +N / -M（涉及文件数）
**关键改动**: <bullet list>
**未覆盖的边界**: <if any>
**等待 Checkpoint X review**
```

