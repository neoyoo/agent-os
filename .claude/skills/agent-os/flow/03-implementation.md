---
name: agent-os-implementation
description: Execute agent implementation from spec — generates project skeleton, dispatches parallel subagents for tool handlers and config, validates end-to-end
---

# Implementation

## Prerequisites

- `specs/agent-spec.yaml` exists and is confirmed by user
- Target project directory is decided
- For production-bound specs, `deployment.production_design_constraints`
  exists. Phase 99: SDK Skill / Spec Generator Finalization makes
  implementation a spec generator finalization enforcement point: the skill is
  a production agent design constraint generator, and the
  `production_design_constraints` SDK-owned constraint template must explicitly
  choose agent form, runtime profile, state plane components, persistence
  backend, registry backend, queue backend, worker supervisor, A2A exposure,
  planner/team mode, production readiness checklist, and sandbox posture:
  trusted tools only | deployment-owned isolation | future adapter. If the
  block is missing, stop before skeleton generation because the SDK does not
  create deployment-owned infrastructure.

## Process

```
1. Generate project skeleton (main thread)
2. Write shared contracts (types, interfaces)
3. Dispatch parallel subagents for independent modules
4. Integrate and validate
```

## Step 1: Project Skeleton

Generate the following structure:

```
<project>/
├── pyproject.toml
├── specs/
│   └── agent-spec.yaml
├── src/
│   └── <package>/
│       ├── __init__.py
│       ├── main.py            ← entry point: build agent, start channel
│       ├── tools/             ← tool handlers (one file per tool)
│       │   ├── __init__.py
│       │   └── <tool_name>.py
│       ├── hooks/             ← hook handlers (if any)
│       │   ├── __init__.py
│       │   └── <hook_name>.py
│       └── config.py          ← load env vars, construct provider
└── tests/
    ├── conftest.py            ← shared fixtures (FakeProvider, etc.)
    ├── test_tools.py          ← tool handler unit tests
    └── test_integration.py    ← end-to-end agent turn test
```

## Step 2: Shared Contracts

Write `main.py` skeleton that shows how everything wires together:

```python
"""Agent entry point — assembles all components via AgentBuilder."""

from agentos import AgentBuilder, Agent, RegisteredTool
from agentos.providers import AnthropicProvider  # or OpenAIProvider

from <package>.config import load_config
from <package>.tools import TOOLS


def build_agent() -> Agent:
    config = load_config()
    builder = AgentBuilder().provider(config.provider)

    if TOOLS:
        builder = builder.tools(TOOLS)

    # Compression (if enabled in spec)
    # builder = builder.with_compression()

    return builder.build()


# For HTTP channel:
# from agentos.channels import AsgiAgentApp, InMemoryAgentSessionProvider
# app = AsgiAgentApp(sessions=InMemoryAgentSessionProvider(lambda sid: build_agent()))

# For programmatic use:
# agent = build_agent()
# result = agent.run("your message here")
```

## Step 3: Parallel Dispatch

Identify independent tasks from the spec and dispatch subagents:

| Task | Independence | Subagent prompt pattern |
|------|-------------|------------------------|
| Tool handlers | Each tool is independent | "Implement `tools/<name>.py` — handler function that takes `arguments: dict[str, object]` and returns `str`. Test in `test_tools.py`." |
| Hook handlers | Each hook is independent | "Implement `hooks/<name>.py` — function matching `HookHandler` protocol." |
| Config loading | Independent | "Implement `config.py` — load env vars, construct Provider instance." |
| Channel wiring | Depends on config | "Wire `main.py` with AsgiAgentApp / programmatic entry based on spec." |
| Integration test | Depends on all above | "Write `test_integration.py` — full turn with FakeProvider, verify tool calls routed correctly." |

**Dispatch rules:**
- Tools and hooks are embarrassingly parallel — one subagent per tool
- Config is one subagent
- Channel wiring + integration test run after tools/config complete

Use `superpowers:subagent-driven-development` or `superpowers:dispatching-parallel-agents` for execution.

## Step 4: Validate

After all subagents complete:

```bash
uv run pytest -q
uv run python -m compileall -q src tests
```

Then run the agent manually:
```bash
# Programmatic
uv run python -c "from <package>.main import build_agent; agent = build_agent(); print(agent.run('hello').content)"

# HTTP (if channel enabled)
uv run uvicorn <package>.main:app --host 0.0.0.0 --port 8000
```

