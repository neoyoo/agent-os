# AgentOS Phase 6 Wave 5 WebSocket / CLI / Team Contract Addendum

> 状态：合同已冻结；5A-5B 已完成，5C-5E 待实现
>
> 日期：2026-07-21
>
> 上位合同：`2026-07-17-agentos-phase6-distributed-runtime-transport-contract.md`
>
> 实施计划：`2026-07-17-agentos-distributed-runtime-transport-implementation-plan.md`

## 1. 目的与取舍

本增补冻结 Wave 5 的 WebSocket、CLI、Migration 和 Team Delivery 边界。上位合同只给出了
能力清单，没有冻结 wire schema、CLI host、Migration authority 和 Team-to-Run 路由；这些差异
会改变公共协议、持久状态和故障恢复，不能留给各支线自行决定。

本增补选择：

- WebSocket 是同一 Run Application Services 的有状态订阅 Adapter，不拥有 Run 真值；
- scoped run cursor 和安全事件投影提升为 wire-neutral `transports.run_stream`；
- CLI 通过受信任 factory 注入组合根，tenant 只作为 route hint；
- Migration Service 是 catalog/checksum/plan 的逻辑 authority，PostgreSQL MigrationPort 是
  advisory lock、ledger 和 DDL transaction 的唯一物理执行 Owner；
- Team message 是 agent-originated runtime fact，新 Run 使用 internal continuation start，绝不写成
  user `StoredMessage`；
- Team message commit、recipient delivery 和 Outbox 在 PostgreSQL 同一事务；
- Team Worker 只编排已有 Run Application Services，不运行 Agent，不拥有第二套 retry/daemon；
- OCR、跨 Session Artifact 授权、跨进程 Worker Admin Control 不进入 Wave 5。

旧 CLI、旧 Team Worker/Retry/Notice 路径是 breaking 删除对象，不增加 alias、shim、fallback 或
双写。

## 2. Shared Run Stream Contract

### 2.1 Owner

新增 `agentos.transports.run_stream`，唯一拥有：

- tenant/session/run-scoped public cursor 的 encode/decode；
- `LiveRunEvent` 到安全 JSON event object 的投影；
- `ReplayItem` 到完整 run stream payload 的投影；
- `StreamGap` payload；
- WAITING/completed/failed/cancelled terminal 判定。

SSE、WebSocket 和 A2A observer 必须复用该模块。A2A 只复用 live event object，继续使用 Wave 4
冻结的官方 `TaskStatusUpdateEvent` wrapper；SSE/WS 使用完整 run stream payload。现有
`transports.sse.cursors` 与 SSE 私有事件
投影在同一 Wave 原子迁移并删除，不保留 re-export。Redis raw position 不出 wire。

cursor 继续使用 canonical base64url JSON，固定包含 `version`、scope digest 和 `position`；scope
digest 绑定 tenant/session/run。非 canonical padding、重复 JSON key、未知字段、跨 scope token、
超长 token 和非法 Redis position 全部拒绝。

### 2.2 Event Payload

公共 event payload 固定为：

```json
{
  "session_id": "session_1",
  "run_id": "run_1",
  "turn_id": "turn_1",
  "execution_attempt": 1,
  "event_sequence": 3,
  "occurred_at": "2026-07-21T10:00:00.000000Z",
  "event": {"index": 0, "text": "..."}
}
```

`project_live_event` 的独立 object 包含 `kind`；`project_replay_item` 为保持既有 SSE bytes，在
envelope `event` 内只放 kind-specific fields，kind 由调用方通过同一投影结果单独承载。kind 使用
既有 `live_event_kind` allowlist；thinking、Prompt、Tool arguments、tenant、内部 exception 和
storage identity 不进入 payload。序列化必须 UTF-8、`sort_keys=True`、compact、
`allow_nan=False`。

Redis append 前的 canonical run event JSON hard max 为 `256 KiB`。Provider content adapter 必须在
产生 live event 前切分过大的 content delta。超限 `LiveFinalResult` 发生在 COMPLETED 已提交之后，
因此省略该 Redis event、保持 Run COMPLETED、继续发送 `turn_completed`；客户端从 PostgreSQL Run
Read Model 获取完整结果。其他在 terminal commit 前产生的超限 event 才使当前执行以脱敏 protocol
failure 失败。不得写入任何 Transport 无法消费的 event。必须覆盖“超大 final result 仍 COMPLETED、
Redis 无超限 item、terminal 可观察、read model 内容完整”。SSE/A2A golden、cursor token 和既有
wire 必须在迁移前后 byte-for-byte 不变。

pre-terminal event 超限由 Worker 在 Redis append 前拒绝，并通过 `AgentStream` 创建时注入的内部
failure control 回到当前 `RunDriver`。该 control 不是 stable Public API，不得导出，也不得由
Channel、Transport 或普通 SDK consumer 调用。它只允许当前唯一 stream consumer 在一次 event 已经
返回、下一次 event 尚未推进时提交一个 `Exception`；`RunDriver` 使用当前 execution guard 提交
FAILED checkpoint/Run，再返回同一异常对象的唯一 `TurnStreamFailed`。projection、observability 和
sync-work wrapper 不得通过跨层 async-generator `athrow` 转发失败。Run 已由外部命令终结或当前
fence 已失效时不得覆盖权威状态，也不得伪造 `turn_failed`。

