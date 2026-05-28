# Tool-Result Token Budget 设计

## Scope Contract

本设计补齐 agentos 的工具结果上下文防爆炸能力，并落地三块生产级硬化中第一块共用的 **TokenCounter 地基**。

采用 **Claude Code 经过线上 A/B 验证的策略**：每个工具结果设 token cap，溢出时返回**极小的 error/nudge**（不是截断内容），由模型用更窄的参数重读。**不做 stash + recall**。

所属阶段：

- Phase 8 之后的 production harness 能力补强。
- 是 [[token-aware-compression-circuit-breaker]] 的前置依赖（共享 TokenCounter）。
- 与 SSE resume 设计完全独立，可并行实现，不混入同一个 diff。

本设计完成：

- 定义 `TokenCounter` 协议与 `HeuristicTokenCounter` 默认实现（item1 / item2 共享地基）。
- 定义工具结果摄入时的 token cap 检查。
- 定义溢出时返回的 **nudge tool-result**：小体积、引导模型缩小范围 / 分页重读。
- 定义 per-tool cap override 与全局默认 cap（含 env 覆盖）。

本设计暂不完成：

- **不做 stash + recall**（`ToolResultOverflowStore` / `recall_tool_result` / snapshot 持久化全部不做）。理由见下方「为什么不 stash + recall」——这是基于 Claude Code 源码 A/B 实证的决策。
- 不做截断后把 head+tail 内容灌进 context（Claude Code 实测此路 mean token 更高，已回退）。
- 不实现 LLM 主动 `free_tool_result`（neoagent v2 主动折叠）。
- 不做多后端精确 token 计数（AgentScope 式 per-provider counter）；TokenCounter 接口为其预留 slot，本期只实现启发式。
- 不替工具实现分页（offset/limit）；分页是工具作者责任，框架只提供 cap + nudge 机制。

必须遵守的架构规则：

- 溢出工具结果的**完整正文不进 context，截断内容也不进 context**——只进一个小 nudge。
- cap 检查是**纯摄入侧操作**，发生在 `append_tool_result` 之前。
- TokenCounter 不得绑定单一 provider；计数逻辑与具体后端解耦。

## 背景问题

当前工具结果在 `query_loop.py:430` 无条件进入消息历史：

```python
tool_result = self.message_runtime.append_tool_result(
    tool_call_id=result.tool_call_id,
    content=result.content,   # ← 可能几十 KB，压缩触发之前就撑爆 provider request
)
```

单个 `fetch_html` / `read_large_file` / `run_python` 的结果就能一次性吃掉上万 token。这是 SDK 评分里 context 维度的核心扣分项。

## 为什么不 stash + recall（关键决策）

初版设计曾考虑 neoagent 式 stash + recall（截断 + 完整内容存 store + `recall_tool_result` 召回）。**查阅 Claude Code 源码后否决，改用 cap + nudge。**

证据（`claude-code-sourcemap/restored-src/src/tools/FileReadTool/limits.ts:1-14`，原注释）：

```
| limit        | default | checks        | on overflow      |
| maxSizeBytes | 256 KB  | 总文件大小     | throws pre-read  |
| maxTokens    | 25000   | 实际输出 token | throws post-read |

Tested truncating instead of throwing for explicit-limit reads that
exceed the byte cap (#21841, Mar 2026). Reverted: tool error rate
dropped but mean tokens rose — the throw path yields a ~100-byte error
tool-result while truncation yields ~25K tokens of content at the cap.
```

**线上 A/B 实测结论**：截断代替抛错 → 工具错误率降，但 mean token 升。因为抛错只产生 ~100 字节 error，截断会把 ~25K token（大部分模型不需要）灌进 context。于是回退，保留抛错。

溢出错误本体（`FileReadTool.ts:175-185`）：

```
File content (N tokens) exceeds maximum allowed tokens (25000).
Use offset and limit parameters to read specific portions of the file,
or search for specific content instead of reading the whole file.
```

