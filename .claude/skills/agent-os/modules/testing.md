---
name: agent-os-testing
description: How to test agents built with agent-os SDK — FakeProvider pattern, tool handler testing, integration tests
---

# Testing agent-os Agents

## FakeProvider

The SDK's test infrastructure is built around `FakeProvider` — a Provider implementation that returns scripted responses without calling any LLM.

```python
from agentos.providers import Provider, ProviderRequest, ProviderResponse, ProviderToolCall


class FakeProvider:
    """Returns scripted responses for testing."""

    def __init__(self, responses: list[ProviderResponse] | None = None):
        self._responses = list(responses or [])
        self._call_count = 0
        self.requests: list[ProviderRequest] = []

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        if self._call_count < len(self._responses):
            response = self._responses[self._call_count]
        else:
            response = ProviderResponse(content="default response")
        self._call_count += 1
        return response


# Helper to create a response with tool calls
def tool_call_response(name: str, arguments: dict, call_id: str = "tc_1") -> ProviderResponse:
    return ProviderResponse(
        content="",
        tool_calls=(ProviderToolCall(id=call_id, name=name, arguments=arguments),),
    )
```

## Testing Tool Handlers (Unit)

Tool handlers are pure functions — test them directly:

```python
from myagent.tools.search import handle_search


def test_search_returns_results():
    result = handle_search({"query": "python async"})
    assert "python" in result.lower()
    assert isinstance(result, str)


def test_search_handles_empty_query():
    result = handle_search({"query": ""})
    assert result  # should return something meaningful
```

## Testing Tool Routing (Integration)

Verify the full loop: model calls tool → router dispatches → handler runs → result returns:

```python
from agentos import AgentBuilder, RegisteredTool
from agentos.providers import ProviderResponse, ProviderToolCall


def test_agent_routes_tool_call_and_uses_result():
    """Model calls search tool, gets result, produces final answer."""
    provider = FakeProvider(responses=[
        # Turn 1: model decides to call search
        ProviderResponse(
            content="",
            tool_calls=(
                ProviderToolCall(id="tc_1", name="search", arguments={"query": "test"}),
            ),
        ),
        # Turn 2: model uses tool result to answer
        ProviderResponse(content="Based on search results: answer"),
    ])

    agent = (
        AgentBuilder()
        .provider(provider)
        .tools([
            RegisteredTool(
                name="search",
                description="Search for info.",
                parameters={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
                handler=lambda args: f"Found: {args['query']}",
            ),
        ])
        .build()
    )

    result = agent.run("Find info about testing")

    # Verify tool was called
    assert len(provider.requests) == 2
    # Verify final response uses tool result
    assert result.content == "Based on search results: answer"
```

## Testing Context Protocol

Verify the model can use working state tools:

```python
def test_agent_can_declare_and_update_working_state():
    provider = FakeProvider(responses=[
        # Model declares schema
        tool_call_response("declare_schema", {
            "fields": [{"name": "goal", "type": "str", "purpose": "Current task goal"}],
        }),
        # Model updates state
        tool_call_response("update_state", {
            "field_name": "goal",
            "value": "Write a test",
        }),
        # Model gives final answer
        ProviderResponse(content="Done"),
    ])

    agent = AgentBuilder().provider(provider).build()
    result = agent.run("Help me write a test")

    # Verify context state was mutated
    state = agent.query_loop.context_runtime.snapshot()
    assert state.working_state["goal"] == "Write a test"
```

## Testing Streaming

```python
from agentos.runtime.stream_events import AssistantContentDelta, TurnStreamCompleted


def test_stream_produces_events():
    provider = FakeProvider(responses=[
        ProviderResponse(content="Hello world"),
    ])
    agent = AgentBuilder().provider(provider).build()

    events = list(agent.stream("hi"))

    content_deltas = [e for e in events if isinstance(e, AssistantContentDelta)]
    completions = [e for e in events if isinstance(e, TurnStreamCompleted)]
    assert len(completions) == 1
    assert completions[0].content == "Hello world"
```

## Testing Hooks

```python
from agentos.hooks import HookManager, HookRegistry, HookContext, HookResult


def test_hook_can_deny_tool_call():
    calls = []
    registry = HookRegistry()
    registry.register(
        "before_tool_call",
        lambda ctx: HookResult(action="deny") if ctx.payload.get("tool_name") == "dangerous" else None,
    )
    manager = HookManager(registry=registry)

    result = manager.dispatch("before_tool_call", {"tool_name": "dangerous"})
    assert result.action == "deny"

    result = manager.dispatch("before_tool_call", {"tool_name": "safe"})
    assert result.action == "allow"
```

## Testing ASGI Channel

```python
import asyncio
import json


async def test_asgi_app_handles_turn():
    from agentos.channels import AsgiAgentApp
    from agentos.channels.session import InMemoryAgentSessionProvider

    app = AsgiAgentApp(
        sessions=InMemoryAgentSessionProvider(
            lambda sid: AgentBuilder().provider(FakeProvider([
                ProviderResponse(content="hello"),
            ])).build(),
        ),
    )

    # Simulate ASGI call
    sent = []
    async def send(msg): sent.append(msg)
    async def receive(): return {"type": "http.request", "body": b'{"message":"hi"}', "more_body": False}

    await app(
        {"type": "http", "method": "POST", "path": "/v1/sessions/s1/turns", "headers": []},
        receive, send,
    )

    body = json.loads(sent[-1]["body"])
    assert body["content"] == "hello"
```

## Test Fixtures (conftest.py)

```python
import pytest
from agentos import AgentBuilder, Agent
from agentos.providers import ProviderResponse


@pytest.fixture
def fake_provider():
    return FakeProvider()


@pytest.fixture
def simple_agent(fake_provider):
    return AgentBuilder().provider(fake_provider).build()


@pytest.fixture
def agent_with_tools(fake_provider):
    from myagent.tools import TOOLS
    return AgentBuilder().provider(fake_provider).tools(TOOLS).build()
```
