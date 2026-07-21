# AgentOS Phase 6 Wave 4 Transport Contract Addendum

> 状态：已确认，进入实现
>
> 日期：2026-07-21
>
> 上位合同：`2026-07-17-agentos-phase6-distributed-runtime-transport-contract.md`

## 1. 目的

本增补冻结 6B 的 HTTP、SSE、Artifact、A2A 和 Channel 公共边界。它解决主合同尚未
明确的 wire 字段、大小门禁、scoped cursor、鉴权到 `RequestScope`、A2A task binding
和 push application boundary。

本增补不改变 Run、Command、Checkpoint、Artifact 或 Event 的领域真值。出现冲突时，
本增补只在 Wave 4 Transport/Channel 范围内精化主合同。

## 2. 不可变边界

依赖方向固定为：

```text
ASGI Channel
  -> Transport decode/encode
  -> Application Service
  -> Port
  -> Adapter
```

`agentos.transports` 只拥有 immutable wire type、严格 decode、纯 mapping 和确定性 encode。
它不得导入或构造 Store、SessionProvider、Agent、QueryLoop、Worker、Daemon、具体 Profile、
ASGI 对象、数据库/Redis client 或 HTTP client。

Channel 拥有连接、路由、鉴权、限流、request ID、heartbeat、disconnect 和资源关闭。
Channel 不拥有 Run/A2A task/push 真值，不执行 Agent/Tool，也不解释 Adapter exception 文本。

## 3. 通用限制

默认限制冻结为：

| 项目 | 默认值 |
|---|---:|
| JSON request body | 256 KiB |
| A2A JSON request body | 1 MiB |
| Artifact file | 25 MiB |
| Artifact multipart request | 26 MiB |
| A2A inline file bytes（解码后） | 512 KiB |
| command payload canonical JSON | 64 KiB |
| A2A metadata canonical JSON | 16 KiB |
| identifier | 255 Unicode characters（网络 byte-size 规则的唯一例外） |
| 普通 header value | 255 UTF-8 bytes |
| Artifact list page | default 20, max 100 |
| SSE cursor token | 1024 ASCII bytes |

配置可以收紧限制，不能超过上述 SDK hard limit。除 identifier 外，长度使用 UTF-8 bytes 或实际
binary bytes，不使用 Python character count 代替网络大小。identifier 单独按 Unicode code point
计数，固定为 1..255 个字符，并继续拒绝空白和控制字符。`Last-Event-ID` 是普通 header 限制的
唯一例外，只应用 1024 ASCII bytes 的 SSE cursor hard limit。

所有 JSON decoder 必须拒绝 malformed UTF-8、重复 object key、非 object 根、非有限 number、
`bool` 冒充 integer 和超限嵌套。HTTP/Artifact 与 JSON-RPC envelope 拒绝未知字段；A2A
ProtoJSON message 按官方前向兼容规则忽略未知字段，但仍严格校验已知字段。Wire decoder 不做
宽松 `str()`/`bool()` 转换，也不静默跳过非法 list item。

所有 JSON response、SSE data 和 A2A JSON 使用 UTF-8、无 BOM、`ensure_ascii=False`、
`allow_nan=False`、`sort_keys=True`、`separators=(",", ":")`。对象 key 因此按 Unicode code
point 升序输出；禁止依赖 dataclass 字段声明顺序。所有 datetime 先转 UTC，再固定编码为
`YYYY-MM-DDTHH:MM:SS.ffffffZ`，包括零 microsecond，不输出 `+00:00`。JSON decoder 最大嵌套
深度为 32；深度从根 object 计为 1。

A2A JSON-RPC 2.0 over HTTP(S) binding 的非流式 request 与 success/error response media type 固定为
`application/json`，不接受或输出 media type parameter；其他 media type 返回 415。SSE response
固定使用 `text/event-stream`，其每个 `data` payload 仍是 ProtoJSON。Outbound Push 不是 JSON-RPC
envelope，按官方 webhook contract 固定使用 `application/a2a+json`。不得把 HTTP+JSON/REST binding
的 `application/a2a+json` 套到 JSON-RPC request/response。

A2A 的 512 KiB 是单个请求中全部 inline raw part 的**解码后累计值**。1 MiB 是收到的完整
JSON body bytes 上限；512 KiB 的 raw bytes 即使经过 canonical Base64 膨胀仍小于 1 MiB，
剩余空间用于 envelope、message 和 metadata。两个门禁都必须满足，不能用其中一个替代另一个。
ASGI Adapter 必须在读取阶段按 route 应用 hard limit：普通 JSON 256 KiB、A2A JSON 1 MiB、
Artifact multipart 26 MiB；不得先按 multipart 上限缓冲普通 JSON，再交给 decoder 拒绝。

## 4. HTTP Request Contract

### 4.1 Header

Channel 以保留重复项的 immutable header collection 交给 Transport。Header name 按 ASCII
case-insensitive 规则归一化；`Authorization`、`A2A-Version`、`A2A-Extensions`、
`Idempotency-Key`、`Last-Event-ID`、`Content-Type`、`Content-Length` 和 `X-Request-ID`
出现多次时拒绝，不得 last-wins，也不得通过大小写变化绕过重复检测。

Header value 的 hard limit 固定为：

| header | 编码与上限 |
|---|---:|
| `Authorization` | 8192 ASCII bytes |
| `A2A-Extensions` | 4096 ASCII bytes |
| `Last-Event-ID` | 1024 ASCII bytes |
| `A2A-Version` | 32 ASCII bytes |
| `Content-Length` | 20 ASCII digits |
| `Idempotency-Key` / `Content-Type` | 255 UTF-8 bytes |
| `X-Request-ID` 候选值 | 255 ASCII bytes |
| 其他 Transport 读取的 header | 255 UTF-8 bytes |

`Authorization` 只交给 `ChannelAuthenticator`，Transport DTO 和错误对象不得保存它。

`Idempotency-Key` 在 submit/command/upload/delete 上必填，经过 identifier 校验后分别映射为
`submission_id`、`command_id`、`upload_id` 和 `deletion_id`。Body 不允许覆盖 path/header ID。

### 4.2 Submit Run

```http
POST /v1/sessions/{session_id}/runs
Idempotency-Key: req_123
Content-Type: application/json
```

```json
{
  "content": "完成这个任务",
  "artifact_handles": ["art_..."]
}
```

只允许 `content` 和 `artifact_handles`。Decoder 直接构造 canonical `RunSubmission`。

成功响应为 `202 application/json`：

```json
{
  "session_id": "session_1",
  "run_id": "run_1",
  "submission_id": "req_123",
  "aggregate_version": 1,
  "duplicate": false
}
```

### 4.3 Submit Command

```http
POST /v1/sessions/{session_id}/runs/{run_id}/commands
Idempotency-Key: cmd_123
Content-Type: application/json
```

```json
{"kind":"cancel","payload":{}}
```

只允许 `kind` 和 `payload`。`run_id` 与 `command_id` 只来自 path/header。Decoder 直接构造
canonical `DurableRunCommand`。成功响应为 `202`，字段固定为 `run_id`、`command_id`、
`kind`、`aggregate_version` 和 `duplicate`。

### 4.4 Query Run

`GET /v1/sessions/{session_id}/runs/{run_id}` 成功返回 `200`：

```json
{
  "session_id": "session_1",
  "run_id": "run_1",
  "status": "waiting",
  "aggregate_version": 4,
  "wait_reason": {
    "kind": "human_input",
    "handle": "approval_1",
    "not_before": null
  },
  "result": null
}
```

响应不输出 `tenant_id` 和 `WaitReason.detail`。只有 COMPLETED 输出
`result={"content":...}`；其他状态的 result 为 null。非 WAITING 的 wait_reason 为 null。

## 5. Artifact HTTP Contract

Upload 必须是有界 `multipart/form-data`，恰好一个 `name="file"` part，禁止额外 part。
Multipart parser 必须按 MIME 结构解析，不能使用裸 boundary split。Part 的 filename、
Content-Type 和 bytes 分别进入 `ArtifactService.upload()`；`upload_id` 来自 Idempotency-Key。

