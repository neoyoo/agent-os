# AgentOS 超大模块拆分计划

> 状态：已登记，等待目标阶段实施
>
> 日期：2026-07-11
>
> 上位规范：`docs/governance/agentos-engineering-standard.md`

## 1. 目的

Phase 0 的模块规模扫描发现下列本次触碰文件超过 800 行。Task 4 只删除未使用导入或修正既有异常变量绑定，净行数不增加，也不新增职责。实际拆分按既定架构阶段执行，不能在基线清理提交中混入行为迁移。

| 当前文件 | Phase 0 扫描行数 | 当前主要职责 | Owner | 目标 Phase |
|---|---:|---|---|---:|
| `src/agentos/channels/a2a_operations.py` | 4647 | A2A wire 类型、operation 路由、客户端与服务端映射 | Distributed Owner | 6 |
| `src/agentos/channels/a2a_conformance.py` | 1837 | A2A conformance 调用、报告和外部执行 | Distributed Owner | 6 |
| `src/agentos/channels/asgi.py` | 2201 | ASGI/HTTP 请求映射、SSE 和 session channel 接线 | Distributed Owner | 6 |
| `src/agentos/deployment.py` | 1461 | 部署校验、profile 证据与报告模型 | Quality/Release Owner | 6 |

## 2. Phase 6 可执行拆分任务

本节只登记 Phase 6 的实施顺序，所有任务在 Phase 0 均为 `deferred`。Phase 0 只执行第 4 节的 no-growth 门禁，不创建下列目标模块，也不迁移任何运行时行为。Phase 6 开始前必须先批准对应子系统 Spec，并把下列工作项展开成 TDD 实施计划；每个任务均先增加目标模块契约测试，再做纯迁移，最后收窄旧入口。

### 2.1 A2A transport workstream

源职责位于 `src/agentos/channels/a2a_operations.py` 和 `src/agentos/channels/a2a_conformance.py`。目标边界是：`agentos.transports.a2a` 只拥有 A2A wire 类型、序列化、协议映射和单次请求路由；push notification 的持久化与 worker 生命周期进入 Adapter；conformance 进入部署/测试边界，不反向依赖 Kernel 实现。

