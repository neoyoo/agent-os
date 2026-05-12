# Strong-typed ProviderMessage 设计

## Scope

将 `ProviderMessage = dict[str, object]` 和 `ProviderToolSpec = dict[str, Any]` 替换为 frozen dataclass 联合类型，消除 provider 层最大的类型安全漏洞。

本设计只改 provider 边界的消息类型，不改 MessageRuntime 内部的 Message 类型（那是另一层）。

## 问题

当前定义（`providers/base.py`）：

```python
ProviderMessage = dict[str, object]
ProviderToolSpec = dict[str, Any]
```

实际使用方式：

```python
# QueryLoop 手动构造
{"role": "assistant", "content": content, "tool_calls": [...]}
{"role": "tool", "content": result, "tool_call_id": tc_id}

# AnthropicProvider 手动解析
msg.get("role")
msg.get("content")
msg.get("tool_calls", [])

# MessageRuntime.materialize_provider_messages()
return [{"role": m.role, "content": m.content, ...} for m in messages]
```

问题：

- `msg.get("cotnent")` 这种 typo 只在 API 返回 400 时发现
- IDE 无法提供自动补全和类型检查
- 每个 Provider 实现都在猜 dict 结构，各自做 `.get()` 防御
- 无法区分 "没有 tool_calls" 和 "tool_calls 为空列表"

## 新类型设计

### 新文件：`src/agentos/providers/messages.py`

```python
@dataclass(frozen=True, slots=True)
class UserMessage:
    """用户消息。"""
    content: str

@dataclass(frozen=True, slots=True)
class AssistantMessage:
    """助手回复，可包含 tool calls。"""
    content: str = ""
    tool_calls: tuple[ProviderToolCall, ...] = ()
    thinking_content: str | None = None

@dataclass(frozen=True, slots=True)
class ToolResultMessage:
    """工具执行结果。"""
    tool_call_id: str
    content: str

ProviderMessage = UserMessage | AssistantMessage | ToolResultMessage
```

### ProviderToolSpec 强类型化

```python
@dataclass(frozen=True, slots=True)
class ProviderToolSpec:
    """Provider 工具 schema 声明。"""
    name: str
    description: str
    input_schema: dict[str, object]
```

### ProviderRequest 更新

```python
@dataclass(frozen=True, slots=True)
class ProviderRequest:
    system: str
    messages: list[ProviderMessage]  # 从 list[dict] 变为 union type
    tools: list[ProviderToolSpec]    # 从 list[dict] 变为 dataclass
```

## 影响分析

### 1. ProviderRequestBuilder

`build_messages()` 返回类型从 `list[dict[str, object]]` 变为 `list[ProviderMessage]`。

改动点：
- `_build_user_message()` → 返回 `UserMessage(content=...)`
- `_build_assistant_message()` → 返回 `AssistantMessage(content=..., tool_calls=...)`
- `_build_tool_result()` → 返回 `ToolResultMessage(tool_call_id=..., content=...)`
- `_build_tool_specs()` → 返回 `list[ProviderToolSpec]`

### 2. QueryLoop

追加消息的代码从：

```python
{"role": "assistant", "content": content, "tool_calls": tool_calls}
```

变为：

```python
AssistantMessage(content=content, tool_calls=tuple(tool_calls))
```

### 3. AnthropicProvider

内部转换方法：

```python
def _message_to_api(self, msg: ProviderMessage) -> dict:
    match msg:
        case UserMessage(content=c):
            return {"role": "user", "content": c}
        case AssistantMessage(content=c, tool_calls=tcs):
            result = {"role": "assistant", "content": c}
            if tcs:
                result["tool_calls"] = [self._tc_to_api(tc) for tc in tcs]
            return result
        case ToolResultMessage(tool_call_id=tid, content=c):
            return {"role": "tool", "tool_call_id": tid, "content": c}
```

### 4. OpenAIProvider / OpenAICompatibleProvider

同样需要内部 `_message_to_api()` 转换。每个 provider 只改一处。

### 5. MessageRuntime

`materialize_provider_messages()` 返回类型从 `list[dict]` 变为 `list[ProviderMessage]`。

## 迁移策略

### Phase 1：新老并存

1. 新增 `providers/messages.py` 定义新类型
2. `ProviderMessage` 重定义为 union type
3. 提供迁移工具：
   ```python
   def provider_message_to_dict(msg: ProviderMessage) -> dict[str, object]: ...
   def provider_message_from_dict(d: dict[str, object]) -> ProviderMessage: ...
   ```
4. 老 `dict[str, object]` 类型别名保留但标记 `@deprecated`

### Phase 2：清理

1. 移除 `provider_message_from_dict()` 和兼容层
2. 删除老类型别名

### 不需要的

- 不需要 Pydantic — frozen dataclass 足够
- 不需要 JSON schema 生成 — ProviderToolSpec.input_schema 已经是 dict
- 不需要改 Message（messages/types.py 的内部类型）— 那是不同的抽象层

## 测试计划

- UserMessage / AssistantMessage / ToolResultMessage 构造和字段访问
- ProviderToolSpec 构造
- ProviderRequest 接受新类型消息列表
- `provider_message_to_dict()` 双向转换正确
- AnthropicProvider 接受新类型消息并正确调用 API
- OpenAIProvider 同上
- FakeProvider 接受新类型消息
- QueryLoop 用新类型追加消息后 turn 正常完成
- ProviderRequestBuilder.build() 返回强类型消息

## 验收标准

- `ProviderMessage` 是 union type，IDE 能自动补全字段
- Provider 实现内部处理 dict 转换，不暴露 dict
- 所有现有测试继续通过
- 新消息类型是 frozen + slots
- 从 `agentos` 顶层可导入新消息类型