拒绝缺失/重复 file、非法 boundary、header injection、路径型 filename、缺失/非法 Content-Type、
request/file 超限。Transport 执行 wire size gate，`ArtifactService` 使用注入的
`ArtifactPolicy` 再执行 canonical media allowlist 和 file size policy。

Artifact 成功响应：

- upload：`201` + `ArtifactRecord` JSON；
- list：`200` + `{"items":[...],"next_cursor":...}`；
- read：`200` typed bytes；
- delete：`204` empty body。

Artifact JSON 只输出 `id/session_id/filename/media_type/size_bytes/created_at`。Read 只输出
受控 `Content-Type`、`Content-Length`、
`X-Content-Type-Options: nosniff` 和 `Cache-Control: private, no-store`。不得输出 blob key、
signed credential、Provider file ID 或本地路径。

所有 JSON success/error response 使用 `Content-Type: application/json; charset=utf-8`。Wave 4
Artifact read 不输出 `Content-Disposition`；filename 只存在 metadata JSON 中。Multipart file part
的 filename 可省略；存在时必须通过 canonical filename 校验。

Canonical Artifact domain 增加固定错误 `ArtifactTooLargeError` 和
`ArtifactMediaTypeUnsupportedError`，Transport 分别映射为 413/415，不依赖异常文本匹配。

## 6. HTTP Error Contract

Channel 生成非空 opaque `request_id`；入站 request-id 只允许作为可观测关联候选，未经严格
校验不能成为响应 ID。所有错误响应固定：

```json
{"code":"run_not_found","message":"run not found","request_id":"req_..."}
```

映射固定为：

| 异常类型或 Transport category | HTTP | code | message |
|---|---:|---|---|
| `HttpParseError` | 400 | `invalid_json` | `invalid JSON request` |
| `HttpValidationError` | 400 | `invalid_request` | `invalid request` |
| `ArtifactValidationError` | 400 | `invalid_artifact` | `invalid artifact request` |
| `AuthenticationRequiredError` | 401 | `authentication_required` | `authentication required` |
| `PermissionDeniedError` | 403 | `permission_denied` | `permission denied` |
| `RunNotFoundError` | 404 | `run_not_found` | `run not found` |
| `ArtifactNotFoundError` | 404 | `artifact_not_found` | `artifact not found` |
| `A2ATaskNotFoundError` | 404 | `a2a_task_not_found` | `a2a task not found` |
| `RunSubmissionConflictError` | 409 | `run_submission_conflict` | `run submission conflicts with an existing request` |
| `ActiveRunConflictError` | 409 | `active_run_conflict` | `session already has an active run` |
| `CommandConflictError` | 409 | `command_conflict` | `command conflicts with an existing request` |
| `CommandStateError` | 409 | `command_state` | `command is invalid for the current run state` |
| `CommandNotDueError` | 409 | `command_not_due` | `command is not due` |
| `ArtifactInUseError` | 409 | `artifact_in_use` | `artifact is referenced by durable session state` |
| `SideEffectInFlightError` | 409 | `side_effect_in_flight` | `side effect is still in flight` |
| `A2ATaskConflictError` | 409 | `a2a_task_conflict` | `a2a task conflicts with an existing binding` |
| `RequestTooLargeError` | 413 | `request_too_large` | `request exceeds maximum size` |
| `ArtifactTooLargeError` | 413 | `artifact_too_large` | `artifact exceeds maximum size` |
| `UnsupportedMediaTypeError` | 415 | `unsupported_media_type` | `unsupported media type` |
| `ArtifactMediaTypeUnsupportedError` | 415 | `unsupported_media_type` | `unsupported media type` |
| `DeliveryUnavailableError` | 503 | `delivery_unavailable` | `delivery backend is unavailable` |
| `DistributedBackendUnavailableError` | 503 | `distributed_backend_unavailable` | `distributed backend is unavailable` |
| `DistributedStoreClosedError` | 503 | `distributed_store_closed` | `distributed store is closed` |
| 其他异常 | 500 | `internal_error` | `internal error` |

`HttpParseError`、`HttpValidationError`、`RequestTooLargeError`、`UnsupportedMediaTypeError` 是
Transport 自己的 typed error；`AuthenticationRequiredError`、`PermissionDeniedError` 是 Channel
鉴权边界的 typed error。只有 decoder 捕获并重新分类的入站 `TypeError/ValueError` 才是 400；
Application Service 内意外产生的 `TypeError/ValueError` 是程序错误，按 500 处理，禁止 blanket catch
后伪装成用户错误。

错误 message 来自固定类型映射。未知异常始终返回 `internal error`；删除
`expose_internal_errors` 网络行为。响应不得包含 exception、stack、SQL、DSN、token、Prompt、
Tool arguments、WaitReason.detail、Artifact path 或 credential。

`agentos.distributed.errors` 增加固定 `CommandNotDueError`，PostgreSQL 在权威数据库时间尚未
到期时使用它；Transport 不通过解析 `CommandStateError` 文本区分 not-due。

## 7. SSE Contract

### 7.1 Scoped Cursor

Redis position 不能直接作为公共 cursor。SSE cursor 是无 padding base64url 的 canonical JSON：

```json
{"position":"123-0","scope":"<sha256>","version":1}
```

`scope = sha256(UTF8(tenant_id + "\n" + session_id + "\n" + run_id)).hexdigest()`。
Decoder 使用当前已鉴权 scope/path 重算并比较，拒绝非 canonical JSON、未知版本、非法 Redis
position、scope mismatch 和超长 token。Cursor 不是授权凭证；`RunEventStream.subscribe()`
仍必须在发 SSE headers 前完成 PostgreSQL preflight。

### 7.2 Frame

Replay event 固定为：

```text
id: {scoped_cursor}
event: {event_kind}
data: {compact_json}

```

data 固定包含 `session_id/run_id/turn_id/execution_attempt/event_sequence/occurred_at/event`，
其中 event 只按 `LiveRunEvent` allowlist 显式序列化。省略 tenant、Prompt、thinking、Tool Result、
异常文本和内部 cancellation detail。

`event` 的完整 shape 固定如下，表外字段禁止输出：

| event kind | `event` JSON |
|---|---|
| `turn_started` | `{}` |
| `status_update` | `{"message":str,"stage":str}` |
| `context_loaded` | `{"source":"runtime|memory|session|attachment"}` |
| `skill_loaded` | `{"skill_name":str}` |
| `plan_updated` | `{"status":"created|updated|completed","summary":str}` |
| `content_delta` | `{"index":int,"text":str}` |
| `tool_started|tool_completed|tool_failed` | `{"status":"started|completed|failed","tool_call_id":str,"tool_name":str}` |
| `final_result` | `{"content":str}` |
| `turn_completed` | `{}` |
| `turn_waiting` | `{"handle":str,"kind":LiveWaitKind,"not_before":datetime|null}` |
| `turn_failed` | `{}` |
| `turn_cancelled` | `{}` |

Top-level key 集合始终完全相同。`not_before` 使用通用 UTC datetime 编码。SSE field value 禁止
CR/LF；JSON data 按通用 compact encoder 生成，所以一个 frame 的 `data:` 只占一行。

Heartbeat 使用 `: heartbeat\n\n`，无 id、无 data、不推进 cursor。Gap 使用
`event: stream_gap` 和固定 `{reason}` data，不附伪造 cursor，发送一次后关闭。

Terminal event 只有 `turn_completed`、`turn_waiting`、`turn_failed`、`turn_cancelled`。
`final_result` 不是关闭信号。

Channel 在 disconnect、consumer cancellation、send/encode 异常、terminal 和 gap 路径都必须
在 finally `await subscription.aclose()`，且恰好一次。HTTP disconnect 只关闭观察订阅，
绝不提交 cancel/interrupt。

## 8. Channel Authentication Contract

旧 `ChannelAuthPolicy.authorize(...)->None` 不进入新路径。新入口使用：

```python
class ChannelAuthenticator(Protocol):
    async def authenticate(
        self,
        headers: HttpHeaders,
        *,
        context: ChannelAuthContext,
    ) -> RequestScope: ...
```

