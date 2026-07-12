# AgentOS Phase 1 Task 7B Callsite Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把已提交 Task 7A SHA 的 Context Protocol Kernel 契约迁移到全仓消费者，消除旧构造/旧 schema/旧 System 语义，并通过 Phase 1 全量零失败门禁。

**Architecture:** Task 7B 只迁移调用点、测试契约、示例组装和治理 baseline，不修改 Task 7A 的核心实现或 public API inventory。先建立唯一的测试侧显式 Renderer fixture，再从同一 fixture SHA 创建四个独立 worktree，按互不重叠的 Owner 并行迁移 runtime、channel/multi、observability/example 和 data-boundary；reviewed SHA 固定顺序集成后，串行校正 subprocess 环境契约并生成模块尺寸 baseline，最后以 pytest exit `0` 和 JUnit `failures/errors == 0` 完成全量验收，失败 nodeid 集合只用于诊断。

**Tech Stack:** Python 3.11+、pytest、PowerShell、Ruff、`compileall`、Git worktree、现有 `SystemSectionRegistry`、`HeuristicTokenCounter` 和 module-size generator。

---

## Scope Contract

- **Phase / Active Specs:** Phase 1 Task 7B；`docs/superpowers/specs/2026-07-10-agentos-next-generation-sdk-architecture-design.md`、`docs/superpowers/specs/2026-07-10-agentos-context-protocol-v1-design.md`、`docs/superpowers/plans/2026-07-10-agentos-context-protocol-kernel-implementation-plan.md`。
- **Acceptance Items:** 全仓消费者显式注入 System Registry/TokenCounter；旧 schema fixture 使用协议类型；Runtime Notice、Compression、Memory、Skill/MCP metadata 不再进入 System；示例和测试只从 `ProviderRequest.tools`/Registry 断言 capability；模块尺寸 baseline 与最终源码一致；隔离 subprocess 在声明的运行时依赖环境中通过；全量 pytest exit `0` 且 JUnit failures/errors 均为 `0`。
- **Allowed Files:** 仅本计划 File Responsibility Map 和各 workstream `Allowed Files` 精确列出的文件。
- **Forbidden Files:** `src/agentos/builder.py`、`src/agentos/context/**`、`src/agentos/runtime/provider_request_builder.py`、`docs/public-api-inventory.json`、Task 7A 已修改的 `tests/context/test_renderer.py`、`tests/context/test_debug_projection.py`、`tests/runtime/test_agent_builder.py`、`tests/runtime/test_provider_request_builder.py`，以及未在 workstream 中列出的任何源码、测试、配置或文档。
- **Dependency Boundaries:** 测试 fixture 可以依赖 `default_system_section_registry()` 和 `HeuristicTokenCounter`；业务源码不得依赖 `tests/**`。Tool/Skill/MCP 元数据只从现有 Registry/`ProviderRequest.tools` 读取，不创建新的 truth source。
- **Completed In This Work Package:** Task 7A 契约的全仓调用点迁移、语义断言迁移、module baseline 串行更新、subprocess 环境契约校正和 Phase 1 全量验收。
- **Explicit Deferrals:** `ContextSnapshot` 注入 Provider messages、Phase 2 synthetic snapshot、`ProviderInputItem`、Frontend `ReadModel`、Attachment `ContextMount`、Planner/Memory/Skill 的真实 Snapshot Projection 均不在本任务实现。
- **Verification Commands:** 每个 workstream 的 Red/Green 命令；最终带 JUnit XML 的 `python -m pytest -q`、`python -m compileall -q src tests`、`python -m ruff check src tests`、module baseline/public API inventory checks、协议 drift scan、诊断 nodeid/JUnit cardinality cross-check 和 `git diff --check`。

## Frozen Semantics

Task 7B 必须保留以下已冻结事实：

- 不恢复 `ContextRenderer()` 默认构造；
- 不恢复 `ContextRenderer.render(ContextState)` 或任何有参 `render(...)`；
- 不恢复 `capability_plane=`；
- 不把 Runtime Notice、Working State、Compressed History、Memory、Skill/MCP metadata 写入 `SystemEnvelope`；
- 不创建 `ProviderInputItem`、synthetic snapshot、`ReadModel` 或 `ContextMount`；
- 不用兼容 alias、默认参数、skip/xfail 或宽泛异常捕获掩盖迁移失败。

机械迁移仅包括：显式默认 `SystemSectionRegistry`/`TokenCounter` 组装，以及非负向 schema fixture 的 `str -> string`、`dict/obj -> object`。语义迁移必须改断言 Owner，不能恢复旧 API。

## Current Evidence And Failure Inventory

以下数字只是 2026-07-12 在 Task 7A 未提交工作树上的 provisional/current
observation，用于估算工作量，不是验收常量：

| Gate | Result |
|---|---:|
| `pytest --collect-only -q` | latest provisional baseline `1957 collected` |
| Task 7A 目标集 | currently at least `418 passed` |
| Full suite after quality fixes, before inventory refresh | `197 failed, 1747 passed, 15 skipped` |
| Expected after Task 7A inventory refresh | latest provisional baseline `194` migration failures；fresh JUnit remains authoritative |
| Failure roots | provisional migration roots plus `3` public API inventory drift failures caused by the new `AgentBuilder`/`ProviderRequestBuilder` signatures |

Task 7A 必须先生成并提交 `docs/public-api-inventory.json`，使 architecture public API
inventory tests Green。Task 1 必须在包含该生成物的已提交 Task 7A SHA 上 fresh collect，并从 fresh JUnit XML 重新取得
tests/failures/errors/skipped；任何旧数量都只能帮助诊断，不得作为 pass/fail 条件。
当前失败目录计数为 channels 62、runtime 60、multi 41、observability 11、examples 9、attachments 5、architecture 3、deployment 1、providers 1、compression 1。直接调用点远少于失败 nodeid；例如 `tests/multi/helpers.py` 同时放大 channels/multi 失败，因此按直接 Owner 迁移，不按 provisional `194` 个 nodeid 平铺修改。

## File Responsibility Map

| Owner | Allowed files | Responsibility |
|---|---|---|
| Fixture foundation | `tests/_context_protocol_fixtures.py`、`tests/test_context_protocol_fixtures.py` | 提供测试唯一显式默认 `ContextRenderer` 工厂。 |
| Runtime workstream | `tests/runtime/test_agent_stream_api.py`、`test_async_agent_api.py`、`test_async_query_loop_native.py`、`test_provider_retry.py`、`test_query_loop.py`、`test_query_loop_boundaries.py`、`test_query_loop_hooks.py`、`test_session_recovery.py`、`test_skill_mcp_tool_loop.py`、`test_streaming_query_loop.py`、`test_streaming_tool_loop.py`、`test_tool_loop.py` | Runtime 直接调用、notice、schema、skill/MCP 语义。 |
| Channel/multi workstream | `tests/multi/helpers.py`、`tests/multi/test_continuation.py`、`tests/multi/test_coordination_integration.py`、`tests/channels/test_sse_channel.py`、`tests/integration/test_distributed_planner_worker_flow.py` | 共享 Agent factory 与 channel/multi/integration 直接调用。 |
| Observability/example workstream | `src/agentos/examples/small_openai_agent.py`、`tests/examples/test_small_openai_agent.py`、`tests/observability/test_query_loop_instrumentation.py`、`tests/observability/test_structured_logging.py` | 示例显式组装；tool/skill metadata 只断言 Registry/tools。 |
| Data-boundary workstream | `tests/attachments/test_turn_scoped_image_lifecycle.py`、`tests/compression/test_runtime.py`、`tests/architecture/test_phase7_memory_boundaries.py`、`tests/providers/test_provider_messages.py`、`tests/persistence/test_serializers.py` | Attachment/schema、Compression/Memory state boundary、Provider typing、持久化 fixture。 |
| Subprocess environment | `tests/deployment/test_live_backend_probe_pack.py` | 校正 `-S` 与新直接运行时依赖的冲突，保留 cwd/PYTHONPATH 隔离。 |
| Serial governance | `docs/governance/agentos-module-size-baseline.json` | 所有迁移合并后生成源码尺寸事实。 |

所有 workstream 都禁止修改其他 workstream 的 Allowed Files。`tests/_context_protocol_fixtures.py` 在 Task 1 提交后只读；并行 worker 不得顺手扩展它。

## Parallel Dependency Graph

```text
Task 1: fixture + failure-name baselines
  |
  +--> four branches/worktrees from the Task 1 fixture SHA
         +--> Task 2: runtime --------------------+
         +--> Task 3: channels / multi -----------+--> reviewed SHAs
         +--> Task 4: observability / examples ---+       |
         +--> Task 5: data boundaries ------------+       v
                                                   fixed-order cherry-picks
                                                           |
                                                           v
                                             Task 6: subprocess environment (serial)
                                                           |
                                                           v
                                             Task 7: module baseline (serial)
                                                           |
                                                           v
                                             Task 8: full gates + final dual review
```

Task 2-5 是四个可并行 workstream，文件 Owner 不重叠。它们必须从同一个已提交
Task 1 fixture SHA 创建独立 branch/worktree。Task 6 和 Task 7 是两个串行收口任务；
Task 7 必须等待四个 reviewed worker SHA 按固定顺序 cherry-pick 且 Task 6 Green。
禁止任何并行 worker 修改 subprocess 环境契约或生成 module baseline。

## Common Review And Commit Rules

每个 workstream 必须严格按以下顺序执行：Red -> Green/static/module checks -> 精确
`git add -- ...` -> Spec Compliance Review -> Code Quality Review -> finding 修复后重新
Green、精确 staging 和两层复审 -> 最终 commit/SHA。reviewer 审查 staged diff；禁止
先 commit 后 Review。

1. **Spec Compliance Review:** 检查 System/Data authority、Owner、禁止 API、明确 deferral 和 Allowed Files。
2. **Code Quality Review:** 检查测试没有复制核心实现、没有新兼容桥、没有宽泛 skip、导入和断言保持最小。