RunDriver terminal commit 清除 claim 后，到 Worker 成功 append terminal event 前允许 heartbeat 与
执行短暂竞态。heartbeat 失败时 Worker 必须重新解析该 outbox 的 PostgreSQL 权威 Run：若 Run 已为
WAITING/COMPLETED/FAILED/CANCELLED，则允许当前 execution 仅完成已提交 outcome 的 event 投影、stream
cleanup 和 Event Sink append；全部成功后才 ACK。此路径不得启动新的 Provider/Tool 工作。若权威
Run 仍为非终态，则按真正 claim/fence 丢失处理，立即关闭 stream 且不 ACK。Event Sink、stream
cleanup 或权威复核失败仍不 ACK，不能用已提交终态掩盖观察面失败。
cleanup 自身抛出的 `CancelledError` 属于 cleanup 未完成，不得与 Worker 主动停止 execution 的取消混同；
严格 recovery 路径必须将其作为可观察失败处理并保留原始异常链。

上述复核不能只读取 Run 当前状态。`ExecutionClaimPort` 必须按原 execution `outbox_id` 查询内部 typed
`CommittedExecutionOutcome`，将该 delivery 确定性关联到已 committed accepted input、它的 checkpoint
和当前 Run。checkpoint 的 `turn_id`、`fencing_token`、`aggregate_version` 与 `created_at` 分别提供
terminal envelope 的 turn、execution attempt、commit version 与稳定发生时间。若 committed version
等于当前 Run version，Worker 必须发布该 outcome；若它小于当前 version，说明该 delivery 已被后续
execution 覆盖，只 ACK，不得用旧 outbox claim 当前 accepted input。不存在关联 outcome、关联损坏或
版本倒退时 fail closed 且不 ACK。

Worker 产生的 terminal live event 固定使用保留 `event_sequence=9007199254740991`（`2^53-1`，JSON
safe integer 最大值）；普通 live event 必须使用更小的 sequence。正常 terminal 发布与 recovery 都从
同一个 committed outcome 构造完全相同的 envelope。Redis Event Sink 必须以
`(tenant_id, run_id, execution_attempt, event_sequence)` 为稳定 identity，在单个 Lua 原子边界内查找
或 `XADD`；已存在且 envelope 不同必须 fail closed，已被 stream trim 的 terminal 可以重新追加。
幂等索引不得使用脱离 replay retention 的无界旁路 key。外部 CANCELLED 产生的新 fence 不属于旧
execution：旧 stream 必须立即关闭，不得继续 Provider/Tool 或发布旧 attempt terminal；Worker 可以从
新 fence 的 committed outcome 发布 `turn_cancelled`，成功 cleanup/append 后才 ACK。

terminal recovery 不得绕过 Session Lease。初次 resolve 或 claim-none 发现 current committed outcome
时，Worker 必须先取得并确认该 Session Lease，避免旧 Worker 在 recovery terminal 之后继续追加旧
attempt event；无法取得 Lease 时 no-ACK。任何 heartbeat 失败都先取消 execution 并等待 stream cleanup，
不得因为同 fence terminal 已提交而继续消费 generator；随后只有在 committed outcome 可验证且当前
Lease 仍归本 Worker 所有时才能 ensure canonical terminal。superseded outcome 不发布旧 terminal。

expired-claim `recover` outbox 必须在 payload 中持久化 accepted `turn_id`、recovery 前置
`fencing_token` 和可复核的 recovery identity。claim 时在 Session/Run 锁内同时校验 turn、当前
recovery fence 与 outbox identity；committed outcome query 也按该 turn binding 关联 checkpoint。
submission/command outbox 继续按原 source identity 关联，不能用同一哈希规则误拒绝 recover delivery。

## 3. WebSocket Contract

### 3.1 Handshake

- path：`/v1/ws`；
- 必选 subprotocol：`agentos.run.v1`；
- 只接受 UTF-8 text JSON，binary 使用 close `1003`；
- 单个 inbound frame hard max `256 KiB`，超限 close `1009`；
- tenant、principal、credential 只来自 handshake headers，query/frame 不得携带；
- `ChannelAuthenticator` 在 accept 前执行一次连接认证；每个资源操作再使用相同 headers 和
  resource-aware `ChannelAuthContext` 授权，返回 scope 必须与 handshake scope 完全相同；
- Origin 校验属于生产 `ChannelAuthenticator` 的 handshake 职责；默认 authenticator fail closed；
- handshake auth/subprotocol 失败时不得发送 `websocket.accept`。portable ASGI baseline 只发送
  pre-accept `websocket.close`，不承诺客户端可见 private close code；若 host 支持
  `websocket.http.response` 扩展，可分别返回 401/403/426，但该扩展不是 SDK 正确性的前提。

状态机：