实现必须从已验证 identity 生成 `RequestScope`，并在同一决策中校验 operation/resource 权限。
tenant/principal 不得来自 JSON body、query、A2A metadata 或未经验证的 tenant header。
默认 authenticator fail closed；仅显式 local/dev profile 可以注入固定 scope authenticator。

## 9. A2A v1.0.1 Wire Contract

本节的唯一标准来源是 A2A `v1.0.1` 的 `specification/a2a.proto`、`docs/specification.md` 和
ADR-001 ProtoJSON。三者与本增补冲突时，以官方 proto 的字段、oneof 和 operation 为准；AgentOS
只增加安全限制、Application mapping 和可选扩展，不能改写标准 wire shape。

Wave 4 只实现 JSON-RPC 2.0 over HTTP(S) binding。Client 必须发送 `A2A-Version: 1.0`；缺失或
空值按官方 legacy default `0.3` 处理，而本实现 supported set 只有 `1.0`，因此返回
`VersionNotSupportedError (-32009)`，不提供 legacy payload fallback。Patch version 只用于比较，
协商值规范化为 Major.Minor。

必须实现官方 11 个 PascalCase operation：

```text
SendMessage
SendStreamingMessage
GetTask
ListTasks
CancelTask
SubscribeToTask
CreateTaskPushNotificationConfig
GetTaskPushNotificationConfig
ListTaskPushNotificationConfigs
DeleteTaskPushNotificationConfig
GetExtendedAgentCard
```

每个 operation 使用独立 immutable params/result type，禁止以通用 `Mapping[str, object]` 作为
canonical contract。JSON-RPC envelope 必须是 `jsonrpc="2.0"`，request id 必须存在且只允许
非空 string 或拒绝 bool 的 integer；`result` 与 `error` 严格互斥。Envelope 未知字段拒绝；params、
result 和嵌套 A2A message 按 ProtoJSON 忽略未知字段。重复 key、malformed UTF-8 和非法已知字段
仍必须拒绝。

### 9.1 ProtoJSON 数据模型

Encoder 与 decoder 规则必须分开实现。Encoder 只输出 lowerCamelCase JSON name；enum 输出 proto
中的 SCREAMING_SNAKE_CASE，例如 `ROLE_USER`、`ROLE_AGENT`、`TASK_STATE_WORKING`；bytes 输出带
padding 的标准 RFC 4648 Base64。可选或未设置的字段默认省略，不编码为 null；显式 `optional`
字段必须保留“未设置”和“设置为默认值”的 presence 差异。Timestamp 使用 UTC ProtoJSON 字符串；
AgentOS encoder 继续做确定性 key order，但不能改变 ProtoJSON 值语义。

Decoder 遵守 ProtoJSON 的兼容输入规则：同一字段接受 lowerCamelCase JSON name 或 proto
snake_case name，但两种别名同时出现视为重复字段并拒绝；enum 接受 symbolic name 或拒绝 bool 的
integer value；bytes 接受 standard/URL-safe Base64 以及带/不带 padding 的合法形式。`null` 对普通
字段表示 unset，不能被转换为该字段的默认标量值，repeated field 的元素不得为 null。例外是
`google.protobuf.NullValue` 的 sentinel-present null，以及 `google.protobuf.Value`/`Struct` 内部的
真实 JSON null；特别是 Part 的 `{"data":null}` 必须保留为已选择 data oneof 的真实 null，不能当作
缺失 content。Encoder 的 canonical 输出限制不得反向用于拒绝上述合法输入。

核心字段与可选性冻结为：

```text
Part(exactly one: text | raw | url | data;
     optional: metadata, filename, mediaType)
Message(required: messageId, role, parts;
        optional: contextId, taskId, metadata, extensions, referenceTaskIds)
Artifact(required: artifactId, parts;
         optional: name, description, metadata, extensions)
TaskStatus(required: state; optional: message, timestamp)
Task(required: id, status;
     optional: contextId, artifacts, history, metadata)
TaskStatusUpdateEvent(required: taskId, contextId, status; optional: metadata)
TaskArtifactUpdateEvent(required: taskId, contextId, artifact;
                        optional/default: append=false, lastChunk=false, metadata)
```

`parts` 是 required repeated field，因此必须非空；Message role 只接受 `ROLE_USER|ROLE_AGENT`，
inbound Send 必须是 `ROLE_USER`。`Task.contextId` 是标准可选字段；AgentOS 自己创建的 Task 因存在
持久 binding 而始终输出它，但 decoder 不得把它提升为标准必填。`history/artifacts/extensions/
referenceTaskIds` 按 ProtoJSON array 处理，metadata 是任意 JSON object。Part metadata、Message
metadata、Artifact metadata 和 request metadata 分别深冻结并各受 16 KiB canonical JSON 上限。

`raw` 是 ProtoJSON bytes。Transport strict decode 后对单请求全部 raw part 应用 512 KiB
decoded-size 累计门禁；canonical Transport DTO 只保留 `bytes`，不得用 canonical re-encode 相等
校验拒绝合法的 URL-safe 或无 padding 输入。Inbound `url` part 在 Wave 4 返回
`InvalidParamsError`，任何层都不抓取 URL。旧 `kind` discriminator、嵌套 legacy file、旧 artifact
fallback 和旧 role/state 值全部拒绝，不做兼容。

### 9.2 Operation Params 与 Result

11 个 operation 精确使用以下 ProtoJSON shape：

```text
SendMessage(params=SendMessageRequest{tenant?,message,configuration?,metadata?})
  -> result=SendMessageResponse{exactly one of task|message}
SendStreamingMessage(params=SendMessageRequest)
  -> SSE result=StreamResponse{exactly one of task|message|statusUpdate|artifactUpdate}
GetTask(params={tenant?,id,historyLength?})
  -> result=Task
ListTasks(params={tenant?,contextId?,status?,pageSize?,pageToken?,
                  historyLength?,statusTimestampAfter?,includeArtifacts?})
  -> result={tasks,nextPageToken,pageSize,totalSize}
CancelTask(params={tenant?,id,metadata?})
  -> result=Task
SubscribeToTask(params={tenant?,id})
  -> SSE result=StreamResponse
CreateTaskPushNotificationConfig(params=TaskPushNotificationConfig)
  -> result=TaskPushNotificationConfig
GetTaskPushNotificationConfig(params={tenant?,taskId,id})
  -> result=TaskPushNotificationConfig
ListTaskPushNotificationConfigs(params={tenant?,taskId,pageSize?,pageToken?})
  -> result={configs,nextPageToken}
DeleteTaskPushNotificationConfig(params={tenant?,taskId,id})
  -> result={}
GetExtendedAgentCard(params={tenant?} or omitted)
  -> result=AgentCard
```

`SendMessageConfiguration` 支持 `acceptedOutputModes`、`taskPushNotificationConfig`、
`historyLength` 和 `returnImmediately`。`returnImmediately=false` 或省略时，Channel 等待 Task
进入 terminal 或 interrupted state 后返回；true 时在创建/接受后立即返回当前 Task。Streaming
忽略该 flag。`historyLength=0` 省略 history，正整数最多返回该数量，未设置使用 AgentOS 默认上限；
实现可以返回更少但不能更多。`acceptedOutputModes` 与配置的 AgentCard 能力校验。Send 内嵌 push
config 在 Task binding 提交后由同一个 `A2APushService` 创建，不能进入 Transport 真值。

`ListTasks.pageSize` 默认 50、范围 1..100；返回 `nextPageToken` 必须始终存在，末页为空字符串；
结果按 status timestamp DESC、task ID DESC 稳定排序。`includeArtifacts=false` 时每个 Task 必须
省略 artifacts；true 时必须输出实际数组，当前 AgentOS 没有 output Artifact 时输出空数组。
`ListTaskPushNotificationConfigs` 同样使用 cursor pagination，默认 50、最大 100。

标准 `tenant` 只作为 AgentInterface 的 opaque routing hint：Transport 可以解码它，Channel 只能
用它校验已鉴权 route/interface 是否匹配，绝不能从该字段构造 `RequestScope`。权限 tenant/principal
始终只来自 `ChannelAuthenticator`。
每个 A2A endpoint 的当前 interface tenant 由部署配置冻结，未分区 route 使用 `None`。读取 public
card 后必须存在 protocolBinding=`JSONRPC`、protocolVersion=`1.0` 且 tenant 与当前配置完全相同的
interface，否则视为本地 card/config mapping error -32006。请求中的所有 tenant hint 必须等于当前
interface tenant；Card 中发布其他 interface tenant 不得扩大当前 route 可接受的 hint 集合。

