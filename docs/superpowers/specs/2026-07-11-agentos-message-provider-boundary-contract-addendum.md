# AgentOS Message / Provider Boundary Contract Addendum

> 状态：已批准（2026-07-15 Task 13 re-baseline 修订）
>
> 日期：2026-07-11，修订于 2026-07-15
>
> 上位规范：`2026-07-10-agentos-next-generation-sdk-architecture-design.md`、`2026-07-10-agentos-context-protocol-v1-design.md`

## 1. 目的

本文只补齐 Phase 2 开始前缺失的类型枚举和所有权，不改变已批准的双平面架构：

```text
system   = SystemEnvelope
messages = ContextSnapshot + Active Messages + Tool Results + ContextMounts
tools    = Provider Tool Schemas
```

## 2. ProviderInputItem 枚举闭集

Phase 2 的 Provider 无关逻辑输入固定使用以下闭集：

```python
ProviderRole = Literal["user", "assistant", "tool"]

ProviderInputKind = Literal[
    "context_snapshot",
    "business_message",
    "tool_result",
    "recalled_message",
    "model_task",
    "context_mount",
]

InputOrigin = Literal[
    "runtime",
    "message_store",
    "recall_runtime",
    "artifact_runtime",
]

InputAuthority = Literal[
    "context_data",
    "conversation_data",
    "tool_data",
    "artifact_data",
]

PersistencePolicy = Literal["stored", "ephemeral"]
VisibilityPolicy = Literal["conversation", "internal"]
```

映射矩阵：

| Input | role | kind | origin | authority | persistence | visibility |
|---|---|---|---|---|---|---|
| ContextSnapshot | user | context_snapshot | runtime | context_data | ephemeral | internal |
| Stored user/assistant message | 原业务 role | business_message | message_store | conversation_data | stored | conversation |
| Stored tool result | tool | tool_result | message_store | tool_data | stored | internal |
| Temporary recalled user/assistant message | 原业务 role | recalled_message | recall_runtime | conversation_data | ephemeral | internal |
| Temporary recalled tool result | tool | recalled_message | recall_runtime | tool_data | ephemeral | internal |
| Runtime internal model task data | user | model_task | runtime | context_data | ephemeral | internal |
| ContextMount | user | context_mount | artifact_runtime | artifact_data | ephemeral | internal |

Phase 2 的 Tool Result 在执行完成后先 append 到 MessageStore，再进入下一次 Provider build；因此“当前尚未消费的 Tool Result”仍使用 `origin="message_store"`。本阶段不存在合法的 `tool_runtime` producer，该值不进入枚举闭集；若未来引入未落库流式 Tool Result，必须先升级本矩阵。

约束：

- `SystemEnvelope` 不属于 `ProviderInputItem`，只进入 `ProviderRequest.system`；
- `tool_result` 必须有 `tool_call_id`；
- `context_snapshot`、`recalled_message`、`model_task` 和 `context_mount` 不得写入 MessageStore；
- `persistence` 描述该 Item 所投影来源的业务持久性，不表示允许持久化 `ProviderInputItem` 对象；任何 `ProviderInputItem` 本身都不得写入 MessageStore；
- `context_snapshot` 的六项元数据必须由 SDK 构造，调用方不能覆盖；
- `model_task` 只允许由 `ProviderInputItem.model_task(text)` 创建，固定使用文本输入，不允许 `tool_calls` 或 `tool_call_id`；`ProviderInputItem` 的公开字段构造入口即使传入完整合法矩阵，也必须拒绝直接创建 `model_task`，工厂使用不导出的内部构造凭证完成创建；
- `ProviderRequestBuilder` 和 Turn message projector 不得生成 `model_task`。它只服务 Compression、Memory extraction、Planner policy、Evaluation 等 Runtime 内部模型任务；
- Provider Adapter 可以改变 wire role 表达，但不能反写逻辑对象。

### 2.1 ProviderRequest 请求平面

`ProviderRequest` 有两个互斥的逻辑请求平面：