Critical/Important finding 修复后必须重新运行该 workstream Green/static 命令、重新
精确 staging 并重新完成两层 Review。只有无未解决 finding 时才允许创建最终 commit
和记录 SHA。controller 只接受该 reviewed commit SHA。只允许计划列出的精确
`git add -- ...`；禁止 `git add .`。

## Worktree Coordination

Task 7B 的 base 必须是包含 Main Kernel Plan Task 7A `Files` 全部修改/删除以及生成后的
`docs/public-api-inventory.json` 的已提交
SHA。不得使用“未提交但已冻结”的工作树。controller 在 Task 1 开始前执行：

```powershell
$task7bBase = $env:AGENTOS_TASK7B_BASE
if ([string]::IsNullOrWhiteSpace($task7bBase)) { throw "AGENTOS_TASK7B_BASE is required" }
$resolvedBase = (git rev-parse "$task7bBase^{commit}").Trim()
$baseExit = $LASTEXITCODE
if ($baseExit -ne 0) { throw "Task 7B base is not a commit: $task7bBase" }
$headSha = (git rev-parse HEAD).Trim()
$headExit = $LASTEXITCODE
if ($headExit -ne 0 -or $headSha -ne $resolvedBase) {
    throw "HEAD must equal committed Task 7A SHA $resolvedBase before Task 1"
}
$dirty = @(git status --porcelain)
$statusExit = $LASTEXITCODE
if ($statusExit -ne 0 -or $dirty.Count -gt 0) {
    throw "Task 7B must start from a clean committed Task 7A worktree"
}
$baseFiles = @(git diff-tree --no-commit-id --name-only -r $resolvedBase)
$baseFilesExit = $LASTEXITCODE
if ($baseFilesExit -ne 0) { throw "Cannot inspect Task 7A base files" }
$requiredTask7AFiles = @(
    'src/agentos/context/__init__.py',
    'src/agentos/runtime/provider_request_builder.py',
    'src/agentos/builder.py',
    'tests/runtime/test_provider_request_builder.py',
    'tests/runtime/test_agent_builder.py',
    'tests/context/test_renderer.py',
    'tests/context/test_debug_projection.py',
    'tests/context/test_capability_plane_phase5.py',
    'tests/context/goldens/default_context.md',
    'docs/public-api-inventory.json'
)
$missingBaseFiles = @($requiredTask7AFiles | Where-Object { $_ -notin $baseFiles })
if ($missingBaseFiles.Count -gt 0) {
    throw "Task 7A base commit is incomplete: $($missingBaseFiles -join ', ')"
}
$sharedPython = (Resolve-Path '.\.venv\Scripts\python.exe').Path
if (-not (Test-Path -LiteralPath $sharedPython -PathType Leaf)) {
    throw "Shared Python does not exist: $sharedPython"
}
$controllerSourceRoot = (Resolve-Path '.\src').Path

function Set-AgentosSourceRoot {
    param(
        [Parameter(Mandatory = $true)][string]$Python,
        [Parameter(Mandatory = $true)][string]$SourceRoot
    )
    $resolvedSourceRoot = [System.IO.Path]::GetFullPath($SourceRoot)
    if (-not (Test-Path -LiteralPath $resolvedSourceRoot -PathType Container)) {
        throw "AgentOS SourceRoot does not exist: $resolvedSourceRoot"
    }
    $separator = [System.IO.Path]::PathSeparator
    $existingPythonPath = [Environment]::GetEnvironmentVariable('PYTHONPATH', 'Process')
    $entries = if ([string]::IsNullOrWhiteSpace($existingPythonPath)) {
        @()
    } else {
        @($existingPythonPath -split [regex]::Escape([string]$separator))
    }
    $remainingEntries = @($entries | Where-Object { $_ -ne $resolvedSourceRoot })
    $env:PYTHONPATH = (@($resolvedSourceRoot) + $remainingEntries) -join [string]$separator
    $env:AGENTOS_EXPECTED_SOURCE_ROOT = $resolvedSourceRoot
    & $Python -c "import os, pathlib, agentos; expected = pathlib.Path(os.environ['AGENTOS_EXPECTED_SOURCE_ROOT']).resolve(); actual = pathlib.Path(agentos.__file__).resolve(); actual.relative_to(expected); print(actual)" | Out-Host
    $sourceIsolationExit = $LASTEXITCODE
    if ($sourceIsolationExit -ne 0) {
        throw "Shared Python imported agentos outside $resolvedSourceRoot"
    }
}

Set-AgentosSourceRoot -Python $sharedPython -SourceRoot $controllerSourceRoot
```

Task 1 fixture commit 后记录 `$fixtureSha = (git rev-parse HEAD).Trim()`；controller 的
当前 branch 作为 integration branch 留在该 SHA。然后从这个精确 SHA 创建四个 GUID
unique branch/worktree，并记录 Owner、branch、absolute path：

```powershell
$fixtureSha = (git rev-parse HEAD).Trim()
$fixtureShaExit = $LASTEXITCODE
if ($fixtureShaExit -ne 0) { throw "Cannot resolve Task 1 fixture SHA" }
$runId = [guid]::NewGuid().ToString('N')
$worktreeRoot = Join-Path ([System.IO.Path]::GetTempPath()) "agentos-task7b-$runId"
$worktreeRoot = [System.IO.Path]::GetFullPath($worktreeRoot)
New-Item -ItemType Directory -Path $worktreeRoot -ErrorAction Stop | Out-Null
$worktrees = @(
    [pscustomobject]@{ Owner = 'runtime'; Branch = "task7b/runtime-$runId"; Path = [System.IO.Path]::GetFullPath((Join-Path $worktreeRoot 'runtime')); SourceRoot = [System.IO.Path]::GetFullPath((Join-Path $worktreeRoot 'runtime\src')) },
    [pscustomobject]@{ Owner = 'channels-multi'; Branch = "task7b/channels-multi-$runId"; Path = [System.IO.Path]::GetFullPath((Join-Path $worktreeRoot 'channels-multi')); SourceRoot = [System.IO.Path]::GetFullPath((Join-Path $worktreeRoot 'channels-multi\src')) },
    [pscustomobject]@{ Owner = 'observability-examples'; Branch = "task7b/observability-examples-$runId"; Path = [System.IO.Path]::GetFullPath((Join-Path $worktreeRoot 'observability-examples')); SourceRoot = [System.IO.Path]::GetFullPath((Join-Path $worktreeRoot 'observability-examples\src')) },
    [pscustomobject]@{ Owner = 'data-boundaries'; Branch = "task7b/data-boundaries-$runId"; Path = [System.IO.Path]::GetFullPath((Join-Path $worktreeRoot 'data-boundaries')); SourceRoot = [System.IO.Path]::GetFullPath((Join-Path $worktreeRoot 'data-boundaries\src')) }
)
$creationSucceeded = $false
$creationError = $null
try {
    foreach ($record in $worktrees) {
        git worktree add -b $record.Branch $record.Path $fixtureSha
        $worktreeAddExit = $LASTEXITCODE
        if ($worktreeAddExit -ne 0) {
            throw "git worktree add failed for $($record.Owner) with exit code $worktreeAddExit"
        }
    }

    $porcelain = git worktree list --porcelain
    $worktreeListExit = $LASTEXITCODE
    if ($worktreeListExit -ne 0) { throw "Cannot verify created worktree registrations" }
    $registeredPaths = @(
        $porcelain | Where-Object { $_ -like 'worktree *' } | ForEach-Object {
            ([System.IO.Path]::GetFullPath($_.Substring(9).Trim())).TrimEnd(
                [System.IO.Path]::DirectorySeparatorChar,
                [System.IO.Path]::AltDirectorySeparatorChar
            )
        }
    )
    foreach ($record in $worktrees) {
        $normalizedPath = ([System.IO.Path]::GetFullPath($record.Path)).TrimEnd(
            [System.IO.Path]::DirectorySeparatorChar,
            [System.IO.Path]::AltDirectorySeparatorChar
        )
        if ($normalizedPath -notin $registeredPaths) {
            throw "$($record.Owner) worktree is not registered at $normalizedPath"
        }
        $createdHead = (git -C $record.Path rev-parse HEAD).Trim()
        $createdHeadExit = $LASTEXITCODE
        if ($createdHeadExit -ne 0 -or $createdHead -ne $fixtureSha) {
            throw "$($record.Owner) worktree does not point at fixture SHA $fixtureSha"
        }
        $createdBranch = (git -C $record.Path rev-parse --abbrev-ref HEAD).Trim()
        $createdBranchExit = $LASTEXITCODE
        if ($createdBranchExit -ne 0 -or $createdBranch -ne $record.Branch) {
            throw "$($record.Owner) worktree branch verification failed"
        }
        $dirtyBaseline = @(git -C $record.Path status --porcelain)
        $dirtyBaselineExit = $LASTEXITCODE
        if ($dirtyBaselineExit -ne 0 -or $dirtyBaseline.Count -gt 0) {
            throw "$($record.Owner) worktree does not have a clean fixture baseline"
        }
    }
    $creationSucceeded = $true
} catch {
    $creationError = $_
} finally {
    if (-not $creationSucceeded) {
        $cleanupPorcelain = git worktree list --porcelain
        $cleanupListExit = $LASTEXITCODE
        if ($cleanupListExit -ne 0) { throw "Cannot inspect partial worktree registrations" }
        $cleanupRegistered = @(
            $cleanupPorcelain | Where-Object { $_ -like 'worktree *' } | ForEach-Object {
                ([System.IO.Path]::GetFullPath($_.Substring(9).Trim())).TrimEnd(
                    [System.IO.Path]::DirectorySeparatorChar,
                    [System.IO.Path]::AltDirectorySeparatorChar
                )
            }
        )
        $cleanupRecords = @($worktrees)
        [array]::Reverse($cleanupRecords)
        foreach ($record in $cleanupRecords) {
            $normalizedPath = ([System.IO.Path]::GetFullPath($record.Path)).TrimEnd(
                [System.IO.Path]::DirectorySeparatorChar,
                [System.IO.Path]::AltDirectorySeparatorChar
            )
            if ($normalizedPath -in $cleanupRegistered) {
                git worktree remove --force -- $normalizedPath
                $cleanupRemoveExit = $LASTEXITCODE
                if ($cleanupRemoveExit -ne 0) {
                    throw "Failed to remove partial worktree $normalizedPath"
                }
                $afterRemove = git worktree list --porcelain
                $afterRemoveExit = $LASTEXITCODE
                $afterRegistered = @(
                    $afterRemove | Where-Object { $_ -like 'worktree *' } | ForEach-Object {
                        ([System.IO.Path]::GetFullPath($_.Substring(9).Trim())).TrimEnd(
                            [System.IO.Path]::DirectorySeparatorChar,
                            [System.IO.Path]::AltDirectorySeparatorChar
                        )
                    }
                )
                if ($afterRemoveExit -ne 0 -or $normalizedPath -in $afterRegistered) {
                    throw "Partial worktree registration remains: $normalizedPath"
                }
            }
            git show-ref --verify --quiet "refs/heads/$($record.Branch)"
            $branchExistsExit = $LASTEXITCODE
            if ($branchExistsExit -eq 0) {
                git branch -D -- $record.Branch
                $branchRemoveExit = $LASTEXITCODE
                if ($branchRemoveExit -ne 0) { throw "Failed to remove partial branch $($record.Branch)" }
            } elseif ($branchExistsExit -ne 1) {
                throw "Cannot inspect partial branch $($record.Branch)"
            }
        }
    }
}
if ($null -ne $creationError) { throw $creationError }
$coordination = [pscustomobject]@{
    FixtureSha = $fixtureSha
    SharedPython = $sharedPython
    Integration = [pscustomobject]@{
        WorktreePath = [System.IO.Path]::GetFullPath((git rev-parse --show-toplevel).Trim())
        SourceRoot = $controllerSourceRoot
    }
    Workers = $worktrees
}
$coordinationFile = Join-Path $env:TEMP "agentos-task7b-worktrees-$runId.json"
$coordination | ConvertTo-Json -Depth 4 | Set-Content -Encoding utf8 $coordinationFile
```

