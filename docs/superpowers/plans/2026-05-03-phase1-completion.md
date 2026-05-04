# Phase 1 Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete Phase 1 by adding messages, request building, a fake provider, and a minimal runtime loop.

**Architecture:** `messages/` owns original messages and active window state. `runtime/provider_request_builder.py` converts rendered context plus active messages plus tool schemas into a provider request. `providers/fake.py` supplies deterministic responses for tests. `runtime/query_loop.py` orchestrates one user-to-assistant turn without mutating context directly.

**Tech Stack:** Python 3.11, dataclasses, pytest, uv.

---

## Baseline

Task 1, Task 2, and Task 3 are already implemented and staged. This plan completes the remaining Phase 1 scope without adding compression, recall injection, MCP execution, real provider adapters, persistence, or observability.

## File Structure

- Create: `src/agentos/messages/types.py`
  - Message roles, `ToolCall`, `Message`, `MessageRef`, and provider message materialization.
- Create: `src/agentos/messages/store.py`
  - Append-only `MessageStore`.
- Create: `src/agentos/messages/window.py`
  - `ActiveWindow` with tool call pair protection for removals.
- Create: `src/agentos/messages/runtime.py`
  - `MessageRuntime` facade for append and materialization operations.
- Create: `src/agentos/messages/__init__.py`
  - Public exports for the messages package.
- Create: `src/agentos/providers/base.py`
  - `ProviderRequest`, `ProviderResponse`, and `Provider` protocol.
- Create: `src/agentos/providers/fake.py`
  - Deterministic `FakeProvider`.
- Create: `src/agentos/providers/__init__.py`
  - Public provider exports.
- Create: `src/agentos/runtime/provider_request_builder.py`
  - `ProviderRequestBuilder`.
- Create: `src/agentos/runtime/query_loop.py`
  - Minimal `QueryLoop`.
- Create: `src/agentos/runtime/__init__.py`
  - Public runtime exports.
- Create: `tests/messages/test_runtime.py`
  - Messages unit tests.
- Create: `tests/runtime/test_provider_request_builder.py`
  - Request builder tests.
- Create: `tests/runtime/test_query_loop.py`
  - Runtime loop tests.

## Task 4: Messages Mainline

**Files:**
- Create: `src/agentos/messages/types.py`
- Create: `src/agentos/messages/store.py`
- Create: `src/agentos/messages/window.py`
- Create: `src/agentos/messages/runtime.py`
- Create: `src/agentos/messages/__init__.py`
- Create: `tests/messages/test_runtime.py`

- [ ] **Step 1: Write failing tests**

Create tests that assert:

```python
from agentos.messages import MessageRuntime, ToolCall, ToolPairWindowError


def test_message_runtime_appends_original_messages_and_active_refs() -> None:
    runtime = MessageRuntime()

    user = runtime.append_user("Build Phase 1.")
    assistant = runtime.append_assistant("Working on it.")

    assert runtime.store.get(user.id).content == "Build Phase 1."
    assert runtime.store.get(assistant.id).content == "Working on it."
    assert [message.id for message in runtime.materialize_active()] == [
        user.id,
        assistant.id,
    ]


def test_active_messages_materialize_provider_shape() -> None:
    runtime = MessageRuntime()
    runtime.append_user("Hello")
    runtime.append_assistant("Hi")

    assert runtime.materialize_provider_messages() == [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi"},
    ]


def test_message_store_is_append_only_when_active_refs_are_removed() -> None:
    runtime = MessageRuntime()
    user = runtime.append_user("Old")
    runtime.append_assistant("New")

    runtime.active_window.remove_refs([user.id], runtime.store)

    assert runtime.store.get(user.id).content == "Old"
    assert [message.content for message in runtime.materialize_active()] == ["New"]


def test_active_window_protects_tool_use_tool_result_pairs() -> None:
    runtime = MessageRuntime()
    assistant = runtime.append_assistant(
        "Need tool.",
        tool_calls=[ToolCall(id="call_1", name="read_file")],
    )
    result = runtime.append_tool_result("call_1", "file content")

    try:
        runtime.active_window.remove_refs([assistant.id], runtime.store)
    except ToolPairWindowError as error:
        assert "tool pair" in str(error)
    else:
        raise AssertionError("Expected ToolPairWindowError")

    runtime.active_window.remove_refs([assistant.id, result.id], runtime.store)

    assert runtime.materialize_active() == []
```

- [ ] **Step 2: Run tests to confirm failure**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/messages/test_runtime.py -q
```

Expected: collection failure because `agentos.messages` does not exist.

- [ ] **Step 3: Implement messages package**

Implement the files listed above. `MessageRuntime` should expose:

```python
append_user(content: str) -> Message
append_assistant(content: str, tool_calls: list[ToolCall] | None = None) -> Message
append_tool_result(tool_call_id: str, content: str) -> Message
materialize_active() -> list[Message]
materialize_provider_messages() -> list[dict[str, object]]
```

`ActiveWindow.remove_refs()` must reject removing only one side of an active assistant tool call / tool result pair.

- [ ] **Step 4: Run messages tests**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/messages/test_runtime.py -q
```

Expected: messages tests pass.

## Task 5: Request Builder

**Files:**
- Create: `src/agentos/providers/base.py`
- Create: `src/agentos/providers/__init__.py`
- Create: `src/agentos/runtime/provider_request_builder.py`
- Create: `src/agentos/runtime/__init__.py`
- Create: `tests/runtime/test_provider_request_builder.py`

- [ ] **Step 1: Write failing tests**

Create tests that assert:

