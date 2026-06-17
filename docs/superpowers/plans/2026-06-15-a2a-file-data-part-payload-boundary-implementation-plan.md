# A2A File/Data Part Payload Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add A2A 1.0 file bytes, file URL, and structured data message part support without moving protocol concerns into runtime loops.

**Architecture:** Extend the existing `A2AMessagePart` dataclass in `agentos.channels.a2a_operations` and keep serialization/deserialization in the same protocol boundary. Preserve text compatibility and legacy inbound parsing, but emit only the A2A 1.0 wrapper-object shape for new payloads.

**Tech Stack:** Python dataclasses, pytest, existing agent-os A2A operation serializers.

---

Spec: `docs/superpowers/specs/2026-06-15-a2a-file-data-part-payload-boundary-design.md`

## Tasks

### Task 1: File/Data Part Tests

**Files:**
- Modify: `tests/channels/test_a2a_operations.py`

- [ ] **Step 1: Write failing tests**

Add tests that expect `A2AMessagePart.from_file_bytes(...)`,
`A2AMessagePart.from_file_url(...)`, and `A2AMessagePart.from_data(...)` to
round-trip through `a2a_message_to_dict(...)` and `a2a_message_from_dict(...)`.
Add parser tests for legacy `kind: file` and `kind: data` payloads and a
negative test for ambiguous wrapper fields.

- [ ] **Step 2: Verify RED**

Run:

```powershell
uv run pytest tests\channels\test_a2a_operations.py::test_a2a_message_file_and_data_parts_round_trip_with_wrapper_shape tests\channels\test_a2a_operations.py::test_a2a_message_part_parser_accepts_legacy_file_and_data_parts tests\channels\test_a2a_operations.py::test_a2a_message_part_parser_rejects_ambiguous_wrapper_fields -q
```

Expected: FAIL because `from_file_bytes`, `from_file_url`, and `from_data` do
not exist yet.

### Task 2: Protocol Boundary Implementation

**Files:**
- Modify: `src/agentos/channels/a2a_operations.py`

- [ ] **Step 1: Extend `A2AMessagePart`**

Add `file` and `data` kinds, optional payload fields, and constructors for file
bytes, file URL, and data parts.

- [ ] **Step 2: Update serializers**

Emit `raw`, `url`, or `data` wrapper shapes with optional `filename` and
`mediaType`. Continue emitting text as `{"text": "..."}`.

- [ ] **Step 3: Update parsers**

Accept A2A 1.0 wrappers and legacy `kind` payloads. Reject ambiguous wrapper
payloads that contain more than one of `text`, `raw`, `url`, or `data`.

- [ ] **Step 4: Verify GREEN**

Run:

```powershell
uv run pytest tests\channels\test_a2a_operations.py::test_a2a_message_file_and_data_parts_round_trip_with_wrapper_shape tests\channels\test_a2a_operations.py::test_a2a_message_part_parser_accepts_legacy_file_and_data_parts tests\channels\test_a2a_operations.py::test_a2a_message_part_parser_rejects_ambiguous_wrapper_fields -q
```

Expected: PASS.

### Task 3: Guidance And Readiness

**Files:**
- Modify: `src/agentos/readiness.py`
- Modify: `docs/production-readiness.md`
- Modify: `.claude/skills/agent-os/modules/agent-forms.md`
- Modify: `.claude/skills/agent-os/modules/multi-agent.md`
- Modify: `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`
- Modify: `tests/test_readiness.py`
- Modify: `tests/docs/test_production_readiness_docs.py`

- [ ] **Step 1: Update readiness evidence**

Add A2A 1.0 file/data part payload shape as protocol evidence and remove
file/data from remaining protocol gaps.

- [ ] **Step 2: Update docs and skill guidance**

Describe file/data part parity as available, but keep artifact/event parity,
extension negotiation, and external conformance tests as remaining gaps.

- [ ] **Step 3: Add roadmap Phase 43**

Record the target conclusion, artifacts, and final conclusion.

### Task 4: Verification

**Files:**
- No code edits.

- [ ] **Step 1: Run targeted tests**

```powershell
uv run pytest tests\channels\test_a2a_operations.py tests\test_readiness.py tests\docs\test_production_readiness_docs.py -q
```

- [ ] **Step 2: Run full tests**

```powershell
uv run pytest -q
```

- [ ] **Step 3: Compile**

```powershell
uv run python -m compileall -q src tests
```

- [ ] **Step 4: Check runtime boundary**

```powershell
rg "PlanRetry|PlannerRuntime|PlannerTools|A2A|PushNotification|Trust|Signature|InboundAuth" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
```

Expected: no matches. `rg` exits `1` for no matches.

- [ ] **Step 5: Check diff hygiene**

```powershell
git diff --check
```

Expected: exit code 0. Existing CRLF warnings are acceptable.