```text
CONNECTING -> AUTHENTICATED -> OPEN -> CLOSING -> CLOSED
subscription: SUBSCRIBING -> ACTIVE -> CLOSED
```

### 3.2 Client Frames

所有 object 拒绝重复 key、未知字段、NaN/Infinity、bool-as-int 和控制字符。`request_id` 必填，
同时是 write idempotency identity。

```json
{"type":"submit_run","request_id":"req_1","session_id":"s1","content":"...","artifact_handles":[]}
{"type":"submit_command","request_id":"req_2","session_id":"s1","run_id":"r1","kind":"cancel","payload":{}}
{"type":"subscribe_run","request_id":"req_3","session_id":"s1","run_id":"r1","cursor":null}
{"type":"unsubscribe_run","request_id":"req_4","session_id":"s1","run_id":"r1"}
```

- `submit_run.request_id -> RunSubmission.submission_id`；
- `submit_command.request_id -> DurableRunCommand.command_id`；
- subscription identity 固定为 `(session_id, run_id)`；
- duplicate active subscribe 返回 `subscription_exists`，不替换旧 subscription；
- unsubscribe 幂等，即使不存在也返回 receipt；
- 显式 `submit_command(kind="cancel")` 是唯一 cancel 路径。

### 3.3 Server Frames

```json
{"type":"receipt","request_id":"req_1","operation":"submit_run","data":{"session_id":"s1","run_id":"r1","submission_id":"req_1","aggregate_version":1,"duplicate":false}}
{"type":"receipt","request_id":"req_2","operation":"submit_command","data":{"run_id":"r1","command_id":"req_2","kind":"cancel","aggregate_version":2,"duplicate":false}}
{"type":"receipt","request_id":"req_3","operation":"subscribe_run","data":{"session_id":"s1","run_id":"r1"}}
{"type":"receipt","request_id":"req_4","operation":"unsubscribe_run","data":{"session_id":"s1","run_id":"r1"}}
{"type":"event","session_id":"s1","run_id":"r1","cursor":"...","event_kind":"content_delta","data":{}}
{"type":"stream_gap","session_id":"s1","run_id":"r1","reason":"trimmed"}
{"type":"error","request_id":"req_1","code":"invalid_request","message":"invalid request"}
```

`event.data` 必须等于 Shared Run Stream 完整 replay payload，`event_kind` 必须来自生成该 payload
的同一次 Shared Run Stream allowlisted projection，WebSocket Channel 不得自行填写或推断。无法可信
解析 request ID 时 `error.request_id`
固定为 `null`，不省略。后台 subscription error 增加 `session_id`、`run_id` 且 request ID 为 null；
request-bound error 不带伪造 resource 字段。

subscribe receipt 必须先于该 subscription 的任何 event。每个 subscription 保序，不承诺不同
subscription 之间的全局顺序。WAITING/completed/failed/cancelled 或 gap 在成功发送对应 frame 后
只关闭该 subscription，连接继续。

领域错误只发送稳定 `error` 并保持连接；坏 JSON/未知 type/非法字段发送 `invalid_request`，连续
协议违规不引入隐藏计数策略，当前帧处理后保持连接。binary、oversize、握手失败、slow consumer
和内部连接一致性错误关闭连接。

### 3.4 Backpressure 与 Close

默认和 hard max：

| 项 | 默认 | hard max |
|---|---:|---:|
| active subscriptions | 32 | 128 |
| inbound frame bytes | 256 KiB | 256 KiB |
| connection queued frames | 256 | 2048 |
| connection queued bytes | 4 MiB | 16 MiB |
| one subscription queued frames | 64 | 512 |
| one subscription queued bytes | 512 KiB | 2 MiB |

同时限制 frame count 和 UTF-8 bytes；禁止无界 `asyncio.Queue`。configured connection limit
不得小于 configured per-subscription limit，default 不得超过 hard max。单帧允许占用 configured
per-subscription byte budget；Shared Run Stream 上游 hard max 保证它不会超过 hard max。普通
overflow：停止所有 replay
读取，关闭全部 subscriptions，优先控制通道发送
`error(code="slow_consumer", resume_cursors=[...])`，随后 close `4408`。数组项固定为
`{"session_id","run_id","cursor"}`；没有成功发送 cursor 的 subscription 使用 `cursor:null`，
按 session_id/run_id 排序。

`resume_cursors` 只记录 `await websocket.send()` 已成功返回的最后 cursor，不能记录已读取或仅
入队 cursor。控制 frame 发送失败仍必须 close 并 exactly-once 释放全部 subscription。

accept 后 close codes：`1000` normal、`1001` shutdown、`1003` binary、`1009` inbound too large、
`4400` protocol violation、`4408` slow consumer、`1011` internal connection failure。`4401/4403/4406`
不用于 portable pre-accept denial。

disconnect 只释放 connection/subscriptions，绝不提交 Run cancel。

## 4. CLI Composition Contract

### 4.1 Factory 与 Scope

全局入口固定：

```text
--factory package.module:create_cli_application
AGENTOS_CLI_FACTORY=package.module:create_cli_application
```

