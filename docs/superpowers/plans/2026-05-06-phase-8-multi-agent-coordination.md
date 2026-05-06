# Phase 8 Local Multi-Agent Coordination Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the local single-process multi-agent coordination layer: local registry, inbox, task state table, spawn execution, expert dispatch, and coordination tools.

**Architecture:** Keep `EventBus` observation-only and put execution messaging in `AgentInbox`. Keep `AgentRegistry` declarative, use `TaskTable` as the task truth source, and keep `AgentCoordinator` as a thin orchestrator over registry, inbox, task table, spawn executor, and expert runner.

**Tech Stack:** Python 3.11 dataclasses, `threading.Event`, `queue.Queue`, `concurrent.futures.ThreadPoolExecutor`, pytest, existing `Agent`, `ToolRegistry`, `FakeProvider`, and typed `EventBus`.

---

### Task 1: Typed Events And Public API

**Files:**
- Modify: `src/agentos/events/types.py`
- Modify: `src/agentos/events/__init__.py`
- Modify: `src/agentos/runtime/__init__.py`
- Test: `tests/runtime/test_typed_events.py`
- Test: `tests/architecture/test_public_api.py`

- [ ] Add failing tests for multi-agent events and public `agentos.multi` exports.
- [ ] Implement typed event dataclasses: `SubagentSpawnedEvent`, `AgentTaskDispatchedEvent`, `AgentTaskCompletedEvent`, `AgentTaskFailedEvent`, `AgentTaskCancelledEvent`, `AgentInboxBackpressureEvent`, `AgentTaskLateResultReceivedEvent`.
- [ ] Export event classes from `agentos.events` and `agentos.runtime`.
- [ ] Create `agentos.multi.__init__` exports after Task 2 creates the module.
- [ ] Run `pytest tests/runtime/test_typed_events.py tests/architecture/test_public_api.py -q`.

### Task 2: Multi-Agent Core Types And Registry

**Files:**
- Create: `src/agentos/multi/types.py`
- Create: `src/agentos/multi/registry.py`
- Create: `src/agentos/multi/__init__.py`
- Test: `tests/multi/test_registry.py`

- [ ] Add failing registry tests for unique registration, resolve, unregister, status update, empty discover, and all-capability matching.
- [ ] Implement frozen `AgentCard`, `TaskRequest`, `TaskResult`, `TaskHandle`, `TaskRecord`, `SubagentInitRequest`, `AgentEnvelope`, and literal aliases.
- [ ] Implement `AgentRegistry` protocol and `InMemoryRegistry`.
- [ ] Run `pytest tests/multi/test_registry.py -q`.

### Task 3: AgentInbox

**Files:**
- Create: `src/agentos/multi/inbox.py`
- Test: `tests/multi/test_inbox.py`

- [ ] Add failing inbox tests for send/collect/wait, missing inbox failure, remove behavior, and backpressure event emission.
- [ ] Implement `AgentInbox`, `AgentInboxError`, `AgentInboxMissingError`, and `AgentInboxFullError`.
- [ ] Run `pytest tests/multi/test_inbox.py -q`.

### Task 4: TaskTable State Machine

**Files:**
- Create: `src/agentos/multi/tasks.py`
- Test: `tests/multi/test_task_table.py`

- [ ] Add failing tests for queued/running/completed, queued cancellation, timeout detection, and late result preservation.
- [ ] Implement `TaskTable` with locked compare-and-set style transitions.
- [ ] Run `pytest tests/multi/test_task_table.py -q`.

### Task 5: Agent Lifecycle Interrupt

**Files:**
- Modify: `src/agentos/runtime/query_loop.py`
- Modify: `src/agentos/runtime/agent.py`
- Test: `tests/runtime/test_agent_stream_api.py`

- [ ] Add failing tests that `Agent.interrupt()` causes the next turn to raise `RuntimeError("agent run interrupted")`, and that `Agent.clear_interrupt()` allows subsequent turns.
- [ ] Implement `QueryLoop.request_interrupt()`, `QueryLoop.clear_interrupt()`, `QueryLoop.interrupted`, and safe-point checks.
- [ ] Implement `Agent.interrupt()`, `Agent.clear_interrupt()`, and `Agent.interrupted`.
- [ ] Run `pytest tests/runtime/test_agent_stream_api.py -q`.

### Task 6: SpawnExecutor And Coordinator Spawn

**Files:**
- Create: `src/agentos/multi/spawn.py`
- Create: `src/agentos/multi/coordinator.py`
- Test: `tests/multi/test_spawn_executor.py`
- Test: `tests/multi/test_coordinator_spawn.py`

- [ ] Add failing tests that `SpawnExecutor(max_workers=1)` queues work, and coordinator spawn registers an ephemeral card, runs the child agent, stores the result in `TaskTable`, sends parent inbox result, and unregisters the child.
- [ ] Implement `SpawnExecutor`.
- [ ] Implement `AgentCoordinator.attach_agent()`, `spawn()`, `collect_results()`, `active_tasks()`, and `cancel()`.
- [ ] Run `pytest tests/multi/test_spawn_executor.py tests/multi/test_coordinator_spawn.py -q`.

### Task 7: Expert Dispatch Runner

**Files:**
- Create: `src/agentos/multi/expert.py`
- Modify: `src/agentos/multi/coordinator.py`
- Test: `tests/multi/test_coordinator_dispatch.py`
- Test: `tests/multi/test_expert_runner.py`

- [ ] Add failing tests that dispatch finds an available expert by capability, sends a task request, the runner executes it, and the result returns to the parent inbox.
- [ ] Add failing test that saturated experts cause `dispatch()` to raise `RuntimeError("no available agent")`.
- [ ] Implement `AgentCoordinator.dispatch()` and `execute_expert_envelope()`.
- [ ] Implement `ExpertAgentRunner.run_once()`, `run_forever()`, and `stop()`.
- [ ] Run `pytest tests/multi/test_coordinator_dispatch.py tests/multi/test_expert_runner.py -q`.

### Task 8: Coordination Tools

**Files:**
- Create: `src/agentos/multi/tools.py`
- Test: `tests/multi/test_coordination_tools.py`

- [ ] Add failing tests that `AgentCoordinationTools.register()` registers `spawn_subagent`, `dispatch_to_expert`, `check_agent_tasks`, and `cancel_agent_task` as external tools.
- [ ] Add failing tests that handlers call the coordinator and return JSON strings with task IDs, statuses, active tasks, and results.
- [ ] Implement `AgentCoordinationTools`.
- [ ] Run `pytest tests/multi/test_coordination_tools.py -q`.

### Task 9: Verification

**Files:**
- All Phase 8 files

- [ ] Run `pytest tests/multi tests/runtime/test_typed_events.py tests/runtime/test_agent_stream_api.py tests/architecture/test_public_api.py -q`.
- [ ] Run `pytest -q`.
- [ ] Run `python -m compileall -q src tests`.
- [ ] Run `git diff --check`.
- [ ] Run `rg -n "append[_]notification|Provider[C]onfig|Tool[D]efinition|Agent[R]untime" src/agentos tests`.
- [ ] Run `rg -n "class Message[B]us|src/agentos/multi/bus[.]py|test[_]bus" src/agentos tests`.
