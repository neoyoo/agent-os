---
name: agent-os-quick-start
description: Minimal copy-paste patterns for common agent-os SDK scenarios.
---

# Quick Start Patterns

## Minimal Agent

```python
from agentos import AgentBuilder
from agentos.providers import AnthropicProvider

agent = (
    AgentBuilder()
    .provider(AnthropicProvider(api_key="...", model="claude-sonnet-4-6"))
    .build()
)

result = agent.run("What is Python?")
print(result.content)
```

## OpenAI-Compatible Provider Extra Body

`OpenAICompatibleProvider` passes provider-specific fields through
`extra_body`. Core request fields such as `model`, `messages`, `tools`, and
`stream` override same-named keys in `extra_body`.

```python
from agentos import AgentBuilder
from agentos.providers import OpenAICompatibleProvider

agent = (
    AgentBuilder()
    .provider(
        OpenAICompatibleProvider(
            api_key="...",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            model="qwen-vl-max",
            extra_body={"vl_high_resolution_images": True},
        ),
    )
    .build()
)
```

## Custom Tools

```python
from agentos import AgentBuilder, RegisteredTool
from agentos.providers import AnthropicProvider

def handle_search(arguments: dict[str, object]) -> str:
    query = str(arguments["query"])
    return f"Results for: {query}"

agent = (
    AgentBuilder()
    .provider(AnthropicProvider(api_key="...", model="claude-sonnet-4-6"))
    .tools([
        RegisteredTool(
            name="search",
            description="Search a knowledge base for relevant information.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                },
                "required": ["query"],
            },
            handler=handle_search,
        ),
    ])
    .build()
)
```

## Compression

```python
from agentos import AgentBuilder
from agentos.providers import AnthropicProvider

agent = (
    AgentBuilder()
    .provider(AnthropicProvider(api_key="...", model="claude-sonnet-4-6"))
    .with_compression()
    .build()
)
```

## Streaming

```python
from agentos import AgentBuilder
from agentos.providers import AnthropicProvider
from agentos.runtime.stream_events import AssistantContentDelta, TurnStreamCompleted

agent = (
    AgentBuilder()
    .provider(AnthropicProvider(api_key="...", model="claude-sonnet-4-6"))
    .build()
)

for event in agent.stream("Write a haiku"):
    if isinstance(event, AssistantContentDelta):
        print(event.text, end="", flush=True)
    elif isinstance(event, TurnStreamCompleted):
        print()
```

## Async Streaming

```python
import asyncio

from agentos import AgentBuilder
from agentos.providers import AnthropicProvider
from agentos.runtime.stream_events import AssistantContentDelta

agent = (
    AgentBuilder()
    .provider(AnthropicProvider(api_key="...", model="claude-sonnet-4-6"))
    .build()
)

async def main() -> None:
    async for event in agent.async_stream("Write a haiku"):
        if isinstance(event, AssistantContentDelta):
            print(event.text, end="", flush=True)

asyncio.run(main())
```

## Async Tool Handlers

Use a native `AsyncQueryLoop` when a tool handler is `async def`.

```python
from agentos import AgentBuilder, RegisteredTool
from agentos.runtime import Agent, AsyncQueryLoop

async def fetch_data(arguments: dict[str, object]) -> str:
    return await some_async_client.get(str(arguments["url"]))

base = (
    AgentBuilder()
    .provider(...)
    .tools([
        RegisteredTool(
            name="fetch_data",
            description="Fetch JSON from a URL.",
            parameters={
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
            handler=fetch_data,
        ),
    ])
    .build()
)

async_loop = AsyncQueryLoop(**{
    name: getattr(base.query_loop, name)
    for name in (
        "context_runtime",
        "message_runtime",
        "request_builder",
        "provider",
        "compression_runtime",
        "tool_call_router",
        "event_bus",
    )
})
agent = Agent(query_loop=async_loop)
```

Pure sync handlers do not need this. `AgentBuilder().build()` uses the sync
loop, and `agent.async_stream(...)` runs sync work through an executor.

## Single-Process HTTP API

```python
from agentos import AgentBuilder
from agentos.channels import (
    AllowAllChannelAuthPolicy,
    AsgiAgentApp,
    InMemoryAgentSessionProvider,
)
from agentos.providers import AnthropicProvider

def make_agent(session_id: str):
    return (
        AgentBuilder()
        .provider(AnthropicProvider(api_key="...", model="claude-sonnet-4-6"))
        .build()
    )

app = AsgiAgentApp(
    sessions=InMemoryAgentSessionProvider(make_agent),
    auth_policy=AllowAllChannelAuthPolicy(),
)
```