显式参数优先。`module:callable` 在成功 parse 且命令不是 help/init 后才 import；callable 必须零参数
返回一个无 I/O `CliHostFactory`。测试传入的 `host_factory` 对象直接替代 import/call。该 factory
按 command 返回最窄 async context：

- run/artifact：`open_service_host()`；
- serve：`open_server_host()`；
- worker：`open_worker_host()`；
- relay：`open_relay_host()`；
- migrate：`open_migration_host()`，只连接 PostgreSQL migration adapter。

parse/help/init 不加载 factory。Migration 不打开 DistributedRuntimeProfile、Redis、BlobStore、
Worker 或 Provider builder；其他命令也不获取无关资源。CLI 不新增 TOML runtime 配置，不构造
PostgreSQL/Redis/Uvicorn client。`main(argv, host_factory=...)` 只作为测试注入 seam。

CLI 不复用绑定 HTTP 的 `ChannelAuthenticator`。`CliOperation` 固定为 `run_submit`、
`run_command`、`side_effect_resolve`、`run_query`、`run_watch`、`artifact_upload`、
`artifact_list`、`artifact_read`、`artifact_delete`。`CliResource` 固定字段为 `session_id`、可选
`run_id`、可选 `artifact_id`；字段组合必须与 operation 精确匹配。`resolve_side_effect` 必须使用
distinct `side_effect_resolve`，不能退化成普通 run_command 授权。
`CliScopeResolver.resolve(operation, resource, tenant_hint)` 成功同时
代表 authenticated + authorized，并从 factory 所属部署身份生成 `RequestScope`。任何拒绝发生在
Service 调用前。`--tenant` 只是 route hint；禁止 `--principal`，principal 不得来自 payload 或自由
环境变量。`init/migrate/serve/worker/relay` 是 deployment operation，不伪造 tenant
`RequestScope`。Side-effect resolution 仍需 host 的额外授权策略，tenant 相同不等于有权 resolve。

### 4.2 Canonical Command Tree

```text
agent-os init PATH
agent-os run submit --tenant --session-id --submission-id (--content|--content-file|--stdin) [--artifact HANDLE]...
agent-os run command --tenant --session-id --run-id --command-id --kind [--payload-json|--payload-file]
agent-os run get --tenant --session-id --run-id
agent-os run watch --tenant --session-id --run-id [--cursor]
agent-os artifact upload --tenant --session-id --upload-id --file --media-type
agent-os artifact list --tenant --session-id [--cursor] [--limit]
agent-os artifact read --tenant --session-id --artifact-id --output PATH|-
agent-os artifact delete --tenant --session-id --artifact-id --deletion-id
agent-os worker start [--drain-timeout SECONDS]
agent-os relay start [--idle-interval SECONDS]
agent-os serve
agent-os migrate [--check]
```

所有幂等 ID 由调用者提供，CLI 不随机生成。hitl_answer/resolve_side_effect payload 必填并走
canonical parser；cancel/resume/wakeup/retry 缺失 payload 归一化为空 object。
`worker start` 在 SIGINT/SIGTERM 后 drain 当前进程 Worker 再 close；没有 WorkerControlPort，因此不提供
独立 `worker drain`。`serve` 只运行 ingress，`worker start` 只运行 Worker，`relay start` 只循环
`relay_once`；三者不暗中互相启动。

`DistributedWorker` 新增 `wait()`：receiver 正常/异常结束均唤醒，异常原样传播。`WorkerHost` 先
start，再并发等待 signal 与 `worker.wait()`；signal 胜出时取消 wait observer、drain(timeout)、
close；worker failure/意外正常停止胜出时不 drain，立即 close 并传播 failure/稳定 unexpected-stop
错误；所有路径 close exactly once。

`RelayHost` 在 active `relay_once` 时收到 signal 不取消该 batch，等待其完成后 close；idle wait 时
收到 signal 取消 idle timer 后 close；`relay_once` error 立即 close 并原样传播，绝不继续 idle；
signal handler 和 pending timer 在 finally 清理。

### 4.3 Output 与 Exit

- unary success：stdout 一行 compact/sorted UTF-8 JSON；
- watch：stdout JSONL，使用 Shared Run Stream payload；不输出 heartbeat；WAITING/terminal/gap 后
  exactly-once close；
- artifact read 到 path：临时文件 + atomic replace，成功后 stdout metadata；
- artifact read 到 `-`：stdout 仅 raw bytes，不混入 JSON/progress；
- error：stderr 一行 `{code,message}`，stdout 为空；不回显 DSN、token、payload、path、SQL 或
  Adapter exception。

exit：`0` success、`2` usage/validation、`3` auth/permission、`4` not-found、`5`
conflict/state/not-due/migration-required、`6` backend unavailable、`130` interrupted、`1` internal。

旧 `agent-os run APP`、直接 Uvicorn、直接同步 psycopg、`--dsn`、`--dry-run`、migration glob 和
缺 DSN 自动退化全部删除。

## 5. Migration Authority

新增独立 `MigrationEntry`、`MigrationPlan`、`MigrationReport`、`DistributedMigrationPort`、
`DistributedMigrationService` 和 PostgreSQL Adapter leaf。

