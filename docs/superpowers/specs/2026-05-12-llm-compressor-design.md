# LLM Compressor 设计

## Scope

为 compression 模块新增 `LlmCompressor`，复用现有 `Provider.complete()` 做 LLM-based summarization，作为 opt-in 的高质量压缩实现。

本设计不改变 `Compressor` protocol、`CompressionRuntime`、`Evictor` 或 `BudgetPolicy` 的接口。

`RuleBasedCompressor` 继续作为默认实现。`LlmCompressor` 不能隐式启用，因为它会在压缩路径额外触发 provider 调用，增加延迟和成本。用户必须通过 `CompressionRuntime(compressor=...)` 或 `AgentBuilder.with_compression(LlmCompressor(provider))` 显式选择。

## 问题

当前 `RuleBasedCompressor` 用启发式规则：

```python
def compress(self, segment_id, messages):
    topic = self._topic(messages)           # 取第一条 user 消息前 48 字符
    snippets = [self._snippet(m) for m in messages[:4]]  # 每条取 80 字符
    return CompressedSegment(id=segment_id, topic=topic, summary=...)
```

对短对话（<20 turn）够用，但长对话的问题：

- 信息损失随压缩次数指数增长——每次只保留前 4 条的前 80 字符
- 无法提取决策、代码变更、用户偏好等高价值信息
- 压缩质量直接决定 agent 的"长期记忆"质量
- 业界共识 LLM summarization 效果最好（Claude Code compaction 机制）

## 设计

### 新文件

`src/agentos/compression/llm_compressor.py`

### LlmCompressor 类

```python
@dataclass(slots=True)
class LlmCompressor:
    """用 LLM 生成高质量压缩摘要。"""

    provider: Provider
    prompt_template: str = DEFAULT_COMPRESSION_PROMPT
    max_output_tokens: int = 1024
    compression_ratio: float = 0.3

    def compress(self, segment_id: str, messages: Sequence[Message]) -> CompressedSegment:
        """用 LLM 压缩消息序列为 topic + summary。"""

    def compress_package(
        self,
        segment_id: str,
        session_id: str,
        messages: Sequence[Message],
    ) -> CompressedSegmentPackage:
        """生成完整 compression package，包含 recall document。"""
```

### 压缩流程

```
1. 将 messages 序列化为 LLM 可读格式（role: content 逐条）
2. 估算输入 token 数（按 4 字符/token 粗估）
3. 计算 max_output_tokens = min(self.max_output_tokens, input_tokens * self.compression_ratio)
4. 构造 ProviderRequest(system=prompt_template, messages=[UserMessage(序列化消息)])
5. 调用 self.provider.complete(request)
6. 解析 LLM 输出为 topic + summary
7. 生成 CompressedSegment 和 recall document
```

### 默认 Prompt 模板

```python
DEFAULT_COMPRESSION_PROMPT = """你是一个上下文压缩助手。将以下对话片段压缩为简洁摘要。

输出格式（严格遵循）：
TOPIC: 一句话主题（不超过 50 字）
SUMMARY: 摘要正文

摘要必须保留：
- 做出的决策和结论
- 代码变更的文件路径和内容要点
- 用户表达的偏好和约束
- 未解决的问题和待办事项
- 关键的技术细节和架构选择

摘要不需要保留：
- 寒暄和确认性回复
- 已被后续修正的中间方案
- 重复出现的信息（只保留最终版本）"""
```

### LLM 输出解析

```python
def _parse_llm_output(self, text: str) -> tuple[str, str]:
    """解析 TOPIC: ... SUMMARY: ... 格式。"""
    # 用 TOPIC: 和 SUMMARY: 前缀分割
    # 如果格式不匹配，整个输出作为 summary，topic 用前 50 字符
    # 永远不抛异常——fallback 到粗糙但可用的结果
```

### Token Budget 策略

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `max_output_tokens` | 1024 | 单次压缩的最大输出 token |
| `compression_ratio` | 0.3 | 输出 token ≤ 输入 token × ratio |

实际使用较小值：`min(max_output_tokens, estimated_input * compression_ratio)`

### 性能考量

每次压缩消耗一次 Provider API 调用：

- **延迟**：增加 1-3 秒（取决于输入长度和 provider）
- **成本**：输入 token（被压缩的消息）+ 输出 token（摘要）
- **频率**：由 `BudgetPolicy` 和 `Evictor` 控制，不会每轮都触发

压缩发生在 `QueryLoop.build_request()` → `CompressionRuntime.maybe_compress()` 路径上，是 provider 调用前的同步操作。LlmCompressor 的 provider 调用和主 turn 的 provider 调用是同一个 Provider 实例，可以复用连接。

### Fallback 策略

```python
class FallbackCompressor:
    """先尝试 LLM，失败则 fallback 到 RuleBased。"""

    def __init__(self, primary: Compressor, fallback: Compressor): ...

    def compress(self, segment_id, messages):
        try:
            return self.primary.compress(segment_id, messages)
        except Exception:
            return self.fallback.compress(segment_id, messages)
```

### 与 AgentBuilder 集成

```python
agent = (
    AgentBuilder()
    .provider(provider)
    .with_compression(LlmCompressor(provider))
    .build()
)

# 或者使用 FallbackCompressor
agent = (
    AgentBuilder()
    .provider(provider)
    .with_compression(FallbackCompressor(
        primary=LlmCompressor(provider),
        fallback=RuleBasedCompressor(),
    ))
    .build()
)
```

`AgentBuilder.with_compression()` 不应默认创建 `LlmCompressor`。如果调用者不传 compressor，v1 使用 deterministic `RuleBasedCompressor`，避免最简 agent 路径产生隐式 LLM 压缩调用。

### RuleBasedCompressor 保留

不删除、不改变。它在以下场景仍然有用：

- 测试（确定性输出，无 API 调用）
- 离线 / 无 LLM 场景
- FallbackCompressor 的 fallback
- AgentBuilder 未显式传入 compressor 时的默认压缩器

## 测试计划

- LlmCompressor 用 FakeProvider 测试，验证 prompt 构造和输出解析
- 标准格式输出（TOPIC: ... SUMMARY: ...）正确解析
- 非标准格式输出 fallback 到整段作为 summary
- compress_package() 生成完整 package 含 recall document
- FallbackCompressor primary 失败时 fallback 到 secondary
- token budget 计算：输出不超过 max_output_tokens 和 ratio 的较小值
- 空消息列表抛 ValueError（复用 RuleBasedCompressor 行为）

## 验收标准

- LlmCompressor 实现 Compressor + PackageCompressor protocol
- 复用现有 Provider.complete()，不引入新 LLM 依赖
- Prompt 模板可注入替换
- CompressionRuntime 无需改动即可接受 LlmCompressor
- LlmCompressor 只在显式注入时启用，不改变默认压缩行为
- RuleBasedCompressor 不受影响
- 新文件无外部依赖
