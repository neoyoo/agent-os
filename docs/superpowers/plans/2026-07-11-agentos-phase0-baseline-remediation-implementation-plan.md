# AgentOS Phase 0 Baseline Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Context Protocol Kernel 开发前恢复可重复的绿色工程基线，并把 Public API、Release Evidence、静态检查和模块规模治理变成确定性契约。

**Architecture:** Phase 0 只处理工程真值和发布治理，不改变 QueryLoop、Context Runtime、Provider Adapter 或 Tool 行为。根包 facade 和其他 re-export 使用显式 alias，不设置 F401 suppress；本地 Release Evidence 与普通单元测试解耦；Public API inventory 由确定性脚本维护。

**Tech Stack:** Python 3.11+、pytest、Ruff、Git、JSON、Markdown。

---

## Scope Contract

- **Phase / Active Specs:** Phase 0；`2026-07-10-agentos-next-generation-sdk-architecture-design.md`、`2026-07-10-agentos-context-protocol-v1-design.md`、`2026-07-10-agentos-context-first-sdk-master-implementation-plan.md`。
- **Acceptance Items:** 全量 pytest 通过；Ruff `F` 规则通过且无宽泛 suppress；Live Backend 子进程显式获得可导入的 `src` 环境；Release Evidence 普通测试与候选发布强制门禁分离；Public API inventory 与 `__all__` 一致并跨 Python 3.11-3.13 规范化；旧七段式范文不再是规范锚点；生成并校验 300/500/800 行规模基线；记录 `0.2.0a1` API 策略。
- **Allowed Files:** `AGENTS.md`、`.gitattributes`、`.github/workflows/ci.yml`、`pyproject.toml`、`src/agentos/__init__.py`、`src/agentos/channels/asgi.py`、`src/agentos/channels/a2a_conformance.py`、`src/agentos/channels/a2a_operations.py`、`src/agentos/deployment.py`、`src/agentos/readiness.py`、`src/agentos/runtime/profile.py`、`src/agentos/testing/contracts/plan_claim_store.py`、`tests/channels/test_a2a_jwks_jwt_verifier.py`、`tests/service/test_agent_service_reference.py`、`tests/multi/test_planner_runtime.py`、`tests/deployment/test_live_backend_probe_pack.py`、`tests/architecture/test_public_api.py`、`tests/architecture/test_module_size_baseline.py`、`tests/docs/test_production_hardening_docs.py`、`tests/test_release_evidence.py`、`scripts/generate_public_api_inventory.py`、`scripts/generate_module_size_baseline.py`、`scripts/validate_release_evidence.py`、`docs/public-api-stability.json`、`docs/public-api-inventory.json`、`docs/api-stability.md`、`docs/release-hardening.md`、`docs/design/sdk-architecture.md`、`docs/design/llm-context-only-example.md`、`docs/governance/agentos-module-size-baseline.json`、`docs/superpowers/plans/2026-07-11-agentos-oversized-module-decomposition-plan.md`。
- **Forbidden Files:** `src/agentos/context/**`、`src/agentos/messages/**`、`src/agentos/runtime/query_loop.py`、`src/agentos/runtime/async_query_loop.py`、Provider Adapter、Artifact、Memory、Planner 领域实现。
- **Dependency Boundaries:** Phase 0 可以读取所有 public modules 做反射，但不能新增运行时依赖；基础安装仍保持零第三方依赖。
- **Completed In This Work Package:** 工程基线修复、治理数据生成、文档取代关系和确定性验证。
- **Explicit Deferrals:** SystemEnvelope/ContextSnapshot 实现进入 Phase 1；StoredMessage/ProviderInputItem 进入 Phase 2；ToolCallScheduler 进入 Local Loop 集成阶段。
- **Verification Commands:** 各任务定向测试、`python -m pytest -q`、`python -m ruff check src tests`、`python -m compileall -q src tests`、`git diff --check`。

---

## Worktree Execution Bootstrap

Phase 0 必须在新建 worktree 根目录先建立自己的虚拟环境，不能复用主工作区 `.venv` 或任何指向其他 checkout 的 editable install：

```powershell
uv sync --extra dev --extra postgres --extra redis
& (Resolve-Path '.\.venv\Scripts\python.exe') -c "import agentos, pathlib; print(pathlib.Path(agentos.__file__).resolve())"
```

最后一条命令输出的 `agentos` 路径必须位于当前 Phase 0 worktree。后续每个独立 PowerShell 命令块都重新使用 `& (Resolve-Path '.\.venv\Scripts\python.exe')`，不依赖跨进程变量；如果环境 bootstrap 失败，不得进入任何 Red 测试。

