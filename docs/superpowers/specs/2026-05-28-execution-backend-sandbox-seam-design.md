# Execution Backend 沙箱接缝预留设计

## Status

**Implemented — Phase D 已落地**。代码库已新增 `ExecutionBackend` / `ResourcePolicy` 抽象；`ToolExecutor` 和 `ToolCallRouter` 通过默认 `InProcessExecutionBackend` 保持现有行为。本 Phase 是纯重构 + 接缝预留，不改行为。

**推荐实施顺序(经 review 调整):D → A → C → B(B1→B2→B3→B4)→ E。本 spec(D)经 review 确认内容无需改动,且建议作为第 1 位先做——它是最小、最低风险的纯抽取式重构(行为不变),为后续 sandbox / backpressure 打地基。**

## Design References

- `src/agentos/capabilities/executor.py`：`ToolExecutor`，当前 `execute` / `async_execute` 的真实调用点
- `src/agentos/capabilities/router.py`：`ToolCallRouter`，安全策略应用点 + executor 调用点
- `src/agentos/capabilities/tools.py`：`RegisteredTool`、`ToolHandler`、`AsyncToolHandler` 类型定义
- `src/agentos/policies/security.py`：`SecurityPolicy` — 现有策略模式的对照基准
- `src/agentos/policies/budget.py`：`BudgetPolicy` / `TokenBudgetPolicy` — frozen dataclass 风格参照
- `src/agentos/providers/__init__.py`（或对应文件）：`ProviderToolCall` 定义

## Goal

在不改变任何运行时行为的前提下，把工具 handler 的**实际调用**抽象到 `ExecutionBackend` 协议之后，使未来的 subprocess / Docker / gVisor / Firecracker 沙箱后端能以"换一个实现"的方式插入，无需触碰 `ToolExecutor` 或 `ToolCallRouter` 的业务逻辑。

**可验证的完成标准**：所有现有工具测试不加修改地通过；一个最小 fake 后端契约测试证明协议可替换；`ResourcePolicy` 可以被构造并传入，但 in-process 后端静默忽略它。

## Contracts

### `ExecutionBackend` 协议

位置：`src/agentos/capabilities/backend.py`（新文件）

```python
from typing import Protocol
from agentos.capabilities.tools import RegisteredTool
from agentos.policies.resource_policy import ResourcePolicy   # 见下

class ExecutionBackend(Protocol):
    """工具 handler 的执行后端接缝。

    实现类负责：调用 handler、强制执行 deadline 和资源限制。
    in-process 实现忽略 ResourcePolicy；沙箱实现消费它。
    """

    def run(
        self,
        tool: RegisteredTool,
        arguments: dict[str, object],
        *,
        resource_policy: ResourcePolicy,
    ) -> str:
        """同步执行 tool.handler(arguments)，返回字符串结果。"""
        ...

    async def async_run(
        self,
        tool: RegisteredTool,
        arguments: dict[str, object],
        *,
        resource_policy: ResourcePolicy,
    ) -> str:
        """异步执行；sync handler 应在线程中执行。"""
        ...
```

> `RegisteredTool` 类型：来自 `capabilities/tools.py`，`handler: ToolHandler | AsyncToolHandler`，`parameters: dict[str, object]`。
> `arguments: dict[str, object]`：即现有 `tool.handler(dict(tool_call.arguments))` 中的参数。
> 返回值 `str`：即现有 handler 返回的 `content: str`。

---

### `InProcessExecutionBackend`（默认实现）

位置：`src/agentos/capabilities/backend.py`，与协议同文件

```python
import asyncio
import inspect
from dataclasses import dataclass
from agentos.capabilities.tools import RegisteredTool
from agentos.policies.resource_policy import ResourcePolicy

@dataclass(slots=True)
class InProcessExecutionBackend:
    """默认 in-process 后端——复现当前 ToolExecutor 的 handler 调用行为。

    ResourcePolicy 字段被完全忽略；deadline / memory 留给未来沙箱后端消费。
    """

    def run(
        self,
        tool: RegisteredTool,
        arguments: dict[str, object],
        *,
        resource_policy: ResourcePolicy,
    ) -> str:
        content = tool.handler(arguments)
        if inspect.isawaitable(content):
            close = getattr(content, "close", None)
            if callable(close):
                close()
            raise RuntimeError("async handler requires AsyncQueryLoop")
        return content  # type: ignore[return-value]

    async def async_run(
        self,
        tool: RegisteredTool,
        arguments: dict[str, object],
        *,
        resource_policy: ResourcePolicy,
    ) -> str:
        if inspect.iscoroutinefunction(tool.handler):
            return await tool.handler(arguments)  # type: ignore[return-value]
        return await asyncio.to_thread(self.run, tool, arguments, resource_policy=resource_policy)
```

