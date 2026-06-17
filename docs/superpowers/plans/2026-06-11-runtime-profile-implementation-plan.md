# RuntimeProfile Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a small public `RuntimeProfile` assembly layer for local, web, and distributed-agent deployment shapes without changing `QueryLoop` orchestration.

**Architecture:** `RuntimeProfile` lives in `agentos.runtime.profile` and assembles existing collaborators. `LocalRuntimeProfile` wraps `AgentBuilder` sync/async build paths, `WebRuntimeProfile` wraps an injected `AgentSessionProvider` into `AsgiAgentApp`, and `DistributedAgentProfile` declares the existing distributed task primitives without claiming team discussion or full A2A compliance.

**Tech Stack:** Python 3.11 dataclasses/protocols, pytest, existing agent-os runtime/channels/multi modules.

---

## File Structure

Create:

- `src/agentos/runtime/profile.py`  
  Public profile protocols and minimal dataclass implementations.

- `tests/runtime/test_runtime_profile.py`  
  Unit tests for local/web/distributed profile assembly and deployment-boundary imports.

Modify:

- `src/agentos/runtime/__init__.py`  
  Export `RuntimeProfile`, `ChannelRuntimeProfile`, `DistributedRuntimeProfile`, `LocalRuntimeProfile`, `WebRuntimeProfile`, `DistributedAgentProfile`.

- `src/agentos/__init__.py`  
  Top-level exports for the same public names.

- `.claude/skills/agent-os/modules/architecture.md`  
  Replace roadmap-only wording with actual import path once source exists.

Do not modify:

- `src/agentos/runtime/query_loop.py`
- `src/agentos/runtime/async_query_loop.py`
- `src/agentos/channels/asgi.py`
- `src/agentos/channels/session.py`

## Task 1: Add RuntimeProfile Contracts And Local Profile

**Files:**
- Create: `src/agentos/runtime/profile.py`
- Test: `tests/runtime/test_runtime_profile.py`

- [ ] **Step 1: Write failing tests for local profile sync/async assembly**

Add this to `tests/runtime/test_runtime_profile.py`:

```python
from agentos import AgentBuilder
from agentos.providers import FakeProvider
from agentos.runtime import AsyncQueryLoop, QueryLoop
from agentos.runtime.profile import LocalRuntimeProfile


def test_local_runtime_profile_builds_sync_agent_by_default() -> None:
    profile = LocalRuntimeProfile(
        agent_builder=AgentBuilder().provider(FakeProvider(["ok"])),
    )

    agent = profile.build_agent()

    assert profile.name == "local"
    assert isinstance(agent.query_loop, QueryLoop)
    assert agent.run("hello").content == "ok"


def test_local_runtime_profile_can_build_async_agent() -> None:
    profile = LocalRuntimeProfile(
        agent_builder=AgentBuilder().provider(FakeProvider(["ok"])),
        loop_mode="async",
    )

    agent = profile.build_agent()

    assert isinstance(agent.query_loop, AsyncQueryLoop)
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest tests/runtime/test_runtime_profile.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'agentos.runtime.profile'`.

- [ ] **Step 3: Implement profile module**

Create `src/agentos/runtime/profile.py`:

