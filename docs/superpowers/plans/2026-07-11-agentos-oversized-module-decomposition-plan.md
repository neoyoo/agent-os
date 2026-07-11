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

## 2. 拆分目标

### 2.1 A2A transport

- 把 A2A wire 类型、序列化和 operation 映射迁入 `agentos.transports.a2a`。
- Transport 只负责外部协议与领域 Command/Event 的转换，不拥有 Run、Session、Retry 或 Tool 执行真值。
- Conformance runner 和报告保持部署/测试边界，不反向进入 Kernel。

### 2.2 HTTP transport

- 把 ASGI/HTTP 请求、响应和 SSE wire mapping 迁入 `agentos.transports.http`。
- Session provider、lease 和 Runtime 通过 Port 注入；Transport 不定义执行状态机。
- 原 `AsgiAgentApp` 保留薄兼容入口，直到 Public API inventory 明确完成迁移。

### 2.3 Deployment evidence

- 按 `validation`、`profile`、`report` 三个责任拆分部署证据模型。
- 共享常量和纯值类型放在无基础设施依赖的叶子模块。
- Readiness 和 release evidence 只消费稳定报告类型，不导入具体运行时 Adapter。

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