Expected: all four worktrees remain only after registration、fixture SHA、branch and clean
baseline checks pass. Any creation failure removes only paths verified in Git's worktree
registry, re-checks their absence, removes only this GUID run's branches, and never uses a
recursive filesystem delete.

controller 必须把 `$sharedPython` 和该 Owner 的 absolute `SourceRoot` 分别作为
`AGENTOS_SHARED_PYTHON=<absolute path>`、`AGENTOS_SOURCE_ROOT=<absolute worktree src>`
写入四个 worker prompt。不得假设 sibling worktree 自带 `.venv`。每个 worker prompt
必须包含上面的 `Set-AgentosSourceRoot` function，并先执行：

```powershell
$python = $env:AGENTOS_SHARED_PYTHON
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "AGENTOS_SHARED_PYTHON does not name a Python executable: $python"
}
$sourceRoot = [System.IO.Path]::GetFullPath($env:AGENTOS_SOURCE_ROOT)
Set-AgentosSourceRoot -Python $python -SourceRoot $sourceRoot
```

每个 worker 完成精确 commit 和两层 Review 后记录最终 SHA。controller 只接受 reviewed
SHA，并按以下固定顺序在 integration branch cherry-pick；每次 cherry-pick 后立即运行该
workstream 的 Green 命令。发生 conflict 时立即停止，保留冲突现场交由 Owner 处理，
绝不自动选择 ours/theirs：

```powershell
$python = $sharedPython
$integrationSourceRoot = $controllerSourceRoot
Set-AgentosSourceRoot -Python $python -SourceRoot $integrationSourceRoot

git cherry-pick $env:AGENTOS_RUNTIME_SHA
$cherryPickExit = $LASTEXITCODE
if ($cherryPickExit -ne 0) { throw "runtime cherry-pick conflicted or failed; stop integration" }
& $python -m pytest tests/runtime/test_agent_stream_api.py tests/runtime/test_async_agent_api.py tests/runtime/test_async_query_loop_native.py tests/runtime/test_provider_retry.py tests/runtime/test_query_loop.py tests/runtime/test_query_loop_boundaries.py tests/runtime/test_query_loop_hooks.py tests/runtime/test_session_recovery.py tests/runtime/test_skill_mcp_tool_loop.py tests/runtime/test_streaming_query_loop.py tests/runtime/test_streaming_tool_loop.py tests/runtime/test_tool_loop.py -q
$runtimeGreenExit = $LASTEXITCODE
if ($runtimeGreenExit -ne 0) { throw "runtime post-cherry-pick Green failed with exit code $runtimeGreenExit" }

git cherry-pick $env:AGENTOS_CHANNELS_MULTI_SHA
$cherryPickExit = $LASTEXITCODE
if ($cherryPickExit -ne 0) { throw "channels/multi cherry-pick conflicted or failed; stop integration" }
& $python -m pytest tests/channels tests/multi tests/integration/test_distributed_planner_worker_flow.py -q
$channelsGreenExit = $LASTEXITCODE
if ($channelsGreenExit -ne 0) { throw "channels/multi post-cherry-pick Green failed with exit code $channelsGreenExit" }

git cherry-pick $env:AGENTOS_OBSERVABILITY_EXAMPLES_SHA
$cherryPickExit = $LASTEXITCODE
if ($cherryPickExit -ne 0) { throw "observability/examples cherry-pick conflicted or failed; stop integration" }
& $python -m pytest tests/examples/test_small_openai_agent.py tests/observability/test_query_loop_instrumentation.py tests/observability/test_structured_logging.py -q
$observabilityGreenExit = $LASTEXITCODE
if ($observabilityGreenExit -ne 0) { throw "observability/examples post-cherry-pick Green failed with exit code $observabilityGreenExit" }

git cherry-pick $env:AGENTOS_DATA_BOUNDARIES_SHA
$cherryPickExit = $LASTEXITCODE
if ($cherryPickExit -ne 0) { throw "data-boundaries cherry-pick conflicted or failed; stop integration" }
& $python -m pytest tests/attachments/test_turn_scoped_image_lifecycle.py tests/compression/test_runtime.py tests/architecture/test_phase7_memory_boundaries.py tests/providers/test_provider_messages.py tests/persistence/test_serializers.py -q
$dataGreenExit = $LASTEXITCODE
if ($dataGreenExit -ne 0) { throw "data-boundaries post-cherry-pick Green failed with exit code $dataGreenExit" }
```

---

### Task 1: Freeze Failure Name Baselines And Add Explicit Test Renderer Fixture

**Allowed Files:** `tests/_context_protocol_fixtures.py`、`tests/test_context_protocol_fixtures.py`。

**Forbidden Files:** 全部 `src/**`、其他 `tests/**`、module baseline 和 Task 7A 文件。

- [ ] **Step 1: Fresh collect from committed Task 7A, capture JUnit Red evidence, and run a diagnostic-only Phase 0 audit**

```powershell
$python = $sharedPython
$controllerSourceRoot = (Resolve-Path '.\src').Path
Set-AgentosSourceRoot -Python $python -SourceRoot $controllerSourceRoot
& $python -m pytest --collect-only -q *> "$env:TEMP\agentos-task7b-collect.txt"
$collectExit = $LASTEXITCODE
if ($collectExit -ne 0) { throw "fresh Task 7B collection failed with exit code $collectExit" }

$task7bRedXml = "$env:TEMP\agentos-task7b-red.xml"
$task7bRedText = "$env:TEMP\agentos-task7b-red.txt"
& $python -m pytest -q --tb=no --junitxml=$task7bRedXml *> $task7bRedText
$task7bRedExit = $LASTEXITCODE
if ($task7bRedExit -notin @(0, 1)) {
    throw "Task 7B Red run had infrastructure/usage exit code $task7bRedExit"
}

[xml]$task7bJunit = Get-Content -Raw -LiteralPath $task7bRedXml
$task7bRootName = $task7bJunit.DocumentElement.LocalName
$task7bSuites = if ($task7bRootName -eq 'testsuites') {
    @($task7bJunit.DocumentElement.SelectNodes('./testsuite'))
} elseif ($task7bRootName -eq 'testsuite') {
    @($task7bJunit.DocumentElement)
} else {
    throw "Unexpected JUnit root: $task7bRootName"
}
if ($task7bSuites.Count -eq 0) {
    throw "JUnit root $task7bRootName contains no testsuite elements"
}
$task7bFailures = [int](($task7bSuites | Measure-Object -Property failures -Sum).Sum)
$task7bErrors = [int](($task7bSuites | Measure-Object -Property errors -Sum).Sum)
$task7bTests = [int](($task7bSuites | Measure-Object -Property tests -Sum).Sum)
$task7bSkipped = [int](($task7bSuites | Measure-Object -Property skipped -Sum).Sum)

$task7bNodeids = @(
    Get-Content -LiteralPath $task7bRedText |
        Where-Object { $_ -match '^(FAILED|ERROR)\s+(\S+)' } |
        ForEach-Object { [regex]::Match($_, '^(FAILED|ERROR)\s+(\S+)').Groups[2].Value } |
        Sort-Object -Unique
)
$task7bNodeids | Set-Content -Encoding utf8 "$env:TEMP\agentos-task7b-red-nodeids.txt"
if ($task7bNodeids.Count -ne ($task7bFailures + $task7bErrors)) {
    throw "Text nodeid count $($task7bNodeids.Count) disagrees with authoritative JUnit failures+errors $($task7bFailures + $task7bErrors)"
}
```

Expected: fresh collection and JUnit totals are recorded from the committed Task 7A SHA.
The latest pre-inventory observation is `197 failed, 1747 passed, 15 skipped`; after the three
inventory drift failures are fixed, the latest provisional baseline is `1957 collected` with
approximately `194` migration failures, but
no exact count is an acceptance constant. JUnit is authoritative for
counts. Text parsing exists only to mirror exact pytest summary nodeids, can miss formats pytest
does not print as `FAILED|ERROR <nodeid>`, and therefore must match JUnit failure+error cardinality
before the diagnostic nodeid list is trusted.