- Agent Turn 请求由 `ProviderRequestBuilder` 创建，可以包含 ContextSnapshot、business message、tool result、recalled message 和 context mount，但不能包含 `model_task`；
- Runtime internal model task 请求由具体 Runtime 直接创建，`messages` 必须且只能包含一个 `model_task`，`tools=()` 且 `parallel_tool_calls=None`。`ProviderRequest.system` 仍然必须来自 `SystemEnvelope.text`：由该 Runtime 使用 SDK 固定模板或应用开发者显式提供的 trusted task template 构建；用户、Tool、Memory、Recall、Artifact 或待压缩正文不得插入该模板，只有经过类型化格式化的有界控制参数（例如输出 token 上限）可以进入。`model_task` 正文始终按不可信数据处理；
- `ProviderRequest.__post_init__` 必须拒绝混合两个平面、多个 `model_task`、为 model task 携带 Tool Schema 或启用 `parallel_tool_calls`；
- internal model task 复用唯一 Provider Protocol、Adapter、错误和传输边界，但不进入 QueryLoop 的 ProviderAttemptRunner、Hook、temporary receipt 或 retry 生命周期。Phase 2 不为它增加第二套 Provider/Adapter/Runner，也不新增隐藏 retry。

上述 internal model task 是对“Agent Turn 的 SystemEnvelope 由 ContextRenderer 唯一组装”规则的受限例外，不是任意 Prompt 追加入口。ContextRenderer 继续是 Agent Turn 唯一 Owner；internal task Runtime 只拥有自己的固定 task instruction 和 data item，不能读取或拼接 Agent Turn 的 Working State、Plan、Memory、Skill、Artifact Catalog 或 Active Window。

当前只有 `LlmCompressor` 是真实 producer。未来只有出现至少两个需要不同结构化输出、批处理、模型路由、取消或 retry 语义的内部任务后，才允许通过新 Spec 提取 `ModelTaskRunner`；即使提取，也优先构建统一 `ProviderRequest`，不要求 Adapter 实现第二套模型调用协议。

### 2.2 深不可变边界

`StoredMessage`、`ToolCall`、`ProviderInputItem`、`ProviderToolCall`、`ProviderToolSpec` 和 `ProviderRequest` 暴露的所有集合与 JSON-like 字段必须递归不可变，不只是 frozen dataclass 外壳或防御性浅复制。

- sequence 统一冻结为 tuple；
- JSON object 统一冻结为 SDK 自有只读 Mapping 值类型；
- key 只允许 string，value 只允许 JSON-compatible 闭集；
- NaN、Infinity、任意 Python object 和可变别名直接拒绝；
- 只有 serializer/Provider Adapter 可在输出边界 thaw 为新的 dict/list，不得返回内部引用。

验收必须直接尝试修改嵌套 Tool arguments 和 tool schema parameters，并确定性失败。

## 3. ArtifactRef 所有权与命名

`StoredMessage` 在 Phase 2 就需要稳定的附件轻量引用，因此 `ArtifactRef` 值类型由 Architecture Owner 在 Phase 2 提前冻结到 `agentos.artifacts.types`。Phase 3A 继续在同一模块增加 `ArtifactRecord`、`ContextMount` 和 Store/Runtime，不重新定义 `ArtifactRef`。

```python
@dataclass(frozen=True, slots=True)
class ArtifactRef:
    artifact_id: str
    filename: str | None
    media_type: str
```

统一使用 `media_type`。Provider SDK 或上传入口中的 `mime_type` 只允许在 Adapter 边界转换，不能进入新领域类型。`ArtifactRef` 不包含 Bytes、Base64、本地路径、Signed URL、Provider File ID、size 或生命周期策略。

Phase 2 只创建该值类型和 package export，不实现 Artifact Store、Session Scope、Catalog、Mount 或附件 Tool。

## 4. Conversation Read Model 事件入口

Phase 2 不给所有领域 Event 增加通用 `user_visible` 字段，也不允许前端直接遍历 EventBus。

前端事件通过显式 projector 注册进入 Read Model：

