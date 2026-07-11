# AgentOS Message / Provider Boundary Contract Addendum

> 状态：已批准
>
> 日期：2026-07-11
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
| ContextMount | user | context_mount | artifact_runtime | artifact_data | ephemeral | internal |

Phase 2 的 Tool Result 在执行完成后先 append 到 MessageStore，再进入下一次 Provider build；因此“当前尚未消费的 Tool Result”仍使用 `origin="message_store"`。本阶段不存在合法的 `tool_runtime` producer，该值不进入枚举闭集；若未来引入未落库流式 Tool Result，必须先升级本矩阵。

约束：

- `SystemEnvelope` 不属于 `ProviderInputItem`，只进入 `ProviderRequest.system`；
- `tool_result` 必须有 `tool_call_id`；
- `context_snapshot`、`recalled_message` 和 `context_mount` 不得写入 MessageStore；
- `persistence` 描述该 Item 所投影来源的业务持久性，不表示允许持久化 `ProviderInputItem` 对象；任何 `ProviderInputItem` 本身都不得写入 MessageStore；
- `context_snapshot` 的六项元数据必须由 SDK 构造，调用方不能覆盖；
- Provider Adapter 可以改变 wire role 表达，但不能反写逻辑对象。

### 2.1 深不可变边界

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
- 具体 OpenAI/Anthropic payload、严格角色合并和 File ID 优化仍属于 Phase 3B；
- Phase 2 可以保留 Provider Adapter 内部使用的私有兼容构造器，但不得从 `agentos.providers` Public API 导出旧名称；
- 本补充说明批准后，Phase 2 详细计划方可进入实现。

## 7. 验收标准

- 五组 ProviderInput 枚举只有本文列出的值；
- `ArtifactRef` 只有一个定义且字段名为 `media_type`；
- Read Model 事件必须通过显式 projector；
- retry attempt 每次重建请求；
- temporary recalled refs 只在成功 attempt 后消费；
- 没有 Adapter-specific 字段进入 StoredMessage、ProviderInputItem 或 ArtifactRef。
