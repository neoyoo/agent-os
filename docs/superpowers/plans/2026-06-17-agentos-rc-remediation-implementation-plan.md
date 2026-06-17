# AgentOS RC Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve the current independent review P1 findings, classify P2 findings, and prepare AgentOS for a fresh production SDK release-candidate review based on code, tests, docs, and release evidence.

**Architecture:** Treat the current branch `review/agentos-sdk-architecture-20260611` as the development branch. Implement fixes in small TDD batches, keep SDK-owned safety boundaries in source/tests, keep deployment-owned responsibilities explicit in docs/evidence, and do not mark independent review evidence passed until a later fresh neutral review succeeds.

**Tech Stack:** Python 3.11, pytest, uv, Redis/Postgres/Nacos adapter boundaries, AgentOS SDK modules under `src/agentos`, release docs under `docs`.

---

## Current Review Classification

### P0

None reported by the fresh reviewers.

### Blocking P1

1. **MCP direct prevalidation is forgeable.**
   - Risk: public callers can pass `prevalidated=True` directly to `MCPToolAdapter.execute` and bypass router validation.
   - Primary files: `src/agentos/capabilities/mcp.py`, `src/agentos/capabilities/router.py`, `tests/capabilities/test_mcp.py`.

2. **Workspace execution evidence can leak secret patterns in neutral metadata values.**
   - Risk: `metadata={"note": "Authorization: Bearer raw-token"}` survives evidence conversion.
   - Primary files: `src/agentos/workspace.py`, `tests/test_workspace.py`, `src/agentos/_redaction.py`.

3. **Lease-fenced session save overclaims atomic lease ownership.**
   - Risk: Redis lease is checked before Postgres CAS; a stale owner can save after lease expiry before the new owner saves.
   - Primary files: `src/agentos/channels/durable_session.py`, `src/agentos/persistence/postgres.py`, `tests/channels/test_durable_session_provider.py`, `tests/persistence/test_postgres_session_snapshot_persistence.py`.

4. **Distributed stream resume readiness is stronger than the evidence.**
   - Risk: readiness reports cross-node stream resume based on configured objects, not proven shared/live backend behavior.
   - Primary files: `src/agentos/runtime/profile.py`, `src/agentos/channels/asgi.py`, `src/agentos/examples/production_reference_web_agent.py`, `tests/examples/test_production_reference_web_agent.py`, `tests/runtime`.

5. **Release evidence is not reproducible from committed source.**
   - Risk: ignored `docs/release-evidence.json` is consumed by tests/gates without a committed generation path.
   - Primary files: `src/agentos/release.py`, `tests/test_release_evidence.py`, `docs/release-hardening.md`, `.gitignore`.

6. **Release manifest identity validation is optional in the release path.**
   - Risk: branch/commit/version drift can be missed when callers omit expected identity.
   - Primary files: `src/agentos/release.py`, `tests/test_release_evidence.py`.

7. **Public API governance does not cover all documented stable surfaces and root exports are too broad.**
   - Risk: `docs/api-stability.md` claims stable API areas that are not fully drift-protected, while `agentos.__all__` exposes too much experimental surface as first-class.
   - Primary files: `src/agentos/__init__.py`, `docs/api-stability.md`, `docs/public-api-inventory.json`, `tests/architecture/test_public_api.py`.

8. **Runtime profile contract is incoherent for composition profiles.**
   - Risk: profiles shaped like `RuntimeProfile` expose `build_agent()` but intentionally raise `NotImplementedError`.
   - Primary files: `src/agentos/runtime/profile.py`, `tests/runtime`.

### P2 To Fix Before Re-Review Or Explicitly Backlog

1. Planner dispatch can leave a step assigned if the process crashes between plan mutation and coordinator spawn.
2. A2A operation server has fail-closed auth, but rate limiting is opt-in.
3. Team worker capability narrowing is optional in production-facing examples/config.
4. Release evidence has ambiguous passed command data such as `passed: 0`.
5. Repository skill entry point still has mojibake in `.claude/skills/agent-os/SKILL.md`.
6. Large modules (`a2a_operations.py`, `planner.py`, `team.py`, `asgi.py`) increase maintenance risk; full splitting can be backlog if public boundaries and tests are strengthened now.

---

### Task 1: Workspace And MCP Security P1

**Files:**
- Modify: `src/agentos/capabilities/mcp.py`
- Modify: `src/agentos/capabilities/router.py`
- Modify: `src/agentos/workspace.py`
- Modify: `tests/capabilities/test_mcp.py`
- Modify: `tests/test_workspace.py`
- Reference: `docs/superpowers/plans/2026-06-17-workspace-security-p1-hardening-implementation-plan.md`