```python
class UserVisibleConversationEvent:
    """只有显式用户可见领域事件才能继承的 marker base。"""


@dataclass(frozen=True, slots=True)
class ConversationEventItem:
    event_id: str
    kind: str
    content: str


class ConversationEventProjector(Protocol):
    event_type: type[UserVisibleConversationEvent]

    def project(
        self,
        event: UserVisibleConversationEvent,
    ) -> ConversationEventItem | None: ...
```

规则：

- 未继承 `UserVisibleConversationEvent` 或未注册 projector 的事件默认不可见；
- projector 返回 `None` 表示该事件不进入 Conversation；
- `ProviderRequestBuiltEvent`、ContextSnapshot、TraceEvent、Tool Result 和内部 Runtime 状态默认不可见；
- Read Model 不读取 ProviderRequest/ProviderResponse Transcript；
- 后续领域模块若要展示事件，必须显式提供 projector 和用户可见内容测试。

## 5. Retry 与临时召回消费边界

一次物理 Provider attempt 也属于一次 Provider 调用。每次 retry attempt 都必须重新从权威状态构建新的不可变 `ProviderRequest`，并重新执行 `before_provider_call` Hook。

Request Factory 返回内部 `ProviderRequestReceipt`，记录该次请求实际投影的 temporary message IDs。Temporary recalled refs 在构建请求时不消费。只有某个 Provider attempt 完成 `after_provider_call` Hook 且响应通过 usability validation 后，协调层才按 receipt 中的 ID 精确清除本次已投影 refs。失败、Timeout、Hook deny、无效/截断响应或 Retry 不得导致下一次 attempt 丢失 recalled data。

如果 `before_provider_call` Hook 用新对象替换完整 `ProviderRequest`，Runtime 无法证明原 temporary refs 仍被发送，本 attempt 的 receipt 必须清空；成功后也不消费这些 refs。Hook 只返回原请求对象时，receipt 保持有效。

Streaming attempt 一旦已经向 QueryLoop 调用方发出第一个用户可见 delta，就不得 retry，避免把第二次 attempt 的内容与已展示内容拼接。可见 delta 标记必须在 yield/forward 前设置；在此之前发生且 RetryPolicy 允许的错误，才可以从权威状态重建下一 attempt。同步与异步 fallback 顺序可以适配 Provider 能力，但必须遵守相同的 build -> before hook -> provider -> after hook -> usability validation -> receipt consumption 时序。

## 6. 兼容与阶段边界

- Phase 2 删除 Public `Message` 和 `ProviderMessage` 名称，不维护双写；
- Task 13 同时删除 `UserMessage`、`AssistantMessage`、`ToolResultMessage`、`ProviderMessageContent` 和 `provider_message_*` 迁移 DTO/serializer；`ProviderRequest.messages` 只接受 `ProviderInputItem`；
- Provider Adapter 必须直接把 `ProviderInputItem` 映射为 wire payload，不得先构造第二套 message DTO；
- 现有附件行为只通过 `AttachmentRuntime._project_provider_inputs_compat()` 保留到 Phase 3A；旧 `project_provider_messages()` 和 ProviderMessage 投影路径在 Task 13 删除；
- 具体 OpenAI/Anthropic payload、严格角色合并和 File ID 优化仍属于 Phase 3B；
- 本补充说明批准后，Phase 2 详细计划方可进入实现。

## 7. 验收标准

- 六组 ProviderInput kind 只有本文列出的值；
- Agent Turn 与 internal model task 两种 ProviderRequest 平面互斥，非法混合在类型边界被拒绝；
- `model_task` 不进入 MessageStore、ContextSnapshot、Conversation Read Model、temporary receipt 或 QueryLoop 的 active message projection；
- `ProviderRequest.messages` 只接受 `ProviderInputItem`，旧 Provider message DTO 和 serializer 在源码与 Public API 中均不存在；
- `ArtifactRef` 只有一个定义且字段名为 `media_type`；
- Read Model 事件必须通过显式 projector；
- retry attempt 每次重建请求；
- temporary recalled refs 只在成功 attempt 后消费；
- 没有 Adapter-specific 字段进入 StoredMessage、ProviderInputItem 或 ArtifactRef。