- Migration Service 拥有显式 ordered catalog、checksum policy 和 plan；
- PostgreSQL MigrationPort 在单次 `apply(plan)` 调用内拥有 advisory lock、ledger read/write 和物理
  transaction；整个缺失版本序列使用一个 transaction，任一版本失败则全部回滚；
- catalog 是显式、不可变的 canonical migration 列表，不按文件 glob 猜测；当前目标显式包含
  version 1 `2026-07-20-postgres-distributed-runtime.sql` 和 Wave 5 Team migration version 2；
- ledger 固定为 `agentos_schema_migrations(version INTEGER PRIMARY KEY, name TEXT UNIQUE,
  sha256 CHAR(64), applied_at TIMESTAMPTZ)`；
- checksum 对 packaged SQL resource bytes 先 canonicalize CRLF/CR 为 LF，再取 exact
  `-- migrate:up\n` 与 `-- migrate:down\n` 之间 bytes 的 SHA-256 lowercase hex；
- `SCHEMA_STATEMENTS` 不再是 distributed schema authority，只允许测试从 migration catalog 派生；
- `migrate` 在单一锁/transaction 内应用未执行版本并记录 checksum；
- 已记录版本 checksum 不一致、数据库版本高于 SDK、缺口或事务失败全部 fail closed；
- `migrate --check` 只比较并验证 exact target，不执行 DDL；
- `MigrationReport` 固定包含 `current_version`、`target_version`、`applied_versions`、`changed`；
- `DistributedRuntimeProfile.open()` 不执行 DDL，通过 MigrationPort 的 read-only compatibility check
  验证 exact target schema，缺失时抛稳定 `SchemaMigrationRequiredError`，不拥有版本写权限；
- 所有 PostgreSQL I/O 原生 async，不使用线程包装或同步 psycopg。

当前未生产，旧 `agentos_distributed_schema` 无 checksum marker 不 backfill、不兼容；检测到后抛
`LegacyDistributedSchemaError`，迁移文档要求清空并由 canonical Migration Service 重建。

apply-only bootstrap 顺序固定：事务开始 -> advisory lock -> 探测 ledger/legacy marker；若 ledger
不存在且 legacy marker 存在则失败，若两者都不存在才创建 ledger，然后应用 catalog。check/profile
永不创建 ledger；ledger 缺失且无 legacy marker 返回 migration-required。实现前必须从 canonical
v1 SQL 和 `SCHEMA_STATEMENTS` 删除 `agentos_distributed_schema` 创建/写入；fresh v1 只创建 ledger
记录。version 2 resource 固定为 `2026-07-21-postgres-team-delivery.sql`。ledger 与 legacy marker
不允许共存，共存一律 fail closed。

Migration 是 deployment authority，不使用 tenant `RequestScope`。授权由受信任 `CliHostFactory`
factory/部署边界完成。

## 6. Canonical Team Domain

### 6.1 保留与删除

新 `team_types/team_ports/team_runtime/team_tools/team_in_memory` 保留：Team/Member/Message、
active/deleted、leader/worker、成员可见性、广播排除 sender、leader 管理、默认拒绝管理 Tool、
sender 不可 spoof、workspace/capability narrowing，以及 async `team_create/agent_create/team_say/
team_read_messages/team_delete`。

新 API 全 async，所有 Port 操作显式接收 `RequestScope`。可投递 member 必须绑定非空
`target_session_id`。时间使用 UTC `datetime`，不使用 float wall clock。TeamMessage 不包含
`artifact_handles`；Wave 5 禁止跨 Session Artifact 传递，未来只能通过显式复制或授权合同增加。

删除对象：旧 TeamWorkerRunner/Daemon/AgentProvider/SessionProvider、Retry/Cancellation Store、
RunResult/Error、LocalTeamWakeupTrigger、distributed TeamNotice、Postgres Team retry/cancel/UI store、
Redis full message envelope、Team LocalContinuationInput 和旧 DistributedTeamRuntimeProfile。旧
`multi/team.py`、同步 `multi/postgres_team.py` 在消费者切换后删除，不留 facade。

### 6.2 Internal Team Start

Team message 是 runtime fact，不能写成 user StoredMessage。主线新增：

- `distributed/internal_models.py`：`InternalRunSubmission`、`InternalSubmissionAuthority`；
- `distributed/internal_errors.py`：非 public `StaleInternalSubmissionAuthorityError`；
- `distributed/internal_protocols.py`：非 public `InternalRunSubmissionPort.submit_internal`；
- `distributed/internal_services.py`：只注入 Team Worker 的 `InternalRunSubmissionService`；
- `runtime/execution.py`：`AcceptedInternalStartInput` 加入 canonical `AcceptedTurnInput` union。

