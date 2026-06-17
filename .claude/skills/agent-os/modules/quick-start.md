---
name: agent-os-quick-start
description: Minimal code patterns for each agent-os SDK scenario — copy-paste ready, no explanation needed
---

# Quick Start Patterns

## Minimal Agent (3 lines)

```python
from agentos import AgentBuilder
from agentos.providers import AnthropicProvider

agent = AgentBuilder().provider(AnthropicProvider(api_key="...", model="claude-sonnet-4-6")).build()
result = agent.run("What is Python?")
print(result.content)
```

## Provider with extra_body (Custom Fields)

`OpenAICompatibleProvider` 支持透传 provider 专属字段（如 Qwen `vl_high_resolution_images`、DeepSeek 实验参数等）。Core 字段（`model`/`messages`/`tools`/`stream`）会覆盖 `extra_body` 中的同名 key。

```python
from agentos import AgentBuilder
from agentos.providers import OpenAICompatibleProvider

agent = AgentBuilder().provider(
    OpenAICompatibleProvider(
        api_key="...",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="qwen-vl-max",
        extra_body={"vl_high_resolution_images": True},
    ),
).build()
```

## Agent with Custom Tools

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

result = agent.run("Search for async patterns in Python")
print(result.content)
```

## Agent with Compression (Long Sessions)

```python
from agentos import AgentBuilder
from agentos.providers import AnthropicProvider

agent = (
    AgentBuilder()
    .provider(AnthropicProvider(api_key="...", model="claude-sonnet-4-6"))
    .with_compression()  # enables RuleBasedCompressor + default budget
    .build()
)

# Agent can handle 100+ message sessions without context overflow
for i in range(50):
    result = agent.run(f"Message {i}: tell me about topic {i}")
```

## Streaming

```python
from agentos import AgentBuilder
from agentos.providers import AnthropicProvider
from agentos.runtime.stream_events import AssistantContentDelta, TurnStreamCompleted

agent = AgentBuilder().provider(AnthropicProvider(api_key="...", model="claude-sonnet-4-6")).build()

for event in agent.stream("Write a haiku"):
    if isinstance(event, AssistantContentDelta):
        print(event.text, end="", flush=True)
    elif isinstance(event, TurnStreamCompleted):
        print()  # newline at end
```

## Async Streaming

```python
import asyncio
from agentos import AgentBuilder
from agentos.providers import AnthropicProvider
from agentos.runtime.stream_events import AssistantContentDelta

agent = AgentBuilder().provider(AnthropicProvider(api_key="...", model="claude-sonnet-4-6")).build()

async def main():
    async for event in agent.async_stream("Write a haiku"):
        if isinstance(event, AssistantContentDelta):
            print(event.text, end="", flush=True)

asyncio.run(main())
```

## Async Tool Handlers

如果你的 tool handler 是 `async def`，**必须**用 `AsyncQueryLoop`——sync `QueryLoop` 遇到 async handler 会抛 `RuntimeError`。

```python
import asyncio
from agentos import AgentBuilder, RegisteredTool
from agentos.runtime import Agent, AsyncQueryLoop

async def fetch_data(arguments: dict[str, object]) -> str:
    # native async I/O, no asyncio.run / to_thread
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
# 重新装配为 native async loop（如果你额外配置了 hook/retry/session，记得一起搬过去）
async_loop = AsyncQueryLoop(**{
    name: getattr(base.query_loop, name)
    for name in (
        "context_runtime", "message_runtime", "request_builder", "provider",
        "compression_runtime", "tool_call_router", "event_bus",
    )
})
agent = Agent(query_loop=async_loop)

async def main():
    result = await agent.async_run("...")
    print(result.content)

asyncio.run(main())
```

纯 sync handler 不需要切换：`AgentBuilder().build()` 默认 sync loop，`agent.async_stream(...)` 会自动放进 executor。

## HTTP API (ASGI)

```python
from agentos import AgentBuilder
from agentos.providers import AnthropicProvider
from agentos.channels import AllowAllChannelAuthPolicy, AsgiAgentApp
from agentos.channels.session import InMemoryAgentSessionProvider

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

# Run with: uvicorn main:app --host 0.0.0.0 --port 8000
# Endpoints:
#   POST /v1/sessions/{id}/turns          → JSON response
#   POST /v1/sessions/{id}/turns/stream   → SSE stream
```

`AllowAllChannelAuthPolicy` is a local/dev convenience only. `AsgiAgentApp`
uses `RejectAllChannelAuthPolicy` for production-facing defaults; production
services must inject their own channel auth, tenant, and gateway policy.

## Hooks (Lifecycle Interception)

```python
from agentos import AgentBuilder
from agentos.providers import AnthropicProvider
from agentos.hooks import HookManager, HookRegistry, HookContext, HookResult

registry = HookRegistry()

def log_provider_call(context: HookContext) -> HookResult | None:
    print(f"Calling provider with {len(context.payload.get('messages', []))} messages")
    return None  # allow

registry.register("before_provider_call", log_provider_call, priority=50)