- [ ] **Step 1: Execute the MCP unforgeable prevalidation test-first change**

Use the exact Task 1 RED/GREEN steps from `docs/superpowers/plans/2026-06-17-workspace-security-p1-hardening-implementation-plan.md`.

Run:

```powershell
uv run pytest tests\capabilities\test_mcp.py::test_mcp_tool_adapter_rejects_forged_public_prevalidation_flag -q
```

Expected RED: direct boolean prevalidation reaches the fake client before the fix.

- [ ] **Step 2: Execute the workspace metadata value redaction test-first change**

Use the exact Task 2 RED/GREEN steps from `docs/superpowers/plans/2026-06-17-workspace-security-p1-hardening-implementation-plan.md`.

Run:

```powershell
uv run pytest tests\test_workspace.py::test_workspace_execution_result_redacts_secret_patterns_in_metadata_values -q
```

Expected RED: neutral metadata values contain raw token/password/key before the fix.

- [ ] **Step 3: Run the focused security gate**

Run:

```powershell
uv run pytest tests\capabilities\test_mcp.py tests\test_workspace.py tests\capabilities\test_tool_sandbox_policy.py tests\capabilities\test_execution_backend.py -q
```

Expected: all pass.

- [ ] **Step 4: Commit Task 1**

Run:

```powershell
git add src\agentos\capabilities\mcp.py src\agentos\capabilities\router.py src\agentos\workspace.py tests\capabilities\test_mcp.py tests\test_workspace.py docs\superpowers\plans\2026-06-17-workspace-security-p1-hardening-implementation-plan.md docs\superpowers\plans\2026-06-17-agentos-rc-remediation-implementation-plan.md
git commit -m "Harden workspace evidence and MCP routing"
```

---

### Task 2: Distributed Session Lease And Save Semantics P1

**Files:**
- Modify: `src/agentos/channels/durable_session.py`
- Modify: `src/agentos/persistence/postgres.py`
- Modify: `tests/channels/test_durable_session_provider.py`
- Modify: `tests/channels/test_redis_session_lease_store.py`
- Modify: `tests/persistence/test_postgres_session_snapshot_persistence.py`
- Modify if needed: `docs/production-readiness.md`

- [ ] **Step 1: Write the failing stale-owner save test**

Add a test proving `release_agent()` or `save_if_lease_owned()` does not persist a snapshot when lease ownership is lost after the first ownership check and before persistence.

Run:

```powershell
uv run pytest tests\channels\test_durable_session_provider.py::test_release_agent_does_not_persist_after_lease_lost_during_save -q
```

Expected RED: current code allows the stale save path or releases active state without preserving retry evidence.

- [ ] **Step 2: Implement honest lease-fenced save behavior**

Preferred implementation:
- Keep Redis lease token/fence checks in `DurableSessionProvider`.
- Require the persistence save path to receive the expected `lease_fence`.
- Re-check lease ownership immediately before save and immediately after save; if the post-save check fails, surface the save as unsafe and do not report it as an owned save.
- Update docs to call this a lease-fenced CAS boundary, not a Redis/Postgres atomic transaction.

If code cannot make the operation atomic across Redis and Postgres, change public wording and method docs so the SDK no longer overclaims atomicity.

- [ ] **Step 3: Add Postgres CAS/fence regression coverage**

Run:

```powershell
uv run pytest tests\persistence\test_postgres_session_snapshot_persistence.py -q
```

Expected: tests prove stale revision/fence cannot overwrite a newer snapshot.

- [ ] **Step 4: Run distributed session focused gate**

Run:

```powershell
uv run pytest tests\channels\test_durable_session_provider.py tests\channels\test_redis_session_lease_store.py tests\persistence\test_postgres_session_snapshot_persistence.py -q
```

Expected: all pass.

- [ ] **Step 5: Commit Task 2**

Run:

```powershell
git add src\agentos\channels\durable_session.py src\agentos\persistence\postgres.py tests\channels\test_durable_session_provider.py tests\channels\test_redis_session_lease_store.py tests\persistence\test_postgres_session_snapshot_persistence.py docs\production-readiness.md
git commit -m "Clarify and harden lease fenced session saves"
```

---

### Task 3: Distributed Web Resume Readiness P1

**Files:**
- Modify: `src/agentos/runtime/profile.py`
- Modify: `src/agentos/examples/production_reference_web_agent.py`
- Modify: `tests/examples/test_production_reference_web_agent.py`
- Modify: `tests/runtime`
- Modify: `docs/production-readiness.md`

- [ ] **Step 1: Write failing readiness evidence tests**