用已知 Phase 0 baseline commit `0d60dd0ad1d07870c0e341f8035c6753275eafb7` 生成
diagnostic-only 历史证据：

```powershell
$phase0 = [System.IO.Path]::GetFullPath(
    (Join-Path $env:TEMP "agentos-phase0-failure-audit-$([guid]::NewGuid().ToString('N'))")
)
$normalizedPhase0 = ([System.IO.Path]::GetFullPath($phase0)).TrimEnd(
    [System.IO.Path]::DirectorySeparatorChar,
    [System.IO.Path]::AltDirectorySeparatorChar
)
$priorPythonPath = [Environment]::GetEnvironmentVariable('PYTHONPATH', 'Process')
$priorExpectedSourceRoot = [Environment]::GetEnvironmentVariable('AGENTOS_EXPECTED_SOURCE_ROOT', 'Process')
try {
    git worktree add --detach $phase0 0d60dd0ad1d07870c0e341f8035c6753275eafb7
    $worktreeAddExit = $LASTEXITCODE
    $addPorcelain = git worktree list --porcelain
    $addListExit = $LASTEXITCODE
    if ($addListExit -ne 0) { throw "Cannot inspect Phase 0 registration after add" }
    $addRegistered = @(
        $addPorcelain | Where-Object { $_ -like 'worktree *' } | ForEach-Object {
            ([System.IO.Path]::GetFullPath($_.Substring(9).Trim())).TrimEnd(
                [System.IO.Path]::DirectorySeparatorChar,
                [System.IO.Path]::AltDirectorySeparatorChar
            )
        }
    )
    if ($worktreeAddExit -ne 0) {
        throw "Phase 0 git worktree add failed with exit code $worktreeAddExit"
    }
    if ($normalizedPhase0 -notin $addRegistered) {
        throw "Phase 0 worktree add returned success without registration"
    }

    Push-Location $phase0
    try {
        $phase0SourceRoot = [System.IO.Path]::GetFullPath((Join-Path $phase0 'src'))
        Set-AgentosSourceRoot -Python $python -SourceRoot $phase0SourceRoot
        $phase0Xml = "$env:TEMP\agentos-phase0-red.xml"
        $phase0Text = "$env:TEMP\agentos-phase0-red.txt"
        & $python -m pytest -q --tb=no --junitxml=$phase0Xml *> $phase0Text
        $phase0PytestExit = $LASTEXITCODE
        if ($phase0PytestExit -notin @(0, 1)) {
            throw "Phase 0 diagnostic pytest had exit code $phase0PytestExit"
        }
    } finally {
        $env:PYTHONPATH = $priorPythonPath
        $env:AGENTOS_EXPECTED_SOURCE_ROOT = $priorExpectedSourceRoot
        Pop-Location
    }

    [xml]$phase0Junit = Get-Content -Raw -LiteralPath $phase0Xml
    $phase0RootName = $phase0Junit.DocumentElement.LocalName
    $phase0Suites = if ($phase0RootName -eq 'testsuites') {
        @($phase0Junit.DocumentElement.SelectNodes('./testsuite'))
    } elseif ($phase0RootName -eq 'testsuite') {
        @($phase0Junit.DocumentElement)
    } else {
        throw "Unexpected Phase 0 JUnit root: $phase0RootName"
    }
    if ($phase0Suites.Count -eq 0) {
        throw "Phase 0 JUnit root $phase0RootName contains no testsuite elements"
    }
    $phase0Failures = [int](($phase0Suites | Measure-Object -Property failures -Sum).Sum)
    $phase0Errors = [int](($phase0Suites | Measure-Object -Property errors -Sum).Sum)
    $phase0Nodeids = @(
        Get-Content -LiteralPath $phase0Text |
            Where-Object { $_ -match '^(FAILED|ERROR)\s+(\S+)' } |
            ForEach-Object { [regex]::Match($_, '^(FAILED|ERROR)\s+(\S+)').Groups[2].Value } |
            Sort-Object -Unique
    )
    $phase0Nodeids | Set-Content -Encoding utf8 "$env:TEMP\agentos-phase0-red-nodeids.txt"
    if ($phase0Nodeids.Count -ne ($phase0Failures + $phase0Errors)) {
        throw "Phase 0 text nodeids disagree with authoritative JUnit cardinality"
    }
} finally {
    $cleanupPorcelain = git worktree list --porcelain
    $cleanupListExit = $LASTEXITCODE
    if ($cleanupListExit -ne 0) {
        throw "Cannot inspect Phase 0 registration during cleanup"
    }
    $cleanupRegistered = @(
        $cleanupPorcelain | Where-Object { $_ -like 'worktree *' } | ForEach-Object {
            ([System.IO.Path]::GetFullPath($_.Substring(9).Trim())).TrimEnd(
                [System.IO.Path]::DirectorySeparatorChar,
                [System.IO.Path]::AltDirectorySeparatorChar
            )
        }
    )
    if ($normalizedPhase0 -in $cleanupRegistered) {
        git worktree remove -- $normalizedPhase0
        $worktreeRemoveExit = $LASTEXITCODE
        if ($worktreeRemoveExit -ne 0) {
            throw "Phase 0 worktree removal failed with exit code $worktreeRemoveExit"
        }
        $afterRemovePorcelain = git worktree list --porcelain
        $afterRemoveListExit = $LASTEXITCODE
        if ($afterRemoveListExit -ne 0) {
            throw "Cannot verify Phase 0 registration removal"
        }
        $afterRemoveRegistered = @(
            $afterRemovePorcelain | Where-Object { $_ -like 'worktree *' } | ForEach-Object {
                ([System.IO.Path]::GetFullPath($_.Substring(9).Trim())).TrimEnd(
                    [System.IO.Path]::DirectorySeparatorChar,
                    [System.IO.Path]::AltDirectorySeparatorChar
                )
            }
        )
        if ($normalizedPhase0 -in $afterRemoveRegistered) {
            throw "Phase 0 worktree registration remains after removal: $normalizedPhase0"
        }
    }
}
```

Expected: Phase 0 JUnit counts and exact text nodeids are retained only as diagnostic context.
They are never an allowlist, never weaken Task 7B gates, and never permit a non-zero final suite.
The GUID path is removed whenever Git actually registered it, including a partial non-zero add；
removal is followed by a second registration check. If the path was never registered, the plan
does not recursively delete or otherwise mutate that unverified directory.

- [ ] **Step 2: Write the fixture Red test**

Create `tests/test_context_protocol_fixtures.py`:

```python
from agentos.context import ContextRenderer, SystemEnvelope

from tests._context_protocol_fixtures import default_context_renderer


def test_default_context_renderer_fixture_builds_explicit_system_envelope() -> None:
    renderer = default_context_renderer()

    assert isinstance(renderer, ContextRenderer)
    envelope = renderer.render()
    assert isinstance(envelope, SystemEnvelope)
    assert "# Runtime Contract" in envelope.text
```

- [ ] **Step 3: Run Red**

```powershell
& $python -m pytest tests/test_context_protocol_fixtures.py -q
$fixtureRedExit = $LASTEXITCODE
if ($fixtureRedExit -ne 1) { throw "fixture Red must fail with pytest exit 1, got $fixtureRedExit" }
```

Expected: FAIL because `tests._context_protocol_fixtures` does not exist.

- [ ] **Step 4: Add the minimal fixture**

Create `tests/_context_protocol_fixtures.py`:

```python
from agentos.context import ContextRenderer
from agentos.context.projection import default_system_section_registry
from agentos.tokens import HeuristicTokenCounter


def default_context_renderer() -> ContextRenderer:
    return ContextRenderer(
        registry=default_system_section_registry(),
        token_counter=HeuristicTokenCounter(),
    )
```

- [ ] **Step 5: Run Green/static checks and stage exactly**

```powershell
& $python -m pytest tests/test_context_protocol_fixtures.py tests/context/test_system_envelope_renderer.py -q
$fixtureGreenExit = $LASTEXITCODE
if ($fixtureGreenExit -ne 0) { throw "fixture Green failed with exit code $fixtureGreenExit" }
git diff --check
$diffCheckExit = $LASTEXITCODE
if ($diffCheckExit -ne 0) { throw "fixture diff check failed with exit code $diffCheckExit" }
git add -- tests/_context_protocol_fixtures.py tests/test_context_protocol_fixtures.py
```

- [ ] **Step 6: Spec Compliance Review the staged fixture diff**

Reviewer confirms the fixture is tests-only、uses explicit Registry/TokenCounter dependencies、
does not restore a default constructor and changes only the two Allowed Files.

- [ ] **Step 7: Code Quality Review the staged fixture diff**

Reviewer confirms the helper is minimal、contains no production truth source、does not copy
renderer implementation and leaves the negative constructor tests intact.

- [ ] **Step 8: Resolve findings and repeat Green、staging and both reviews**

Any finding routes back to its Owner. Re-run the Step 5 pytest and `git diff --check`, execute the
same exact `git add -- ...`, then repeat Steps 6-7. Do not commit while any finding remains.

- [ ] **Step 9: Commit the reviewed staged diff and record the fixture SHA**

```powershell
git commit -m "test: add explicit context renderer fixture"
$fixtureCommitExit = $LASTEXITCODE
if ($fixtureCommitExit -ne 0) { throw "fixture commit failed with exit code $fixtureCommitExit" }
$fixtureSha = (git rev-parse HEAD).Trim()
$fixtureShaExit = $LASTEXITCODE
if ($fixtureShaExit -ne 0) { throw "cannot record fixture commit SHA" }
$fixtureSha | Set-Content -Encoding ascii "$env:TEMP\agentos-task7b-fixture-sha.txt"
```

Expected: PASS；核心 constructor 的负向测试仍证明缺少 `registry`/`token_counter` 会失败；
Task 1 fixture commit SHA is recorded and is the exact base for all four worker worktrees.

---

### Task 2: Runtime Workstream - Mechanical Calls And Runtime Semantics

**Allowed Files:** 仅 File Responsibility Map 的 Runtime workstream 十二个文件。