### 9.3 Message、Task 与 AgentCard

Inbound Message 按 part 原始顺序归一化：text 保留原文；data 使用通用 canonical JSON；raw 只产生
artifact handle；url 已拒绝。`RunSubmission.content` 是 text/data fragment 以单个 `"\n"` 连接的
结果，无 fragment 时为空串。Inline raw 的 upload ID 固定为
`"a2a_artifact_" + sha256(message_id + "\n" + decimal_part_index).hexdigest()`。Follow-up 的
`hitl_answer|wakeup` payload 固定为
`{"artifact_handles":[...],"content":normalized_content,"metadata":message_metadata_or_empty}`，
并受 64 KiB command payload 门禁；不得追加展示标签或 filename。

AgentCard 精确遵守 v1.0.1 proto：

```text
AgentCard(required: name, description, supportedInterfaces, version, capabilities,
          defaultInputModes, defaultOutputModes, skills;
          optional: provider, documentationUrl, securitySchemes,
                    securityRequirements, signatures, iconUrl)
AgentInterface(required: url, protocolBinding, protocolVersion; optional: tenant)
AgentProvider(required: url, organization)
AgentCapabilities(optional: streaming, pushNotifications, extensions,
                   extendedAgentCard)
AgentExtension(optional: uri, description, required=false, params)
AgentSkill(required: id, name, description, tags;
           optional: examples, inputModes, outputModes, securityRequirements)
AgentCardSignature(required: protected, signature; optional: header)
```

`supportedInterfaces/defaultInputModes/defaultOutputModes/skills/tags` 是 required repeated field，
必须非空。SecurityScheme 必须完整实现 proto oneof：API key、HTTP auth、OAuth2、OpenID Connect、
mTLS 及其 flow 类型；SecurityRequirement 使用
`schemes: {schemeName: {list:[scope,...]}}`。不输出旧
`url/protocolVersion/preferredTransport/security/stateTransitionHistory` 顶层字段，不提供
`transport` alias。URL 先做语法校验，信任/egress 决策仍归 Policy。Mapping、tuple 和 JSON
在构造边界深冻结。

`supportedInterfaces` 至少包含一个当前可用的
`AgentInterface(protocolBinding="JSONRPC", protocolVersion="1.0")`，其 URL 指向本合同的 A2A
JSON-RPC endpoint。若不存在该 interface，AgentCard 构造失败，不能发布一张与实际 Channel binding
不一致的 card。

AgentCard 签名与验签先按 A2A ProtoJSON 构造，并严格保留字段 presence：未显式设置的 optional
字段必须省略；显式设置为默认值的 optional 字段必须保留；标记 required 的字段必须始终输出；其余
处于默认值且无 presence 的字段必须省略。随后删除顶层 `signatures` 字段，并对剩余 JSON 执行
RFC 8785 JSON Canonicalization Scheme。验签使用同一流程，任何 DTO 转换都不得补默认值或丢失
explicit-default presence；不得使用 AgentOS 的普通 `sort_keys` JSON 代替 JCS。

`GetExtendedAgentCard` 由 Channel 注入的 immutable `A2AAgentCardProvider` 提供。Provider 同时拥有
public/extended card 的单一配置真值；未声明 `capabilities.extendedAgentCard` 返回 -32004，已声明但
没有 extended card 返回 -32007。返回前仍执行当前 authenticated scope 的 operation authorization；
Card 不从 Store、Task 或 metadata 动态拼接。

Provider boundary 冻结为纯 async、无 close 所有权：

```python
class A2AAgentCardProvider(Protocol):
    async def get_public_card(self, *, scope: RequestScope) -> A2AAgentCard: ...
    async def get_extended_card(
        self, *, scope: RequestScope
    ) -> A2AAgentCard | None: ...
```

`get_public_card()` 必须返回 immutable card；`get_extended_card()` 的 `None` 只表示已声明 extended
能力但没有配置 extended card，并映射 -32007。是否声明能力始终从同一个 public card 判断；Provider
不得同步阻塞、动态读取 Task metadata 或自行鉴权。

扩展协商只使用 public AgentCard 的 `capabilities.extensions` 和唯一的 `A2A-Extensions` header。
AgentCard 必须声明 AgentOS snapshot-resume 扩展：

```text
AgentExtension(
    uri="https://agentos.dev/a2a/extensions/snapshot-resume/v1",
    required=false
)
```

以下 OWS、重复项和处理顺序是 AgentOS 在官方未冻结处增加的确定性策略。Header 是逗号分隔的 URI
列表；每项只裁剪两侧 OWS（SP/HTAB），空项或非法 URI 拒绝，重复 URI
按首次出现顺序去重。协商顺序固定为：完成鉴权并取得 `RequestScope` -> 校验 `A2A-Version` -> 解析
header -> 读取 public card 声明 -> 检查 card 中 `required=true` 的扩展是否全部由客户端声明 -> 忽略
客户端声明但 card 未知的 optional URI -> 启用双方交集 -> 校验当前 operation 所需扩展。缺失 card
required extension 或调用方请求了必须协商但未启用的能力时返回 -32008；未知 optional URI 本身不报错。
`Last-Event-ID` 存在但 snapshot-resume 未进入双方交集时返回 -32008。协商不得改变
`RequestScope`，也不得从 private/extended card 动态增加扩展。
该 -32008 检查先于当前 interface tenant hint 和 operation 类型校验；只有扩展已进入交集后，
非 `SubscribeToTask` 携带 `Last-Event-ID` 才返回 -32602。

## 10. A2A Task Mapping Contract

Task binding 保持独立 Application boundary：

```python
class A2ATaskPort(Protocol):
    async def bind(
        self, *, scope: RequestScope, binding: A2ATaskBinding
    ) -> A2ATaskBinding: ...
    async def resolve(
        self, *, scope: RequestScope, task_id: str
    ) -> A2ATaskBinding | None: ...

class A2ATaskService:
    async def bind(
        self, scope: RequestScope, *, task_id: str, session_id: str, run_id: str
    ) -> A2ATaskBinding: ...
    async def resolve(
        self, scope: RequestScope, task_id: str
    ) -> A2ATaskBinding: ...
```

`A2ATaskBinding(tenant_id, task_id, session_id, run_id)` 是 PostgreSQL tenant-scoped truth。v1 固定
`task_id == run_id`、`context_id == session_id`；get/cancel/subscribe 仍通过 binding 解析 session，
endpoint 不扫描 Store 或猜 session。新 Message 只有 contextId 时使用经验证值；只有 taskId 时从
binding 推导 contextId；两者存在时必须与 binding 相同；都缺失时 session ID 确定性派生为：

```text
"a2a_" + sha256(tenant_id + "\n" + message_id).hexdigest()
```

首次 Send 流程固定为：

```text
decode/auth
-> optional inline Artifact upload
-> RunSubmissionService.submit(messageId -> submission_id)
-> A2ATaskService.bind(task_id=receipt.run_id)
-> optional A2APushService.create(messageId-derived operation identity)
-> RunQueryService.get / optional wait-until-interrupted-or-terminal
-> SendMessageResponse{task=...}
```

Binding exact duplicate 幂等；task/session/run 冲突拒绝。响应只能在 binding 提交后发送；提交后、
响应前崩溃通过相同 messageId 重试收敛。Follow-up 先 resolve binding 和 query Run，再按主合同
WAITING matrix 生成 `hitl_answer` 或 `wakeup`；timer/retry_backoff 返回 state/not-due，
side_effect_reconciliation 永远拒绝普通 Message。跨 tenant 与未知 binding 使用同一 not-found。

`CancelTask` 是领域级幂等，不只依赖 JSON-RPC request id：当前 Run 已为 CANCELLED 时，即使换用新的
request id，也返回当前 CANCELLED Task 且不重复提交 cancel command；CREATED/QUEUED/RUNNING/
WAITING 状态提交一次 cancel 并返回更新后的 Task；COMPLETED/FAILED 等其他不可取消终态才返回
`TaskNotCancelableError`。未知或跨 tenant task 仍统一返回 not-found。