这些名称不从根、`distributed.__init__`、ChannelServices、CLI、HTTP/WS/A2A facade 导出。Service
调用必须携带当前 `InternalSubmissionAuthority(delivery_id, claim_id, fence)`；PostgreSQL Port 在
提交事务内重新锁定 TeamDelivery，并以 database time 原子验证 tenant、target_session、recipient、
`state == CLAIMED`、claim_id、fence 且 `expires_at > database_now` 后才允许创建 Run。release 后的
`PENDING`、已过期但尚未 takeover、APPLIED/REJECTED 或 claim/fence 不匹配统一抛出 typed
`StaleInternalSubmissionAuthorityError`，事务不得创建或修改 Run。wire payload 无法构造 authority。

`InternalRunSubmission` 固定字段为 `session_id`、`submission_id`、
`source_kind=team_message` 和 frozen `source_payload`，payload 只允许：

```json
{
  "team_id":"team_1",
  "message_id":"msg_1",
  "recipient_agent_id":"agent_2",
  "action":"team_read_messages"
}
```

`AcceptedInternalStartInput` 的字段精确冻结为：

```python
@dataclass(frozen=True, slots=True)
class AcceptedInternalStartInput:
    run_id: str
    submission_id: str
    source_kind: Literal["team_message"]
    source_payload: FrozenJsonObject
    turn_id: str
```

持久化 accepted input 的 discriminator 固定为 `input_kind="internal_start"`；它没有
`user_message_id` 字段。`turn_id` 在 acceptance 事务中确定性分配并与 canonical `source_payload`、
submission identity 一起持久化。`AcceptedTurnInput` 精确扩为 `AcceptedStartInput |
AcceptedContinuationInput | AcceptedInternalStartInput`，并沿用同一 claim、RunWriteGuard、
`CREATED -> QUEUED -> RUNNING`、running cursor、checkpoint 和 terminal 原子合同。

执行分支固定如下：

- `ApplyAcceptedInput + AcceptedInternalStartInput` 调用专用
  `prepare_internal_start_turn(input)`：以持久化 `turn_id` 创建 continuation Turn，从 canonical
  payload 构建一次 ephemeral `continuation-data`，只发布一次 `TurnStartedEvent` 和
  `TurnStreamStarted`（后者投影为 `LiveTurnStarted`）；
- `RestoreAcceptedTurn + AcceptedInternalStartInput` 调用专用
  `restore_internal_start_turn(input, cursor)`：验证 cursor 属于同一 turn，并从同一 canonical payload
  重建字节等价的 ephemeral `continuation-data`，但不再发布首次 Turn/stream event；
- 两条路径都不得追加 `StoredMessage`、发布 `UserMessageAppendedEvent` 或递增/重新分配 turn identity。

因此“恢复不重复”只约束首次事件和持久副作用，不约束当前 claim 所需的临时 Context Plane 重建；
每次 restore 必须重建投影，且单个 claim 内以 replace 语义挂载，不能重复追加。

首次 Turn 不追加 StoredMessage；Context Plane 投影为：

```xml
<continuation-data protocol="agentos.continuation" version="1.0"
    origin="runtime" authority="context-data" persistence="ephemeral"
    visibility="internal" source="internal-start" kind="team_message">
  <payload-json>{...canonical JSON...}</payload-json>
</continuation-data>
```

正文仍在 Team PostgreSQL truth，由目标 agent 使用 owner-scoped `team_read_messages` 读取。该投影不
拥有指令 authority，前端 StoredMessage 查询不会显示伪用户问题。

PostgreSQL Team member 是 `target_session_id -> recipient_agent_id` 的唯一权威绑定。每一次 Run
Worker claim 都由 `WorkerAgentFactory.hydrate(claimed=...)` 重新查询该 PostgreSQL binding；范围包括
internal-start 首次 apply、`RestoreAcceptedTurn` 和 team wakeup continuation，禁止复用 TeamDelivery
claim 时创建的对象或从 Redis/delivery payload 延续 authority。Factory 只把 accepted payload 中的
team/session/recipient 当作 lookup 与一致性校验输入，随后从当前 active binding 创建非
Provider-visible `TeamAccessContext(tenant_id, team_id, recipient_agent_id, target_session_id)`，并注入
claim-scoped TeamTools。`team_read_messages` 的 owner 只能取自该 context，不能取 internal payload、
模型参数或 Redis。binding 缺失、已删除或与 accepted payload 不一致时 typed fail closed，不创建
无 owner authority 的 Agent，也不执行 Team Tool。

为保证 accepted Run 必然收敛，Team member binding 删除和 `team_delete` 事务必须先按
`target_session_id` canonical 顺序锁定相关 binding 与 Session，并拒绝任何仍有非终态 active Run 的
binding，返回 typed `TeamActiveRunConflictError`；调用方必须先取消或等待 Run terminal。internal
submission 事务使用同一 binding -> Session 锁序，因此 deletion 与 Run acceptance 不存在竞态。
尚未创建 Run 的 PENDING/CLAIMED delivery 若发现 binding 已撤销，稳定提交
`REJECTED_BINDING_REVOKED` 与 Team UI/result Outbox 后 ACK，不进入 reclaim 循环。

internal-start 与 wakeup 的 payload 完全相同，正文不进入 command。payload canonical JSON 使用
compact/sorted/`allow_nan=False`，UTF-8 hard max `4 KiB`，Context XML 使用既有严格 XML escape；
两条路径必须有 golden equivalence test。