**Forbidden Files:** 全部 `src/**`、fixture foundation、其他 workstream、Task 7A 文件和 module baseline。

- [ ] **Step 1: Run the existing Runtime Red set**

```powershell
$python = $env:AGENTOS_SHARED_PYTHON
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "AGENTOS_SHARED_PYTHON does not name a Python executable: $python"
}
$sourceRoot = [System.IO.Path]::GetFullPath($env:AGENTOS_SOURCE_ROOT)
Set-AgentosSourceRoot -Python $python -SourceRoot $sourceRoot
& $python -m pytest tests/runtime/test_agent_stream_api.py tests/runtime/test_async_agent_api.py tests/runtime/test_async_query_loop_native.py tests/runtime/test_provider_retry.py tests/runtime/test_query_loop.py tests/runtime/test_query_loop_boundaries.py tests/runtime/test_query_loop_hooks.py tests/runtime/test_session_recovery.py tests/runtime/test_skill_mcp_tool_loop.py tests/runtime/test_streaming_query_loop.py tests/runtime/test_streaming_tool_loop.py tests/runtime/test_tool_loop.py -q
$runtimeRedExit = $LASTEXITCODE
if ($runtimeRedExit -ne 1) { throw "runtime Red must exit 1, got $runtimeRedExit" }
```

Expected: FAIL only from missing explicit Renderer dependencies, rejected `capability_plane=`, and invalid legacy schema fixtures.

- [ ] **Step 2: Apply mechanical constructor and schema migration**

In every allowed file, replace production-style test construction with:

```python
from tests._context_protocol_fixtures import default_context_renderer

request_builder = ProviderRequestBuilder(
    context_renderer=default_context_renderer(),
    message_runtime=messages,
    tools=tools,
)
```

Change non-negative fixtures exactly as follows:

```python
WorkingStateField(name="task_goal", type="string", purpose="...")
WorkingStateField(name="drawing_info", type="object", purpose="...")
```

Do not change the intentional legacy-type rejection tests in `tests/context/test_context_state_projection.py`.

- [ ] **Step 3: Move Runtime Notice assertions away from System**

In `test_agent_continuation_injects_notice_without_user_message` and the stream-close regression, retain continuation execution/consumption assertions but replace old System visibility expectations with:

```python
system = provider.requests[0].system
assert "# Runtime Notice" not in system
assert "Task task_1 completed." not in system
assert context.snapshot().runtime_notices == ()
assert notice_provider.calls == 1
```

Do not introduce a synthetic Provider message. Phase 2 owns Snapshot delivery.

- [ ] **Step 4: Move Skill/MCP metadata assertions to Registry and tools**

Remove `CapabilityPlane` imports and constructor arguments from `test_skill_mcp_tool_loop.py`. Keep tool execution assertions and add/retain:

```python
tool_names = [tool["function"]["name"] for tool in provider.requests[0].tools]
assert "load_skill" in tool_names
assert "code-review" not in provider.requests[0].system

mcp_tool_names = [tool["function"]["name"] for tool in provider.requests[0].tools]
assert "mcp__docs__lookup" in mcp_tool_names
assert "docs" not in provider.requests[0].system
```

Skill body and MCP result remain Tool Results in active messages; metadata does not enter System.

- [ ] **Step 5: Preserve recovery truth and exclude dynamic state from System**

In `test_session_recovery.py`, keep snapshot/message/compression/recall round-trip assertions, assert restored Working State explicitly, and assert the dynamic goal is absent from both trusted envelopes:

```python
assert restored_context.snapshot().working_state["task_goal"] == "Recover session."
assert request.system == rendered_before_save
assert "Recover session." not in request.system
```

- [ ] **Step 6: Run Green/static checks and stage exactly**

```powershell
& $python -m pytest tests/runtime/test_agent_stream_api.py tests/runtime/test_async_agent_api.py tests/runtime/test_async_query_loop_native.py tests/runtime/test_provider_retry.py tests/runtime/test_query_loop.py tests/runtime/test_query_loop_boundaries.py tests/runtime/test_query_loop_hooks.py tests/runtime/test_session_recovery.py tests/runtime/test_skill_mcp_tool_loop.py tests/runtime/test_streaming_query_loop.py tests/runtime/test_streaming_tool_loop.py tests/runtime/test_tool_loop.py -q
$runtimeGreenExit = $LASTEXITCODE
if ($runtimeGreenExit -ne 0) { throw "runtime Green failed with exit code $runtimeGreenExit" }
git diff --check
$diffCheckExit = $LASTEXITCODE
if ($diffCheckExit -ne 0) { throw "runtime diff check failed with exit code $diffCheckExit" }
git add -- tests/runtime/test_agent_stream_api.py tests/runtime/test_async_agent_api.py tests/runtime/test_async_query_loop_native.py tests/runtime/test_provider_retry.py tests/runtime/test_query_loop.py tests/runtime/test_query_loop_boundaries.py tests/runtime/test_query_loop_hooks.py tests/runtime/test_session_recovery.py tests/runtime/test_skill_mcp_tool_loop.py tests/runtime/test_streaming_query_loop.py tests/runtime/test_streaming_tool_loop.py tests/runtime/test_tool_loop.py
```

- [ ] **Step 7: Spec Compliance Review the staged Runtime diff**

Reviewer checks System/Data authority、Runtime Notice deferral、Skill/MCP tools-only metadata、
Allowed Files and all forbidden legacy APIs.

- [ ] **Step 8: Code Quality Review the staged Runtime diff**

Reviewer checks direct factories rather than mass edits、minimal imports、no duplicated core
implementation、no skip/xfail and preserved recovery assertions.

- [ ] **Step 9: Resolve findings and repeat Green、staging and both reviews**

Re-run the exact Step 6 pytest and `git diff --check`, repeat the exact staging command, then
repeat Steps 7-8 after every finding fix. Do not create a provisional commit for review.

- [ ] **Step 10: Commit the reviewed staged diff and record the Runtime SHA**

```powershell
git commit -m "test: migrate runtime context renderer callsites"
$runtimeCommitExit = $LASTEXITCODE
if ($runtimeCommitExit -ne 0) { throw "runtime commit failed with exit code $runtimeCommitExit" }
$runtimeSha = (git rev-parse HEAD).Trim()
$runtimeShaExit = $LASTEXITCODE
if ($runtimeShaExit -ne 0) { throw "cannot record runtime commit SHA" }
$runtimeSha | Set-Content -Encoding ascii "$env:TEMP\agentos-task7b-runtime-sha.txt"
```

Expected: all listed runtime tests PASS；no old constructor, `capability_plane=` or non-negative legacy schema remains in these files.

---

### Task 3: Channel And Multi Workstream - Shared Agent Factories

**Allowed Files:** `tests/multi/helpers.py`、`tests/multi/test_continuation.py`、`tests/multi/test_coordination_integration.py`、`tests/channels/test_sse_channel.py`、`tests/integration/test_distributed_planner_worker_flow.py`。

**Forbidden Files:** `src/**`、其他 channel/multi tests、fixture foundation、其他 workstream、Task 7A 文件和 module baseline。

- [ ] **Step 1: Run the amplified Red set**

```powershell
$python = $env:AGENTOS_SHARED_PYTHON
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "AGENTOS_SHARED_PYTHON does not name a Python executable: $python"
}
$sourceRoot = [System.IO.Path]::GetFullPath($env:AGENTOS_SOURCE_ROOT)
Set-AgentosSourceRoot -Python $python -SourceRoot $sourceRoot
& $python -m pytest tests/channels tests/multi tests/integration/test_distributed_planner_worker_flow.py -q
$channelsRedExit = $LASTEXITCODE
if ($channelsRedExit -ne 1) { throw "channels/multi Red must exit 1, got $channelsRedExit" }
```

Expected: FAIL through direct old constructors in the five Allowed Files, especially the shared `tests/multi/helpers.py` factory.

- [ ] **Step 2: Migrate only direct Owner files**

Import `default_context_renderer` and replace every non-negative `ContextRenderer()` in the Allowed Files. Do not edit the dozens of downstream tests that only consume `build_agent_with_response`; their Green transition must prove the shared factory migration is sufficient.

- [ ] **Step 3: Run Green/static checks and stage exactly**

```powershell
& $python -m pytest tests/channels tests/multi tests/integration/test_distributed_planner_worker_flow.py -q
$channelsGreenExit = $LASTEXITCODE
if ($channelsGreenExit -ne 0) { throw "channels/multi Green failed with exit code $channelsGreenExit" }
git diff --check
$diffCheckExit = $LASTEXITCODE
if ($diffCheckExit -ne 0) { throw "channels/multi diff check failed with exit code $diffCheckExit" }
git add -- tests/multi/helpers.py tests/multi/test_continuation.py tests/multi/test_coordination_integration.py tests/channels/test_sse_channel.py tests/integration/test_distributed_planner_worker_flow.py
```

- [ ] **Step 4: Spec Compliance Review the staged Channel/multi diff**

Reviewer confirms only direct Owner factories changed、the shared fixture remains read-only and
no System/Data boundary or forbidden API was restored.

- [ ] **Step 5: Code Quality Review the staged Channel/multi diff**

Reviewer confirms downstream tests Green through the shared factory、no mass edits、no copied
renderer construction logic and no skip/xfail.

- [ ] **Step 6: Resolve findings and repeat Green、staging and both reviews**

Re-run the exact Step 3 pytest and `git diff --check`, repeat the exact staging command, then
repeat Steps 4-5. No commit is allowed while a finding remains.

- [ ] **Step 7: Commit the reviewed staged diff and record the Channel/multi SHA**

```powershell
git commit -m "test: migrate channel and multi renderer fixtures"
$channelsCommitExit = $LASTEXITCODE
if ($channelsCommitExit -ne 0) { throw "channels/multi commit failed with exit code $channelsCommitExit" }
$channelsSha = (git rev-parse HEAD).Trim()
$channelsShaExit = $LASTEXITCODE
if ($channelsShaExit -ne 0) { throw "cannot record channels/multi commit SHA" }
$channelsSha | Set-Content -Encoding ascii "$env:TEMP\agentos-task7b-channels-multi-sha.txt"
```