这是**全工具系统的一致设计**，不是 Read 特例：

- `GrepTool.ts:80-81`：`head_limit` 默认 250 行，注释明写 "large result sets waste context"，`head_limit=0` 才解除限流。
- Bash 输出靠 `head`/`tail` 管道收口。

**核心哲学**：每个工具 cap 自己的输出 → 溢出给极小 nudge（不是内容）→ 模型用更窄参数（offset/limit/head_limit/search）**重读**。源还在，重读即可，无需 stash。

适用判据：当工具是**幂等、可廉价重读**（读文件、grep、查询）——这是 agent 工具的绝大多数。stash + recall 只在工具**非幂等/昂贵/一次性**时才有价值，那是后续可选增量，不是本期默认。

## 共享地基：TokenCounter

新建 `src/agentos/tokens/` 包。

```python
# src/agentos/tokens/counter.py
from typing import Protocol, Sequence

class TokenCounter(Protocol):
    def count_text(self, text: str) -> int: ...
    def count_messages(
        self,
        messages: Sequence[object],
        tools: Sequence[object] | None = None,
    ) -> int: ...
```

`count_messages` **必须把 tools schema 算进去**——KB 陷阱：50+ 工具 schema 能多吃 20-30K token，漏算导致压缩阈值晚触发、首次 API 调用超限。

默认实现 `HeuristicTokenCounter`：

- OpenAI 系模型（model 名匹配 `gpt`/`o1`/`o3` 等）且 `tiktoken` 已安装 → 用 tiktoken 对应 encoding。
- 否则 → `ceil(len(text) / char_per_token)`，`char_per_token: float = 4.0` 可调，docstring 标注 CJK/代码误差边界（2-5x）。
- tiktoken 为**可选依赖**，import 失败静默回退启发式。

接口可插拔：以后 `AnthropicTokenCounter`（count_tokens API）/ `HuggingFaceTokenCounter`（apply_chat_template）能直接换进来。`messages` 用 `object` 是刻意的：本期要同时服务 runtime 内部的 `agentos.messages.Message` 和 provider-facing message/tool spec，默认实现通过 `to_provider_dict()` / dataclass / dict 结构做 JSON-safe 估算。

> ⚠ 假设：本期 `count_messages` 对 tools schema 的估算用"JSON 序列化后过 `count_text`"的近似，不追求与各 provider 内部 tokenization 完全一致。够触发 cap 判断即可。

## 工具结果 token cap

### 配置

```python
@dataclass(frozen=True)
class ToolResultBudget:
    default_max_tokens: int = 25000          # 仿 Claude Code DEFAULT_MAX_OUTPUT_TOKENS
    # per-tool override：{tool_name: max_tokens}
    overrides: Mapping[str, int] = field(default_factory=dict)
    env_var: str = "AGENTOS_TOOL_RESULT_MAX_TOKENS"   # 优先级：env > override > default
```

cap 解析优先级仿 Claude Code：env var > per-tool override > default。

### 摄入流程（query_loop 集成点）

在 `QueryLoop` 和 `AsyncQueryLoop` 调用 `append_tool_result` 之前插入 cap 检查：

```text
result.content 产出
  -> tokens = token_counter.count_text(content)
  -> cap = budget.cap_for(tool_call.name)
  -> tokens <= cap ?
       是 -> 原样 append_tool_result（无行为变化）
       否 -> 1. 不写完整内容；构造 nudge tool-result（小体积）
             2. append_tool_result 写入 nudge
             3. emit ToolResultCappedEvent（可观测：tool_name / tokens / cap）
```

### nudge tool-result 文本

仿 Claude Code 的 nudge，引导缩小范围。框架不知道具体工具有没有分页参数，所以措辞通用：

```text
[tool result omitted: ~<N> tokens exceeds the <cap> token limit for "<tool_name>".
 Re-run this tool with a narrower request — use pagination/range/filter
 parameters if it has them, or request a more specific subset.]
```