---

### Task 1: 隔离本地 Release Evidence 与普通测试基线

**Files:**
- Modify: `tests/test_release_evidence.py`
- Modify: `tests/docs/test_production_hardening_docs.py`
- Modify: `docs/release-hardening.md`
- Create: `scripts/validate_release_evidence.py`

普通 docs/unit tests 不得读取被 Git 忽略的
`docs/release-evidence.json` 本地候选文件。候选内容、identity 与 blocking gate
由 `tests/test_release_evidence.py` 的显式环境变量门禁和不可跳过的 CLI 契约覆盖；
普通文档测试只验证文档明确描述了该边界和对应命令。

- [ ] **Step 1: 固定不可移动的 Phase 0 起点**

在 Phase 0 worktree 创建后、任何实现提交前运行：

```powershell
git show-ref --verify --quiet refs/tags/agentos-phase0-start-20260711
if ($LASTEXITCODE -eq 0) { throw 'phase0 baseline tag already exists' }
git tag agentos-phase0-start-20260711 HEAD
```

最终文件边界统一与该 tag 比较；Phase 0 合并完成并确认后再删除本地 tag。

- [ ] **Step 2: 写入默认模式不得读取本地证据的失败测试**

在 `tests/test_release_evidence.py` 中先增加 `import pytest`，然后只写测试，不写 helper。测试通过无效 JSON 证明默认普通测试不会读取候选发布文件：

```python
def test_local_release_evidence_is_not_an_implicit_unit_test_input(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "release-evidence.json"
    manifest.write_text("not-json", encoding="utf-8")
    monkeypatch.delenv(
        "AGENTOS_VALIDATE_LOCAL_RELEASE_EVIDENCE",
        raising=False,
    )

    assert _validate_local_release_evidence_if_requested(manifest) is None
```

该测试调用真实的文件验证入口；实现若在检查门禁前读取文件，会因无效 JSON 失败。

同时在 Step 2 写入 helper 显式启用路径的测试，不实现任何分支：

- `test_local_release_evidence_validation_accepts_matching_identity_when_enabled`：临时 manifest 匹配当前 branch/commit/version，设置门禁后返回 accepted report。
- `test_local_release_evidence_validation_requires_manifest_when_enabled`：设置门禁但文件不存在，断言 `pytest.fail()` 的明确诊断。
- `test_local_release_evidence_validation_rejects_identity_drift_when_enabled`：设置门禁并提供旧 commit manifest，断言返回 rejected report 且包含 commit drift。
- `test_local_release_evidence_validation_preserves_blocking_gates_when_enabled`：设置门禁并提供 pending independent review，断言 blocking gate 不被吞掉。

这些测试名称统一包含 `local_release_evidence`，必须被 Step 3 的 Red 命令选中。

同一步先定义 `RELEASE_EVIDENCE_VALIDATOR = ROOT / "scripts" / "validate_release_evidence.py"` 和只负责执行 CLI 的测试 helper，再写以下四个测试，不创建脚本：

- `test_release_evidence_validator_cli_accepts_matching_manifest`：把 `release_manifest()` 写入临时文件，传入匹配的 branch/commit/version，断言 exit code 0 且输出报告为 accepted。
- `test_release_evidence_validator_cli_rejects_missing_manifest`：传入不存在的路径，断言 exit code 非 0 且 stderr 明确包含 manifest missing 诊断。
- `test_release_evidence_validator_cli_rejects_identity_drift`：写入有效 manifest 但传入旧 commit，断言 exit code 非 0 且输出包含 commit drift 诊断。
- `test_release_evidence_validator_cli_rejects_pending_independent_review_with_matching_identity`：branch/commit/version 完全匹配，只把 `independent_review` 和对应 gate 设为 pending，断言 exit code 非 0、`blocking_gates` 仅包含 `independent_review`，且没有 identity drift 诊断。

四个测试都必须检查领域诊断内容，不能只断言非零，否则“Python 无法打开尚不存在的脚本”会形成伪 Green。

- [ ] **Step 3: 运行测试并确认当前实现失败**

Run:

```powershell
& (Resolve-Path '.\.venv\Scripts\python.exe') -m pytest tests/test_release_evidence.py -k "local_release_evidence or release_evidence_validator_cli" -q
```

Expected: FAIL；默认入口测试因 `_validate_local_release_evidence_if_requested()` 尚不存在而失败，CLI 测试因 `scripts/validate_release_evidence.py` 尚不存在且没有预期领域诊断而失败。

- [ ] **Step 4: 让当前候选证据验证只在显式门禁下运行**