`ListTasks` 不能由 Channel 循环 resolve 或扫描 Store。新增 tenant-scoped catalog boundary：

```python
@dataclass(frozen=True, slots=True)
class A2ATaskListQuery:
    context_id: str | None
    status: A2ATaskState | None
    page_size: int
    page_token: str | None
    history_length: int | None
    status_timestamp_after: datetime | None
    include_artifacts: bool

@dataclass(frozen=True, slots=True)
class A2ATaskListItem:
    binding: A2ATaskBinding
    run: RunReadModel
    status_updated_at: datetime

@dataclass(frozen=True, slots=True)
class A2ATaskListPage:
    items: tuple[A2ATaskListItem, ...]
    next_page_token: str
    page_size: int
    total_size: int

class A2ATaskCatalogPort(Protocol):
    async def list(
        self, *, scope: RequestScope, query: A2ATaskListQuery
    ) -> A2ATaskListPage: ...

class A2ATaskCatalogService:
    async def list(
        self, scope: RequestScope, query: A2ATaskListQuery
    ) -> A2ATaskListPage: ...
```

PostgreSQL Adapter 在一次 scoped query 中 join binding/run，使用 `(status_updated_at, task_id)`
keyset pagination。Page token 是无 padding base64url canonical JSON，包含 version、query digest、
scope digest、timestamp 和 task ID；任一 filter 或 tenant 变化都拒绝，不能作为授权凭证。

`RunReadModel -> Task` 状态映射沿用主合同并使用官方 enum。Task status message 默认省略；timestamp
只有存在权威数据库时间时才输出。COMPLETED Task 在 `historyLength != 0` 时最多输出一个
`ROLE_AGENT` Message，`messageId="a2a_result_" + run_id`，parts 为单个 text；其他状态默认省略
history。Get/List 不伪造 output Artifact、status message、tenant、WaitReason.detail、fence、claim、
aggregate version 或 Provider 数据。

## 11. A2A Error、Stream 与 Push

### 11.1 Error Mapping

JSON-RPC 标准错误 message 必须使用官方大小写和文本：

| code | message |
|---:|---|
|-32700 | `Invalid JSON payload` |
|-32600 | `Request payload validation error` |
|-32601 | `Method not found` |
|-32602 | `Invalid parameters` |
|-32603 | `Internal error` |

A2A-specific code 只使用官方 `-32001..-32009`：

| code | error type | stable message |
|---:|---|---|
|-32001 | `TaskNotFoundError` | `Task not found` |
|-32002 | `TaskNotCancelableError` | `Task cannot be canceled` |
|-32003 | `PushNotificationNotSupportedError` | `Push notifications are not supported` |
|-32004 | `UnsupportedOperationError` | `Operation is not supported` |
|-32005 | `ContentTypeNotSupportedError` | `Content type is not supported` |
|-32006 | `InvalidAgentResponseError` | `Invalid agent response` |
|-32007 | `ExtendedAgentCardNotConfiguredError` | `Extended Agent Card is not configured` |
|-32008 | `ExtensionSupportRequiredError` | `Extension support is required` |
|-32009 | `VersionNotSupportedError` | `A2A protocol version is not supported` |

parse/UTF-8/duplicate-key -> -32700；非法 envelope -> -32600；未知 method -> -32601；typed params、
part、metadata、page token、Artifact policy validation、`A2ATaskConflictError` 和
`A2APushConflictError` -> -32602；task/binding/config not-found -> -32001；Cancel state/conflict ->
-32002；未启用 push/stream/extended-card/extension/version 分别映射
-32003/-32004/-32007/-32008/-32009；不支持的 media type -> -32005；本地 mapping 生成非法标准
response -> -32006；backend/store-closed、stream gap 和其他未知异常 -> -32603。不得新增 -32010、
-32011 或 -32012 私有码。

`error.data` 若存在必须是 ProtoJSON Any detail array，每项包含 `@type`。AgentOS 只可增加脱敏的
`google.rpc.ErrorInfo`；其顶层 `reason` 是 stable machine reason，顶层 `domain` 固定为
`a2a-protocol.org`，`metadata` 只放 opaque `requestId`。不得返回 task 是否存在、原始字段值、
exception、SQL、URL、credential 或 stack。鉴权在 JSON-RPC dispatch 前执行，
失败使用 HTTP 401/403 通用 error body；已鉴权 JSON-RPC error 返回 HTTP 200，body 超限仍为 413，
Content-Type 错误仍为 415。无法读取 request id 时 envelope id 为 null。

完整 wire parse/decode 可以早于鉴权，因为 `ChannelAuthContext` 需要已解码的 operation 和 resource
identity；此阶段只允许返回固定的 parse/envelope/method/params error，不得读取 AgentCard、调用
Application Service 或暴露资源是否存在。所有合法请求的版本/扩展协商、路由 hint 校验和业务 dispatch
必须在 `ChannelAuthenticator` 成功产生 `RequestScope` 后执行。本合同不要求重复解码或双重鉴权。

### 11.2 Streaming Snapshot 与 Snapshot Resume

每个 A2A SSE `data` 是带原 JSON-RPC request id 的 success/error envelope；成功 result 必须是
`StreamResponse` oneof wrapper。`SendStreamingMessage` 与 `SubscribeToTask` 的首帧必须是
`result={"task": Task}` 或单次 `result={"message": Message}`。Task lifecycle stream 后续帧只输出
`statusUpdate` 或 `artifactUpdate`；Message-only stream 一帧后立即关闭。

两个 streaming operation 的 stable preflight 必须分开：`SubscribeToTask` 在创建 SSE response 前
先解析 binding 并查询当前 Task；若已 WAITING 或 terminal，使用普通 JSON-RPC response 返回 -32004
`UnsupportedOperationError`，不得建立 SSE 或发送 snapshot。`SendStreamingMessage` 创建/接受的新 Task
即使在 stream barrier 前已 WAITING 或 terminal，仍发送一次 Task snapshot 后关闭；Message-only result
同样发送一次后关闭。WAITING 表示当前执行切片已经持久化并停止，不允许留下只发送 heartbeat 的
无生产者订阅；后续 wakeup/resume 由新操作重新建立流。

首个 Task/Message 是 PostgreSQL snapshot，不对应 ReplayItem，因此 SSE frame **没有 `id`**，不推进
cursor。后续每个 ReplayItem 使用 scoped cursor 作为 SSE `id`。`SubscribeToTask` 标准 params 不含
`afterEventId`；AgentOS snapshot-resume extension 只从 `Last-Event-ID` header 接收 cursor，并且必须在
`A2A-Extensions` 声明 `https://agentos.dev/a2a/extensions/snapshot-resume/v1` 后启用。A2A SSE frame 只输出
可选 `id:` 与必填 `data:`，不输出自定义 `event:` field，保证标准 EventSource `message` handler
可以接收全部 StreamResponse。

无论是否携带 cursor，snapshot-resume 顺序都固定为：先在 Replay Port 捕获当前 high-water cursor，
再查询 PostgreSQL 当前 Task snapshot，发送无 SSE id 的 snapshot 首帧，最后只 follow 该 high-water
之后的事件。已有 cursor 只用于在捕获 barrier 前验证 tenant/session/run scope 与 cursor 完整性；
不得从旧 cursor 回放 high-water 之前的历史事件。这样当前稳定 snapshot 后不会再发送历史
WORKING，且 barrier 之后的事件不会丢失。Server 不允许为 snapshot 伪造 cursor；WAITING/terminal
snapshot 发送后直接关闭，不再 follow。该 stable-snapshot 规则只适用于 `SendStreamingMessage`，
`SubscribeToTask` 使用上面的 stable preflight error。该扩展提供“恢复到当前状态后继续”，不承诺
历史事件重放。

ReplayItem 映射为官方 `TaskStatusUpdateEvent`：