nudge 体积固定在 ~150 字节级别，不随原始结果大小膨胀——这正是 A/B 实测胜出的关键。

> ⚠ 设计决策：nudge **不含任何原始内容片段**。理由：A/B 证据表明"塞 head+tail 切片"会重新引入 mean-token 上升问题；而且若工具幂等，模型重读即可拿到精确片段，切片是浪费。若未来发现某些非幂等工具确实需要保底内容，再做 per-tool 的 `keep_head_tokens` 选项（YAGNI，本期不做）。

### 可观测

`ToolResultCappedEvent`（typed，进 EventBus + observability）：`tool_name` / `actual_tokens` / `cap` / session/turn id。让线上能看到哪些工具频繁溢出——这本身是工具设计需要加分页的信号。

## 不需要的改动（相对初版的删减）

明确记录砍掉了什么，避免实现时复活：

- ❌ `ToolResultOverflowStore`
- ❌ `recall_tool_result` 工具（默认 context protocol 工具集不增加新工具）
- ❌ ContextRenderer 的折叠清单渲染
- ❌ snapshot 持久化 / bump `SNAPSHOT_VERSION`（本设计不动持久化层）

## 测试策略

- TokenCounter：tiktoken 路径 vs 启发式回退；tools schema 纳入计数；tiktoken 缺失静默回退；env/override/default 优先级。
- cap：超 cap 结果被替换为 nudge（断言写入 message 的是 nudge 不是内容、体积小、含 tool_name 和数字）；未超 cap 原样通过；per-tool override 生效。
- 集成：query_loop 跑一个返回超大结果的工具，断言 active window token 受控、message 里是 nudge、emit ToolResultCappedEvent。
- 回归：正常小结果工具行为不变。

## 组件边界小结

| 组件 | 职责 | 依赖 |
|------|------|------|
| `tokens/counter.py` | token 估算（共享地基） | 无（tiktoken 可选） |
| `ToolResultBudget` | cap 配置 + 优先级解析 | 无 |
| `query_loop` 集成点 | 摄入时 cap 检查 + nudge 替换 | TokenCounter, ToolResultBudget |
| `ToolResultCappedEvent` | 溢出可观测 | EventBus |

## 实现交接须知（给实现者）

先读 `AGENTS.md` —— 命名纪律、模块边界、typed event 规则、test-first、完成度 checklist（targeted tests + 全套 + `python -m compileall -q src tests` + `git diff --check`）一律遵守。本节只列 AGENTS.md 覆盖不到的 spec 特有点：

- **`tokens/` 是新顶层包**，AGENTS.md 的模块 taxonomy 里没有它。它归属 `context-management` 概念域（token budgeting 是 context-management 的一部分）。放 `src/agentos/tokens/`，不要塞进 `context/` 或 `compression/`——item2 也要 import 它，独立包避免循环依赖。
- **不新增 context-protocol 工具**。默认 LLM 可见工具维持现状（不加 `recall_tool_result`）。`ToolResultCappedEvent` 按 AGENTS.md 的 typed `*Event` 命名规则，进 observability，不进默认 prompt。
- **不动持久化层**：本 spec 不碰 `persistence/`，不 bump `SNAPSHOT_VERSION`。
- **cap 检查不得违反 query_loop 边界**：query_loop 只做"量 token + 决定写 nudge 还是原文"，token 计数逻辑在 `tokens/`，cap 配置在 `ToolResultBudget`，nudge 文本构造可放一个小 helper——不要把这些塞进 query_loop 本体膨胀它。
- **TDD**：先写 `tests/` 下的 cap/counter 测试再写实现（AGENTS.md test-first）。nudge 用确定性断言（体积、含 tool_name 和数字、不含原始内容）。
- **diff 要 surgical**：只改直接相关的；不顺手改相邻代码/格式；不 refactor 没坏的东西；发现无关 dead code 在报告里提一句，不自作主张删。
