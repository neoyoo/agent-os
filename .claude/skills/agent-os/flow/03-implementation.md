---
name: agent-os-implementation
description: Execute agent implementation from spec — generates project skeleton, dispatches parallel subagents for tool handlers and config, validates end-to-end
---

# Implementation

## Prerequisites

- `specs/agent-spec.yaml` exists and is confirmed by user
- Target project directory is decided

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

Replace `InMemoryAgentSessionProvider` with a stateless provider that loads/saves from Redis:

```python
from agentos.memory import RedisHotSessionStore, MemoryRuntime
from agentos.persistence import PostgresDurableSessionStore

hot_store = RedisHotSessionStore(url=config.redis_url, ttl_seconds=3600)
durable_store = PostgresDurableSessionStore(dsn=config.postgres_dsn)

# AgentSessionProvider that loads from hot_store before each turn
# and saves back after each turn — implementation pattern in modules/persistence.md
```

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

For endpoint-backed A2A dispatch:
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
        description="Reviews code over A2A.",
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