Expected: channels/multi/integration set PASS with pytest exit `0`. Phase 0 diagnostics
cannot authorize any retained failure.

---

### Task 4: Observability And Example Workstream - Remove Capability Plane Semantics

**Allowed Files:** `src/agentos/examples/small_openai_agent.py`、`tests/examples/test_small_openai_agent.py`、`tests/observability/test_query_loop_instrumentation.py`、`tests/observability/test_structured_logging.py`。

**Forbidden Files:** 所有其他 `src/**`/`tests/**`、fixture foundation、Task 7A 文件和 module baseline。

- [ ] **Step 1: Run Red**

```powershell
$python = $env:AGENTOS_SHARED_PYTHON
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "AGENTOS_SHARED_PYTHON does not name a Python executable: $python"
}
$sourceRoot = [System.IO.Path]::GetFullPath($env:AGENTOS_SOURCE_ROOT)
Set-AgentosSourceRoot -Python $python -SourceRoot $sourceRoot
& $python -m pytest tests/examples/test_small_openai_agent.py tests/observability/test_query_loop_instrumentation.py tests/observability/test_structured_logging.py -q
$observabilityRedExit = $LASTEXITCODE
if ($observabilityRedExit -ne 1) { throw "observability/examples Red must exit 1, got $observabilityRedExit" }
```

Expected: pytest exit `1`; current provisional evidence is approximately `20` failures from
rejected `capability_plane=` or missing explicit Renderer dependencies, but the count is not a gate.

- [ ] **Step 2: Build the example Renderer explicitly**

Remove `CapabilityPlane` from the example and add a local assembly helper:

```python
from agentos.context.projection import default_system_section_registry
from agentos.tokens import HeuristicTokenCounter


def _default_context_renderer() -> ContextRenderer:
    return ContextRenderer(
        registry=default_system_section_registry(),
        token_counter=HeuristicTokenCounter(),
    )
```

Use `_default_context_renderer()` in `build_agent()`. Registered tool schemas continue to come only from `capabilities.tool_specs()`.

- [ ] **Step 3: Rewrite example capability assertions**

Rename `test_build_agent_renders_capability_plane_from_registered_tools` to `test_build_agent_exposes_registered_tools_only_through_request_tools` and assert:

```python
request = provider.requests[0]
tool_names = [tool["function"]["name"] for tool in request.tools]
assert "recall_context" in tool_names
assert "read_file" in tool_names
assert "Registered tools" not in request.system
assert "read_file" not in request.system
```

- [ ] **Step 4: Rewrite observability capability assertions**

Use `default_context_renderer()` in observability tests. For read-file and Skill cases, verify spans as before, verify the expected schema in `provider.requests[*].tools`, and assert tool/skill metadata is absent from System. Do not change instrumentation production code.

- [ ] **Step 5: Record the 300-499 line responsibility review**

`src/agentos/examples/small_openai_agent.py` is currently `438` lines. Record that it remains
one example assembly/CLI responsibility, this work adds only the Renderer assembly helper, and
it introduces no new Owner or truth source. After editing, measure the file again:

```powershell
$exampleLines = (Get-Content -Encoding utf8 src/agentos/examples/small_openai_agent.py).Count
if ($exampleLines -ge 500) {
    throw "small_openai_agent.py is $exampleLines lines; stop, split responsibility, and update the plan"
}
```

Expected: file remains below 500 lines. A result at or above 500 blocks Green and requires an
approved split/update rather than a size exception.

- [ ] **Step 6: Run Green/static/size checks and stage exactly**

```powershell
& $python -m pytest tests/examples/test_small_openai_agent.py tests/observability/test_query_loop_instrumentation.py tests/observability/test_structured_logging.py -q
$observabilityGreenExit = $LASTEXITCODE
if ($observabilityGreenExit -ne 0) { throw "observability/examples Green failed with exit code $observabilityGreenExit" }
$exampleLines = (Get-Content -Encoding utf8 src/agentos/examples/small_openai_agent.py).Count
if ($exampleLines -ge 500) { throw "small_openai_agent.py must remain below 500 lines" }
git diff --check
$diffCheckExit = $LASTEXITCODE
if ($diffCheckExit -ne 0) { throw "observability/examples diff check failed with exit code $diffCheckExit" }
git add -- src/agentos/examples/small_openai_agent.py tests/examples/test_small_openai_agent.py tests/observability/test_query_loop_instrumentation.py tests/observability/test_structured_logging.py
```

- [ ] **Step 7: Spec Compliance Review the staged Observability/example diff**

Reviewer confirms capability schemas remain Registry/`ProviderRequest.tools` truth、metadata is
absent from System、only Allowed Files changed and no deferred Snapshot projection was added.

- [ ] **Step 8: Code Quality Review the staged Observability/example diff**

Reviewer records the 300-499 responsibility conclusion: the 438-line starting file remains one
example assembly/CLI responsibility、adds only the Renderer assembly helper、creates no Owner or
truth source and remains below 500 lines.

- [ ] **Step 9: Resolve findings and repeat Green/size、staging and both reviews**

Re-run the exact Step 6 pytest、line gate and `git diff --check`, repeat the exact staging command,
then repeat Steps 7-8. Do not commit a review candidate.

- [ ] **Step 10: Commit the reviewed staged diff and record the Observability/example SHA**

```powershell
git commit -m "refactor: migrate example and observability context assembly"
$observabilityCommitExit = $LASTEXITCODE
if ($observabilityCommitExit -ne 0) { throw "observability/examples commit failed with exit code $observabilityCommitExit" }
$observabilitySha = (git rev-parse HEAD).Trim()
$observabilityShaExit = $LASTEXITCODE
if ($observabilityShaExit -ne 0) { throw "cannot record observability/examples commit SHA" }
$observabilitySha | Set-Content -Encoding ascii "$env:TEMP\agentos-task7b-observability-examples-sha.txt"
```

Expected: all listed tests PASS；tool/skill schemas remain available through Registry/tools and are absent from System.

---

### Task 5: Data Boundary Workstream - Attachment, Compression, Memory, Provider, Persistence

**Allowed Files:** `tests/attachments/test_turn_scoped_image_lifecycle.py`、`tests/compression/test_runtime.py`、`tests/architecture/test_phase7_memory_boundaries.py`、`tests/providers/test_provider_messages.py`、`tests/persistence/test_serializers.py`。

**Forbidden Files:** 全部 `src/**`、其他 tests、fixture foundation、其他 workstream、Task 7A 文件和 module baseline。

- [ ] **Step 1: Run Red**

```powershell
$python = $env:AGENTOS_SHARED_PYTHON
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "AGENTOS_SHARED_PYTHON does not name a Python executable: $python"
}
$sourceRoot = [System.IO.Path]::GetFullPath($env:AGENTOS_SOURCE_ROOT)
Set-AgentosSourceRoot -Python $python -SourceRoot $sourceRoot
& $python -m pytest tests/attachments/test_turn_scoped_image_lifecycle.py tests/compression/test_runtime.py tests/architecture/test_phase7_memory_boundaries.py tests/providers/test_provider_messages.py tests/persistence/test_serializers.py -q
$dataRedExit = $LASTEXITCODE
if ($dataRedExit -ne 1) { throw "data-boundary Red must exit 1, got $dataRedExit" }
```

Expected: FAIL from old constructor、有参 render 和 non-negative legacy schema fixtures。

- [ ] **Step 2: Apply mechanical fixture/schema migration**

Use `default_context_renderer()` for request builders. Change Attachment `type="dict"` to `type="object"`; change persistence round-trip fixtures `type="str"` to `type="string"` and `type="obj"` to `type="object"`。Serialization wire behavior must remain unchanged.

- [ ] **Step 3: Move Compression assertions to state and trusted-envelope boundaries**

Replace `ContextRenderer().render(context_runtime.state)` with assertions equivalent to:

```python
snapshot = context_runtime.snapshot()
assert [segment.id for segment in snapshot.compressed_history] == ["seg_1"]
assert snapshot.compressed_history[0].summary

system = default_context_renderer().render().text
assert "Old detail" not in system
for forbidden_term in ["source", "message_id", "compression_id"]:
    assert forbidden_term not in system
```

Do not implement Phase 2 compressed-history Snapshot messages.

- [ ] **Step 4: Keep Memory metadata outside System without rendering ContextState**

In `test_phase7_memory_boundaries.py`, render only the explicit default SystemEnvelope:

```python
rendered = default_context_renderer().render().text.lower()
```

Keep the source dependency assertion and forbidden storage metadata list. Do not pass `ContextState` to Renderer or invent Memory projection delivery.

- [ ] **Step 5: Run Green/static checks and stage exactly**

```powershell
& $python -m pytest tests/attachments/test_turn_scoped_image_lifecycle.py tests/compression/test_runtime.py tests/architecture/test_phase7_memory_boundaries.py tests/providers/test_provider_messages.py tests/persistence/test_serializers.py -q
$dataGreenExit = $LASTEXITCODE
if ($dataGreenExit -ne 0) { throw "data-boundary Green failed with exit code $dataGreenExit" }
git diff --check
$diffCheckExit = $LASTEXITCODE
if ($diffCheckExit -ne 0) { throw "data-boundary diff check failed with exit code $diffCheckExit" }
git add -- tests/attachments/test_turn_scoped_image_lifecycle.py tests/compression/test_runtime.py tests/architecture/test_phase7_memory_boundaries.py tests/providers/test_provider_messages.py tests/persistence/test_serializers.py
```

- [ ] **Step 6: Spec Compliance Review the staged Data-boundary diff**

Reviewer confirms Compression/Memory truth remains in state/boundary assertions、schema types use
protocol names、no dynamic data enters System and only Allowed Files changed.

- [ ] **Step 7: Code Quality Review the staged Data-boundary diff**

Reviewer confirms fixtures use the shared explicit renderer、serialization wire behavior is
unchanged、no copied implementation and no skip/xfail was added.

