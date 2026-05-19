# Ephemeral Attachment Lifecycle 设计

> Status note: this historical design predated the implemented attachment boundary. Current SDK behavior is image-only: uploaded images are projected as `ImagePart` on entry, re-inspection uses `load_image(handle="att:...")`, and `recall_context` is reserved for compressed/semantic text recall returned as a normal tool result.

## Scope Contract

本设计补齐 agentos 的大文件 / 多模态附件上下文生命周期。

所属阶段：

- Phase 7/8 之后的 production harness 能力补强。
- 直接依赖 Strong-typed ProviderMessage spec。
- 与当前 async provider cancellation 修复并行，但不混入同一个 implementation diff。

本设计完成：

- 定义 session-scoped attachment 的生命周期。
- 定义 `upload()` 作为 v1 public 主入口。
- 定义内部三类 provider 传输 source：URL、inline base64、provider file reference。
- 定义首轮展开、后续占位、按需 tool 召回的 context 防爆炸机制。
- 定义 provider adapter 的映射责任和能力降级策略。

本设计暂不完成：

- 不实现通用视频理解 pipeline；当前 SDK 只面向 OpenAI 和 Anthropic API。
- 不实现跨 session 永久文件库。
- 不实现自动 OCR / 抽帧 / 转写，只预留 tool 降级边界。
- 不把 URL/base64 暴露为 v1 平级 public API。
- 不改 memory runtime 默认持久化策略。

必须遵守的架构规则：

- 大文件正文不得长期进入 active messages、compressed history 或 memory context。
- 默认 LLM-visible context 只能暴露 attachment handle、摘要和查看方式。
- provider 多模态协议差异必须封装在 provider adapter / projector 层。
- ContextRuntime 不直接保存文件 bytes；MessageRuntime 不直接保存大文件正文。

## 背景问题

当前 `ProviderMessage` 仍以文本消息为主：

```python
UserMessage(content: str)
AssistantMessage(content: str, ...)
ToolResultMessage(content: str, ...)
```

这对普通对话足够，但对用户上传图片、PDF、视频、音频或大文件会出现三个问题：

1. 如果把文件内容塞进 message content，会快速撑爆 context window。
2. 如果把文件永久写入 active messages，后续每轮都会重复付 token / media 处理成本。
3. 如果压缩器把文件内容摘要进 compressed history，LLM 可能把有损摘要当成完整证据。

目标行为应该是：

```text
用户上传大文件
  -> 本轮模型可看见完整附件
  -> 模型给出响应后，后续 context 只保留 handle / metadata / preview
  -> 如果模型需要再次查看，调用 recall_context(handle="att:...")
  -> runtime 再把附件一次性注入下一次 provider request
```

这和现有 `recall_context` 的 temporary ref 语义一致：完整内容只在下一次 provider request 中短暂展开，用完即折叠。

## Provider 协议事实

各家多模态 / 文件输入接口不能假设完全一致，但可以归纳出三类 source：

```text
remote_url
inline_base64
provider_file_reference
```

### OpenAI

OpenAI 的 Responses API 对图片和文件有 content part 概念：

- 图片可通过 URL、base64 data URL 或 file ID 输入。
- 文件可通过 uploaded file ID、file URL 或 inline file data 输入。
- Chat Completions 的图片输入更像 legacy 子集，主要是 `image_url` content block。

设计影响：

- agentos 的 OpenAI 多模态主 adapter 应优先面向 Responses-style content parts。
- 现有 Chat Completions adapter 只能支持图片子集，不应承诺 PDF / 视频通用能力。

### Anthropic Claude

Anthropic Messages API 使用 content blocks：

- 图片通过 `type: "image"` block，source 可为 base64、URL 或 file reference。
- PDF 通过 `type: "document"` block，source 可为 URL、base64 或 file reference。
- file API 适合多轮复用，避免每轮重复传 base64。

设计影响：

- Anthropic projector 不能复用 OpenAI 字段名。
- `AttachmentPart` 必须保留 MIME type，才能区分 image block 与 document block。