```json
{
  "statusUpdate": {
    "taskId": "run_1",
    "contextId": "session_1",
    "status": {"state": "TASK_STATE_WORKING"},
    "metadata": {
      "agentosEventKind": "content_delta",
      "agentosEvent": {"index": 0, "text": "..."}
    }
  }
}
```

标准 v1.0.1 `TaskStatusUpdateEvent` 没有 `final` 字段；terminal 由 status.state 判断。普通事件为
`TASK_STATE_WORKING`，human_input waiting 为 `TASK_STATE_INPUT_REQUIRED`，其余 waiting 为 working，
completed/failed/cancelled 映射官方终态。安全 metadata 只包含 allowlisted LiveRunEvent 投影。
Heartbeat 为 `: heartbeat\n\n`，无 id/data。Gap 使用 -32603 error envelope、无伪造 cursor，发出一次
后关闭；terminal status update 发出后关闭。disconnect、consumer cancellation、encode/send 异常、
terminal 和 gap 全部在 finally `await subscription.aclose()` exactly once，断连不取消 Run。

### 11.3 Push Contract

Push 使用官方 singular authentication shape：

```text
AuthenticationInfo(required: scheme; optional: credentials)
TaskPushNotificationConfig(required: url;
                           optional: tenant, id, taskId, token, authentication)
```

AgentOS 不从 config.tenant 构造 scope。Create 时 taskId 必须与已解析 binding 一致；缺失由 params path/
binding 填入。id 缺失时由持久 operation identity 确定性派生，重复请求必须返回首次创建的 config。
read/list response 可以输出 authentication.scheme，但必须省略 credentials 和 token；secret absence 是
合法 ProtoJSON optional omission，不用占位文本替代。

缺失 config id 的派生算法固定为
`"a2a_push_" + sha256(UTF8(operation_id)).hexdigest()`；Channel 不生成随机 ID，Service 在 URL policy
和 secret protection 前完成派生，并把最终 ID 纳入 canonical input digest。

Application contract 冻结为：

```python
@dataclass(frozen=True, slots=True, repr=False)
class A2APushAuthenticationInput:
    scheme: str
    credentials: str | None = None

@dataclass(frozen=True, slots=True, repr=False)
class A2APushConfigInput:
    config_id: str | None
    url: str
    token: str | None = None
    authentication: A2APushAuthenticationInput | None = None

@dataclass(frozen=True, slots=True, repr=False)
class A2APushConfigRecord:
    tenant_id: str
    task_id: str
    config_id: str
    url: str
    authentication_scheme: str | None
    secret_ref: ProtectedPayloadRef | None

@dataclass(frozen=True, slots=True)
class A2APushConfigView:
    task_id: str
    config_id: str
    url: str
    authentication_scheme: str | None

@dataclass(frozen=True, slots=True, repr=False)
class A2APushConfigRecordPage:
    records: tuple[A2APushConfigRecord, ...]
    next_page_token: str

@dataclass(frozen=True, slots=True)
class A2APushConfigPage:
    configs: tuple[A2APushConfigView, ...]
    next_page_token: str

@dataclass(frozen=True, slots=True, repr=False)
class A2APushDeliveryTarget:
    scope: RequestScope
    outbox_id: str
    delivery_id: str
    task_id: str
    context_id: str
    config_id: str
    url: str
    authentication_scheme: str | None
    secret_ref: ProtectedPayloadRef | None
    event_id: str
    protocol_version: str
    status_sequence: int
    task_state: A2ATaskState
    delivered_at: datetime | None
    suppressed_at: datetime | None
    abandoned_at: datetime | None

class A2APushDeliveryAttempt(Protocol):
    @property
    def target(self) -> A2APushDeliveryTarget: ...
    @property
    def attempt_id(self) -> str: ...
    @property
    def expires_at(self) -> datetime: ...
    def authorize_send(self) -> AsyncContextManager[None]: ...
    async def mark_delivered(self) -> None: ...
    async def mark_failed(
        self, *, category: A2APushFailureCategory
    ) -> A2APushAttemptResolution: ...

class A2APushAttemptResolution(str, Enum):
    RETRY_PENDING = "retry_pending"
    ACK_SAFE = "ack_safe"

class A2APushFailureCategory(str, Enum):
    HTTP_REJECTED = "http_rejected"
    NETWORK = "network"
    SECURITY = "security"
    RESPONSE_TOO_LARGE = "response_too_large"
    SECRET_UNAVAILABLE = "secret_unavailable"
    PROTOCOL_ENCODE = "protocol_encode"

A2APushService.create(
    scope, *, binding, config_input, operation_id
) -> A2APushConfigView
A2APushService.get(scope, *, task_id, config_id) -> A2APushConfigView
A2APushService.list(
    scope, *, task_id, page_size, page_token
) -> A2APushConfigPage
A2APushService.delete(
    scope, *, task_id, config_id, operation_id
) -> None

class A2APushPort(Protocol):
    async def create(
        self, *, scope: RequestScope, binding: A2ATaskBinding,
        record: A2APushConfigRecord, operation_id: str,
    ) -> A2APushConfigRecord: ...
    async def get(
        self, *, scope: RequestScope, task_id: str, config_id: str,
    ) -> A2APushConfigRecord | None: ...
    async def list(
        self, *, scope: RequestScope, task_id: str,
        page_size: int, page_token: str | None,
    ) -> A2APushConfigRecordPage: ...
    async def delete(
        self, *, scope: RequestScope, task_id: str,
        config_id: str, operation_id: str,
    ) -> None: ...

class A2APushDeliveryPort(Protocol):
    async def open_attempt(
        self, *, outbox_id: str, worker_id: str, ttl: timedelta,
    ) -> A2APushDeliveryAttempt | None: ...
```

`open_attempt()` 使用一个短 PostgreSQL transaction 原子校验 active config、顺序前驱、
`next_attempt_at` 和现有 attempt lease，再写入随机 `attempt_id/attempt_owner_id/attempt_expires_at` 后提交；
`open_attempt()` 的 transaction/locks 不跨 HTTP I/O。唯一例外是下文 `authorize_send()` gate，它只
包围 socket write 与 bounded drain，不包围连接建立、response 等待或 redirect 解析。返回 `None` 表示
delivery 不存在或已经
`delivered/suppressed/abandoned`，Consumer 直接 ACK；状态尚未到 `next_attempt_at`、同一 task/config
存在更早的非终态 delivery，或已有未过期 attempt 时抛出内部 `A2APushDeliveryDeferredError`，Consumer
不 ACK。过期 attempt 可以被新 Worker fencing takeover。

`authorize_send()` 是每个 HTTP hop 的外部副作用 gate。它在异步 context enter 的短 transaction 中按
`active config FOR KEY SHARE -> delivery FOR UPDATE` 重新验证非终态、当前 attempt identity 与未过期
lease，并把锁保持到 context exit。HTTP Adapter 必须先完成 URL/DNS/TLS/peer 校验，再进入 gate；enter
成功返回后，中间不得插入其他 await，必须立即同步调用 `writer.write(request_bytes)`，随后只 await 一个
有界 `writer.drain()`，退出 gate 后才读取 webhook response。Gate drain timeout 默认 2 秒，SDK hard
max 为 5 秒，部署只能收紧不能放宽；timeout、取消和 write/drain 异常必须回滚 gate transaction 并在
finally 释放 config/delivery 锁。连接建立、response headers/body 等待和
redirect 解析都不得持有数据库 transaction。每一个 redirect hop 都重新调用同一 gate；Delete 后的
redirect 因 config/suppression 校验失败而不能发送。

`mark_delivered()` 与 `mark_failed()` 使用独立短 transaction，并且必须匹配当前 `attempt_id`；stale
attempt 不能修改非终态 delivery，并抛出内部 `A2APushAttemptFencedError`，Worker 不 ACK。若 delivery
已进入 `delivered/suppressed/abandoned`，stale completion 只观察既有终态并允许幂等 ACK。前者写
`delivered_at` 并清空 attempt lease；后者按 typed failure category 增加失败计数，在同一事务设置
`next_attempt_at` 并清空 `attempt_id/attempt_owner_id/attempt_expires_at`。仍可重试时返回
`RETRY_PENDING`，delivery 已处于或本次进入 `delivered/suppressed/abandoned` 任一终态时返回
`ACK_SAFE`，且所有终态路径同样清空 attempt lease。若 Delete 已把 delivery 标记为 suppressed，则
两个方法都只完成当前
attempt 的幂等收口并允许 ACK，不得恢复 delivery。该 attempt 对象不进入 Public API。

