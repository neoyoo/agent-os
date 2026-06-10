# Token-Aware Compression + 熔断 设计

## Scope Contract

本设计把 agentos 压缩触发从"按消息条数"升级为"按 token"，并补上压缩失败的熔断降级策略。

所属阶段：

- Phase 8 之后的 production harness 能力补强。
- 依赖 [[tool-result-token-budget]] 提供的 `TokenCounter` 地基（先实现那份 spec）。
- 与 SSE resume 设计独立。

本设计完成：

- 定义 `CompressionBudget` 协议，让 `Evictor` 同时兼容 message-count 与 token 两种预算。
- 定义 `TokenBudgetPolicy`：按 token 触发，含 `reserve_output_tokens` headroom 与 token-budget 尾部保留。
- 定义压缩失败熔断：连续失败计数 + 失败保留原始消息 + 降级到确定性压缩器。
- 明确 tools schema 必须纳入 token 估算。

本设计暂不完成：

- 不实现 RAG / 向量检索（方案 C/D）；本期只硬化已有的"主动压缩"路径。
- 不实现三模式触发全集（token/message/fraction 任选其一）；本期落 token 触发，message-count 作为既有方案共存保留。
- 不做从 API 错误实时解析真实窗口限制（Claude Code 的上下文探测）；窗口大小走配置，预留后续。
- 不实现独立小模型压缩成本分离（`CompressionConfig.agent_token_counter`）；预留接口。

必须遵守的架构规则（来自 KB `wiki/context-management.md` 陷阱清单）：

- **压缩失败绝不静默删除消息**——保留原始消息（Claude Code 策略），反对 Hermes 的冷却期静默删除。
- token 估算**必须包含 tools schema**——50+ 工具能多 20-30K token，漏算导致阈值晚触发、首次 API 调用超限。
- 压缩边界**必须 tool-pair 安全**——复用 `Evictor._expand_for_tool_pairs` 已有逻辑，不另起炉灶。
- 保留 headroom：触发阈值用 `effective_window = window - reserve_output_tokens`，给模型输出留空间。

## 背景问题

当前 `BudgetPolicy`（`policies/budget.py`）纯按消息条数：

```python
def should_compress(self, messages):
    return len(messages) > self.max_active_messages
```

KB 陷阱直接命中：`keep_recent` 用 turn/消息数而非 token 数会让保留量不可预测——工具密集场景 `retain_latest_messages=2` 可能保留上万 token，纯文本场景只保留几百。压缩时机判断与真实 context 占用脱节。

同时 `CompressionRuntime.maybe_compress`（`runtime.py:82`）里压缩器调用**没有任何失败处理**：

```python
package = self._compress_package(source_messages)   # ← 抛异常直接冒泡，无熔断、无降级、无保留
```

当 `compressor` 是 LLM 驱动（`LLMCompressor`）时，网络抖动 / 限流 / 输出格式错误会让这里抛异常并打断整个 turn。KB 明确要求：必须有最大重试次数和失败后降级策略，否则失败重试会消耗更多 token 最终雪崩。

> 好消息：agentos 现有压缩**不物理删除**——被选中消息进 `compressed_history` + `CompressionIndex` + `memory_sink`（可 recall）。所以"不静默删除"在结构上已成立。本设计的熔断重点是**不让失败的压缩器打断 turn**，并在连续失败时降级到确定性压缩器。

## 设计一：token-aware 预算

### CompressionBudget 协议

抽出 `Evictor` 实际依赖的两个方法为协议，让两种预算策略可互换：

```python
class CompressionBudget(Protocol):
    def should_compress(self, messages: Sequence[Message]) -> bool: ...
    def oldest_prefix_size(self, messages: Sequence[Message]) -> int: ...
```

`Evictor` 的字段类型从 `BudgetPolicy` 放宽到 `CompressionBudget`。`Evictor.select_message_ids` 与 `_expand_for_tool_pairs` **完全不动**——tool-pair 安全逻辑原样复用。这是本设计最关键的 surgical 约束。