# HookManager is wired into QueryLoop automatically when passed to builder
# (currently via query_loop_kwargs — builder.hook_manager() coming in next version)
```

## Event Bus (Observation)

```python
from agentos import AgentBuilder
from agentos.providers import AnthropicProvider
from agentos.runtime.event_bus import EventBus
from agentos.events import TurnCompletedEvent

bus = EventBus()

class PrintCompleted:
    def record(self, event):
        if isinstance(event, TurnCompletedEvent):
            print(f"Turn completed: {event.turn_id}")

bus.subscribers.append(PrintCompleted())

agent = (
    AgentBuilder()
    .provider(AnthropicProvider(api_key="...", model="claude-sonnet-4-6"))
    .event_bus(bus)
    .build()
)
```

## Single-Process HTTP API

```python
from agentos.channels import AsgiAgentApp, InMemoryAgentSessionProvider, SlidingWindowRateLimiter

app = AsgiAgentApp(
    sessions=InMemoryAgentSessionProvider(make_agent),
    readiness_checks={"provider": lambda: True},
    rate_limiter=SlidingWindowRateLimiter(max_requests=60, window_seconds=60),
)

# GET /health
# GET /ready
# POST /v1/sessions/{id}/turns
```

Source: `src/agentos/channels/asgi.py`, `src/agentos/channels/rate_limit.py`, `tests/channels/test_health_endpoint.py`, `tests/channels/test_rate_limit.py`.

This is suitable for a single-process service or prototype. `InMemoryAgentSessionProvider` does not hydrate shared session state across arbitrary nodes.

## Agent Service Reference Layer

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

Use this reference service when a distributed web agent needs standard
`AsgiAgentApp` composition, readiness aggregation, auth/rate-limit injection,
and JSON-safe evidence. It is not a platform; gateways, tenant directory,
Kubernetes/systemd/autoscaling, credentials, migrations, and live backend
verification remain deployment-owned.

## Multi-Node Primitives (Redis + Postgres)

```python
from agentos.memory import RedisHotSessionStore
from agentos.persistence import PostgresDurableSessionStore

hot_store = RedisHotSessionStore(
    url="redis://localhost:6379",
    key_prefix="myagent",
    ttl_seconds=3600,
)

durable_store = PostgresDurableSessionStore(
    dsn="postgresql://user:pass@localhost/agentdb",
)

# Use hot_store.load_hot_state(session_id) before turn
# Use hot_store.save_hot_state(state) after turn
# Use durable_store for segment persistence and message recovery
```

This is not yet a complete copy-paste production web runtime. For arbitrary-node session routing, add an app-owned `AgentSessionProvider` with session locking, snapshot/projection restore, turn execution, save-back, and failure recovery. See `modules/persistence.md`.

## Progressive Skill Disclosure

让 LLM 按需加载 skill 文档与资源，避免把全部 skill 内容硬塞进 system prompt。

```python
import asyncio
from pathlib import Path
from agentos import AgentBuilder
from agentos.capabilities import ToolCallRouter, ToolRegistry
from agentos.capabilities.skills import (
    SkillRegistry,
    FileSystemSkillSource,
    register_skill_loader_tools,
)

async def build_agent():
    skills = await SkillRegistry.aload(
        FileSystemSkillSource(skill_dirs=[Path("./skills")]),
    )
    tool_registry = ToolRegistry()
    register_skill_loader_tools(tool_registry, skills)  # in-place 注册 load_skill + load_skill_resource
    router = ToolCallRouter(tool_registry=tool_registry)

    return (
        AgentBuilder()
        .provider(...)
        .tool_call_router(router)  # 与 .tools(...) 互斥
        .build()
    )

agent = asyncio.run(build_agent())
```

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

`SkillReleaseDriftReport` only reports whether the repository skill and the
installed user-level skill match. Copying, overwriting, publishing, signing,
and release approval stay outside the SDK boundary.

## Production Reference Web Agent

```python
from agentos.examples.production_reference_web_agent import (
    build_production_reference_web_agent,
)

example = build_production_reference_web_agent()
app = example.app
evidence = example.as_dict()
```

Phase 101: Production Reference Example lives at
`src/agentos/examples/production_reference_web_agent.py`, with tests in
`tests/examples/test_production_reference_web_agent.py`. It composes the
production reference web agent from `AgentServiceReference`,
`DistributedWebRuntimeProfile`, a Nacos/Redis/Postgres state plane, a
readiness endpoint, backend verification,
`ProductionReadinessEvidenceBundle`, `ReferenceStatePlaneStack`,
`ReferenceLiveBackendProbePack`, and a planner primitive.

This production reference web agent does not create backend clients. Nacos,
Redis, Postgres, credentials, migrations, CI/CD, process supervision, live
backend probe execution, gateway/TLS, tenant directory integration, rollout,
rollback, alerting, runbooks, and sandbox isolation are deployment-owned real
infrastructure.

The demo runtime blocks production readiness by default. Imported backend
verification evidence proves deployment-owned probes ran, but the reference app
still uses demo Memory/InMemory runtime bindings unless a deployment injects
real Redis/Postgres/Nacos state-plane clients.

`SkillContentSource` 是 async ABC——自定义实现（如 Redis backed）需实现 4 个 async 方法：`list_skills` / `load_skill` / `list_resources` / `load_resource`。