> 以上逻辑是从 `executor.py:36-56` 提取的等价实现，行为完全不变。

---

### `ResourcePolicy` 声明

位置：`src/agentos/policies/resource_policy.py`（新文件），风格与 `budget.py` 一致

```python
from __future__ import annotations
from dataclasses import dataclass, field

@dataclass(frozen=True, slots=True)
class ResourcePolicy:
    """工具执行资源限制声明。

    InProcessExecutionBackend 静默忽略所有字段。
    沙箱后端（Docker / gVisor / Firecracker）在执行前读取并强制执行这些限制。

    未来沙箱分级参考：
      - Tier 1 (subprocess)：仅 deadline_seconds 有效
      - Tier 2 (Docker/gVisor)：deadline + memory_limit_mb + network_allowlist
      - Tier 3 (Firecracker microVM)：全字段强制 + 进程级隔离
    详见 wiki/sandbox-isolation.md（待编写）。
    """

    deadline_seconds: float | None = None
    """handler 执行超时（秒）。None = 无限制。"""

    memory_limit_mb: int | None = None
    """进程/容器内存上限（MB）。None = 无限制。in-process 后端忽略。"""

    network_allowlist: frozenset[str] = field(default_factory=frozenset)
    """允许 handler 访问的域名白名单。空集 = 不限制（in-process 后端忽略）。"""

    def __post_init__(self) -> None:
        if self.deadline_seconds is not None and self.deadline_seconds <= 0:
            raise ValueError("deadline_seconds must be positive")
        if self.memory_limit_mb is not None and self.memory_limit_mb < 1:
            raise ValueError("memory_limit_mb must be at least 1")
```

---

## File Change Map

### 新增文件

| 文件 | 说明 |
|------|------|
| `src/agentos/capabilities/backend.py` | `ExecutionBackend` 协议 + `InProcessExecutionBackend` |
| `src/agentos/policies/resource_policy.py` | `ResourcePolicy` frozen dataclass |
| `tests/capabilities/test_execution_backend.py` | 协议可替换性契约测试（见 Acceptance Criteria） |

### 修改文件

#### `src/agentos/capabilities/executor.py`

**重构目标**：把 `tool.handler(dict(tool_call.arguments))` 的裸调用替换为通过 `backend.run` / `backend.async_run` 调用，其余逻辑（安全策略、参数校验、`_tool_for_call`）**原样保留**。

关键改动（行号参考当前版本）：

- `ToolExecutor` 新增字段 `backend: ExecutionBackend`（默认 `field(default_factory=InProcessExecutionBackend)` 保证后向兼容）
- `ToolExecutor` 新增字段 `resource_policy: ResourcePolicy`（默认 `field(default_factory=ResourcePolicy)` = 空策略，全字段 None）
- `execute`（当前 L30-44）：将 L36-41 的 handler 调用替换为 `content = self.backend.run(tool, dict(tool_call.arguments), resource_policy=self.resource_policy)`
- `async_execute`（当前 L47-56）：将 L53-56 的 handler 调用替换为 `content = await self.backend.async_run(tool, dict(tool_call.arguments), resource_policy=self.resource_policy)`
- 其余方法（`_tool_for_call`、`_validate_arguments`、`_matches_json_type`、`_redact_value`）**一行不改**

> 改动仅涉及 L30-56 之间约 8 行，diff 极小。

#### `src/agentos/capabilities/router.py`

`ToolCallRouter._tool_executor()`（当前 L69-77）创建 `ToolExecutor` 时透传 `backend` 和 `resource_policy`（若 router 上有这两个可选字段，默认值保持后向兼容）：

```python
# 新增两个 dataclass 字段（有默认值，不破坏现有 ToolCallRouter() 调用）
backend: ExecutionBackend = field(default_factory=InProcessExecutionBackend)
resource_policy: ResourcePolicy = field(default_factory=ResourcePolicy)

def _tool_executor(self) -> ToolExecutor:
    if self._executor is None:
        self._executor = ToolExecutor(
            registry=self.tool_registry,
            security_policy=self.security_policy,
            backend=self.backend,           # ← 新增
            resource_policy=self.resource_policy,  # ← 新增
        )
    return self._executor
```

**其余 ToolCallRouter 方法一行不改**，context tools / MCP 路径完全不受影响。

#### `src/agentos/policies/__init__.py`

追加导出：`ResourcePolicy`

```python
from agentos.policies.resource_policy import ResourcePolicy

__all__ = [
    ...,
    "ResourcePolicy",
]
```

#### `src/agentos/capabilities/__init__.py`（若存在）

按现有导出风格，酌情追加 `ExecutionBackend`、`InProcessExecutionBackend`。