定义 `LOCAL_RELEASE_EVIDENCE_ENV` 和 `_local_release_evidence_validation_enabled()`，并实现 `_validate_local_release_evidence_if_requested(manifest_path)`：必须先检查门禁，禁用时直接返回 `None`，启用时文件缺失立即 `pytest.fail()`，随后读取 JSON 并保留现有 `validate_release_candidate_evidence_manifest()` 的严格 branch/commit/version 语义。修改文件级测试调用该入口：

```python
def test_generated_release_evidence_artifact_is_validated_when_present() -> None:
    report = _validate_local_release_evidence_if_requested(RELEASE_EVIDENCE)
    if report is None:
        pytest.skip(
            f"set {LOCAL_RELEASE_EVIDENCE_ENV}=1 for local candidate validation",
        )
    # 后续保持当前 independent review 断言逻辑不变。
```

在 `docs/release-hardening.md` 中记录普通测试与候选发布验证的不同命令：

```powershell
$env:AGENTOS_VALIDATE_LOCAL_RELEASE_EVIDENCE='1'
& (Resolve-Path '.\.venv\Scripts\python.exe') -m pytest tests/test_release_evidence.py -q
```

实现 `scripts/validate_release_evidence.py` 作为候选发布不可静默跳过的 CLI。它必须要求 `--manifest`、`--branch`、`--commit`、`--version`，文件缺失、identity 不一致或任何 blocking gate 时返回非零；仅 accepted report 返回 0。实现必须让 Step 2 已写入的成功、缺文件、旧 commit 和 identity 匹配但 independent review pending 四种测试从 Red 变为 Green，不得修改断言来适配实现。

- [ ] **Step 5: 运行 Release Evidence 契约测试**

Run:

```powershell
& (Resolve-Path '.\.venv\Scripts\python.exe') -m pytest tests/test_release_evidence.py tests/docs/test_production_hardening_docs.py -q
```

Expected: PASS；默认测试不读取陈旧本地候选证据，显式候选校验仍严格绑定 identity。

再单独运行 identity 完全匹配、仅 independent review pending 的强制候选门禁路径：

```powershell
& (Resolve-Path '.\.venv\Scripts\python.exe') -m pytest tests/test_release_evidence.py::test_release_evidence_validator_cli_rejects_pending_independent_review_with_matching_identity -q
```

Expected: PASS；被测 CLI 自身 exit code 非 0 的原因只能是 `independent_review` blocking gate，不允许由 branch/commit/version drift 代替该证据。

- [ ] **Step 6: Spec Compliance 与 Code Quality Review 通过后精确提交**

```powershell
git add -- tests/test_release_evidence.py scripts/validate_release_evidence.py docs/release-hardening.md
git commit -m "test: isolate local release evidence validation"
```

---

### Task 2: 固化 Live Backend 子进程导入环境

**Files:**
- Modify: `tests/deployment/test_live_backend_probe_pack.py`

- [ ] **Step 1: 写统一子进程入口的失败测试**

先只写测试，不实现 helper。新增测试使用 `tmp_path` 作为 cwd，并通过期望中的 `_run_live_backend_probe()` 以 `sys.executable -S -m agentos.examples.live_backend_probe ...` 启动，禁用 site initialization，从而不依赖 editable install：

```python
def test_reference_live_backend_probe_subprocess_has_isolated_import(
    tmp_path: Path,
) -> None:
    completed = _run_live_backend_probe(
        "agent_registry",
        cwd=tmp_path,
        disable_site=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["records"][0]["backend_name"] == (
        "agent_registry"
    )
```

- [ ] **Step 2: 运行并确认旧子进程调用在隔离环境失败**

Run:

```powershell
& (Resolve-Path '.\.venv\Scripts\python.exe') -m pytest tests/deployment/test_live_backend_probe_pack.py -k "isolated_import" -q
```

Expected: FAIL，因为 `_run_live_backend_probe()` 尚不存在。

- [ ] **Step 3: 实现统一子进程入口并迁移现有调用**

在测试文件中加入 `Path`、`os` 和 `PROJECT_ROOT`，实现 `_sdk_subprocess_env()`：复制父环境，将 `PROJECT_ROOT / "src"` 放在 `PYTHONPATH` 首位，并保留已有非空 `PYTHONPATH`。实现 `_run_live_backend_probe()`，统一组装 argv、传入 `env=_sdk_subprocess_env()`、`check=False`、`capture_output=True` 和 `text=True`；`disable_site=True` 时在 `sys.executable` 后加入 `-S`。

