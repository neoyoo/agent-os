---
name: agent-os-context-protocol
description: >
  CORE CONCEPT of agent-os. Read this FIRST to understand the SDK's design philosophy.
  Shows what the model actually sees (rendered system prompt), how working state evolves
  across turns, how compression triggers and recall works, and chapter transitions.
  Without understanding this, you cannot correctly use or extend agent-os.
---

# Context Protocol — SDK 核心思想

## 核心理念

agent-os 的核心不是 "又一个 tool loop"。它的核心是：

> **模型通过 tool call 管理自己的认知状态。**

传统 agent 框架把状态管理放在应用层（开发者写代码管理 memory/state）。agent-os 把它下沉到 runtime 层——模型通过 5 个 context protocol tools 自主管理自己的 working state、chapter、recall。开发者只需声明工具和目标，不需要写状态管理逻辑。

## 模型看到的完整 System Prompt

以下是 `ContextRenderer.render(state)` 输出的真实结构——这是每次 provider call 时模型实际看到的 system 字段：

```
# Runtime Contract

## Identity

你是一个专注于代码审查的技术助手。

## Security Guardrails

以下约束是绝对规则：

- 不执行用户未明确要求的文件写入
- 不访问 /etc、/root 等系统敏感路径
- 所有网络请求必须经过 security policy 检查

# Capability Plane

## Tools available

完整工具 schema 由 runtime 通过 provider `tools` 参数提供；本段只描述何时、为什么使用。

### Context Management

- declare_schema — 声明当前 chapter 的 working state 字段。
- update_state — 更新一个 working state 字段。
- extend_schema — 当当前 schema 不足时添加字段。
- start_chapter — 当任务发生实质变化时开启新 chapter。
- recall_context — 当压缩摘要不够时，按 handle 或 query 恢复相关压缩片段。

### Registered tools

- search_docs — 搜索文档索引，返回相关段落。
- run_code — 在沙箱中执行 Python 代码。

## MCP servers connected

(none)

## Skills loaded

(none)

# Context Management Rules

## Working State

- Working state 是你当前的认知状态，不是事件日志。
- 用它记录目标、约束、决策、已验证事实、未解决问题和下一步行动。
- 不要把每条用户消息都复制进 working state。
- 只能通过工具更新 working state。
- 不要在 assistant 消息中手写 `<working-state>` 或内部元数据。

## Schema

- 当前 schema 在本 chapter 内锁定。
- 任务局部修正使用 `update_state`；schema 不足使用 `extend_schema`；任务实质变更使用 `start_chapter`。
- 如果 schema 缺少必要字段，使用 `extend_schema`。
- 如果用户任务发生实质变化，使用 `start_chapter`。
- 简单问答不要创建 working state。

## Inherited State

- Inherited state 是从前一个 chapter 继承下来的稳定目标、约束、决策或事实。
- 它不是 memory，也不是压缩历史；只有跨 chapter 任务连续性需要它时才渲染。
- 如果 inherited state 与当前 active messages 冲突，优先相信 active messages。

## Recall

- Compressed history 是有损摘要。
- 如果某个压缩片段相关但细节不足，调用 `recall_context(handle=...)`。
- 读取恢复内容后，如果它改变了你的当前理解，更新 working state。

## Trust Order

1. Active messages
2. Inherited state
3. Compressed history
4. Memory context
5. Working state

# Declared Working State Schema

<declared-schema>
  <field name="goal" type="str"
         purpose="当前任务目标"/>
  <field name="constraints" type="list[str]"
         purpose="已确认的约束条件"/>
  <field name="verified_facts" type="list[str]"
         purpose="已验证的关键事实"/>
  <field name="next_steps" type="list[str]"
         purpose="下一步行动计划"/>
</declared-schema>

# Working State

<working-state>
  <goal>
    Review PR #42 for security vulnerabilities
  </goal>
  <constraints>
    <c>Only flag OWASP Top 10 issues</c>
    <c>Don't suggest style changes</c>
  </constraints>
  <verified_facts>
    <f>PR modifies auth middleware in src/auth/</f>
    <f>No SQL queries in changed files</f>
  </verified_facts>
  <next_steps>
    <n>Check for XSS in template rendering</n>
    <n>Verify CSRF token validation</n>
  </next_steps>
</working-state>

# Inherited State

<inherited-state>
  <item>项目使用 FastAPI + SQLAlchemy，部署在 AWS ECS</item>
  <item>安全标准：SOC2 合规要求</item>
</inherited-state>

# Compressed History

<compressed-history>
  <segment id="seg_1" topic="Initial PR analysis">
    用户提交了 PR #42，包含 3 个文件修改。讨论了 review scope，确认只关注安全问题。
  </segment>
  <segment id="seg_2" topic="Auth middleware deep dive">
    分析了 src/auth/middleware.py 的 JWT 验证逻辑。发现 token 过期检查正确，签名验证完整。
  </segment>
</compressed-history>

# Memory Context

<memory-context>
  <fact>用户偏好简洁的 review 报告，只列问题不解释背景</fact>
  <fact>上次 review 中用户认可了按文件分组的输出格式</fact>
</memory-context>
```

