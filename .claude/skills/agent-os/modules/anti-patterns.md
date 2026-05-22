---
name: agent-os-anti-patterns
description: Common mistakes when using agent-os SDK — check every generated code block against these before output
---

# Anti-Patterns

**Check every generated code block against these before output.**

## 1. Forgetting context protocol tools exist

```python
# BAD — manually managing "agent memory" in tool handlers
def handle_remember(arguments: dict[str, object]) -> str:
    GLOBAL_MEMORY[arguments["key"]] = arguments["value"]  # reinventing the wheel
    return "remembered"

# GOOD — the model already has declare_schema / update_state / extend_schema
# for structured state. Don't build a parallel state system.
# Context protocol tools are auto-wired by AgentBuilder.
# The model decides when to use them based on ContextRenderer prompts.
```

## 2. Passing raw dicts where typed messages expected

```python
# BAD — ProviderRequest now normalizes, but bypassing types loses guarantees
request = ProviderRequest(
    system="...",
    messages=[{"role": "user", "content": None}],  # None → "None" in old code
)

# GOOD — use typed constructors
from agentos.providers import UserMessage, AssistantMessage, ToolResultMessage

request = ProviderRequest(
    system="...",
    messages=[UserMessage(content="hello")],
)
```

## 3. Creating Agent per request in multi-node

```python
# BAD — builds fresh agent each request, loses all session state
@app.post("/chat")
async def chat(message: str, session_id: str):
    agent = AgentBuilder().provider(provider).build()  # fresh every time!
    return agent.run(message).content

# GOOD — use AgentSessionProvider that loads state from Redis
app = AsgiAgentApp(
    sessions=StatelessSessionProvider(hot_store, agent_factory),
)
```

## 4. Blocking the event loop in ASGI

```python
# BAD — agent.run() is synchronous, blocks asyncio
async def handle(request):
    result = agent.run(request.message)  # blocks event loop!
    return result

# GOOD — SDK's AsgiAgentApp already handles this:
# - Non-streaming: wraps in asyncio.to_thread()
# - Streaming: uses async bridge with thread worker
# Just use AsgiAgentApp, don't DIY the async wrapping.

# BAD #2 — registering async tool handler with sync QueryLoop
async def fetch_user(args): ...
agent = AgentBuilder().tools([RegisteredTool("fetch_user", "...", fetch_user)]).build()
agent.run("get user 42")  # RuntimeError: async handler requires AsyncQueryLoop

# GOOD — wrap in AsyncQueryLoop (see Async Tool Handlers in quick-start)
```

## 5. Registering async hook handlers

```python
# BAD — HookManager is synchronous, async handlers silently fail
async def my_hook(context: HookContext) -> HookResult | None:
    await some_async_op()  # NEVER AWAITED — returns coroutine object
    return None

registry.register("before_tool_call", my_hook)  # raises TypeError now

# GOOD — use sync handlers in HookManager
def my_hook(context: HookContext) -> HookResult | None:
    # If you need async, use EventBus subscription instead
    return None
```

## 6. Over-engineering tool descriptions

```python
# BAD — vague, the model won't know when to use it
RegisteredTool(
    name="do_stuff",
    description="Does stuff with the system.",  # useless
    ...
)

# GOOD — precise, tells the model exactly when and how
RegisteredTool(
    name="search_docs",
    description="Search the documentation index. Returns up to 5 relevant passages. Use when the user asks about API usage, configuration, or troubleshooting.",
    ...
)
```

## 7. Mutating frozen dataclass fields

```python
# BAD — ProviderToolCall.arguments is deepcopied, but holding original ref
args = {"path": "/tmp/foo"}
tc = ProviderToolCall(id="1", name="read", arguments=args)
args["path"] = "/etc/passwd"  # doesn't affect tc (deepcopied), but confusing

# BAD — trying to mutate response
response.tool_calls.append(extra_call)  # TypeError: tuple doesn't support append

# GOOD — treat all provider types as immutable values
# Create new instances if you need modifications
```

## 8. Manual SSE implementation instead of using Agent.stream_sse()

```python
# BAD — reimplementing SSE formatting
for event in agent.stream(message):
    yield f"data: {json.dumps(event)}\n\n"  # wrong format, missing type dispatch

# GOOD — use built-in serializers
for chunk in agent.stream_sse(message):
    yield chunk  # correctly formatted SSE with proper event types
```

## 9. Compression without understanding the budget

```python
# BAD — enabling compression but not understanding when it fires
agent = AgentBuilder().provider(p).with_compression().build()
# ...expecting compression to happen after 5 messages

# REALITY — default budget: max_active_messages=20, retain_latest_messages=6
# Compression triggers when active window exceeds 20 messages.
# First 6 most recent messages are always retained.
# If you need earlier compression, pass custom BudgetPolicy.
```

## 10. Using AgentBuilder for everything when QueryLoop kwargs suffice

```python
# OVERKILL for tests — AgentBuilder is for production wiring
agent = (
    AgentBuilder()
    .provider(FakeProvider())
    .tools([...])
    .context_runtime(custom_context)
    .message_runtime(custom_messages)
    .build()
)

# SIMPLER for tests — direct construction is fine
from agentos.runtime import Agent, QueryLoop
agent = Agent(query_loop=QueryLoop(
    context_runtime=fake_context,
    message_runtime=MessageRuntime(),
    request_builder=ProviderRequestBuilder(...),
    provider=FakeProvider(),
    tool_call_router=router,
))
```