把该文件内所有调用 `agentos.examples.live_backend_probe` 的 `subprocess.run()` 迁移到 `_run_live_backend_probe()`。不得依赖开发者 shell、editable install 或父进程偶然设置的 `PYTHONPATH`。

- [ ] **Step 4: 运行定向和无 PYTHONPATH 回归**

```powershell
$previous=$env:PYTHONPATH
$env:PYTHONPATH=''
& (Resolve-Path '.\.venv\Scripts\python.exe') -m pytest tests/deployment/test_live_backend_probe_pack.py -q
$env:PYTHONPATH=$previous
```

Expected: PASS。

- [ ] **Step 5: 双层 Review 后精确提交**

```powershell
git add -- tests/deployment/test_live_backend_probe_pack.py
git commit -m "test: make live backend subprocess imports explicit"
```

---

### Task 3: 收敛根 Public API facade 并建立 Ruff re-export 规则

**Files:**
- Modify: `src/agentos/__init__.py`
- Modify: `tests/architecture/test_public_api.py`
- Modify: `docs/public-api-inventory.json`

- [ ] **Step 1: 写 AST 分析 helper 的 mutation 失败测试**

在 `tests/architecture/test_public_api.py` 中先写期望中的 `_root_facade_import_contract_violations(source: str, public_names: set[str])` 调用，不实现 helper。参数化源码分别包含：star import、`if`/`try` 内条件 import、`importlib.import_module()`、`from importlib import import_module` 后动态调用，以及不在 public names 中的普通 import；每个 case 断言精确 violation code。

- [ ] **Step 2: 运行 mutation 测试并确认 helper 缺失**

Run:

```powershell
& (Resolve-Path '.\.venv\Scripts\python.exe') -m pytest tests/architecture/test_public_api.py -k "root_facade_import_contract_rejects" -q
```

Expected: FAIL，因为 `_root_facade_import_contract_violations()` 尚不存在。

- [ ] **Step 3: 实现并验证 AST 分析 helper**

只实现测试侧 `_root_facade_import_contract_violations()`：使用 `ast.walk()` 收集所有层级的 import，拒绝 star import、条件/异常分支内 import、两种 `import_module()` 动态导入形式和不在 public names 中的导入。先重新运行 Step 2 命令并确认 mutation tests PASS；此时禁止修改 `src/agentos/__init__.py`。

- [ ] **Step 4: 写真实 root facade 契约并观察具体 violation**

在 helper 已通过 mutation tests 后新增：

```python
def test_root_facade_only_imports_declared_public_exports() -> None:
    root_init = PROJECT_ROOT / "src" / "agentos" / "__init__.py"
    agentos = importlib.import_module("agentos")
    violations = _root_facade_import_contract_violations(
        root_init.read_text(encoding="utf-8"),
        set(agentos.__all__),
    )
    assert violations == ()
```

Run:

```powershell
& (Resolve-Path '.\.venv\Scripts\python.exe') -m pytest tests/architecture/test_public_api.py::test_root_facade_only_imports_declared_public_exports -q
```

Expected: FAIL，失败输出必须列出当前根包导入但未声明在 `__all__` 的具体隐藏名称；若仍因 helper、fixture 或收集错误失败，不得进入 facade 修改。

- [ ] **Step 5: 重写根 facade 为显式稳定导出**

删除根 facade 中不在 `__all__` 的 import 和末尾动态清理循环，只保留稳定 facade 实际需要的 import。保持 `__all__` 名称与现有稳定策略一致，并使用显式 re-export alias，例如：

```python
from agentos.builder import AgentBuilder as AgentBuilder
from agentos.channels import AsgiAgentApp as AsgiAgentApp
from agentos.runtime import Agent as Agent
from agentos.runtime import AsyncQueryLoop as AsyncQueryLoop
from agentos.runtime import ProviderRequestBuilder as ProviderRequestBuilder
from agentos.runtime import QueryLoop as QueryLoop
```

所有其他根导出采用相同形式；不得为了消除 Ruff 而重新导入实验性子包类型。

在同一提交中把当前已公开但 inventory 缺失的 `agentos.runtime` stream event 五个条目补入 `docs/public-api-inventory.json`，稳定级别为 `stable`，签名取当前规范化 `inspect.signature`。该同步只恢复既有公开 API 基线，不引入新的 root export。

- [ ] **Step 6: 禁止宽泛 Ruff suppress**

本阶段不增加 `per-file-ignores`。公开 re-export 使用 `Foo as Foo`，普通未使用 import 必须删除。Step 3 的 mutation 契约与 Step 4 的真实文件测试共同证明 AST 检查拒绝 root facade 中的 star import、条件 import、动态 `import_module()` 和不在 `__all__` 的导入。