---

## Acceptance Criteria

1. **现有工具测试原封不动通过**：`pytest tests/capabilities/` 全绿，无一修改。等效于"行为零变更"。

2. **协议可替换性契约测试**（`tests/capabilities/test_execution_backend.py`）：
   - 实现一个 `FakeSandboxBackend` (同文件内的 test double)，其 `run` 返回固定字符串并记录被调用的 `resource_policy`；
   - 把它注入 `ToolExecutor(backend=FakeSandboxBackend(), ...)` 并调用 `execute()`；
   - 断言：`FakeSandboxBackend.run` 被恰好调用一次，`arguments` 正确，`resource_policy` 是传入的那个实例；
   - 断言：`ToolExecutor` 的安全策略检查和参数校验依然在 backend 调用之前执行（backend 不绕过这些前置检查）。

3. **`ResourcePolicy` 被 in-process 后端接受且静默忽略**：
   - 构造 `ResourcePolicy(deadline_seconds=1.0, memory_limit_mb=128)`；
   - 注入 `InProcessExecutionBackend` 并调用一个正常 handler；
   - 断言：handler 正常执行完成（无超时、无报错），返回值正确。

4. **`ResourcePolicy` 校验**：`deadline_seconds=0` / `-1` 抛 `ValueError`；`memory_limit_mb=0` 抛 `ValueError`。

5. **回归：现有 `ToolCallRouter` 无参数构造不变**：`ToolCallRouter(tool_registry=...)` 构造不抛异常，路由行为与改前一致。

---

## Risks & Non-Goals

### Non-Goals（本 Phase 明确不做）

- **不实现任何真实沙箱**：subprocess、Docker、gVisor、Firecracker 全部不在本 Phase 范围。接缝留好即止。
- **`ResourcePolicy` 不被强制执行**：in-process 后端静默忽略所有字段；字段仅作类型化声明。
- **不改变 SecurityPolicy 的职责**：工具名称 allow/deny 仍由 `SecurityPolicy` 负责，与 `ResourcePolicy` 正交；两者不合并。
- **不改变 MCP / context tool 路径**：`ToolCallRouter` 对 `mcp__` 前缀工具和 context protocol 工具的分支逻辑一行不动。
- **不为 ResourcePolicy 添加运行时 enforcement**：deadline enforcement（asyncio.timeout / signal）属于未来沙箱 Phase。

### Risks

| 风险 | 概率 | 缓解 |
|------|------|------|
| `ToolExecutor` 新增字段改变 `__eq__` / pickle 行为 | 低 | 字段有默认值；slots=True 不影响 dataclass 相等语义 |
| `ToolCallRouter._executor` 缓存逻辑在注入 backend 后失效 | 低 | 只在 `_executor is None` 时创建；backend 在 router 构造时已固定 |
| 沙箱后端未来需要 async context manager 生命周期 | 中 | 协议现在不声明 `__aenter__`/`__aexit__`；若需要由沙箱 Phase 扩展协议，不影响本 Phase |
| 沙箱后端未来需要细化威胁模型 | 低 | `wiki/sandbox-isolation.md` 已作为设计参考；本 Phase 只声明接缝，不实现 enforcement |

### 未来沙箱分级参考（不在本 Phase 实现）

`ResourcePolicy` docstring 已提及三级：

- **Tier 1 subprocess**：`deadline_seconds` → `asyncio.timeout` 包裹 handler 子进程调用
- **Tier 2 Docker / gVisor**：`memory_limit_mb` + `network_allowlist` → container run 参数
- **Tier 3 Firecracker microVM**：全字段强制 + 进程级隔离，参见 `wiki/sandbox-isolation.md`

## 实现交接须知（给实现者）

先读 `AGENTS.md`，以下是 spec 特有要点：

- **这是纯提取重构**：改动只在 `executor.py` L30-56、`router.py` L69-77，以及两个新文件。绝不修改其他逻辑。
- **默认值铁律**：`ExecutionBackend` 和 `ResourcePolicy` 两个新字段必须有 `field(default_factory=...)` 默认值，保证所有现有 `ToolExecutor(registry=..., security_policy=...)` 和 `ToolCallRouter(tool_registry=...)` 调用零修改。
- **改前先跑绿**：`pytest tests/capabilities/` 跑绿留作基线，重构后必须仍绿，再加新契约测试。
- **Keep diff surgical**：不顺手重命名、不 reformat、不拆 `executor.py` 的其他方法。改了无关注释也要还原。
- **FakeSandboxBackend 只在测试文件里**：不进 `src/`。
- **`ResourcePolicy` 用 `frozenset` 不用 `set`**：frozen dataclass 要求字段可哈希，参照 `budget.py` 模式。