### 结论

SDK 当前只支持 OpenAI 和 Anthropic API。内部仍应该支持三类 source，但 public v1 不应该把三种 source 都暴露为平级用户 API。

```python
AttachmentSourceKind = Literal[
    "url",
    "inline_base64",
    "provider_file",
]
```

Provider adapter 根据目标模型和文件类型选择具体传输方式：

```text
OpenAI     provider_file -> file_id
Anthropic  provider_file -> source.file_id
```

## Public API v1

v1 只暴露统一上传入口：

```python
attachment = agent.attachments.upload(
    path="diagram.png",
    mime_type="image/png",
)

agent.run(
    "分析这张架构图里的关键组件",
    attachments=[attachment],
)
```

或 bytes 入口：

```python
attachment = agent.attachments.upload_bytes(
    data=image_bytes,
    filename="frame.png",
    mime_type="image/png",
)
```

不在 v1 暴露：

```python
agent.attachments.from_url(...)
agent.attachments.from_base64(...)
agent.attachments.from_provider_file(...)
```

这些可以作为内部 source 或后续 public API 扩展，但第一版用户心智应该是：

```text
把文件交给 SDK，SDK 决定怎么传给 provider。
```

## 核心类型

### Attachment

```python
@dataclass(frozen=True, slots=True)
class Attachment:
    """session 内的附件引用。"""

    handle: str
    filename: str | None
    mime_type: str
    size_bytes: int
    source: AttachmentSource
    lifecycle: AttachmentLifecycle = "ephemeral"
    preview: str | None = None
    summary: str | None = None
```

### AttachmentSource

```python
@dataclass(frozen=True, slots=True)
class LocalFileSource:
    """SDK 可读取的本地文件。"""

    path: Path


@dataclass(frozen=True, slots=True)
class BytesSource:
    """调用方提供的 bytes。"""

    data: bytes


@dataclass(frozen=True, slots=True)
class UrlSource:
    """远程 URL source，v1 内部使用。"""

    url: str


@dataclass(frozen=True, slots=True)
class InlineBase64Source:
    """provider-ready base64 source，v1 内部使用。"""

    data: str
    mime_type: str


@dataclass(frozen=True, slots=True)
class ProviderFileSource:
    """provider 文件 API 上传后的引用。"""

    provider_name: str
    file_id: str
    state: Literal["uploading", "processing", "ready", "failed"] = "ready"
```

当前 OpenAI 和 Anthropic 的 provider file 引用都可以用 `file_id` 表达。未来如果增加其他 provider，不应把非 ID 形态强行塞进这个字段，应扩展 source 类型。

### ProviderContentPart

`ProviderMessage.content` 需要从纯 `str` 扩展为 content parts：

```python
ProviderMessageContent = str | tuple[ProviderContentPart, ...]

@dataclass(frozen=True, slots=True)
class TextPart:
    text: str


@dataclass(frozen=True, slots=True)
class ImagePart:
    attachment: Attachment
    detail: Literal["auto", "low", "high"] = "auto"


@dataclass(frozen=True, slots=True)
class FilePart:
    attachment: Attachment


@dataclass(frozen=True, slots=True)
class AttachmentPlaceholderPart:
    handle: str
    mime_type: str
    size_bytes: int
    preview: str | None
    instruction: str
```

Provider adapters must reject unsupported parts explicitly.

## Runtime Components

### AttachmentRuntime

New module:

```text
src/agentos/attachments/
  types.py
  store.py
  runtime.py
  projector.py
```

Responsibilities:

- Generate stable session-scoped attachment handles.
- Store attachment metadata and local source.
- Track which attachments should be expanded for the next provider request.
- Track provider-specific file references.
- Produce placeholder parts for later turns.
- Enforce size / MIME / lifecycle policy.

It must not:

- Render default prompt text directly.
- Mutate ContextState.
- Persist files across sessions unless explicitly configured.
- Decide tool execution policy.

### AttachmentStore