### 6.3 WAITING Matcher

新增 active-run-by-session Application Query。Team 只在以下条件全部满足时提交 `wakeup`：

- active Run 状态为 WAITING；
- wait kind 是 `remote_result` 或 `resource_availability`；
- TeamMessage `correlation_id` 非空且与 `WaitReason.handle` 完全相等。

Team 不触发 timer/retry/human_input/side_effect_reconciliation。不匹配 WAITING、CREATED、QUEUED 或
RUNNING 持久记为 `REJECTED_NONTERMINAL` 并 ACK；不得绕过 active-run 约束创建新 Run。无 active
Run 才提交 deterministic internal start。

active-run query 是跨事务 saga。每个 claim 最多执行两次 route evaluation：首次 internal submit
遇到 `ActiveRunConflictError`，或 wakeup 遇到 `CommandStateError/CommandNotDueError` 时重新读取一次
active Run。第二次若得到可执行路径则用同一 deterministic identity 调用；若状态再次变化则 release
claim 且不 ACK，交由 takeover 重算。durable reject 必须记录 observed run_id、aggregate_version 和
status 作为线性化证据；不能基于过期 read model 静默 reject。

## 7. Team Delivery Transaction 与 Fence

`team_say` 的单一 PostgreSQL 事务：

1. 校验 scope、active team、sender membership 和 recipient membership；
2. 插入 canonical TeamMessage；
3. 为每个 recipient 插入独立 `TeamDelivery(PENDING)`；
4. 插入 deterministic Outbox；
5. commit 后由既有 Relay 发布，Redis 只携带 `outbox_id`。

Team message ID 由 trusted Tool invocation `operation_id`、权威 sender 和 team scope 派生；
`team_say` schema 不允许模型传入或覆盖 sender/message_id。非 Tool Application caller 必须显式提供
idempotency operation ID。唯一算法 Owner 是 `multi/team_identity.py`，Team Domain、PostgreSQL 和
Worker 必须调用该 helper，禁止各自复制 hashing 逻辑。

canonical bytes 固定为 `json.dumps(value, ensure_ascii=False, sort_keys=True,
separators=(",", ":"), allow_nan=False).encode("utf-8")`；ID digest 固定为
`sha256(domain_label.encode("ascii") + b"\x00" + canonical_bytes).hexdigest()`，不截断，输出为
`prefix + digest`。domain/prefix 精确映射：

| identity | domain label | prefix |
|---|---|---|
| message | `agentos.team.message.v1` | `team_msg_` |
| delivery | `agentos.team.delivery.v1` | `team_delivery_` |
| submission | `agentos.team.submission.v1` | `team_submission_` |
| command | `agentos.team.command.v1` | `team_command_` |
| outbox | `agentos.team.outbox.v1` | `team_outbox_` |

各 identity 的 canonical input 精确冻结：

- message：`{version:1, tenant_id, team_id, sender_agent_id, operation_id}`；
- delivery：`{version:1, tenant_id, team_id, message_id, recipient_agent_id,
  target_session_id}`；
- submission：`{version:1, tenant_id, delivery_id, target_session_id}`；
- command：`{version:1, tenant_id, delivery_id, target_session_id, run_id,
  command_kind:"wakeup"}`；
- outbox：`{version:1, tenant_id, delivery_id, outbox_kind}`，其中 `outbox_kind` 只允许
  `delivery_ready` 或 `delivery_result`。

原始 addressing 精确表示为 `addressing_kind="direct" | "broadcast"` 和
`addressed_agent_id`；direct 必须是非空目标 agent，broadcast 必须为 null。message request digest 的
canonical input 固定为 `{version:1, tenant_id, team_id, message_id, sender_agent_id, message_kind,
content, correlation_id, addressing_kind, addressed_agent_id}`。per-recipient delivery source digest 在
同一 object 上增加 `recipient_agent_id, target_session_id`。两者都使用同一 canonical bytes 后直接
SHA-256 lowercase hex。

首次 `team_say` 事务按当前 active membership 解析 recipient，并把按 `recipient_agent_id` 排序的
`recipient_snapshot` 与每个 `target_session_id` 持久化到 TeamMessage/Delivery；broadcast 排除 sender。
duplicate operation 必须先读取既有 TeamMessage、比较完整 message request digest，再直接复用首次
recipient snapshot 和既有 deliveries，绝不重新扫描 membership 或给后来加入的 member 增补 delivery。
同 operation 的 sender、direct/broadcast、direct target 或正文语义任一变化都必须 conflict，不能当作
duplicate。固定测试向量必须跨 Team Domain/PostgreSQL/Worker 三个 Owner 得到相同 ID 与 digest。

Team Worker：

```text
Queue receive/reclaim
-> PostgreSQL resolve outbox + authoritative tenant/team/session
-> PENDING/expired CLAIMED -> CLAIMED(claim_id, monotonic fence, DB expiry)
-> active-run query
-> InternalRunSubmission 或 wakeup command，或 durable reject
-> fenced APPLIED/REJECTED + Team UI/result Outbox transaction
-> Redis ACK
```

