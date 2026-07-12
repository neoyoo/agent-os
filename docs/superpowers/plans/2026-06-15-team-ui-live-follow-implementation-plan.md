# Team UI Live Follow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an SSE live follow endpoint for team UI events using the existing `TeamUiStreamStore` cursor model.

**Architecture:** Keep `TeamUiStreamStore` unchanged. `AsgiAgentApp` adds a read-only SSE route that replays existing events and polls the store for new events until idle timeout or disconnect. This keeps the endpoint compatible with both in-memory and Postgres stores.

**Tech Stack:** Python ASGI, SSE formatting, JSON serialization, pytest async helpers.

---

## Files

- Modify: `src/agentos/channels/asgi.py`
- Modify: `tests/channels/test_asgi_app.py`
- Modify: `src/agentos/readiness.py`
- Modify: `tests/test_readiness.py`
- Modify: `docs/production-readiness.md`
- Modify: `.claude/skills/agent-os/SKILL.md`
- Modify: `.claude/skills/agent-os/flow/01-requirements.md`
- Modify: `.claude/skills/agent-os/flow/02-spec-generation.md`
- Modify: `.claude/skills/agent-os/modules/agent-forms.md`
- Modify: `.claude/skills/agent-os/modules/multi-agent.md`
- Modify: `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`

## Task 1: Failing Endpoint Tests

- [ ] Add tests for replay, live follow, Last-Event-ID resume, missing store,
  and invalid cursor.
- [ ] Run targeted tests and verify RED.

## Task 2: Implement Endpoint

- [ ] Add stream route matcher.
- [ ] Add cursor parser that accepts Last-Event-ID or query cursor.
- [ ] Add SSE writer for `TeamUiEvent`.
- [ ] Add follow loop with poll interval, idle timeout, heartbeat, and
  disconnect handling.
- [ ] Run targeted tests and verify GREEN.

## Task 3: Docs And Verification

- [ ] Update readiness/docs/skills so live follow is no longer a gap.
- [ ] Keep tool sandbox enforcement as the remaining team production gap.
- [ ] Run focused tests, runtime boundary search, compileall, full pytest, and
  `git diff --check`.