现有 `BudgetPolicy`（message-count）保留不变，继续满足该协议。

### TokenBudgetPolicy

```python
@dataclass(frozen=True, slots=True)
class TokenBudgetPolicy:
    token_counter: TokenCounter
    context_window: int                      # 模型窗口
    reserve_output_tokens: int = 4096        # headroom：留给输出
    retain_latest_tokens: int = 8000         # 尾部按 token 保留（替代 retain_latest_messages）
    static_overhead_tokens: int = 0          # tools schema 等固定开销

    @property
    def effective_window(self) -> int:
        return self.context_window - self.reserve_output_tokens

    def should_compress(self, messages):
        used = self.token_counter.count_messages(messages) + self.static_overhead_tokens
        return used > self.effective_window

    def oldest_prefix_size(self, messages):
        # 从尾部累计 token，保留 <= retain_latest_tokens 的最新后缀；
        # 其余最旧消息构成 oldest_prefix（返回其长度）。
        # 不满足 should_compress 时返回 0。
```

要点：

- **`static_overhead_tokens` 解决"Evictor 看不到 tools schema"的问题**。`Evictor` / `maybe_compress` 只拿到 `agentos.messages.Message`，拿不到 provider tools。由 builder 在装配时用 `TokenCounter.count_messages([], tools)` 算一次 tools schema 固定开销，注入这里。动态 system prompt 会随 working state / compressed history 变化，本期不把它伪装成固定开销，后续可在 ProviderRequestBuilder 边界做更精确的 request-level 预算。
- **token-budget 尾部**（`retain_latest_tokens`）替代消息条数尾部，解决 KB 的"保留量不可预测"陷阱。
- `oldest_prefix_size` 算出的前缀仍交给 `Evictor` 做 tool-pair 边界扩展，所以即使按 token 选出的边界切到 tool pair 中间，也会被 Evictor 修正。

> ⚠ 假设：`TokenCounter.count_messages(...)` 必须能处理 `agentos.messages.Message` 和 provider-facing message 两类对象；tools 开销通过 `static_overhead_tokens` 单独加，避免 Evictor 路径反复传 tools。

### 装配

`AgentBuilder.with_compression()` 增加可选参数选择预算策略：默认仍可用 message-count（向后兼容），传入 `context_window` 等参数时切到 `TokenBudgetPolicy`。具体 builder API 在实现计划里定。

## 设计二：压缩失败熔断

改造 `CompressionRuntime.maybe_compress`，把 `_compress_package` 包进失败处理。

### 状态

`CompressionRuntime` 增加：

```python
_consecutive_failures: int = 0
max_consecutive_failures: int = 3          # 构造参数，参考 Claude Code MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES
fallback_compressor: Compressor            # 确定性、不会失败（默认 RuleBasedCompressor / FallbackCompressor）
```

### 流程

```text
maybe_compress():
  选出 selected_message_ids（不变）
  active_compressor = fallback_compressor if _consecutive_failures >= max_consecutive_failures
                      else self.compressor
  try:
      package = _compress_package_with(active_compressor, source_messages)
  except Exception as error:
      _consecutive_failures += 1
      emit CompressionFailedEvent(reason=..., consecutive=_consecutive_failures)
      return None        # ★ 不 remove_refs：原始消息保留在 active window，绝不删除
  else:
      _consecutive_failures = 0           # 成功即清零
      ...（原有 append_compressed_segment / index.record / remove_refs 流程不变）
```

关键语义：

- **失败 = 保留**：异常路径直接 `return None`，不执行 `remove_refs`，原始消息留在 window（内容安全，代价是这一轮 context 没压下来）。
- **连续失败降级**：达到阈值后，下一次压缩改用 `fallback_compressor`（确定性，结构上不会抛 API 异常）。降级压缩**仍走完整 append_segment + index + memory_sink 流程**——被压消息进 compressed history 可 recall，不是删除。
- **成功清零**：任意一次压缩成功就把计数器清零，避免偶发失败累积触发降级。
- `_consecutive_failures` 是 runtime 内瞬态，**不进 snapshot**（恢复后重新观察，避免把历史失败状态错误带入新进程）。