状态机固定：`PENDING -> CLAIMED -> APPLIED | REJECTED`。expired CLAIMED takeover 保持 CLAIMED、
替换 claim_id、`fence += 1`；owned release 回到 PENDING 且保留单调 fence。heartbeat 间隔不大于
TTL/3，使用 database time 延长 expiry。heartbeat 失权后 runner 立即停止启动新的 Application
Service 调用；已开始的调用允许完成，但不得用旧 fence 写 Team result，也不得 ACK，后继 takeover
依赖 Run Service 幂等收敛。

APPLIED/REJECTED duplicate 直接 ACK。所有 claim/heartbeat/release/takeover/result 比较 claim_id +
fence 并使用 database time；heartbeat 与 result commit 还必须原子验证 `state == CLAIMED` 且
`expires_at > database_now`。claim 自然过期但尚未 takeover 时，旧 runner 的 result commit 必须抛出
typed stale/fenced failure，不写 Team result、不写 UI/result Outbox 且不 ACK。Team fence 只保护
TeamDelivery，绝不冒充 RunWriteGuard。

Run receipt 成功后、Team result commit 前崩溃时，takeover 使用同一 deterministic ID 获得 duplicate
receipt 后收敛；PostgreSQL、Application Service 或 result commit 失败时不 ACK。不得新增 Team retry
store/daemon；pending reclaim 是唯一重投机制。

Team result 与 Team UI/result Outbox 同事务。新增 typed `TeamEventEnvelope/TeamEventReplayPort`；
PostgreSQL 是事件真值，Redis 只做有界 replay/tail。Run `EventReplayPort` 强绑定 run scope，禁止塞入
Team event 或维护进程内 UI list。

`DistributedWorker` 抽取窄 `DeliveryRunner` Protocol（`heartbeat_interval`、`claim_ttl`、
`run_delivery`），Run 和 Team runner 共用同一 Supervisor，不复制 daemon。

Worker 内部 principal 是部署注入的固定 service identity；tenant 只能来自 PostgreSQL-resolved
TeamDelivery，不能来自 Redis payload、TeamMessage metadata 或 Tool arguments。

## 8. 文件与实施顺序

Wave 5 分为：

1. 5A Shared Run Stream + WebSocket wire/channel/ASGI；
2. 5B Migration authority + CLI factory/commands；
3. 5C Team types/ports/in-memory；
4. 5D Team PostgreSQL/Redis/Worker + internal start；
5. 5E facade/inventory/cutover 和旧 Team/CLI 删除；
6. 全量门禁与两层只读 Review。

生产文件 300 行触发职责检查，500 行必须拆分。测试按 wire、auth、commands、lifecycle、
backpressure、transaction、fence 和 crash window 拆分，禁止用 sleep 构造竞态。

## 9. 验收门禁

- WebSocket exact JSON/golden（覆盖全部 `LiveRunEvent` kind，并断言顶层 `event_kind` 与
  `data` 来自同一次 Shared Run Stream projection）、auth、multi-run、gap、terminal、disconnect、
  cancel、overflow、close；
- pre-terminal event 超限必须由内部 failure control 提交 FAILED 并发布 `turn_failed`；observability
  wrapper 不改变原始异常且确定性关闭内层 generator；terminal commit 后 heartbeat 失败不得吞掉
  terminal event，非终态 stale fence 仍 no-ACK，竞态测试必须使用 Event/Barrier；
- CLI parser/scope/DTO/output/error/signal lifecycle/import boundary；
- migration advisory lock、checksum、version gap、check/apply、profile no-DDL；
- Team scope/membership/idempotency/fanout、sender 与 direct/broadcast identity 冲突、broadcast retry
  在 membership 变化后复用首次 recipient snapshot、并发相同 operation 返回同一 duplicate、全部
  identity 固定测试向量、WAITING matcher；
- internal-start apply/restore 的投影字节等价、apply-only 首次 event、两条路径均无 StoredMessage 和
  `UserMessageAppendedEvent`；
- InternalSubmissionAuthority 对 expired/released/stale fence fail closed 且不创建 Run；
- `TeamAccessContext` 在 internal-start apply/restore/wakeup 的每次 Worker claim 都从 PostgreSQL active
  binding 重建，payload/Redis 不得提供 owner authority；
- active Run 期间 binding/team 删除返回 `TeamActiveRunConflictError`；pending delivery 的 binding 已
  撤销时稳定 REJECTED 并 ACK；
- Team claim/takeover/stale fence、claim 自然过期但未 takeover 时 result 零写入且 no-ACK、两次 route
  race 后 release 且 no-ACK、receipt-result crash、ACK-after-commit；
- Team UI typed replay、无 LocalContinuation/独立 retry/daemon；
- Base import 不加载 psycopg/redis/uvicorn；
- OCR 零新增；
- 全量 pytest、Architecture、Ruff、compileall、module size、public API 和 `git diff --check`；
- Spec Compliance 与 Code Quality/Security Review 分离，P0/P1 清零。
