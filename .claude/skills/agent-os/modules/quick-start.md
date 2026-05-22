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
from agentos.channels import AsgiAgentApp
from agentos.channels.session import InMemoryAgentSessionProvider

def make_agent(session_id: str):
    return (
        AgentBuilder()
        .provider(AnthropicProvider(api_key="...", model="claude-sonnet-4-6"))
        .build()
    )

app = AsgiAgentApp(
    sessions=InMemoryAgentSessionProvider(make_agent),
)

# Run with: uvicorn main:app --host 0.0.0.0 --port 8000
# Endpoints:
#   POST /v1/sessions/{id}/turns          → JSON response
#   POST /v1/sessions/{id}/turns/stream   → SSE stream
```

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

## Production HTTP API

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

## Multi-Node (Redis + Postgres)

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

`SkillContentSource` 是 async ABC——自定义实现（如 Redis backed）需实现 4 个 async 方法：`list_skills` / `load_skill` / `list_resources` / `load_resource`。