| ID | 源职责 | 具体目标模块文件 | 前置依赖与迁移顺序 | Owner | 旧 facade/兼容入口收口或删除门禁 | 目标验证命令 |
|---|---|---|---|---|---|---|
| A1 | `A2AMessagePart`、`A2AMessage`、`A2AArtifact`、`A2ATask`、stream event、operation request/response/error 等纯 wire 值类型 | `src/agentos/transports/__init__.py`；`src/agentos/transports/a2a/__init__.py`；`src/agentos/transports/a2a/wire_types.py` | **串行起点**。先建立不依赖 `channels`、Store、worker 或 concrete Adapter 的叶子类型；A2、A3、A4 均依赖 A1。 | Distributed Owner | `agentos.channels.a2a_operations` 在本步只允许从新模块显式 re-export，且对象 identity、签名和 `docs/public-api-inventory.json` 不变；不得删除旧入口。 | `python -m pytest tests/channels/test_a2a_operations.py tests/architecture/test_public_api.py -q`；`python -m ruff check src/agentos/transports src/agentos/channels/a2a_operations.py tests/channels/test_a2a_operations.py` |
| A2 | `*_to_dict`、`*_from_dict`、SSE event parsing、保留字段恢复及 JSON object 校验 | `src/agentos/transports/a2a/serialization.py`；`tests/transports/a2a/test_serialization.py` | **必须在 A1 后串行**，只依赖 `wire_types.py` 和稳定领域枚举；完成后 A3 才能迁移 mapping/router。 | Distributed Owner | 旧序列化函数保留显式 re-export，直至新路径 golden/round-trip/legacy payload 测试绿色，且 `rg -n "a2a_(message|artifact|task|operation).*_(to|from)_dict|parse_a2a_sse_events" src tests` 不再发现计划外旧实现。 | `python -m pytest tests/transports/a2a/test_serialization.py tests/channels/test_a2a_operations.py -q`；`python -m compileall -q src/agentos/transports/a2a tests/transports/a2a` |
| A3 | official operation 映射、版本/extension negotiation、领域 `TaskRecord` 到 A2A task 的映射、server/client 单次 operation 路由 | `src/agentos/transports/a2a/mapping.py`；`src/agentos/transports/a2a/router.py`；`src/agentos/transports/a2a/client.py`；`tests/transports/a2a/test_mapping.py`；`tests/transports/a2a/test_router.py` | **必须在 A2 后串行**。先迁纯 mapping，再迁 router，最后迁 client；Router 只调 Port/runner，不拥有 TaskStore、retry 或 push worker 生命周期。A5 依赖本步稳定的新路径。 | Distributed Owner | `A2AOperationServer`、`A2AOperationClient` 和历史 helper 在旧模块保留显式兼容导出；只有 inventory 已迁往新 canonical module、仓库调用方已切换、旧路径 compatibility test 明确批准弃用后，才允许删除旧实现。 | `python -m pytest tests/transports/a2a/test_mapping.py tests/transports/a2a/test_router.py tests/channels/test_a2a_operations.py tests/channels/test_a2a_server.py tests/channels/test_a2a_adapter.py -q`；`python -m ruff check src/agentos/transports/a2a src/agentos/channels/a2a_operations.py` |
| A4 | push notification config/policy、memory/PostgreSQL Store、delivery dispatcher、worker 和 daemon | `src/agentos/transports/a2a/push_types.py`；`src/agentos/adapters/a2a/push_memory.py`；`src/agentos/adapters/a2a/push_postgres.py`；`src/agentos/adapters/a2a/push_worker.py`；`tests/adapters/a2a/test_push_notifications.py` | A1 完成后可与 A2/A3 的后续工作**并行**；但内部必须按 pure types -> memory contract -> PostgreSQL adapter -> worker/daemon **串行**迁移。不得让 `transports.a2a` 导入 PostgreSQL client。 | Distributed Owner | 旧 push notification 名称在 `a2a_operations.py` 保持显式 re-export，直至 memory/PostgreSQL contract、daemon shutdown 和部署 profile 测试全部绿色；旧 Store/worker 实现必须通过 `rg -n "^class (PostgresA2A|InMemoryA2A|A2APushNotification)" src/agentos/channels/a2a_operations.py` 归零后才可删除 facade 内实现。 | `python -m pytest tests/adapters/a2a/test_push_notifications.py tests/channels/test_postgres_a2a_push_notifications.py tests/channels/test_a2a_push_notification_daemon.py tests/channels/test_a2a_egress_url_policy.py -q`；`python -m ruff check src/agentos/adapters/a2a src/agentos/transports/a2a/push_types.py` |
| A5 | conformance check/finding/report、external invocation plan/gates/runner、sample harness | `src/agentos/testing/a2a_conformance/types.py`；`src/agentos/testing/a2a_conformance/runner.py`；`src/agentos/testing/a2a_conformance/harness.py`；`tests/testing/a2a_conformance/test_runner.py`；`tests/testing/a2a_conformance/test_harness.py` | **必须在 A3 后串行**，先迁 report leaf types，再迁 external runner，最后迁 harness；仅可依赖公开 transport 契约，不得导入 `channels.a2a_operations`。A4 不阻塞不涉及 push 的 conformance；涉及 push 的检查等待 A4。 | Quality/Release Owner | `agentos.channels.a2a_conformance` 保留薄 re-export，直到新测试路径覆盖全部报告/外部执行契约、`rg -n "channels\.a2a_conformance|channels\.a2a_operations" src/agentos/testing/a2a_conformance tests/testing/a2a_conformance` 无反向依赖，且 Public API inventory 明确 canonical module。 | `python -m pytest tests/testing/a2a_conformance tests/channels/test_a2a_conformance.py tests/architecture/test_public_api.py -q`；`python -m compileall -q src/agentos/testing/a2a_conformance tests/testing/a2a_conformance` |