`A2APushConfigInput` 使用 `repr=False`；token/credentials 在 Port I/O 前组成 canonical secret payload，
以 binding tenant/session 为 protection context 转换为 `ProtectedPayloadRef`。Port 只接收 protected
record；View/Page/repr/log/outbox 不得包含 tenant、token、credentials 或 secret_ref。URL 默认只允许
public HTTPS，拒绝 localhost、private/link-local/reserved IP、IP literal 和 userinfo credential。

Push list 的 `pageSize` 默认 50、范围 1..100，按 `config_id ASC` 做 keyset pagination；末页
`nextPageToken` 固定为空字符串。非末页 token 是无 padding base64url canonical JSON，至少绑定
version、tenant scope digest、task ID、page size 和最后一个 config ID；任一 scope/query 变化或非法
token 都拒绝为 invalid params。Port 必须在一次 tenant-scoped query 中返回 page，Service 不得先全量
list 再切片。已存在 task 但无 config 返回空 page；未知/跨 tenant task 统一 task not-found；Get
未知 config 返回 config not-found。

JSON-RPC request id 不直接成为持久主键。Cancel、Create Push 和 Delete Push 的 operation identity
统一派生为：

```text
"a2a_op_" + sha256(
    UTF8(canonical_json({
        "method": method,
        "requestId": request_id,
        "resource": {
            "taskId": task_id,
            "configId": config_id_or_null
        },
        "version": 1
    }))
).hexdigest()
```

`method` 使用官方 PascalCase operation name；`canonical_json` 保留 string/integer request id 类型差异；
Cancel 的 `configId` 固定为 null，Create 缺失 config ID 时也固定为 null，Delete 使用实际 config ID。
Send 仍按标准使用 messageId 作为 submission/command identity。Send 内嵌 push config 的独立 operation
identity 固定为：

```text
"a2a_inline_push_" + sha256(
    UTF8(canonical_json({
        "messageId": message_id,
        "taskId": task_id,
        "version": 1
    }))
).hexdigest()
```

相同 messageId/taskId 的重试因此收敛到同一 config operation；config 的 canonical input digest 仍包含
最终 config ID、URL、authentication scheme 和未加密 secret 明文 digest。相同 operation identity 但
不同 canonical input digest 必须 conflict；随机 ciphertext 不影响 digest 幂等。

Create 的相同 operation identity 与相同 digest 返回首次保存的结果；相同 identity、不同 digest 返回
conflict。Delete 还必须是资源级幂等：若 config 当前存在则删除并写 tombstone；若 active row 已删除，
但 PostgreSQL 存在同 tenant/task/config 的历史 delete operation/tombstone，则即使新的 request id 产生
新的 operation identity 也返回成功 `{}`；只有 active row 和历史 tombstone 都不存在时才返回 config
not-found。重复删除不得重复产生业务 side effect。

Delete tombstone 至少保留到所属 A2A task binding 的公开 retention 生命周期结束；只要 Task 仍可被
Get/List/Cancel 定位，tombstone 就不得独立 GC。Task retention 到期后，binding、push configs、push
operations/tombstones、push deliveries 与未投递 Outbox 引用必须由同一受控回收流程按外键顺序处理；
不得先删 tombstone 而让仍可寻址 Task 的重复 Delete 从成功退化为 not-found。

Config create/delete 只在 PostgreSQL 单事务提交 config mutation、operation record 和 tombstone；它们
不是 Task lifecycle event，不创建 `a2a.push.configured|a2a.push.deleted` webhook Outbox。内部审计若
需要观察配置变化，使用独立 audit event，不进入 outbound Push delivery。

Create 必须在一个 PostgreSQL transaction 内按统一
`Session -> Run -> A2A task binding -> config` 锁序执行：先锁定 tenant/session/run 权威行，再验证
binding，最后创建或读取 config。该 transaction 在 config 可见后读取
同一把 Run 锁保护的当前 `status/aggregate_version`，并为新 config 创建一条当前状态 snapshot delivery。
若 Run 已 terminal，则 snapshot delivery 就是该终态；若状态转换先获得 Run 锁，则 Create 读取转换后的
状态；若 Create 先获得 Run 锁，则后续状态 transaction 必须看到新 config 并 fan out。两种顺序都不得
丢失当前或后续终态通知。duplicate Create 只返回首次结果，不重复创建 snapshot delivery。

Wave 4 的 durable Push event 覆盖 PostgreSQL 已原子提交的全部 Task lifecycle 状态：`queued`、
`running`、`waiting`、`completed`、`failed` 和 `cancelled`。映射固定为：

| Run 状态 | A2A task state |
|---|---|
| `queued` | `TASK_STATE_SUBMITTED` |
| `running` | `TASK_STATE_WORKING` |
| `waiting` + `human_input` | `TASK_STATE_INPUT_REQUIRED` |
| 其他 `waiting` | `TASK_STATE_WORKING` |
| `completed` | `TASK_STATE_COMPLETED` |
| `failed` | `TASK_STATE_FAILED` |
| `cancelled` | `TASK_STATE_CANCELED` |

Create reconciliation 使用同一映射。当前 AgentOS 没有 durable output Artifact truth，因此本 Wave 不伪造
`TaskArtifactUpdateEvent`；Redis-only content/tool live event 也不升级为 durable webhook。

写入上述 status Outbox 的同一个 PostgreSQL transaction 必须按当前 active push configs fan out：

1. 为每个 config 生成一个 immutable delivery row；
2. snapshot task/context/config identity、URL、authentication scheme、protected secret ref、A2A task
   state、`status_sequence=aggregate_version`、event identity 和 protocol version；
3. 为每个 delivery 生成 topic=`agentos.a2a.push` 的独立 Phase 6 Outbox；
4. 该 Outbox payload 只保存 immutable `delivery_id`，不保存 URL、secret 或 ProtoJSON body。

`event_id` 是内部 identity/audit 字段，不写入 A2A metadata；`protocol_version` 只允许 `1.0`，只用于
选择/校验 encoder，不伪造不存在的 wire 字段。Lifecycle fanout 的 `event_id` 使用 source status Outbox
ID；Create reconciliation 使用由 create operation identity 和当前 aggregate version 派生的稳定 ID。
`delivery_id` 和 delivery Outbox ID 必须由 tenant、task、event ID 与 config ID 确定性派生。

同一 `(tenant_id, task_id, config_id)` 的 delivery 必须按
`(status_sequence ASC, created_at ASC, delivery_id ASC)` 串行发送。存在更早的非终态 delivery 时，后继
delivery 不得开始 HTTP attempt；多 Worker、Redis reclaim 和失败重试都不能让 completed 越过 working。
delivery row 是 PostgreSQL 顺序与重试真值，Redis pending/reclaim 只负责 at-least-once 交付提醒。

Delete 成功后不得再向该 config 的 webhook 发送通知。Delete transaction 按
`Session -> Run -> A2A task binding -> config -> delivery` 锁序获取 config 冲突锁，把所有尚未
`delivered/abandoned` 的 delivery 原子标记为 `suppressed`，清除 attempt lease 和 `next_attempt_at`，
再删除 config 并写 tombstone。它与 `authorize_send()` 的 config/delivery 锁形成唯一线性化顺序：

1. Delete 先获得 config 锁：delivery 被 suppressed，后续 gate 校验失败，socket 不写入；
2. gate 先获得 config key-share 锁：Adapter 在同一 gate 内立即写入并 drain 请求字节，退出后 Delete
   才能成功；该 POST 因此在 Delete 成功前已经发送；
3. `open_attempt()` 只领取 lease，不构成发送授权；即使 Worker 在领取后暂停或被 fencing takeover，恢复后
   也必须通过 gate，不能绕过 Delete 或新 attempt identity。