V1 store can be in-memory plus local temp-file-backed:

```python
class AttachmentStore(Protocol):
    def put(self, attachment: Attachment) -> None: ...
    def get(self, handle: str) -> Attachment: ...
    def list(self) -> list[Attachment]: ...
    def delete(self, handle: str) -> None: ...
```

Production stores can come later:

- filesystem session directory
- S3 / GCS object store
- encrypted local store

### AttachmentProjector

ProviderRequestBuilder should not know every provider protocol. It should attach canonical content parts, then provider adapter maps them.

```text
MessageRuntime
  -> materialize provider messages
  -> AttachmentRuntime rewrites attachment placeholders / expanded parts
  -> ProviderRequest
  -> Provider adapter maps canonical parts to provider payload
```

This keeps provider-specific protocol logic inside providers.

## Context Lifecycle

### First Turn

User uploads a file and sends a message:

```text
User: "请分析这张架构图"
Attachment: att_01 diagram.png image/png 2.4MB
```

The first provider request includes:

```text
UserMessage(
  content=[
    TextPart("请分析这张架构图"),
    ImagePart(attachment=att_01),
  ]
)
```

If the selected provider cannot consume a given attachment type directly, runtime should not send invalid provider payload. It should either:

- fail with a deterministic unsupported error, or
- route to a configured media tool, such as OCR or text extraction.

V1 should prefer deterministic unsupported errors unless a media tool is explicitly registered.

### Later Turns

After the provider response is appended, the active window stores only placeholder content:

```text
Attachment att_01
- filename: diagram.png
- mime_type: image/png
- size: 2.4MB
- status: not loaded in current context
- preview: user uploaded image diagram.png
- To inspect it again, call recall_context(handle="att:att_01").
```

The original file remains in `AttachmentStore`, not in `MessageStore.content`.

### Recall

LLM calls:

```json
{
  "name": "recall_context",
  "arguments": {
    "handle": "att:att_01"
  }
}
```

Router result:

```text
attachment att_01 scheduled for next provider request
```

Then the next provider request receives:

```text
UserMessage(
  content=[
    TextPart("Recalled attachment att_01 for inspection."),
    ImagePart(attachment=att_01),
  ]
)
```

After that request is built, the expanded attachment is consumed and cleared, mirroring `MessageRuntime.materialize_active(consume_temporary=True)`.

## Context Rules

The renderer should add a short rule only when attachments exist or attachment tools are available:

```md
## Attachments

- Uploaded attachments may be visible for only the current turn.
- If an attachment is listed as not loaded and you need to inspect it again, call `recall_context(handle="att:...")`.
- Do not infer unseen attachment details from filename or preview.
- If an attachment summary conflicts with currently loaded attachment content, trust the loaded attachment content.
```

Trust order should become:

```text
1. Active messages and currently loaded attachments
2. Inherited state
3. Compressed history
4. Memory context
5. Working state
6. Attachment placeholders / previews
```

The extra rule must not expose internal file paths, temp directories, provider file IDs, or trace metadata.

## Tool Design

### Attachment recall via recall_context

V1 does not add a sixth default context protocol tool. Attachment recall reuses the existing `recall_context` tool with an `att:` handle namespace:

```python
recall_context(handle="att:att_01") -> str
```

Behavior:

- Validates the handle exists in current session.
- Marks the attachment for one-shot expansion on the next provider request.
- Returns a short tool result.

It does not return base64 or raw bytes in the tool result. Returning bytes through tool output would recreate the context explosion problem.

Routing rule:

```text
handle starts with "att:" -> AttachmentRuntime schedules one-shot expansion
otherwise                 -> existing RecallRuntime handles compressed-history / memory recall
```

This keeps the LLM-visible protocol surface small and preserves the current five default context protocol tool names.

### list_attachments

Not in v1.

Reason: the renderer can list active attachment handles when relevant. A separate listing tool is useful later for long sessions, but it is not needed for the first implementation.

### summarize_attachment

Not in v1.