### 新事件

`CompressionFailedEvent`（typed，进 EventBus + observability）：`reason` / `consecutive_failures` / `degraded: bool` / session/turn id。补齐可观测，让线上能看到压缩健康度。

## 测试策略

- TokenBudgetPolicy：`should_compress` 在 token 超 `effective_window` 时为真、含 `static_overhead_tokens`；`oldest_prefix_size` 的 token 尾部保留正确；不超预算返回 0。
- 协议兼容：`Evictor` 用 `TokenBudgetPolicy` 跑通，tool-pair 扩展行为与 message-count 一致（同一组 tool-pair fixture 两种 policy 结果边界都 pair-safe）。
- 熔断：注入一个必抛异常的 compressor —— 断言不 remove_refs（消息保留）、emit CompressionFailedEvent、计数递增；连续 3 次后切到 fallback 且压缩成功；成功后计数清零。
- headroom：`effective_window` 扣除 `reserve_output_tokens` 后提前触发。
- 回归：现有 message-count 压缩测试全绿（向后兼容）。

## 组件边界小结

| 组件 | 改动 | 说明 |
|------|------|------|
| `CompressionBudget` 协议 | 新增 | 抽出 Evictor 依赖的两方法 |
| `Evictor` | 字段类型放宽 | 逻辑不动，tool-pair 安全复用 |
| `BudgetPolicy` | 不变 | message-count，向后兼容 |
| `TokenBudgetPolicy` | 新增 | token 触发 + headroom + token 尾部 |
| `CompressionRuntime.maybe_compress` | 包失败处理 | 熔断 + 降级 + 保留 |
| `CompressionFailedEvent` | 新增 typed event | 可观测 |
| `AgentBuilder.with_compression` | 加参数 | 选预算策略 |

## 实现交接须知（给实现者）

先读 `AGENTS.md`，命名/边界/typed event/test-first/完成度 checklist 一律遵守。本节只列 spec 特有点：

- **依赖前置 spec**：`TokenCounter` 来自 [[tool-result-token-budget]]（`src/agentos/tokens/`）。先实现那份，本份直接 import，不要重新发明 counter。
- **`TokenBudgetPolicy` 放 `policies/budget.py`**，与现有 `BudgetPolicy` 并列；`CompressionBudget` 协议也放 `policies/`。两种 policy 都满足协议，`Evictor` 字段类型放宽到协议。
- **`Evictor._expand_for_tool_pairs` 一行都不改**——tool-pair 安全逻辑复用，这是本设计最硬的 surgical 约束（AGENTS.md「Compression 必须保护 tool_use/tool_result pair」）。
- **不 bump `SNAPSHOT_VERSION`**：熔断计数 `_consecutive_failures` 是 runtime 瞬态，**不进 snapshot**；`TokenBudgetPolicy` 是配置不是持久化状态。本 spec 不碰 `persistence/` 的版本。
- **AGENTS.md「失败保留」边界**：异常路径 `return None` 不 `remove_refs`——这同时满足 KB「绝不静默删除」和 AGENTS.md「compressing 不删原始消息」。降级用 `FallbackCompressor` 仍走完整 append_segment + index，不是删除。
- **`CompressionFailedEvent` 按 typed `*Event` 命名**，进 observability，不进默认 prompt（AGENTS.md：默认 prompt 不含 runtime metadata）。
- **TDD**：先写"必抛异常的 fake compressor"测试驱动熔断逻辑（AGENTS.md：provider/compressor 用确定性 fake）。
- **diff 要 surgical**：只改直接相关的；现有 message-count 压缩测试必须全绿（向后兼容）。