- [ ] **Step 7: 验证根 API 和 Ruff 约束**

Run:

```powershell
& (Resolve-Path '.\.venv\Scripts\python.exe') -m pytest tests/architecture/test_public_api.py -q
& (Resolve-Path '.\.venv\Scripts\python.exe') -m ruff check src/agentos/__init__.py
```

Expected: Public API 测试和上述 Ruff 命令全部通过，不允许带着已知 inventory 失败提交。

- [ ] **Step 8: Spec Compliance 与 Code Quality Review 通过后精确提交**

```powershell
git add -- src/agentos/__init__.py tests/architecture/test_public_api.py docs/public-api-inventory.json
git commit -m "refactor: narrow the root AgentOS facade"
```

---

### Task 4: 清除根 facade 之外的真实 F 规则错误

**Files:**
- Modify: `src/agentos/channels/asgi.py`
- Modify: `src/agentos/channels/a2a_conformance.py`
- Modify: `src/agentos/channels/a2a_operations.py`
- Modify: `src/agentos/deployment.py`
- Modify: `src/agentos/readiness.py`
- Modify: `src/agentos/runtime/profile.py`
- Modify: `src/agentos/testing/contracts/plan_claim_store.py`
- Modify: `tests/channels/test_a2a_jwks_jwt_verifier.py`
- Modify: `tests/service/test_agent_service_reference.py`
- Modify: `tests/multi/test_planner_runtime.py`
- Create: `docs/superpowers/plans/2026-07-11-agentos-oversized-module-decomposition-plan.md`

- [ ] **Step 1: 登记超大模块的拆分计划和本次减法例外**

运行 300/500/800 扫描，并在拆分计划中记录：`channels/a2a_operations.py`、`channels/a2a_conformance.py`、`channels/asgi.py`、`deployment.py` 均超过 800 行；本任务只删除 import 或修正既有错误变量，净行数不增加、不新增职责。后续拆分目标固定为：A2A wire/operation 进入 Phase 6 `agentos.transports.a2a`，ASGI/HTTP 映射进入 `agentos.transports.http`，部署证据模型按 validation/profile/report 分包。该计划必须列出 Owner、目标 phase、兼容测试和禁止继续增长规则。

- [ ] **Step 2: 定位并验证两个 F841**

Run:

```powershell
& (Resolve-Path '.\.venv\Scripts\python.exe') -m ruff check src tests --select F841 --output-format concise
```

Expected: 报告两个 F841；同时运行 `--select F401` 时，根 facade 收敛后只剩 11 个可机械确认的真实未使用 import。

- [ ] **Step 3: 删除无意义绑定，不改变异常和测试语义**

把异常目标复制为普通局部变量，后续覆盖和输出都使用该变量：

```python
except Exception as caught_error:
    error: BaseException = caught_error
    heartbeat_error: BaseException | None = None
```

保留后续：

```python
if heartbeat_error is not None:
    error = heartbeat_error
# release_error 同样覆盖 error，最终传给 _public_error_message(error)。
```

把只为构造副作用但未读取的测试变量：

```python
runtime = PlannerRuntime(...)
```

该测试实际验证 `InMemoryPlanClaimStore.release_expired_claim()` 的 generation safety，而没有调用 Runtime。删除无效的 Runtime 构造，并把测试重命名为：

```python
def test_plan_claim_store_release_expired_claim_is_generation_safe() -> None:
```

保留 stale generation 释放失败和当前 generation 仍为 2 的断言；禁止使用 `_ = runtime` 掩盖缺失验证。

删除以下已确认未使用 import，不增加 noqa：

```text
channels/a2a_conformance.py: os
channels/a2a_operations.py: AllowAllA2AInboundAuthPolicy
channels/asgi.py: AllowAllA2AInboundAuthPolicy, AllowAllChannelAuthPolicy
deployment.py: os
readiness.py: Callable
runtime/profile.py: PRODUCTION_STATE_PLANE_REQUIRED_COMPONENTS, ProductionStatePlaneDeploymentProfile
testing/contracts/plan_claim_store.py: PlanClaimSweepStore
tests/channels/test_a2a_jwks_jwt_verifier.py: serialization
tests/service/test_agent_service_reference.py: pytest
```

- [ ] **Step 4: 运行定向测试和 Ruff**

Run:

```powershell
& (Resolve-Path '.\.venv\Scripts\python.exe') -m pytest tests/multi/test_planner_runtime.py tests/channels tests/service tests/runtime/test_runtime_profile.py tests/deployment -q
& (Resolve-Path '.\.venv\Scripts\python.exe') -m ruff check src tests --select F401,F841
```