Reason: summarization is provider- and media-type-dependent. It should be implemented later as a media processing tool or LLM compressor extension.

## Provider Mapping

### OpenAI Responses Adapter

Canonical parts:

```text
TextPart -> input_text
ImagePart -> input_image
FilePart -> input_file
```

Source mapping:

```text
UrlSource / remote_url             -> image_url / file_url
InlineBase64Source                 -> base64 data URL or file_data
ProviderFileSource.file_id         -> file_id
LocalFileSource / BytesSource      -> upload or inline based on policy
```

Chat Completions adapter:

- supports `TextPart`
- supports image `ImagePart` when mappable to `image_url`
- rejects PDF / video / generic file parts unless a later OpenAI Responses adapter is used

### Anthropic Adapter

Canonical parts:

```text
TextPart -> {type: "text"}
ImagePart -> {type: "image", source: ...}
FilePart(pdf) -> {type: "document", source: ...}
```

Unsupported:

- generic video input unless Anthropic adapter explicitly supports it in future.
- unknown binary files.

Do not silently encode unsupported files as text placeholders and call the model; that would make the model hallucinate about unseen content.

## Transfer Policy

V1 default policy:

```text
if provider has Files API and attachment is large or non-image:
    upload to provider file API
elif attachment is small image and provider accepts inline images:
    inline base64
elif attachment has remote URL and provider accepts URL:
    pass URL
else:
    unsupported unless a media tool is configured
```

Initial thresholds:

- Inline image threshold: 4 MB.
- Inline non-image threshold: 0 bytes by default; require provider file path.
- Video always provider-file or media-tool path.

Thresholds should live in an `AttachmentTransferPolicy`, not hardcoded in provider adapters.

```python
@dataclass(frozen=True, slots=True)
class AttachmentTransferPolicy:
    inline_image_max_bytes: int = 4 * 1024 * 1024
    prefer_provider_files: bool = True
    allow_remote_urls: bool = True
    wait_for_provider_file_ready: bool = False
```

If a provider file reference is not ready, SDK behavior is explicit:

- when `wait_for_provider_file_ready=False`, request building raises a retryable not-ready error.
- when `wait_for_provider_file_ready=True`, the provider uploader may wait within its configured timeout.
- SDK core must not perform unbounded implicit polling.

## Persistence And Cleanup

Default lifecycle:

```text
ephemeral session-scoped
```

Meaning:

- available only within the current agent session.
- deleted when session closes or AttachmentRuntime cleanup runs.
- not written to memory store.
- not included in compressed history.

Optional future lifecycle:

```text
persistent memory artifact
```

Requires explicit user / application opt-in because it has privacy and storage-cost implications.

Provider file references should be cached per provider:

```python
attachment.provider_refs["openai"] = ProviderFileSource(...)
attachment.provider_refs["anthropic"] = ProviderFileSource(...)
```

Provider refs must not appear in default prompt.

## Security

Minimum v1 rules:

- MIME type allowlist.
- max file size.
- no implicit network fetch for arbitrary URL in public API.
- local path upload must copy into controlled attachment store or keep a safe read reference.
- provider upload errors must not leak local filesystem paths into LLM-visible messages.
- placeholders must not include signed URLs, provider file IDs, or local temp paths.

Deferred:

- virus scanning.
- content moderation.
- encrypted file store.
- signed URL rotation.

These are deployment-policy concerns and should not block the first SDK boundary.

## Events

Add typed observation events:

```python
AttachmentUploadedEvent
AttachmentExpandedEvent
AttachmentPlaceholderRenderedEvent
AttachmentRecallRequestedEvent
AttachmentRecallScheduledEvent
AttachmentProviderUploadStartedEvent
AttachmentProviderUploadCompletedEvent
AttachmentProviderUploadFailedEvent
AttachmentDeletedEvent
```

Events are observation-only. They must not modify flow. Policy belongs to hooks or AttachmentTransferPolicy.

## Tests

### Unit Tests

