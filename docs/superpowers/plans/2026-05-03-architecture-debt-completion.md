# Architecture Debt Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Finish the deferred architecture debt from the Phase 2-4 review hardening pass.

**Architecture:** This is a mechanical cleanup around already-decided names and contracts. Public imports move to `agentos`, responsibility-specific class names replace vague runtime names, event records become typed dataclasses, empty M2 sections stop rendering, and `QueryLoop` no longer reaches through `ContextRuntime.state`.

**Tech Stack:** Python 3.11, dataclasses, pytest, hatchling package configuration.

---

## Scope

This plan completes these deferred items:

- Public package import target is `agentos`.
- Public runtime names use `QueryLoop`, `ProviderRequestBuilder`, `Provider`, `ToolCallRouter`, and `HookManager`.
- Loose string runtime events become typed event dataclasses.
- Empty declared schema and working state sections are omitted from simple prompts.
- `QueryLoop` asks `ContextRuntime` for a renderable snapshot instead of reading `.state` directly.

This plan does not add MCP, skills, memory, persistence, multi-agent, or remote channel behavior.

## File Map

- Keep the public package directory at `src/agentos/`.
- Update package config: `pyproject.toml`.
- Modify runtime names: `src/agentos/runtime/query_loop.py`, `src/agentos/runtime/provider_request_builder.py`, `src/agentos/runtime/__init__.py`.
- Modify capability routing name: `src/agentos/capabilities/router.py`, `src/agentos/capabilities/__init__.py`.
- Modify hook manager name: `src/agentos/hooks/manager.py`, `src/agentos/hooks/__init__.py`.
- Modify provider protocol name: `src/agentos/providers/base.py`, `src/agentos/providers/__init__.py`.
- Modify typed events: `src/agentos/runtime/event_bus.py`, `src/agentos/hooks/base.py`, `src/agentos/hooks/manager.py`.
- Modify context render boundary: `src/agentos/context/runtime.py`, `src/agentos/runtime/provider_request_builder.py`, `src/agentos/runtime/query_loop.py`.
- Update tests and docs to use new names.

## Tasks

### Task 1: Red Tests For Remaining Drift

- [x] Add tests that import `agentos` and assert old public names are absent.
- [x] Add tests that `EventBus.emit(TurnStartedEvent(...))` stores typed event objects.
- [x] Add tests that simple prompts omit `# Declared Working State Schema` and `# Working State`.
- [x] Add tests that `ProviderRequestBuilder.build()` accepts `ContextRuntime` instead of raw `ContextState`.
- [x] Run targeted tests and confirm failures are due to missing implementation.

### Task 2: Package And Naming Migration

- [x] Use `src/agentos` as the public package directory.
- [x] Replace imports in `src`, `tests`, docs, and `pyproject.toml`.
- [x] Rename public classes and exports to the target names.
- [x] Keep no compatibility aliases for old names in the public package.
- [x] Run targeted import/name tests until green.

### Task 3: Typed Events And Hook Manager

- [x] Replace loose string runtime event with typed event dataclasses.
- [x] Make `EventBus.emit()` accept event objects.
- [x] Keep `EventBus` as observation-only event publication.
- [x] Make `HookRegistry` register explicit hook points, not runtime event classes.
- [x] Make `HookManager` return explicit `HookResult` decisions and keep failure recording.
- [x] Update `QueryLoop` to emit typed events.
- [x] Run runtime and hook tests until green.

### Task 4: Context Boundary And Simple Prompt Elision

- [x] Add `ContextRuntime.snapshot()` for request building.
- [x] Update `ProviderRequestBuilder.build()` to receive `ContextRuntime`.
- [x] Update `QueryLoop.build_request()` to call the builder without reading `.state`.
- [x] Omit empty declared schema and working state sections in `ContextRenderer`.
- [x] Update golden files and tests.

### Task 5: Full Verification

- [x] Run `uv run --python 3.11 --extra dev pytest -q`.
- [x] Run `uv run --python 3.11 --extra dev python -m compileall -q src tests`.
- [x] Run `git diff --cached --check`.
- [x] Run drift search for old public names and review intentional historical mentions in old plan docs.

## Self Review

- Spec coverage: all deferred items from the previous final answer have a task.
- Placeholder scan: no TODO/TBD placeholders.
- Type consistency: public names use `agentos`, `QueryLoop`, `ProviderRequestBuilder`, `Provider`, `ToolCallRouter`, `HookManager`, and typed `*Event` dataclasses.