Expected: PASS，F841 为 0。

- [ ] **Step 5: Spec Compliance 与 Code Quality Review 通过后精确提交**

```powershell
git add -- src/agentos/channels/asgi.py src/agentos/channels/a2a_conformance.py src/agentos/channels/a2a_operations.py src/agentos/deployment.py src/agentos/readiness.py src/agentos/runtime/profile.py src/agentos/testing/contracts/plan_claim_store.py tests/channels/test_a2a_jwks_jwt_verifier.py tests/service/test_agent_service_reference.py tests/multi/test_planner_runtime.py docs/superpowers/plans/2026-07-11-agentos-oversized-module-decomposition-plan.md
git commit -m "test: remove unused baseline bindings"
```

---

### Task 5: 让 Public API inventory 可重复生成

**Files:**
- Create: `scripts/generate_public_api_inventory.py`
- Create: `docs/public-api-stability.json`
- Modify: `docs/public-api-inventory.json`
- Modify: `docs/api-stability.md`
- Modify: `tests/architecture/test_public_api.py`
- Modify: `.github/workflows/ci.yml`
- Modify: `pyproject.toml`

- [ ] **Step 1: 建立结构化 CI 测试依赖并写失败测试**

先在 `pyproject.toml` 的 `dev` extra 增加 `pyyaml>=6.0`，然后在当前 worktree 重新运行 `uv sync --extra dev --extra postgres --extra redis`。PyYAML 只用于解析 CI 测试，不得进入 `[project].dependencies` 或 AgentOS 运行时代码。

```python
def test_public_api_inventory_is_reproducible(tmp_path: Path) -> None:
    # 在文件 import 区加入 subprocess 和 sys。
    output = tmp_path / "public-api-inventory.json"
    subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "generate_public_api_inventory.py"),
            "--policy",
            str(PROJECT_ROOT / "docs" / "public-api-stability.json"),
            "--output",
            str(output),
        ],
        cwd=PROJECT_ROOT,
        check=True,
    )
    assert json.loads(output.read_text(encoding="utf-8")) == (
        _load_public_api_inventory()
    )


def test_public_api_inventory_ci_covers_supported_python_minors() -> None:
    workflow = yaml.safe_load(
        (PROJECT_ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8",
        ),
    )
    job = workflow["jobs"]["public-api-inventory"]
    assert job["strategy"]["matrix"]["python-version"] == [
        "3.11",
        "3.12",
        "3.13",
    ]
    run_steps = [step["run"] for step in job["steps"] if "run" in step]
    assert any("generate_public_api_inventory.py" in command for command in run_steps)
    assert any("git diff --exit-code --no-index" in command for command in run_steps)
```

在文件 import 区加入 `import yaml`。测试必须从 `jobs.public-api-inventory` 的结构化对象读取 matrix 和 steps；不得通过全文件 substring、注释或其他 job 满足断言。

- [ ] **Step 2: 运行测试并确认失败**

Run:

```powershell
& (Resolve-Path '.\.venv\Scripts\python.exe') -m pytest tests/architecture/test_public_api.py -k "public_api_inventory" -q
```

Expected: FAIL，因为生成脚本尚不存在，且 CI 尚无 Python 3.11/3.12/3.13 inventory matrix。

- [ ] **Step 3: 拆分稳定性策略与生成结果**

`docs/public-api-stability.json` 只保存受治理模块和每个 export 的 `stable|experimental` 分类，不保存签名。生成器必须从该策略文件读取模块清单，再从每个模块的 `__all__` 重建完整 inventory；策略缺少导出、包含已删除导出、重复分类或模块没有 `__all__` 时立即失败。

- [ ] **Step 4: 实现跨 Python 版本的确定性签名规范化**

生成器执行：

```python
for module_name in sorted(policy["modules"]):
    module = importlib.import_module(module_name)
    for export_name in sorted(module.__all__):
        exported = getattr(module, export_name)
        # 从独立 policy 读取 classification；新名称没有 classification 时立即失败。
        # callable 使用 inspect.signature(eval_str=False) 后进入 normalize_signature；
        # Protocol 同步 public methods，并使用同一 normalize_signature。
        # JSON 使用 ensure_ascii=False、indent=2、sort_keys=True，并以换行结束。
```

`normalize_signature()` 至少执行以下公共规范：

```python
text = text.replace("pathlib._local.Path", "pathlib.Path")
# frozenset({...}) 内按 repr 排序，避免 hash seed 和 Python minor 顺序漂移。
# 保留公开模块限定名，不记录对象内存地址或私有实现限定名。
```