### 2.2 HTTP transport workstream

源职责位于 `src/agentos/channels/asgi.py`。目标边界是：请求/响应/SSE wire mapping 进入 `agentos.transports.http`；`AsgiAgentApp` 的 runtime、session、lease、A2A server 和 UI stream 组合仍属于 Channel composition，不下沉到 Transport，也不在 Transport 中定义执行状态机。

| ID | 源职责 | 具体目标模块文件 | 前置依赖与迁移顺序 | Owner | 旧 facade/兼容入口收口或删除门禁 | 目标验证命令 |
|---|---|---|---|---|---|---|
| H1 | ASGI scope/header/query/body 解析、状态码和普通 JSON/health/readiness 响应映射 | `src/agentos/transports/http/__init__.py`；`src/agentos/transports/http/request_mapping.py`；`src/agentos/transports/http/response_mapping.py`；`tests/transports/http/test_request_mapping.py`；`tests/transports/http/test_response_mapping.py` | **串行起点**。先迁纯 request mapping，再迁 response mapping；H2、H3 均依赖 H1。不得依赖 `AgentSessionProvider`、lease Store 或 runtime profile。 | Distributed Owner | `AsgiAgentApp` 仍从 `agentos.channels.asgi` 和 `agentos.channels` 导出；旧文件只调用新 mapping。只有映射契约测试覆盖 malformed body、size limit、headers 和 error redaction 后，才可删除旧 private mapping 实现。 | `python -m pytest tests/transports/http/test_request_mapping.py tests/transports/http/test_response_mapping.py tests/channels/test_input_validation.py tests/channels/test_health_endpoint.py -q`；`python -m ruff check src/agentos/transports/http tests/transports/http` |
| H2 | SSE frame 编码、heartbeat、resume cursor、terminal event 和 stream response wire mapping | `src/agentos/transports/http/sse_mapping.py`；`tests/transports/http/test_sse_mapping.py` | **必须在 H1 后串行**。只消费稳定 stream event 和 buffer Port；不得获取 session lease 或推进 Turn。H3 在本步稳定后才能接线。 | Distributed Owner | `channels.asgi` 中 SSE 编码 helper 只保留委托；`tests/channels/test_asgi_app_async.py` 中对旧 private method 源码结构的断言必须先改为行为契约，且 SSE resume/heartbeat/terminal retention 测试绿色后才可删除旧 helper。 | `python -m pytest tests/transports/http/test_sse_mapping.py tests/channels/test_sse_channel.py tests/channels/test_sse_buffer.py tests/channels/test_asgi_app_async.py -q`；`python -m compileall -q src/agentos/transports/http tests/transports/http` |
| H3 | `AsgiAgentApp` route dispatch、session provider、lease heartbeat、SSE turn control、A2A operation server 和 team UI stream 组合接线 | `src/agentos/channels/asgi_app.py`；`src/agentos/channels/asgi_session_wiring.py`；`tests/channels/test_asgi_session_wiring.py` | **必须在 H2 后串行**。先把 lease/session 生命周期迁入 `asgi_session_wiring.py`，再让 `asgi_app.py` 只协调 route 与 transport mapping；依赖现有 `channels.session`、`channels.durable_session` 和 lease Port，禁止定义新的 Run/Turn 状态。 | Distributed Owner | `src/agentos/channels/asgi.py` 只保留 `AsgiAgentApp`、auth policy 和 error 的显式 re-export；删除该 facade 前必须同时满足：inventory canonical module 已更新、`rg -n "agentos\.channels\.asgi import" src tests` 只剩明确 compatibility tests、根/`channels` facade import identity 测试绿色、发布说明批准 breaking change。 | `python -m pytest tests/channels/test_asgi_session_wiring.py tests/channels/test_asgi_app.py tests/channels/test_asgi_app_async.py tests/channels/test_durable_session_provider.py tests/channels/test_redis_session_lease_store.py tests/runtime/test_runtime_profile.py tests/architecture/test_public_api.py -q`；`python -m ruff check src/agentos/channels/asgi.py src/agentos/channels/asgi_app.py src/agentos/channels/asgi_session_wiring.py` |