```python
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Literal, Protocol

from agentos.builder import AgentBuilder
from agentos.channels import (
    A2AServerAdapter,
    AsgiAgentApp,
    ChannelAuthPolicy,
    RateLimiter,
)
from agentos.channels.session import AgentSessionProvider
from agentos.multi import AgentCoordinator
from agentos.multi.remote import RemoteTaskExecutor
from agentos.registry import AgentResolver, PersistentAgentRegistry
from agentos.runtime.agent import Agent


class RuntimeProfile(Protocol):
    """部署形态装配边界，不负责执行 turn。"""

    name: str

    def build_agent(self, session_id: str | None = None) -> Agent:
        """构建当前 profile 下的 Agent。"""


class ChannelRuntimeProfile(RuntimeProfile, Protocol):
    """可暴露 channel app 的 runtime profile。"""

    def build_channel_app(self) -> object:
        """构建 ASGI 或其他 channel app。"""


class DistributedRuntimeProfile(ChannelRuntimeProfile, Protocol):
    """包含分布式 session/registry/task 边界的 runtime profile。"""

    def readiness_checks(self) -> dict[str, object]:
        """返回可接入 readiness endpoint 的检查项。"""


@dataclass(slots=True)
class LocalRuntimeProfile:
    """本地 terminal/script 形态 profile。"""

    agent_builder: AgentBuilder
    loop_mode: Literal["sync", "async"] = "sync"
    name: str = "local"

    def build_agent(self, session_id: str | None = None) -> Agent:
        """按 sync/async 模式构建本地 Agent。"""

        if self.loop_mode == "async":
            return self.agent_builder.build_async()
        return self.agent_builder.build()
```

- [ ] **Step 4: Run local profile tests**

Run:

```bash
uv run pytest tests/runtime/test_runtime_profile.py::test_local_runtime_profile_builds_sync_agent_by_default tests/runtime/test_runtime_profile.py::test_local_runtime_profile_can_build_async_agent -q
```

Expected: PASS.

## Task 2: Add WebRuntimeProfile

**Files:**
- Modify: `src/agentos/runtime/profile.py`
- Test: `tests/runtime/test_runtime_profile.py`

- [ ] **Step 1: Write failing tests for web profile**

Append:

```python
import json

import pytest

from agentos.channels import AsgiAgentApp, InMemoryAgentSessionProvider
from agentos.runtime.profile import WebRuntimeProfile


def test_web_runtime_profile_requires_session_id_for_build_agent() -> None:
    provider = InMemoryAgentSessionProvider(
        lambda sid: AgentBuilder().provider(FakeProvider(["ok"])).build(),
    )
    profile = WebRuntimeProfile(session_provider=provider)

    with pytest.raises(ValueError, match="requires a session_id"):
        profile.build_agent()


def test_web_runtime_profile_builds_agent_from_session_provider() -> None:
    provider = InMemoryAgentSessionProvider(
        lambda sid: AgentBuilder().provider(FakeProvider([f"ok:{sid}"])).build(),
    )
    profile = WebRuntimeProfile(session_provider=provider)

    agent = profile.build_agent("s1")

    assert agent.run("hello").content == "ok:s1"


async def test_web_runtime_profile_builds_asgi_app() -> None:
    provider = InMemoryAgentSessionProvider(
        lambda sid: AgentBuilder().provider(FakeProvider(["web ok"])).build(),
    )
    profile = WebRuntimeProfile(session_provider=provider)
    app = profile.build_channel_app()

    assert isinstance(app, AsgiAgentApp)

    sent: list[dict[str, object]] = []

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    async def receive() -> dict[str, object]:
        return {
            "type": "http.request",
            "body": json.dumps({"message": "hello"}).encode("utf-8"),
            "more_body": False,
        }

    await app(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/sessions/s1/turns",
            "headers": [],
        },
        receive,
        send,
    )

    body = json.loads(sent[-1]["body"])
    assert body["content"] == "web ok"
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest tests/runtime/test_runtime_profile.py -q
```

Expected: FAIL with `ImportError` or `AttributeError` for `WebRuntimeProfile`.

- [ ] **Step 3: Implement WebRuntimeProfile**

Append to `src/agentos/runtime/profile.py`:

```python
@dataclass(slots=True)
class WebRuntimeProfile:
    """Web channel 形态 profile，依赖外部 session provider。"""

    session_provider: AgentSessionProvider
    auth_policy: ChannelAuthPolicy | None = None
    rate_limiter: RateLimiter | None = None
    readiness_checks: Mapping[str, Callable[[], object]] | None = None
    health_checks: Mapping[str, Callable[[], object]] | None = None
    a2a_server: A2AServerAdapter | None = None
    name: str = "web"

    def build_agent(self, session_id: str | None = None) -> Agent:
        """从 session provider 取出 Agent。"""

        if session_id is None:
            raise ValueError("WebRuntimeProfile requires a session_id")
        return self.session_provider.get_agent(session_id)

    def build_channel_app(self) -> AsgiAgentApp:
        """构建 ASGI channel app。"""

        return AsgiAgentApp(
            sessions=self.session_provider,
            auth_policy=self.auth_policy,
            rate_limiter=self.rate_limiter,
            readiness_checks=self.readiness_checks,
            health_checks=self.health_checks,
            a2a_server=self.a2a_server,
        )
```