在 `tests/architecture/test_public_api.py` 增加 3.11/3.13 代表性输入的参数化测试，确保 `pathlib.Path` 与 `pathlib._local.Path` 得到同一结果，`frozenset({'task', 'session'})` 与反序输入得到同一结果。

在 `.github/workflows/ci.yml` 增加独立 `public-api-inventory` job，matrix 固定为 `python-version: ["3.11", "3.12", "3.13"]`。每个版本都使用对应 Python 执行生成器输出到临时文件，再用 `git diff --exit-code --no-index docs/public-api-inventory.json <generated>` 比较。任何 minor 版本生成不同结果都必须使 CI 失败；不得只在单一解释器上模拟版本字符串。

策略文件必须显式把新增的五个 stream event 分类为 stable：

```json
{
  "ContextLoaded": "stable",
  "FinalResult": "stable",
  "PlanUpdated": "stable",
  "SkillLoaded": "stable",
  "StatusUpdate": "stable"
}
```

- [ ] **Step 5: 生成并验证 inventory**

Run:

```powershell
& (Resolve-Path '.\.venv\Scripts\python.exe') scripts/generate_public_api_inventory.py --policy docs/public-api-stability.json --output docs/public-api-inventory.json
& (Resolve-Path '.\.venv\Scripts\python.exe') -m pytest tests/architecture/test_public_api.py -q
```

Expected: PASS；连续运行两次不会产生 diff。

合并前还必须取得 CI `public-api-inventory` job 在 Python 3.11、3.12、3.13 三个 matrix cell 全部通过的证据；本地单解释器参数化测试不能替代该证据。

- [ ] **Step 6: 记录 `0.2.0a1` 策略并在双层 Review 后提交**

在 `docs/api-stability.md` 中记录：`0.2.0a1` 的 root facade 只承诺 Level 1 本地 Loop 所需稳定入口；未实现的 ContextSnapshot、Artifact、Durable、Distributed 新类型不提前导出；breaking migration 只在对应阶段和 inventory 更新同时发生。记录生成器命令，然后：

```powershell
git add -- .github/workflows/ci.yml pyproject.toml scripts/generate_public_api_inventory.py docs/public-api-stability.json docs/public-api-inventory.json docs/api-stability.md tests/architecture/test_public_api.py
git commit -m "build: make the public API inventory reproducible"
```

---

### Task 6: 固化文档取代关系和模块规模基线

**Files:**
- Create: `.gitattributes`
- Modify: `AGENTS.md`
- Modify: `docs/design/sdk-architecture.md`
- Modify: `docs/design/llm-context-only-example.md`
- Create: `scripts/generate_module_size_baseline.py`
- Create: `docs/governance/agentos-module-size-baseline.json`
- Create: `tests/architecture/test_module_size_baseline.py`

生成型治理 JSON 必须通过 `.gitattributes` 固定为 `eol=lf`，避免 Windows
`core.autocrlf` 让已跟踪基线与生成器输出发生字节漂移。该规则至少覆盖
`docs/governance/agentos-module-size-baseline.json` 和
`docs/public-api-inventory.json`。

- [ ] **Step 1: 写规模基线结构测试**

```python
def test_module_size_baseline_has_governed_thresholds() -> None:
    payload = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert payload["schema"] == "agentos.module_size_baseline"
    assert payload["schema_version"] == 1
    assert payload["thresholds"] == {
        "responsibility_review": 300,
        "split_or_exception": 500,
        "no_new_responsibility": 800,
    }
    assert payload["modules"] == sorted(
        payload["modules"],
        key=lambda item: (-item["lines"], item["path"]),
    )
    assert all(item["lines"] >= 300 for item in payload["modules"])


def test_module_size_baseline_matches_current_source_tree() -> None:
    payload = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert payload["modules"] == scan_governed_modules(PROJECT_ROOT / "src" / "agentos")
```

- [ ] **Step 2: 运行测试并确认失败**

Run:

```powershell
& (Resolve-Path '.\.venv\Scripts\python.exe') -m pytest tests/architecture/test_module_size_baseline.py -q
```

Expected: FAIL，因为 baseline 尚不存在。

- [ ] **Step 3: 生成结构化规模基线**

实现 `scripts/generate_module_size_baseline.py`，公开纯函数 `scan_governed_modules(root: Path)` 供测试和 CLI 共用。扫描 `src/agentos/**/*.py`，使用 UTF-8 `splitlines()` 统一计数，记录所有 300 行以上模块，字段固定为：