## Tool Handler Contract

Every tool handler follows this exact signature:

```python
def handle_<tool_name>(arguments: dict[str, object]) -> str:
    """<LLM-facing description from spec>."""
    # Extract typed params
    param = str(arguments["param_name"])
    # Do work
    result = ...
    # Return string content for tool result message
    return str(result)
```

Register in `tools/__init__.py`:

```python
from agentos import RegisteredTool
from <package>.tools.<name> import handle_<name>

TOOLS: list[RegisteredTool] = [
    RegisteredTool(
        name="<tool_name>",
        description="<from spec>",
        parameters={
            "type": "object",
            "properties": { ... },  # from spec
            "required": [ ... ],
        },
        handler=handle_<name>,
    ),
]
```

## Multi-Node Wiring (if deployment.mode == multi-node)

Current SDK status: multi-node web session support is primitives-ready. `WebRuntimeProfile` can assemble the channel, but production arbitrary-node routing still requires an application-owned provider that:

1. acquires a session lock or lease,
2. loads `SessionSnapshot` or an app-defined hot projection,
3. rebuilds `ContextRuntime`, `MessageRuntime`, `CompressionRuntime`, and `Agent`,
4. runs the turn,
5. saves the updated state,
6. releases the lock.

```python
from agentos.channels.session import AgentSessionProvider
from agentos.runtime import Agent


class DurableSessionProvider(AgentSessionProvider):
    """App-owned multi-node provider injected through WebRuntimeProfile."""

    def __init__(self, locks, persistence, agent_factory):
        self._locks = locks
        self._persistence = persistence
        self._agent_factory = agent_factory
        self._leases = {}

    def get_agent(self, session_id: str) -> Agent:
        lease = self._locks.acquire(session_id)
        self._leases[session_id] = lease
        try:
            snapshot = self._persistence.load(session_id)
        except KeyError:
            snapshot = None
        return self._agent_factory(session_id, snapshot)

    def release_agent(self, session_id: str, agent: Agent) -> None:
        try:
            snapshot = build_snapshot_from_agent(session_id, agent)
            self._persistence.save(snapshot)
        finally:
            self._locks.release(self._leases.pop(session_id))
```

Do not call this production-complete without tests for cross-node hydrate/save, same-session concurrency, turn failure policy, and lock expiry/recovery.

## Multi-Agent Wiring (if multi_agent.mode != single)

For local/in-memory coordination:
```python
from agentos.multi import (
    AgentCoordinator,
    AgentInbox,
    InMemoryRegistry,
    SpawnExecutor,
    TaskTable,
)

coordinator = AgentCoordinator(
    registry=InMemoryRegistry(),
    task_store=TaskTable(),
    message_queue=AgentInbox(),
    spawn_executor=SpawnExecutor(max_workers=4),
    subagent_factory=subagent_factory,
)
```

For endpoint-backed internal task bridge dispatch:
```python
from agentos.multi import AgentCard, AgentCoordinator, RemoteTaskExecutor

coordinator = AgentCoordinator(
    registry=registry,
    task_store=task_store,
    message_queue=message_queue,
    spawn_executor=spawn_executor,
    subagent_factory=subagent_factory,
    remote_task_executor=RemoteTaskExecutor(),
)

registry.register(
    AgentCard(
        agent_id="remote-reviewer",
        name="Remote Reviewer",
        description="Reviews code over the agent-os internal task bridge.",
        capabilities=("code-review",),
        endpoint="http://other-agent:8000",
    ),
)

handle = coordinator.dispatch(
    instruction="Review this module.",
    required_capabilities=("code-review",),
    parent_agent_id="parent",
)
```

For cross-process task state and delivery, replace `TaskTable` with `PostgresTaskStore` and `AgentInbox` with `RedisAgentMessageQueue`.

Source: `src/agentos/multi/coordinator.py`, `src/agentos/multi/remote.py`, `tests/multi/test_coordinator_distributed_boundaries.py`, `tests/multi/test_remote_dispatch.py`.

Boundary: `A2AAdapter` / `A2AServerAdapter` currently speak an agent-os task JSON shape over `/a2a/tasks`. They are not full A2A protocol compliance.