Add tests that distinguish these states:
- shared stream components configured
- shared backend evidence present
- live cross-node resume evidence present

Run:

```powershell
uv run pytest tests\examples\test_production_reference_web_agent.py tests\runtime -q
```

Expected RED: current readiness marks `distributed_stream_resume_ready` based on component presence.

- [ ] **Step 2: Split readiness metadata**

Change `DistributedWebRuntimeProfile.readiness_metadata()` to expose separate fields:
- `distributed_stream_resume_configured`
- `distributed_stream_resume_shared_backend_evidenced`
- `distributed_stream_resume_cross_node_evidenced`
- `distributed_stream_resume_ready`

Only set `distributed_stream_resume_ready` true when the evidence level is strong enough for the claim.

- [ ] **Step 3: Update production reference claims**

Ensure fake/in-memory/demo backends produce demo evidence only. Production-ready claims must require explicit live/shared backend evidence.

- [ ] **Step 4: Run focused gate**

Run:

```powershell
uv run pytest tests\channels\test_sse_buffer.py tests\channels\test_sse_turn_control.py tests\examples\test_production_reference_web_agent.py tests\runtime -q
```

Expected: all pass.

- [ ] **Step 5: Commit Task 3**

Run:

```powershell
git add src\agentos\runtime\profile.py src\agentos\examples\production_reference_web_agent.py tests\examples\test_production_reference_web_agent.py tests\runtime docs\production-readiness.md
git commit -m "Make distributed web resume readiness evidence based"
```

---

### Task 4: Release Evidence And Manifest Governance P1

**Files:**
- Modify: `src/agentos/release.py`
- Modify: `tests/test_release_evidence.py`
- Modify or create: `scripts/generate_release_evidence.py`
- Modify: `docs/release-hardening.md`
- Modify: `docs/release-evidence.example.json`

- [ ] **Step 1: Write failing generator reproducibility test**

Add a test that asserts a committed generator entry point exists and can produce a manifest with schema, version, branch, commit, command results, and explicit independent review status.

Run:

```powershell
uv run pytest tests\test_release_evidence.py::test_release_evidence_generator_entrypoint_is_committed_and_reproducible -q
```

Expected RED: no committed generator path currently proves reproducibility.

- [ ] **Step 2: Add the generator script**

Create `scripts/generate_release_evidence.py` with a deterministic CLI:

```powershell
uv run python scripts\generate_release_evidence.py --output docs\release-evidence.json --branch review/agentos-sdk-architecture-20260611 --commit <current-commit> --version <pyproject-version> --independent-review-status pending
```

The script must not mark review passed. It may include command result placeholders only when the command result is supplied or freshly run.

- [ ] **Step 3: Make release identity mandatory in the release gate path**

Keep `validate_release_evidence_manifest()` usable for unit tests, but add a release-gate wrapper that requires expected branch, commit, and version. Tests must prove omitting identity fails in the release gate path.

- [ ] **Step 4: Run release evidence focused gate**

Run:

```powershell
uv run pytest tests\test_release_evidence.py tests\docs\test_production_hardening_docs.py -q
```

Expected: all pass; generated local `docs/release-evidence.json` may remain ignored.

- [ ] **Step 5: Commit Task 4**

Run:

```powershell
git add src\agentos\release.py tests\test_release_evidence.py scripts\generate_release_evidence.py docs\release-hardening.md docs\release-evidence.example.json
git commit -m "Make release evidence generation reproducible"
```

---

### Task 5: Public API And Runtime Profile Governance P1

**Files:**
- Modify: `src/agentos/__init__.py`
- Modify: `src/agentos/runtime/profile.py`
- Modify: `docs/api-stability.md`
- Modify: `docs/public-api-inventory.json`
- Modify: `tests/architecture/test_public_api.py`
- Modify: `tests/runtime`

- [ ] **Step 1: Decide the public API governance strategy**

Use this decision rule:
- Stable root exports stay in `agentos.__all__`.
- Experimental/deployment-heavy surfaces remain importable from namespaces but are removed from root `agentos.__all__` unless the docs explicitly classify them stable.
- Every documented stable namespace must appear in `docs/public-api-inventory.json`.

- [ ] **Step 2: Write failing inventory coverage test**

Add a test that compares documented stable namespaces in `docs/api-stability.md` with governed modules in `docs/public-api-inventory.json`.

Run:

```powershell
uv run pytest tests\architecture\test_public_api.py::test_documented_stable_namespaces_are_governed -q
```

Expected RED: currently documented stable areas are not all governed.

- [ ] **Step 3: Update inventory and root exports**