- `AttachmentRuntime.upload()` creates handle and metadata without appending raw bytes to MessageStore.
- First provider request expands attachment exactly once.
- Next provider request renders placeholder only.
- `recall_context(handle="att:...")` schedules one-shot expansion.
- Expansion is consumed after request build.
- Unknown handle returns deterministic tool error.
- Placeholder does not contain local path, provider file ID, signed URL, or raw base64.

### Provider Adapter Tests

- OpenAI Responses maps image/file parts to expected payload shapes.
- OpenAI Chat rejects PDF/video with explicit error.
- Anthropic maps image/document parts to content blocks.
- Provider-specific unsupported attachment types raise deterministic errors.

### Context Tests

- Renderer includes attachment rules only when attachment capability exists or active placeholders exist.
- Trust order includes currently loaded attachments above summaries.
- Golden prompt does not include internal metadata.

### Persistence Tests

- Session snapshot includes metadata and handle refs, not raw bytes unless store explicitly supports it.
- Memory extraction ignores raw attachment content by default.

## Acceptance Criteria

- Public v1 API has one main entry: `upload(path|bytes, mime_type?)`.
- Internal source model can represent URL, base64, and provider file reference.
- Large file content is never persisted into `Message.content`.
- Attachment expansion is one-shot and automatically folds back to placeholder.
- LLM has a clear `recall_context(handle="att:...")` path for re-inspection.
- Provider adapters reject unsupported file/media types explicitly.
- No provider-specific file IDs leak into default LLM-visible context.
- Tests cover first-turn expansion, second-turn placeholder, recall, unsupported provider mapping, and metadata privacy.

## Implementation Order

1. Add attachment types and in-memory AttachmentStore.
2. Extend ProviderMessage content type to support content parts.
3. Add AttachmentRuntime one-shot expansion state.
4. Extend `recall_context` routing for the `att:` handle namespace.
5. Add placeholder rendering and prompt rules.
6. Implement provider projector support for existing adapters:
   - OpenAI Chat image-only subset.
   - Anthropic image/PDF subset.
7. Add optional provider file upload protocol only after source model is in place.
8. Add OpenAI Responses-style adapter later if it becomes a first-class provider path in this SDK.

## Open Questions

1. Should `upload(path=...)` copy file bytes into SDK-owned temp storage immediately, or hold a read-only path reference until provider upload?

   Recommendation: copy into SDK-owned session attachment directory for predictable cleanup and path safety.

2. Should first-turn expansion happen automatically for every uploaded attachment?

   Recommendation: yes for attachments passed to `agent.run(..., attachments=[...])`; no for attachments merely uploaded to the store.

3. Should provider file upload happen eagerly at upload time or lazily at first use?

   Recommendation: lazy. Eager upload binds attachment storage to one provider before the runtime knows which provider will handle the request.

4. Should attachment summaries be auto-generated?

   Recommendation: no in v1. The model response or a future summarization tool may create summaries explicitly.

## Review Notes

This spec intentionally separates three layers:

```text
Public user API: upload file
Internal source model: url / base64 / provider_file
Provider protocol mapping: adapter-specific payloads
```

That split keeps the user-facing API simple while avoiding a false lowest-common-denominator abstraction. It also preserves the core context-first rule: the LLM sees loaded attachment content only when intentionally projected into the current provider request; otherwise it sees handles and must call a tool to inspect again.

## References

- OpenAI platform docs: Images and vision, Responses file/image inputs.
  - https://platform.openai.com/docs/guides/images-vision
  - https://platform.openai.com/docs/api-reference/responses
- Anthropic docs: Claude vision, PDF support, and file source content blocks.
  - https://docs.anthropic.com/en/docs/build-with-claude/vision
  - https://docs.anthropic.com/en/docs/build-with-claude/pdf-support
  - https://docs.anthropic.com/en/docs/build-with-claude/files
- ai-knowledge: `wiki/context-management.md`.
- ai-knowledge pattern: `wiki/_patterns/tool-metadata-driven-context-lifecycle.md`.