- [ ] **Step 4: Run web profile tests**

Run:

```bash
uv run pytest tests/runtime/test_runtime_profile.py -q
```

Expected: PASS.

## Task 3: Add DistributedAgentProfile

**Files:**
- Modify: `src/agentos/runtime/profile.py`
- Test: `tests/runtime/test_runtime_profile.py`

- [ ] **Step 1: Write failing tests**

Append:

```python
from agentos.multi import AgentCoordinator, AgentInbox, InMemoryRegistry, SpawnExecutor, TaskTable
from agentos.runtime.profile import DistributedAgentProfile


class EmptySubagentFactory:
    def create_subagent(self, request):
        return AgentBuilder().provider(FakeProvider(["child"])).build()


def test_distributed_agent_profile_holds_coordination_primitives() -> None:
    coordinator = AgentCoordinator(
        registry=InMemoryRegistry(),
        task_store=TaskTable(),
        message_queue=AgentInbox(),
        spawn_executor=SpawnExecutor(max_workers=1),
        subagent_factory=EmptySubagentFactory(),
    )

    profile = DistributedAgentProfile(coordinator=coordinator)

    assert profile.name == "distributed-agent"
    assert profile.coordinator is coordinator
    with pytest.raises(NotImplementedError, match="coordination"):
        profile.build_agent()
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest tests/runtime/test_runtime_profile.py -q
```

Expected: FAIL with missing `DistributedAgentProfile`.

- [ ] **Step 3: Implement distributed profile**

Append:

```python
@dataclass(slots=True)
class DistributedAgentProfile:
    """分布式任务协调 profile，不代表 team discussion runtime。"""

    coordinator: AgentCoordinator
    resolver: AgentResolver | None = None
    registry: PersistentAgentRegistry | None = None
    remote_task_executor: RemoteTaskExecutor | None = None
    readiness: dict[str, object] = field(default_factory=dict)
    name: str = "distributed-agent"

    def build_agent(self, session_id: str | None = None) -> Agent:
        """分布式协调 profile 不直接构建单个 Agent。"""

        raise NotImplementedError(
            "DistributedAgentProfile assembles coordination, not a single agent",
        )

    def readiness_checks(self) -> dict[str, object]:
        """返回配置的 readiness checks。"""

        return dict(self.readiness)
```

- [ ] **Step 4: Run distributed profile tests**

Run:

```bash
uv run pytest tests/runtime/test_runtime_profile.py -q
```

Expected: PASS.

## Task 4: Export Public API

**Files:**
- Modify: `src/agentos/runtime/__init__.py`
- Modify: `src/agentos/__init__.py`
- Modify: `tests/architecture/test_public_api.py`

- [ ] **Step 1: Write failing public API tests**

Add assertions in `test_public_api_uses_responsibility_specific_names`:

```python
    for name in [
        "RuntimeProfile",
        "ChannelRuntimeProfile",
        "DistributedRuntimeProfile",
        "LocalRuntimeProfile",
        "WebRuntimeProfile",
        "DistributedAgentProfile",
    ]:
        assert hasattr(runtime, name)
        assert hasattr(agentos, name)
```

- [ ] **Step 2: Run public API tests to verify failure**

Run:

```bash
uv run pytest tests/architecture/test_public_api.py::test_public_api_uses_responsibility_specific_names -q
```

Expected: FAIL because names are not exported.

- [ ] **Step 3: Export from runtime package**

Modify `src/agentos/runtime/__init__.py`:

```python
from agentos.runtime.profile import (
    ChannelRuntimeProfile,
    DistributedAgentProfile,
    DistributedRuntimeProfile,
    LocalRuntimeProfile,
    RuntimeProfile,
    WebRuntimeProfile,
)
```

Add names to `__all__`.

- [ ] **Step 4: Export top-level names**

Modify `src/agentos/__init__.py` runtime import block:

```python
from agentos.runtime import (
    Agent,
    AgentResult,
    ChannelRuntimeProfile,
    DistributedAgentProfile,
    DistributedRuntimeProfile,
    LocalRuntimeProfile,
    ProviderRequestBuilder,
    QueryLoop,
    RetryPolicy,
    RunOptions,
    RuntimeProfile,
    WebRuntimeProfile,
)
```

Add the names to `__all__`.

- [ ] **Step 5: Run public API tests**

Run:

```bash
uv run pytest tests/architecture/test_public_api.py -q
```

Expected: PASS.

## Task 5: Guard QueryLoop Boundary

**Files:**
- Test: `tests/runtime/test_runtime_profile.py`

- [ ] **Step 1: Add import-boundary test**

Append:

```python
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_runtime_profile_does_not_leak_into_query_loop() -> None:
    query_loop = PROJECT_ROOT / "src" / "agentos" / "runtime" / "query_loop.py"
    text = query_loop.read_text(encoding="utf-8")

    assert "runtime.profile" not in text
    assert "agentos.channels" not in text
    assert "agentos.registry" not in text
```

- [ ] **Step 2: Run boundary tests**

Run:

```bash
uv run pytest tests/runtime/test_runtime_profile.py::test_runtime_profile_does_not_leak_into_query_loop -q
```

Expected: PASS.

## Task 6: Update Docs

**Files:**
- Modify: `.claude/skills/agent-os/modules/architecture.md`
- Modify: `.claude/skills/agent-os/modules/agent-forms.md`

- [ ] **Step 1: Update architecture docs**

Replace roadmap-only language with:

```markdown
Runtime profiles are available from `agentos.runtime.profile`:

- `LocalRuntimeProfile`
- `WebRuntimeProfile`
- `DistributedAgentProfile`

They assemble deployment collaborators and do not run turns directly.
```

- [ ] **Step 2: Update agent forms docs**

In the Future Extensions table, change `Runtime Profile Agent` from "No first-class RuntimeProfile" to:

```markdown
Runtime Profile Agent | Initial `LocalRuntimeProfile`, `WebRuntimeProfile`, and `DistributedAgentProfile` exist; distributed lease/snapshot adapters remain separate roadmap work.
```

- [ ] **Step 3: Run diff hygiene**

Run:

```bash
git diff --check
```

Expected: no errors.

## Task 7: Verification

**Files:** all changed files

- [ ] **Step 1: Run targeted tests**

Run:

```bash
uv run pytest tests/runtime/test_runtime_profile.py tests/architecture/test_public_api.py -q
```

Expected: PASS.

- [ ] **Step 2: Run compileall**

Run:

```bash
uv run python -m compileall -q src tests
```

Expected: no output and exit code 0.

- [ ] **Step 3: Run full suite**

Run:

```bash
uv run pytest -q
```

Expected: all tests pass.

- [ ] **Step 4: Run final diff check**

Run:

```bash
git diff --check
```

Expected: no errors.

## Self-Review

Spec coverage:

- Local terminal/script form: Task 1.
- Web profile and channel assembly: Task 2.
- Distributed task profile boundary: Task 3.
- Public API: Task 4.
- QueryLoop deployment-agnostic invariant: Task 5.
- Skill docs alignment: Task 6.
- Verification: Task 7.

Placeholder scan:

- No implementation step contains `TBD`, `TODO`, or unspecified test instructions.

Type consistency:

- `RuntimeProfile`, `ChannelRuntimeProfile`, `DistributedRuntimeProfile`, `LocalRuntimeProfile`, `WebRuntimeProfile`, and `DistributedAgentProfile` names match the Phase 1 design spec.