Gate 不等待 webhook response，因此接收方在处理当前 webhook 时同步 Delete 最多等待当前有界
`writer.drain()` 完成，不会形成等待 response 的闭环。Delete 成功响应保证没有正在写入或未来可开始的
该 config HTTP POST。delivery 的 URL、secret snapshot 只保证重试一致性，不赋予 Delete 后继续发送的
权利。

Worker 只从 Redis 获得 `outbox_id`，再通过 `A2APushDeliveryPort.open_attempt()` 加载权威 target。它用
冻结的 `task_state` 和 protocol version 构造官方直接 ProtoJSON `StreamResponse{statusUpdate}`，不加
JSON-RPC envelope。若 port 返回 `None`，表示 delivery 已为 `delivered/suppressed/abandoned`，直接 ACK；
若 deferred，则不 ACK。只有 HTTP 2xx 后调用 `mark_delivered()`，attempt transaction 成功提交后才 ACK
Redis。HTTP 非 2xx 或网络失败调用 `mark_failed()`；前七次失败不 ACK，第八次进入 `abandoned` 后 ACK。
数据库失败、Worker 取消或 attempt context 提交失败均不 ACK。2xx 后、事务提交前崩溃允许重复 webhook，
这是明确的 at-least-once 边界，不得宣传 exactly-once。

失败预算固定为最多 8 次。第 `failure_count` 次失败后的退避为
`min(2 ** failure_count, 300)` 秒，使用 PostgreSQL `clock_timestamp()` 计算 `next_attempt_at`；未到期时
`open_attempt()` deferred。第 8 次失败原子写 `abandoned_at` 和脱敏稳定 failure category，不保存响应体、
URL、credential 或 exception 文本。`delivered`、`suppressed`、`abandoned` 是互斥终态，终态 delivery
不再发送。该失败预算是 delivery truth 的字段，不创建第二套 daemon/dead-letter store。

`mark_delivered()` 与 `mark_failed()` 只允许由 `open_attempt()` 返回的、携带 opaque attempt identity 的
对象调用，不接受调用方构造 target 直接更新。Profile 统一拥有 PostgreSQL pool、Queue 和 HTTP client
生命周期；Service、Port 与 Worker 借用这些资源，不自行关闭。attempt lease、connect/read/total
timeout、send-gate drain timeout 与 Worker 并发上限都是强制有界门禁，不得
使用无限 timeout、无租约 claim 或无界并发。Profile 必须验证 PostgreSQL transaction/
`idle_in_transaction_session_timeout` 不短于实际 gate drain timeout，并为 5 秒 hard max 保留释放余量；
不得让数据库先终止 gate transaction 后 Worker 仍继续 socket write。Worker 取消不记录 failure
category，等待租约过期后 reclaim。

Outbound request 的 body 固定为 direct `StreamResponse` ProtoJSON，`Content-Type` 固定为
`application/a2a+json`。protected secret 解密后，token 投影为 `X-A2A-Notification-Token`；仅当
authentication scheme 与 credentials 都存在时投影 `Authorization: {scheme} {credentials}`。两个
credential header 都不得进入日志、错误、trace 或 redirect history。redirect 跨 origin 时必须同时删除
`Authorization` 与 `X-A2A-Notification-Token`；同 origin redirect 仍需重新执行 DNS/peer 门禁。
secret 解密失败按 delivery failure 处理，不发送无凭据降级请求。

投影前必须 fail closed 校验：authentication scheme 满足 HTTP auth-scheme token grammar，token 与
credentials 只能包含 ASCII visible character（credentials 可含 SP，二者均拒绝 CR/LF、DEL 和其他
control），`X-A2A-Notification-Token` 与完整 `Authorization` value 各自最多 8192 ASCII bytes。scheme
存在但 credentials 缺失时不生成 Authorization；credentials 存在但 scheme 缺失时拒绝。非法或超限
secret 归类 `SECRET_UNAVAILABLE`，不得依赖 HTTP library 偶然清洗或静默截断。

Key rotation 必须保证所有非终态 delivery 的 `ProtectedPayloadRef` 可解密：部署方要么保留旧 key，直到
引用它的 delivery 全部进入 `delivered/suppressed/abandoned`，要么在删除旧 key 前以受审计 transaction
重加密全部历史 config/delivery secret refs。不得先删除旧 key 再依赖无限 retry；read/list/repr 仍不得
暴露 key ID、ciphertext、token 或 credentials。

保存时 URL 校验不能替代发送时 SSRF 防护。每次 delivery attempt 和每个 redirect hop 都必须：

1. 重新解析 hostname 的全部 A/AAAA；任一地址为 loopback/private/link-local/multicast/reserved/
   unspecified 时拒绝；
2. 将连接固定到已验证地址，并保留原 hostname 做 TLS SNI/证书与 Host 校验，或验证实际 peer IP；
   禁止“校验一次 DNS 后再由普通 client 重新解析”的 TOCTOU；
3. 默认禁用自动 redirect；若部署策略允许，最多 3 hop，每个新 URL 重做 scheme、DNS 和 peer 检查，
   且 credential 不跨 origin 转发；
4. 应用有界 connect/read timeout、response bytes 和并发限制，只有 2xx ACK delivery。

PostgreSQL pool、Outbox/Queue 和 HTTP client 由 Profile 统一关闭；Task/Push Service 和借用 pool 的
Port 不拥有 close。A2A Channel 可以额外调用 `A2ATaskService`、`A2ATaskCatalogService` 和
`A2APushService`，并读取 immutable `A2AAgentCardProvider`；其他 HTTP/SSE/Artifact Channel 仍只调用
主合同五个 Service。该白名单与上位合同、实施计划完全相同。

## 12. 文件 Owner

除实施计划原有文件外，主线批准新增：

- `channels/run_endpoint.py`：submit/command/get；
- `channels/service_wiring.py`：Service bundle 与 authenticator；取代含混的
  `session_wiring.py` 目标名；
- `distributed/a2a_models.py`、`a2a_protocols.py`、`a2a_services.py`：task/push application
  contract；
- `distributed/postgres/a2a.py`：binding 与 push config/operation 唯一 PostgreSQL truth adapter；
- `distributed/postgres/a2a_catalog.py`：tenant-scoped `ListTasks` join 与 keyset pagination；
- `distributed/postgres/a2a_delivery.py`：status transaction fanout 与 immutable delivery truth；
- `distributed/worker/a2a_push.py`：复用 Phase 6 delivery/ACK 的 push worker；
- `adapters/a2a/client.py`：async outbound HTTP adapter，不保存 truth。

实施计划中的 `adapters/a2a/push_postgres.py` 删除，不再是 owner；任何第二套 push PostgreSQL
Store、retry daemon 或同步 urllib client 都禁止进入新架构。

`asgi_router.py` 只做 method/path match 和 dispatch，不承载业务 DTO mapping。

## 13. 明确延期

- WebSocket、CLI、Team：Wave 5；
- A2A inbound URL fetch、malware scan、streaming Artifact upload：独立后续 Spec；
- live PostgreSQL/Redis failure injection、cross-worker probe、breaking legacy deletion、Public API
  cutover：Wave 6；
- OCR 永久不属于 AgentOS SDK。

## 14. 验收标准

- HTTP 八条 operation 的 success/error golden 全绿；
- JSON/header/multipart 大小与严格校验全绿；
- Artifact policy 与 read header 无 path/credential 泄漏；
- scoped cursor 不能跨 tenant/session/run 复用；
- 所有 LiveRunEvent、heartbeat、gap、terminal golden 全绿；
- subscription 六类退出路径都 `aclose()` exactly once，disconnect 不 cancel Run；
- A2A v1.0.1 ProtoJSON message/card、11 个 operation、version/extension 与 mapping matrix 全绿；
- `ListTasks` scoped keyset pagination、history/includeArtifacts 和 immutable extended card 全绿；
- task binding 幂等/冲突/跨 tenant、snapshot-first replay 和 push Outbox/fencing 全绿；
- push 保存期与每次发送/redirect 的 DNS、peer、credential SSRF 门禁全绿；
- Channel scope 只能来自 authenticator；
- Transport/Channel import architecture gates 全绿；
- targeted、full pytest、Ruff、compileall、module size 和 diff check 全绿；
- Spec Compliance 与 Code Quality Review 无 P0/P1。