### 2.3 Deployment evidence workstream

源职责位于 `src/agentos/deployment.py`。目标使用平铺模块名，避免在兼容期同时存在 `agentos/deployment.py` 与 `agentos/deployment/` 的模块解析冲突。共享叶子类型和常量不依赖 subprocess、Store 或 runtime Adapter；validation 产生稳定报告，profile 只装配策略，readiness/release 等消费者只读取报告契约。

| ID | 源职责 | 具体目标模块文件 | 前置依赖与迁移顺序 | Owner | 旧 facade/兼容入口收口或删除门禁 | 目标验证命令 |
|---|---|---|---|---|---|---|
| D1 | backend kind 常量、verification state、worker process spec/state、不可变 report/value 基元 | `src/agentos/deployment_constants.py`；`src/agentos/deployment_types.py`；`tests/deployment/test_deployment_types.py` | **串行起点**。先常量、后纯值类型；D2、D3 均依赖 D1。叶子模块不得导入 `runtime.profile`、`state_plane`、subprocess 或 concrete backend。 | Quality/Release Owner | `agentos.deployment` 只增加显式 re-export，不改变对象 identity/签名；Public API inventory 和现有 `from agentos.deployment import ...` 调用在本步保持兼容。 | `python -m pytest tests/deployment/test_deployment_types.py tests/deployment/test_live_backend_verification.py tests/architecture/test_public_api.py -q`；`python -m ruff check src/agentos/deployment_constants.py src/agentos/deployment_types.py tests/deployment/test_deployment_types.py` |
| D2 | report import、metadata 校验/脱敏、invocation gate、CLI runner、worker supervisor 校验流程 | `src/agentos/deployment_validation.py`；`src/agentos/deployment_reports.py`；`tests/deployment/test_deployment_validation.py`；`tests/deployment/test_deployment_reports.py` | **必须在 D1 后串行**。先迁纯 validation/report import，再迁 CLI runner 和 supervisor；报告类型不得反向导入 runner。D3 依赖稳定的 validation/report API。 | Quality/Release Owner | `deployment.py` 中实现体只有在 importer/runner/supervisor 的失败、timeout、restricted metadata 和 redaction 契约测试绿色，且 `rg -n "^(class|def) (BackendVerification|DeploymentLiveBackend|WorkerProcess|_validate|_reject|_json_safe)" src/agentos/deployment.py` 只剩允许的 facade 声明时才可删除。 | `python -m pytest tests/deployment/test_deployment_validation.py tests/deployment/test_deployment_reports.py tests/deployment/test_live_backend_verification_runner.py tests/deployment/test_worker_process_supervisor.py -q`；`python -m compileall -q src/agentos/deployment_validation.py src/agentos/deployment_reports.py tests/deployment` |
| D3 | `DeploymentLiveBackendVerificationProfile` 和 `ProductionStatePlaneDeploymentProfile` 的验证策略与 state-plane 装配 | `src/agentos/deployment_profiles.py`；`tests/deployment/test_deployment_profiles.py` | **必须在 D2 后串行**。Profile 只组合 D1/D2 稳定类型和 Port，不复制 validation，不直接创建 concrete PostgreSQL/Redis client。D4 依赖本步。 | Quality/Release Owner | 旧 profile 名称从 `agentos.deployment` 显式 re-export；只有 profile 默认值、builder 行为、optional backend 缺失错误和 inventory 测试绿色后，才可移除旧实现。 | `python -m pytest tests/deployment/test_deployment_profiles.py tests/deployment/test_live_backend_probe_pack.py tests/runtime/test_runtime_profile.py tests/test_reference_state_plane_stack.py -q`；`python -m ruff check src/agentos/deployment_profiles.py tests/deployment/test_deployment_profiles.py` |
| D4 | readiness、release evidence、probe、state-plane 和 runtime profile 对部署报告/profile 的消费 | `src/agentos/readiness.py`；`src/agentos/release.py`；`src/agentos/probes.py`；`src/agentos/state_plane.py`；`src/agentos/runtime/profile.py`；对应既有测试 | **必须在 D3 后串行**。逐个消费者改为只导入 `deployment_types`、`deployment_reports` 或 `deployment_profiles`，每迁一个消费者即运行其目标测试；不得让 leaf report 类型反向依赖消费者。 | Quality/Release Owner；各消费者模块 Owner 负责本文件改动 | `src/agentos/deployment.py` 最终只保留经 inventory 批准的兼容 re-export。删除 facade 必须满足 `rg -n "agentos\.deployment import" src tests` 只剩 compatibility tests、所有 public 名称已有新 canonical module、迁移说明和版本策略获批、全量测试绿色。 | `python -m pytest tests/test_readiness.py tests/test_release_evidence.py tests/deployment tests/runtime/test_runtime_profile.py tests/test_reference_state_plane_stack.py tests/architecture/test_public_api.py -q`；`python -m ruff check src/agentos/readiness.py src/agentos/release.py src/agentos/probes.py src/agentos/state_plane.py src/agentos/runtime/profile.py` |