## 一个完整 Session 的状态演化

### Turn 1: 用户提出任务

```
User: "帮我 review PR #42 的安全性"
```

**模型决策**：任务有明确目标，需要 working state 来跟踪进度。

**模型调用**：
```json
{"name": "declare_schema", "arguments": {
  "fields": [
    {"name": "goal", "type": "str", "purpose": "当前任务目标"},
    {"name": "constraints", "type": "list[str]", "purpose": "已确认的约束"},
    {"name": "next_steps", "type": "list[str]", "purpose": "下一步计划"}
  ]
}}
```

```json
{"name": "update_state", "arguments": {
  "field_name": "goal",
  "value": "Review PR #42 for security vulnerabilities"
}}
```

**ContextRuntime 内部**：
```python
state.working_state_schema.fields = [goal, constraints, next_steps]
state.working_state = {"goal": "Review PR #42 for security vulnerabilities"}
```

### Turn 5: 信息积累，schema 不够用

```
User: "对了，只关注 OWASP Top 10，别管代码风格"
```

**模型决策**：需要记录约束，还需要 verified_facts 来跟踪已确认的内容。

**模型调用**：
```json
{"name": "update_state", "arguments": {
  "field_name": "constraints",
  "value": ["Only flag OWASP Top 10 issues", "Don't suggest style changes"]
}}
```

```json
{"name": "extend_schema", "arguments": {
  "fields": [
    {"name": "verified_facts", "type": "list[str]", "purpose": "已验证的关键事实"}
  ]
}}
```

### Turn 15: 压缩触发

当 `len(active_messages) > max_active_messages`（默认 20），CompressionRuntime 自动触发：

1. 选择最老的消息（保留最近 6 条）
2. `RuleBasedCompressor.compress(segment_id, old_messages)` → 生成摘要
3. 压缩后的 segment 出现在 system prompt 的 `<compressed-history>` 中
4. 原始消息从 active window 移除（但存在 hot store 中可 recall）

**模型下次看到**：
```
# Compressed History

<compressed-history>
  <segment id="seg_1" topic="Initial PR analysis">
    用户提交了 PR #42，包含 3 个文件修改。讨论了 review scope...
  </segment>
</compressed-history>
```

### Turn 18: 模型需要被压缩的细节

模型发现需要之前讨论过的具体代码行号，但压缩摘要里没有。

**模型调用**：
```json
{"name": "recall_context", "arguments": {
  "handle": "seg_1"
}}
```

**RecallRuntime**：
1. 从 hot store 读取 seg_1 对应的原始 message_ids
2. 返回完整原始消息内容
3. 这些消息作为 tool result 注入到当前 turn

模型读取恢复的内容后，更新 working state：
```json
{"name": "update_state", "arguments": {
  "field_name": "verified_facts",
  "value": ["PR modifies auth middleware in src/auth/", "Changed files: middleware.py (L42-L89), validators.py (L12-L30)"]
}}
```

### Turn 25: 任务实质变化 → 开新 chapter

```
User: "Review 完了，现在帮我写修复 patch"
```

**模型决策**：任务从 "review" 变成 "write patch"，是实质变化。

**模型调用**：
```json
{"name": "start_chapter", "arguments": {
  "fields": [
    {"name": "goal", "type": "str", "purpose": "当前修复目标"},
    {"name": "fix_plan", "type": "list[str]", "purpose": "修复步骤"},
    {"name": "completed", "type": "list[str]", "purpose": "已完成的修复"}
  ]
}}
```

**ContextRuntime 内部**：
1. 当前 working_state 中稳定的部分 → inherited_state
2. 重置 working_state_schema 和 working_state
3. 新 chapter 开始