```python
from agentos.context import ContextRuntime, ContextRenderer, WorkingStateField
from agentos.messages import MessageRuntime
from agentos.runtime import ProviderRequestBuilder


def test_provider_request_builder_uses_rendered_context_and_active_messages() -> None:
    context = ContextRuntime()
    context.declare_schema(
        [
            WorkingStateField(
                name="task_goal",
                type="str",
                purpose="当前任务目标和完成标准",
            ),
        ],
    )
    context.update_state("task_goal", "Build request builder.")
    messages = MessageRuntime()
    messages.append_user("Please build it.")

    request = ProviderRequestBuilder(
        context_renderer=ContextRenderer(),
        message_runtime=messages,
        tools=[{"name": "read_file", "input_schema": {"type": "object"}}],
    ).build(context.state)

    assert "# Runtime Contract" in request.system
    assert "Build request builder." in request.system
    assert request.messages == [{"role": "user", "content": "Please build it."}]
    assert request.tools == [{"name": "read_file", "input_schema": {"type": "object"}}]


def test_provider_request_builder_does_not_render_tool_schema_into_system() -> None:
    context = ContextRuntime()
    messages = MessageRuntime()
    tool_schema = {
        "name": "dangerous_schema_marker",
        "input_schema": {"type": "object", "properties": {"secret": {"type": "string"}}},
    }

    request = ProviderRequestBuilder(
        context_renderer=ContextRenderer(),
        message_runtime=messages,
        tools=[tool_schema],
    ).build(context.state)

    assert request.tools == [tool_schema]
    assert "dangerous_schema_marker" not in request.system
    assert "secret" not in request.system
```

- [ ] **Step 2: Run tests to confirm failure**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/runtime/test_provider_request_builder.py -q
```

Expected: collection failure because `agentos.runtime` and `agentos.providers` do not exist.

- [ ] **Step 3: Implement request builder**

Implement `ProviderRequest` in `providers/base.py` and `ProviderRequestBuilder` in `runtime/provider_request_builder.py`. `ProviderRequestBuilder.build()` should accept `ContextState` and produce `ProviderRequest(system, messages, tools)`.

- [ ] **Step 4: Run request builder tests**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/runtime/test_provider_request_builder.py -q
```

Expected: request builder tests pass.

## Task 6: Fake Provider And Minimal Loop

**Files:**
- Create: `src/agentos/providers/fake.py`
- Create: `src/agentos/runtime/query_loop.py`
- Modify: `src/agentos/providers/__init__.py`
- Modify: `src/agentos/runtime/__init__.py`
- Create: `tests/runtime/test_query_loop.py`

- [ ] **Step 1: Write failing tests**

Create tests that assert:

```python
from agentos.context import ContextRuntime, ContextRenderer, WorkingStateField
from agentos.messages import MessageRuntime
from agentos.providers import FakeProvider
from agentos.runtime import QueryLoop, ProviderRequestBuilder


def test_query_loop_runs_one_user_to_assistant_turn() -> None:
    context = ContextRuntime()
    context.declare_schema(
        [
            WorkingStateField(
                name="task_goal",
                type="str",
                purpose="当前任务目标和完成标准",
            ),
        ],
    )
    context.update_state("task_goal", "Run a fake provider loop.")
    messages = MessageRuntime()
    provider = FakeProvider(["Fake assistant response."])
    request_builder = ProviderRequestBuilder(
        context_renderer=ContextRenderer(),
        message_runtime=messages,
        tools=[],
    )

    loop = QueryLoop(
        context_runtime=context,
        message_runtime=messages,
        request_builder=request_builder,
        provider=provider,
    )

    response = loop.run_turn("Hello")

    assert response == "Fake assistant response."
    assert messages.materialize_provider_messages() == [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Fake assistant response."},
    ]
    assert provider.requests[0].messages == [{"role": "user", "content": "Hello"}]
    assert "Run a fake provider loop." in provider.requests[0].system
```

- [ ] **Step 2: Run tests to confirm failure**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/runtime/test_query_loop.py -q
```

Expected: failure because `FakeProvider` and `QueryLoop` do not exist.

- [ ] **Step 3: Implement fake provider and loop**

Implement:

```python
FakeProvider.complete(request: ProviderRequest) -> ProviderResponse
QueryLoop.run_turn(user_message: str) -> str
```

The loop must append the user message, build a provider request, call the provider, append the assistant response, and return the response content. It must not directly mutate working state.

- [ ] **Step 4: Run loop tests**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/runtime/test_query_loop.py -q
```

Expected: loop tests pass.

## Task 7: Full Verification

**Files:**
- No new files.

- [ ] **Step 1: Run all tests**

Run:

```bash
uv run --python 3.11 --extra dev pytest -q
```

Expected: all tests pass.

- [ ] **Step 2: Run compile check**

Run:

```bash
uv run --python 3.11 --extra dev python -m compileall -q src tests
```

Expected: exit code `0`.

- [ ] **Step 3: Clean generated caches**

Run:

```bash
rm -rf src/agentos/__pycache__ src/agentos/context/__pycache__ src/agentos/messages/__pycache__ src/agentos/providers/__pycache__ src/agentos/runtime/__pycache__ tests/context/__pycache__ tests/messages/__pycache__ tests/runtime/__pycache__ .pytest_cache
```

Expected: no cache artifacts in `git status`.

- [ ] **Step 4: Stage Phase 1 completion files**

Run:

```bash
git add docs/superpowers/plans/2026-05-03-phase1-completion.md src/agentos/messages src/agentos/providers src/agentos/runtime tests/messages tests/runtime
git status --short
```

Expected: Phase 1 completion files are staged.

## Self Review

- Spec coverage: Messages, ProviderRequestBuilder, FakeProvider, and QueryLoop all map to Phase 1 requirements.
- Placeholder scan: no placeholder requirements remain.
- Boundary check: messages do not mutate context; request builder does not know context internals beyond rendered system input; loop only orchestrates.