Either:
- add missing stable modules to the inventory, or
- narrow `docs/api-stability.md` to match the intended stable surface.

Then reduce root exports to the stable core and keep experimental imports namespaced.

- [ ] **Step 4: Fix RuntimeProfile composition contract**

Write tests that assert composition profiles do not advertise a callable `build_agent()` contract if they intentionally cannot build an agent. Implement by splitting protocol typing or renaming composition profiles so the contract is honest.

- [ ] **Step 5: Run public API/runtime gate**

Run:

```powershell
uv run pytest tests\architecture\test_public_api.py tests\runtime -q
```

Expected: all pass.

- [ ] **Step 6: Commit Task 5**

Run:

```powershell
git add src\agentos\__init__.py src\agentos\runtime\profile.py docs\api-stability.md docs\public-api-inventory.json tests\architecture\test_public_api.py tests\runtime
git commit -m "Tighten public API and runtime profile contracts"
```

---

### Task 6: P2 Release Polishing And Backlog Decisions

**Files:**
- Modify as needed: `src/agentos/multi/planner.py`
- Modify as needed: `src/agentos/channels/a2a_operations.py`
- Modify as needed: `src/agentos/multi/team.py`
- Modify as needed: `tests/multi`
- Modify as needed: `tests/channels/test_a2a_operations.py`
- Modify: `.claude/skills/agent-os/SKILL.md`
- Modify or create: `docs/release-backlog.md`

- [ ] **Step 1: Planner dispatch partial failure**

If a small fix is possible, add recovery evidence or a pending-dispatch marker before external coordinator spawn. If not, document it in `docs/release-backlog.md` as non-blocking with mitigation.

- [ ] **Step 2: A2A rate limiting reference default**

For production reference service/profile, configure or document a default rate policy. Do not weaken fail-closed auth.

- [ ] **Step 3: Team worker capability allowlist guidance**

Add production example/test coverage showing `agent_create` is paired with explicit worker capability allow-lists.

- [ ] **Step 4: Fix skill mojibake**

Repair the unreadable section in `.claude/skills/agent-os/SKILL.md` or replace it with a clean pointer to the quick-start module.

- [ ] **Step 5: Classify large module splitting**

Add `docs/release-backlog.md` entries for large-module decomposition if not done in this release. The entry must explain why it is non-blocking for RC: public boundary tests, focused behavior tests, and no known P1 behavior bug.

- [ ] **Step 6: Run P2 focused gate**

Run:

```powershell
uv run pytest tests\multi tests\channels\test_a2a_operations.py tests\channels\test_a2a_egress_url_policy.py tests\docs -q
```

Expected: all pass.

- [ ] **Step 7: Commit Task 6**

Run:

```powershell
git add src\agentos\multi\planner.py src\agentos\channels\a2a_operations.py src\agentos\multi\team.py tests\multi tests\channels .claude\skills\agent-os\SKILL.md docs\release-backlog.md docs\production-readiness.md
git commit -m "Close release P2 governance items"
```

---

### Final Verification Before Fresh Review

- [ ] **Run full gate**

```powershell
uv run pytest -q
uv run pytest tests\architecture\test_public_api.py -q
uv run python -m compileall -q src tests
git diff --check
rg -n "ReferenceLiveBackendProbe|REFERENCE_LIVE_BACKEND|ReferenceStatePlane|state plane|readiness|planner|team|A2A|sandbox|worker supervisor|production_design_constraints|release hardening|production reference" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
```

Expected:
- full tests pass
- public API tests pass
- compileall exits 0
- diff check exits 0
- boundary scan finds no runtime contamination matches; `rg` exit 1 is acceptable when there are no matches

- [ ] **Generate local pending release evidence**

Run the committed generator and leave `independent_review.status` as `pending`.

- [ ] **Dispatch fresh neutral review**

Dispatch fresh reviewers with no conversation history and without mentioning any target score. Require category scores, P0/P1/P2 findings, evidence used, and production SDK RC readiness.

- [ ] **Only after fresh review passes**

Update local ignored `docs/release-evidence.json` to mark independent review passed, validate with mandatory branch/commit/version identity, and then consider the goal for completion audit.

---

### Self-Review Checklist

- [ ] No P0 remains.
- [ ] All listed P1 have code, tests, docs/evidence, or an explicit corrected contract proving the issue is no longer a blocking release claim.
- [ ] P2 items are either fixed or entered into `docs/release-backlog.md` with mitigation and non-blocking rationale.
- [ ] The final review prompt does not mention a target score or induce favorable scoring.
- [ ] The goal is not marked complete until fresh independent review and all final gates prove completion.