```json
{
  "path": "src/agentos/runtime/query_loop.py",
  "lines": 884,
  "gate": "no_new_responsibility"
}
```

`gate` 映射规则：300-499 为 `responsibility_review`，500-799 为 `split_or_exception`，800 以上为 `no_new_responsibility`。

在同一测试文件中增加取代关系和根指令契约：

```python
def test_legacy_context_example_is_explicitly_superseded() -> None:
    content = LEGACY_CONTEXT_EXAMPLE.read_text(encoding="utf-8")
    assert "status: superseded" in content
    assert "2026-07-10-agentos-context-protocol-v1-design.md" in content
    assert "仅作为早期设计输入保留" in content


def test_active_governance_uses_context_protocol_v1_as_the_anchor() -> None:
    agents = (PROJECT_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "Context Protocol v1" in agents
    assert "context example is the golden target" not in agents
    assert "system: rendered context" not in agents
```

- [ ] **Step 4: 标记旧设计范文的取代关系**

把 `docs/design/llm-context-only-example.md` frontmatter 更新为：

```yaml
status: superseded
superseded_by:
  - docs/superpowers/specs/2026-07-10-agentos-context-protocol-v1-design.md
```

正文标题后增加明确说明：该文件仅作为早期设计输入保留，不再约束 SystemEnvelope、ContextSnapshot、Slot Registry 或 Provider 角色映射。`docs/design/sdk-architecture.md` 同步指向当前架构 Spec 和 Context Protocol v1。

更新 `AGENTS.md`：Required Design References 以 Context Protocol v1 Spec 和下一代 SDK 架构 Spec 为权威；旧范文只放在 Historical Design Inputs，不再称为 golden target；Provider 输入描述改为 `system: SystemEnvelope` 与 `messages: ContextSnapshot + active ProviderInputItem`。

- [ ] **Step 5: 验证文档与基线**

Run:

```powershell
& (Resolve-Path '.\.venv\Scripts\python.exe') scripts/generate_module_size_baseline.py --root src/agentos --output docs/governance/agentos-module-size-baseline.json
& (Resolve-Path '.\.venv\Scripts\python.exe') -m pytest tests/architecture/test_module_size_baseline.py tests/docs -q
```

Expected: PASS；活动设计文档不再把旧七段式 system prompt 当作规范。

- [ ] **Step 6: Spec Compliance 与 Code Quality Review 通过后精确提交**

```powershell
git add -- .gitattributes AGENTS.md docs/design/sdk-architecture.md docs/design/llm-context-only-example.md scripts/generate_module_size_baseline.py docs/governance/agentos-module-size-baseline.json tests/architecture/test_module_size_baseline.py
git commit -m "docs: freeze the AgentOS architecture baseline"
```

---

### Task 7: Phase 0 完整质量门禁

**Files:**
- Verify only: all Phase 0 allowed files

- [ ] **Step 1: 运行目标矩阵**

```powershell
& (Resolve-Path '.\.venv\Scripts\python.exe') -m pytest tests/architecture/test_public_api.py tests/architecture/test_module_size_baseline.py tests/test_release_evidence.py tests/docs -q
```

Expected: PASS。

- [ ] **Step 2: 运行全量测试和静态检查**

```powershell
& (Resolve-Path '.\.venv\Scripts\python.exe') -m pytest -q
& (Resolve-Path '.\.venv\Scripts\python.exe') -m ruff check src tests
& (Resolve-Path '.\.venv\Scripts\python.exe') -m compileall -q src tests
git diff --check
```

Expected: 全部 exit code 0。

同时确认 CI `public-api-inventory` job 的 Python 3.11、3.12、3.13 三个 matrix cell 全部通过；缺少任一版本的远端证据时 Phase 0 只能标记为 partially complete。

- [ ] **Step 3: 验证无越界文件**

```powershell
git status --short
git diff --name-only agentos-phase0-start-20260711..HEAD
```

Expected: 只包含 Scope Contract 的允许文件；本地忽略的 `docs/release-evidence.json` 不进入提交。

- [ ] **Step 4: Phase 0 双层 Review**

先进行 Spec Compliance Review，确认所有 Phase 0 完成条件有证据；通过后再进行 Code Quality Review，确认无宽泛 lint suppress、无动态隐藏 root export、无 branch/commit 脆弱测试和无未声明延期项。

- [ ] **Step 5: 合并确认后清理本地基线 tag**

仅在 Phase 0 分支已完成集成、全量门禁通过并由主工作区确认合并后执行：

```powershell
git tag --delete agentos-phase0-start-20260711
```

不得在最终文件边界检查、双层 Review 或主工作区集成完成前删除该 tag。