- [ ] **Step 8: Resolve findings and repeat Green、staging and both reviews**

Re-run the exact Step 5 pytest and `git diff --check`, repeat the exact staging command, then
repeat Steps 6-7. No commit is allowed while a finding remains.

- [ ] **Step 9: Commit the reviewed staged diff and record the Data-boundary SHA**

```powershell
git commit -m "test: migrate context data boundary assertions"
$dataCommitExit = $LASTEXITCODE
if ($dataCommitExit -ne 0) { throw "data-boundary commit failed with exit code $dataCommitExit" }
$dataSha = (git rev-parse HEAD).Trim()
$dataShaExit = $LASTEXITCODE
if ($dataShaExit -ne 0) { throw "cannot record data-boundary commit SHA" }
$dataSha | Set-Content -Encoding ascii "$env:TEMP\agentos-task7b-data-boundaries-sha.txt"
```

Expected: all listed tests PASS；Compression/Memory truth remains in existing state/boundary assertions, not System。

---

### Task 6: Subprocess Environment Workstream - Distinguish Baseline Assumption From Dependency Defect

**Allowed Files:** `tests/deployment/test_live_backend_probe_pack.py`。

**Forbidden Files:** `pyproject.toml`、`uv.lock`、全部 `src/**`、其他 tests、其他 workstream 和 module baseline。若诊断不符合下述分支，停止本 workstream 并升级主 Owner，不能扩权修改。

- [ ] **Step 1: Reproduce and diagnose before editing**

```powershell
$python = $sharedPython
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Shared Python does not name a Python executable: $python"
}
$integrationSourceRoot = (Resolve-Path '.\src').Path
Set-AgentosSourceRoot -Python $python -SourceRoot $integrationSourceRoot
& $python -m pytest tests/deployment/test_live_backend_probe_pack.py::test_reference_live_backend_probe_subprocess_has_isolated_import -q
$probeRedExit = $LASTEXITCODE
if ($probeRedExit -ne 1) { throw "deployment probe Red must exit 1, got $probeRedExit" }
& $python -c "import markdown_it; print(markdown_it.__file__)"
$normalImportExit = $LASTEXITCODE
if ($normalImportExit -ne 0) { throw "normal markdown_it import failed with exit code $normalImportExit" }
& $python -S -c "import markdown_it"
$isolatedImportExit = $LASTEXITCODE
if ($isolatedImportExit -eq 0) { throw "python -S unexpectedly imported markdown_it" }
Select-String -Path pyproject.toml,uv.lock -Pattern 'markdown-it-py'
```

Expected: target test FAIL；normal venv import PASS；`-S` import FAIL because it disables site packages；`pyproject.toml` and `uv.lock` both declare `markdown-it-py>=4.0,<5.0`。This proves a historical zero-third-party `-S` assumption, not a missing Phase 1 dependency declaration.

If normal venv import or dependency declarations do not match this evidence, do not edit the test; report a real packaging propagation defect for a separately approved scope.

- [ ] **Step 2: Keep process isolation but run with declared runtime dependencies**

Remove the `disable_site` parameter/`-S` branch from `_run_live_backend_probe` and the single caller. Keep the temporary cwd, explicit `src` `PYTHONPATH`, captured stdout/stderr, return-code assertion and JSON assertion. Do not add skip/xfail.

- [ ] **Step 3: Run Green/static checks and stage exactly**

```powershell
& $python -m pytest tests/deployment/test_live_backend_probe_pack.py -q
$probeGreenExit = $LASTEXITCODE
if ($probeGreenExit -ne 0) { throw "deployment probe Green failed with exit code $probeGreenExit" }
git diff --check
$diffCheckExit = $LASTEXITCODE
if ($diffCheckExit -ne 0) { throw "deployment diff check failed with exit code $diffCheckExit" }
git add -- tests/deployment/test_live_backend_probe_pack.py
```

- [ ] **Step 4: Spec Compliance Review the staged subprocess diff**

Reviewer confirms cwd/PYTHONPATH process isolation remains、the declared runtime dependency is
used、no packaging files changed and no skip/xfail was introduced.

- [ ] **Step 5: Code Quality Review the staged subprocess diff**

Reviewer confirms the test still launches a real child process、checks return code/stdout JSON and
removes only the obsolete `-S` assumption.

- [ ] **Step 6: Resolve findings and repeat Green、staging and both reviews**

Re-run the exact Step 3 pytest and `git diff --check`, repeat the exact staging command, then
repeat Steps 4-5. Do not commit until both reviewers approve the staged diff.

- [ ] **Step 7: Commit the reviewed staged diff and record the subprocess SHA**

```powershell
git commit -m "test: run isolated probe with declared dependencies"
$probeCommitExit = $LASTEXITCODE
if ($probeCommitExit -ne 0) { throw "deployment probe commit failed with exit code $probeCommitExit" }
$probeSha = (git rev-parse HEAD).Trim()
$probeShaExit = $LASTEXITCODE
if ($probeShaExit -ne 0) { throw "cannot record deployment probe commit SHA" }
$probeSha | Set-Content -Encoding ascii "$env:TEMP\agentos-task7b-subprocess-sha.txt"
```

Expected: deployment probe pack PASS and still proves cwd-independent SDK import/JSON execution.

---

### Task 7: Regenerate Module Size Baseline After All Migrations Merge

**Dependency:** Tasks 2-6 must be merged and Green. This task is serial.

**Allowed Files:** `docs/governance/agentos-module-size-baseline.json`。

**Forbidden Files:** 全部 `src/**`、`tests/**`、scripts、其他 docs 和 Task 7A files。

- [ ] **Step 1: Confirm Red is only baseline drift**

```powershell
$python = $sharedPython
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Shared Python does not name a Python executable: $python"
}
$integrationSourceRoot = (Resolve-Path '.\src').Path
Set-AgentosSourceRoot -Python $python -SourceRoot $integrationSourceRoot
& $python -m pytest tests/architecture/test_module_size_baseline.py -q
$baselineRedExit = $LASTEXITCODE
if ($baselineRedExit -ne 1) { throw "module baseline Red must exit 1, got $baselineRedExit" }
```

Expected before regeneration: only `test_module_size_baseline_matches_current_source_tree` and `test_module_size_generator_writes_deterministic_json` fail.

- [ ] **Step 2: Generate the factual baseline once**

```powershell
& $python scripts/generate_module_size_baseline.py --root src/agentos --output docs/governance/agentos-module-size-baseline.json
$baselineGenerateExit = $LASTEXITCODE
if ($baselineGenerateExit -ne 0) { throw "module baseline generator failed with exit code $baselineGenerateExit" }
& $python -m pytest tests/architecture/test_module_size_baseline.py -q
$baselineGreenExit = $LASTEXITCODE
if ($baselineGreenExit -ne 0) { throw "module baseline Green failed with exit code $baselineGreenExit" }
git diff --check
$diffCheckExit = $LASTEXITCODE
if ($diffCheckExit -ne 0) { throw "module baseline diff check failed with exit code $diffCheckExit" }
git add -- docs/governance/agentos-module-size-baseline.json
```

Expected: architecture module-size suite PASS。Do not hand-edit counts and do not generate this file before workstream merges.

- [ ] **Step 3: Review the staged generated baseline and resolve findings**

Spec reviewer confirms the baseline records facts only；quality reviewer compares generator output byte-for-byte and confirms no unrelated module churn。

Any finding requires re-running the Step 2 generator、module pytest and `git diff --check`,
repeating the exact staging command, and repeating both reviews. Do not commit before approval.

- [ ] **Step 4: Commit the reviewed staged baseline and record its SHA**

```powershell
git commit -m "docs: refresh phase1 module size baseline"
$baselineCommitExit = $LASTEXITCODE
if ($baselineCommitExit -ne 0) { throw "module baseline commit failed with exit code $baselineCommitExit" }
$baselineSha = (git rev-parse HEAD).Trim()
$baselineShaExit = $LASTEXITCODE
if ($baselineShaExit -ne 0) { throw "cannot record module baseline commit SHA" }
$baselineSha | Set-Content -Encoding ascii "$env:TEMP\agentos-task7b-module-baseline-sha.txt"
```

---

### Task 8: Phase 1 Zero-Failure Gates And Final Dual Review

**Allowed Files:** none by default。Any finding routes back to the owning workstream and is fixed there；Task 8 itself does not perform drive-by edits。

- [ ] **Step 1: Run the authoritative zero-failure dynamic and static gates**

```powershell
$python = $sharedPython
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Shared Python does not name a Python executable: $python"
}
$integrationSourceRoot = (Resolve-Path '.\src').Path
Set-AgentosSourceRoot -Python $python -SourceRoot $integrationSourceRoot
$finalXml = "$env:TEMP\agentos-task7b-final.xml"
$finalText = "$env:TEMP\agentos-task7b-final.txt"
& $python -m pytest -q --junitxml=$finalXml *> $finalText
$pytestExit = $LASTEXITCODE
if ($pytestExit -ne 0) {
    throw "full pytest must exit exactly 0; got $pytestExit. Preserve JUnit/text artifacts and use Step 2 only for diagnostics"
}

[xml]$finalJunit = Get-Content -Raw -LiteralPath $finalXml
$finalRootName = $finalJunit.DocumentElement.LocalName
$finalSuites = if ($finalRootName -eq 'testsuites') {
    @($finalJunit.DocumentElement.SelectNodes('./testsuite'))
} elseif ($finalRootName -eq 'testsuite') {
    @($finalJunit.DocumentElement)
} else {
    throw "Unexpected final JUnit root: $finalRootName"
}
if ($finalSuites.Count -eq 0) {
    throw "Final JUnit root $finalRootName contains no testsuite elements"
}
$finalFailures = [int](($finalSuites | Measure-Object -Property failures -Sum).Sum)
$finalErrors = [int](($finalSuites | Measure-Object -Property errors -Sum).Sum)
if ($finalFailures -ne 0 -or $finalErrors -ne 0) {
    throw "Authoritative JUnit reports failures=$finalFailures errors=$finalErrors"
}

& $python -m compileall -q src tests
$compileallExit = $LASTEXITCODE
if ($compileallExit -ne 0) { throw "compileall failed with exit code $compileallExit" }
& $python -m ruff check src tests
$ruffExit = $LASTEXITCODE
if ($ruffExit -ne 0) { throw "Ruff failed with exit code $ruffExit" }
& $python -m pytest tests/architecture/test_module_size_baseline.py -q
$baselineGateExit = $LASTEXITCODE
if ($baselineGateExit -ne 0) { throw "module baseline gate failed with exit code $baselineGateExit" }
& $python -m pytest tests/architecture/test_public_api.py tests/architecture/test_public_api_inventory.py -q
$publicApiGateExit = $LASTEXITCODE
if ($publicApiGateExit -ne 0) { throw "public API inventory gate failed with exit code $publicApiGateExit" }
git diff --check
$diffCheckExit = $LASTEXITCODE
if ($diffCheckExit -ne 0) { throw "final diff check failed with exit code $diffCheckExit" }
```