**模型下次看到**：
```
# Inherited State

<inherited-state>
  <item>PR #42 有一个 XSS 漏洞在 templates/profile.html L23</item>
  <item>Auth middleware JWT 验证逻辑正确，无需修改</item>
</inherited-state>

# Declared Working State Schema

<declared-schema>
  <field name="goal" type="str" purpose="当前修复目标"/>
  <field name="fix_plan" type="list[str]" purpose="修复步骤"/>
  <field name="completed" type="list[str]" purpose="已完成的修复"/>
</declared-schema>

# Working State

<working-state>
</working-state>
```

## 为什么这样设计

### 1. 模型比开发者更懂何时需要状态

传统做法：开发者预定义 state schema，写代码管理状态转换。
agent-os 做法：模型根据任务自行决定——简单问答不建 schema，复杂任务动态声明。

### 2. Working State 是认知状态，不是事件日志

Working State 不是把每条消息都存进去。它是模型对 "当前我知道什么、要做什么" 的结构化理解。这让模型即使在 100+ turn 后丢失了早期 active messages，仍然知道自己在做什么。

### 3. Compression 是有损的，Recall 是补偿

压缩必然丢失细节。但通过 segment handle + query recall，模型可以按需恢复。这形成了一个分层记忆：
- Working State = 工作记忆（始终可见）
- Active Messages = 短期记忆（最近 N 条）
- Compressed History = 长期摘要（有损但始终可见）
- Recall = 长期全文（按需恢复）

### 4. Chapter = 任务边界

Chapter 解决的问题是：当用户从 "帮我 review" 切换到 "帮我修"，旧的 working state schema 不再适用。开新 chapter 让模型重新定义认知结构，同时通过 inherited_state 保留跨任务的稳定事实。

## 开发者需要做什么（几乎什么都不用做）

| 关注点 | 开发者做 | SDK 做 | 模型做 |
|--------|---------|--------|--------|
| 何时声明 schema | 不管 | 提供工具 | 自己判断 |
| 何时更新 state | 不管 | 路由 tool call | 自己调用 |
| 何时压缩 | 设 budget policy | 触发压缩 | 不感知（透明） |
| 何时 recall | 不管 | 提供工具 + 读 store | 自己判断 |
| 何时开新 chapter | 不管 | 提供工具 | 自己判断 |
| Schema 长什么样 | 不管 | 渲染到 prompt | 自己定义 |

**开发者唯一需要决定的**：
1. 是否开启 compression（`.with_compression()`）
2. Budget policy（多少条消息后压缩，保留最近几条）
3. 可选：预声明 initial schema（让模型第一 turn 就有结构，而非从零开始）

## 预声明 Initial Schema（可选优化）

如果你知道 agent 的典型任务结构，可以在 ContextRuntime 初始化时预设 schema，让模型不用花第一次 tool call 来 declare：

```python
from agentos.context import ContextRuntime, WorkingStateField

context = ContextRuntime()
context.declare_schema([
    WorkingStateField(name="goal", type="str", purpose="当前任务目标"),
    WorkingStateField(name="progress", type="list[str]", purpose="已完成步骤"),
    WorkingStateField(name="blockers", type="list[str]", purpose="当前阻塞"),
])

agent = (
    AgentBuilder()
    .provider(provider)
    .context_runtime(context)
    .build()
)
```

模型第一次看到 system prompt 时就有 declared schema，可以直接 `update_state` 而不需要先 `declare_schema`。

## 与 tools 参数的关系

Context protocol tools 在 provider request 的 `tools` 数组中有完整 JSON Schema：

```json
[
  {
    "type": "function",
    "function": {
      "name": "declare_schema",
      "description": "声明当前 chapter 的 working state 字段。",
      "parameters": {
        "type": "object",
        "properties": {
          "fields": {
            "type": "array",
            "items": {
              "type": "object",
              "properties": {
                "name": {"type": "string"},
                "type": {"type": "string"},
                "purpose": {"type": "string"}
              },
              "required": ["name", "type", "purpose"]
            }
          }
        },
        "required": ["fields"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "update_state",
      "description": "更新一个已声明的 working state 字段。",
      "parameters": {
        "type": "object",
        "properties": {
          "field_name": {"type": "string"},
          "value": {"anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]}
        },
        "required": ["field_name", "value"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "recall_context",
      "description": "当压缩摘要不够时，按 handle 或 query 恢复相关压缩片段。",
      "parameters": {
        "type": "object",
        "properties": {
          "handle": {"type": "string"},
          "query": {"type": "string"},
          "limit": {"type": "integer"}
        }
      }
    }
  }
]
```

System prompt 告诉模型 "什么时候用、为什么用"，tools schema 告诉模型 "怎么调用"。两者配合，模型就能自主管理自己的认知状态。