`AllowAllChannelAuthPolicy` is for local/dev examples only. Production-facing
`AsgiAgentApp` uses `RejectAllChannelAuthPolicy` for production-facing defaults;
production apps should inject auth, tenant, rate-limit, and gateway policy.

## Agent Service Reference

```python
from agentos import (
    AGENT_SERVICE_REFERENCE_REQUIRED_COMPONENTS,
    AgentServiceReference,
    AgentServiceReferenceProfile,
)

service = AgentServiceReference(
    runtime_profile=distributed_web_runtime_profile,
    service_profile=AgentServiceReferenceProfile(
        configured_components=AGENT_SERVICE_REFERENCE_REQUIRED_COMPONENTS,
    ),
)

app = service.build_asgi_app()
```

This reference service standardizes ASGI composition, readiness aggregation,
auth/rate-limit injection, stream resume configuration, and JSON-safe
evidence. Gateways, tenant directory, credentials, migrations, rollout,
rollback, and live backend verification remain deployment-owned.

## Distributed Web Runtime

```python
from agentos.channels import (
    RedisSessionLeaseStore,
    RedisSseEventBuffer,
    RedisSseTurnControlStore,
)
from agentos.persistence import PostgresSessionSnapshotPersistence
from agentos.runtime import DistributedWebRuntimeProfile

profile = DistributedWebRuntimeProfile(
    agent_factory=snapshot_agent_factory,
    lease_store=RedisSessionLeaseStore("redis://redis:6379/0"),
    snapshot_persistence=PostgresSessionSnapshotPersistence(
        "postgresql://user:pass@postgres/agentos",
    ),
    owner_id="web-node-a",
    sse_event_buffer=RedisSseEventBuffer(...),
    sse_turn_control=RedisSseTurnControlStore(...),
    session_lease_heartbeat_interval_seconds=10.0,
)

app = profile.build_channel_app()
```

For cross-node reconnect, configure shared SSE buffer, turn control, and lease
heartbeat. Redis/Postgres credentials, migrations, stale lease recovery,
tenant routing, and live backend verification are deployment-owned.

## Production Reference Web Agent

```python
from agentos.examples.production_reference_web_agent import (
    build_production_reference_web_agent,
)

example = build_production_reference_web_agent()
app = example.app
evidence = example.as_dict()
```

Phase 101: Production Reference Example provides the production reference web
agent at `src/agentos/examples/production_reference_web_agent.py`, with tests
in `tests/examples/test_production_reference_web_agent.py`. It composes
`AgentServiceReference`, `DistributedWebRuntimeProfile`, a
Nacos/Redis/Postgres state plane, a readiness endpoint, backend verification,
`ProductionReadinessEvidenceBundle`, `ReferenceStatePlaneStack`,
`ReferenceLiveBackendProbePack`, and a planner primitive.

It does not create backend clients. Nacos, Redis, Postgres, credentials,
migrations, live backend probe execution, gateway/TLS, tenant directory
integration, rollout, rollback, alerting, runbooks, and sandbox isolation are
deployment-owned real infrastructure. The demo runtime blocks production
readiness by default.

## Skill Release Drift Check

```python
from agentos import (
    build_skill_release_manifest,
    compare_skill_release_manifests,
)

repository = build_skill_release_manifest(
    ".claude/skills/agent-os",
    version="2026.06.16",
    source="repo://agent-os/.claude/skills/agent-os",
)
installed = build_skill_release_manifest(
    "~/.codex/skills/agent-os",
    version="2026.06.16",
    source="user://agent-os",
)

report = compare_skill_release_manifests(repository, installed)
if not report.ready:
    raise SystemExit(report.as_dict())
```

`SkillReleaseDriftReport` compares the repository skill with the installed user-level skill so the release manifest can prove version synchronization.
Copying, overwriting, publishing, signing, and release approval stay outside
the SDK boundary.

## Progressive Skill Loading

```python
import asyncio
from pathlib import Path

from agentos import AgentBuilder
from agentos.capabilities import ToolCallRouter, ToolRegistry
from agentos.capabilities.skills import (
    FileSystemSkillSource,
    SkillRegistry,
    register_skill_loader_tools,
)

async def build_agent():
    skills = await SkillRegistry.aload(
        FileSystemSkillSource(skill_dirs=[Path("./skills")]),
    )
    tool_registry = ToolRegistry()
    register_skill_loader_tools(tool_registry, skills)
    router = ToolCallRouter(tool_registry=tool_registry)

    return (
        AgentBuilder()
        .provider(...)
        .tool_call_router(router)
        .build()
    )

agent = asyncio.run(build_agent())
```

Custom `SkillContentSource` implementations, such as Redis-backed sources,
must implement `list_skills`, `load_skill`, `list_resources`, and
`load_resource` as async methods.