Expected: full pytest exit code is exactly `0`; authoritative JUnit reports `failures == 0`
and `errors == 0`; every subsequent gate also exits `0`. No historical or Task 7B Red
failure set can weaken this requirement.

- [ ] **Step 2: Diagnose a failed full run without creating an allowlist**

```powershell
[xml]$failedJunit = Get-Content -Raw -LiteralPath $finalXml
$failedRootName = $failedJunit.DocumentElement.LocalName
$failedSuites = if ($failedRootName -eq 'testsuites') {
    @($failedJunit.DocumentElement.SelectNodes('./testsuite'))
} elseif ($failedRootName -eq 'testsuite') {
    @($failedJunit.DocumentElement)
} else {
    throw "Unexpected failed-run JUnit root: $failedRootName"
}
if ($failedSuites.Count -eq 0) {
    throw "Failed-run JUnit root $failedRootName contains no testsuite elements"
}
$failedCount = [int](($failedSuites | Measure-Object -Property failures -Sum).Sum)
$errorCount = [int](($failedSuites | Measure-Object -Property errors -Sum).Sum)
$current = @(
    Get-Content -LiteralPath $finalText |
        Where-Object { $_ -match '^(FAILED|ERROR)\s+(\S+)' } |
        ForEach-Object { [regex]::Match($_, '^(FAILED|ERROR)\s+(\S+)').Groups[2].Value } |
        Sort-Object -Unique
)
$current | Set-Content -Encoding utf8 "$env:TEMP\agentos-task7b-final-nodeids.txt"
if ($current.Count -ne ($failedCount + $errorCount)) {
    throw "Text nodeid count $($current.Count) disagrees with authoritative JUnit failures+errors $($failedCount + $errorCount)"
}
$task7bRed = @(Get-Content "$env:TEMP\agentos-task7b-red-nodeids.txt" -ErrorAction SilentlyContinue)
$phase0 = @(Get-Content "$env:TEMP\agentos-phase0-red-nodeids.txt" -ErrorAction SilentlyContinue)
$knownTask7b = @($current | Where-Object { $_ -in $task7bRed })
$seenInPhase0 = @($current | Where-Object { $_ -in $phase0 })
```

Expected: this step captures both test `FAILED` and collection/fixture/setup `ERROR` nodeids
for triage. Text parsing only mirrors pytest summary lines and is accepted only when its
cardinality matches authoritative JUnit. `$knownTask7b` and `$seenInPhase0` are labels for
diagnosis, never allowlists. Any non-zero `$failedCount` or `$errorCount` routes back to the
owning task and blocks final reviews.

- [ ] **Step 3: Run protocol drift scans**

```powershell
rg -n -F "ContextRenderer()" src tests -g "*.py" -g "!tests/context/test_system_envelope_renderer.py"
$constructorScanExit = $LASTEXITCODE
if ($constructorScanExit -ne 1) { throw "legacy ContextRenderer() drift scan found hits or failed: exit $constructorScanExit" }
rg -n "capability_plane\s*=" src tests -g "*.py"
$capabilityScanExit = $LASTEXITCODE
if ($capabilityScanExit -ne 1) { throw "capability_plane drift scan found hits or failed: exit $capabilityScanExit" }
rg -n "\.render\((ContextState|context_runtime\.state|context_state)" src tests -g "*.py"
$renderScanExit = $LASTEXITCODE
if ($renderScanExit -ne 1) { throw "stateful render drift scan found hits or failed: exit $renderScanExit" }
rg -n 'type="(str|dict|obj)"' src tests -g "*.py" -g "!tests/context/test_context_state_projection.py"
$schemaScanExit = $LASTEXITCODE
if ($schemaScanExit -ne 1) { throw "legacy schema drift scan found hits or failed: exit $schemaScanExit" }
rg -n "# Runtime Notice|# Capability Plane" tests/runtime tests/examples tests/observability
$systemSemanticScanExit = $LASTEXITCODE
if ($systemSemanticScanExit -ne 1) { throw "legacy System semantic drift scan found hits or failed: exit $systemSemanticScanExit" }
$task7bBase = $env:AGENTOS_TASK7B_BASE
$task7bDiff = git diff --unified=0 "$task7bBase...HEAD" -- src tests
$task7bDiffExit = $LASTEXITCODE
if ($task7bDiffExit -ne 0) { throw "Task 7B diff scan failed with exit code $task7bDiffExit" }
$deferredHits = @($task7bDiff | Select-String -Pattern '^\+.*(ProviderInputItem|synthetic snapshot|ReadModel|ContextMount)')
if ($deferredHits.Count -gt 0) { throw "Task 7B introduced deferred Phase 2/3 types" }
git diff --exit-code "$task7bBase...HEAD" -- docs/public-api-inventory.json
$inventoryDiffExit = $LASTEXITCODE
if ($inventoryDiffExit -ne 0) { throw "Task 7B modified the Task 7A public API inventory" }
```

Expected: zero hits except the intentional constructor/legacy rejection tests excluded by path。Set `AGENTOS_TASK7B_BASE` to the Task 7A commit before execution begins；the diff scan proves Task 7B did not add deferred Phase 2/3 types。

- [ ] **Step 4: Spec Compliance Review after all serial gates pass**

Independent reviewer verifies:

- Task 7A core files were not modified by 7B；
- System/Data authority remains separated；
- Runtime Notice、Compression、Memory、Skill/MCP metadata are asserted at existing owners/boundaries；
- no default constructor、stateful render、capability plane or deferred Phase 2/3 object was restored；
- every changed file belongs to exactly one Owner and every deferral is explicit。

- [ ] **Step 5: Code Quality Review after all serial gates pass**

Independent reviewer verifies:

- shared fixture is minimal and tests only；
- example assembly does not duplicate Registry truth；
- downstream failures were fixed at direct factories rather than mass-edited；
- subprocess test still exercises a real child process and has no skip；
- module baseline was generator-produced after all merges；
- full output and nodeid set artifacts support every completion claim。

- [ ] **Step 6: Record the integration boundary**

Task 8 creates no catch-all code commit。Final reviews start only after Tasks 6-7 and Task 8
Steps 1 and 3 pass. After both reviews approve, record the reviewed worker SHAs, serial commit
SHAs, JUnit totals, exact pytest exit code and verification output in the integration handoff；
only then may the parent Task 7 be declared complete。

---

## Commit Boundary Summary

| Order | Commit message | Owner |
|---:|---|---|
| 1 | `test: add explicit context renderer fixture` | Fixture foundation |
| parallel | `test: migrate runtime context renderer callsites` | Runtime |
| parallel | `test: migrate channel and multi renderer fixtures` | Channel/multi |
| parallel | `refactor: migrate example and observability context assembly` | Observability/example |
| parallel | `test: migrate context data boundary assertions` | Data-boundary |
| serial | `test: run isolated probe with declared dependencies` | Subprocess environment |
| serial | `docs: refresh phase1 module size baseline` | Governance |

No commit may include another Owner's files。Task 7A must be committed before setting
`AGENTOS_TASK7B_BASE`; that exact commit must contain every Task 7A file, including generated
`docs/public-api-inventory.json`. “Otherwise frozen” is not an allowed integration boundary。

## Self-Review Result

- **Spec coverage:** explicit Renderer injection、schema protocol types、Runtime Notice、Compression/Memory、Skill/MCP tools-only metadata、subprocess dependency environment、module baseline、public API inventory freeze、four-worktree integration and JUnit zero-failure gates all have owned tasks。
- **Placeholder scan:** 无占位步骤；every workstream has exact Allowed/Forbidden files、Red、Green、review and commit boundary。
- **Type consistency:** all positive fixtures use `string`/`object`；`ContextRenderer.render()` remains zero-argument and returns `SystemEnvelope`；request builders receive a `SystemEnvelopeRenderer` implementation and `ContextRuntime` input。
- **JUnit authority:** every JUnit parser uses namespace-safe `DocumentElement.LocalName`, selects direct `<testsuite>` children from a `<testsuites>` root, rejects an empty suite collection, and treats JUnit failure/error totals as authoritative。
- **Source isolation:** every controller, worker, historical Phase 0, integration, subprocess, baseline and final-gate execution binds the intended absolute per-worktree `src` at the front of process `PYTHONPATH` and verifies `agentos.__file__` is beneath it before tests run。
- **Review ordering:** every Task 1-7 commit follows Red、Green/static checks、exact staging、staged Spec review、staged Quality review and a fix/rerun/restage/re-review loop；no review relies on a provisional commit。
- **Parallel safety:** Tasks 2-5 have disjoint write sets, share only the read-only fixture created by Task 1, use a controller-resolved shared Python, and integrate reviewed SHAs in a fixed order；four-worktree creation is transactional, retains worktrees on success, and on failure removes only verified registered GUID worktrees plus this run's GUID branches without recursive filesystem deletion；Tasks 6-7 are explicit serial convergence steps。
- **Deferral safety:** no `ProviderInputItem`、synthetic snapshot、`ReadModel` or `ContextMount` is introduced；Phase 2/3 work remains deferred。