### 2.4 并行与集成顺序

| 阶段 | 可并行工作 | 必须等待的串行门禁 |
|---|---|---|
| Phase 0 | 仅 Task 4 机械 Ruff 清理和本计划登记；不得启动 A/H/D 拆分 | no-growth 检查、Task 4 目标测试和双层 Review 完成 |
| Phase 6 wave 1 | A1、H1、D1 可由独立 worktree 并行；文件 Owner 不重叠 | 每个 leaf contract 测试和 Public API compatibility 测试绿色后进入 wave 2 |
| Phase 6 wave 2 | A2、A4、H2、D2 可并行；A4 内部步骤保持串行 | A2 通过后才能开始 A3；H2 通过后才能开始 H3；D2 通过后才能开始 D3 |
| Phase 6 wave 3 | A3、H3、D3 可并行 | A3 完成后才能开始 A5；D3 完成后才能迁 D4 consumers |
| Phase 6 wave 4 | A5 与 D4 可并行 | 各 workstream 目标测试、inventory drift 检查和兼容 facade 门禁全部通过 |
| Phase 6 integration | 不再并行修改公共 facade | 按 A2A -> HTTP -> Deployment 顺序集成，每次集成后运行第 3 节全量契约；最后才允许删除已批准的旧 facade |

## 3. 兼容测试

拆分必须保持并运行以下契约：

- `tests/channels/test_a2a_operations.py` 与全部 A2A server/client/conformance 测试；
- `tests/channels/test_asgi_app.py`、`tests/channels/test_asgi_app_async.py` 和 SSE/session lease 测试；
- `tests/runtime/test_runtime_profile.py`；
- `tests/deployment/` 全量测试；
- `tests/architecture/test_public_api.py`，确保兼容入口与 inventory 同步；
- `python -m pytest -q`、`python -m ruff check src tests`、`python -m compileall -q src tests`。

## 4. 禁止继续增长规则

- 上述四个文件在拆分完成前不得增加新的独立子系统、公共领域类型或基础设施职责。
- 必需修复优先落在目标 transport/deployment 子模块，旧文件只保留薄协调或兼容导出。
- 任何无法纯减法完成的修改都必须先更新 Phase 6 Spec 和详细实施计划。
- 不得以 `noqa`、Ruff suppress 或规模例外掩盖新增职责。
- 拆分提交必须逐步保持兼容测试绿色，禁止一次性重写全部模块。

## 5. Phase 0 例外结论

本次 Task 4 仅执行机械清理和既有异常语义等价修正，未增加代码路径或模块责任，因此允许在拆分前触碰这些文件。该例外只适用于提交 `test: remove unused baseline bindings`，不授权后续继续增长。
