# Skill Release Governance Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an SDK-owned release manifest and drift report boundary for the repository `agent-os` developer skill.

**Architecture:** Create a focused `agentos.skills` module that scans a skill directory, hashes files deterministically, and compares two manifests. Keep installation, publishing, signing, and overwrite policy deployment-owned.

**Tech Stack:** Python dataclasses, `pathlib`, `hashlib`, pytest docs/public API tests.

---

### Task 1: Skill Manifest And Drift API

**Files:**
- Create: `src/agentos/skills.py`
- Test: `tests/test_skill_release.py`

- [ ] **Step 1: Write failing tests**

Add tests for `build_skill_release_manifest(...)` and
`compare_skill_release_manifests(...)`. The tests should create temporary skill
directories, verify deterministic file hashes and manifest hash, verify
missing/extra/changed drift reporting, and verify invalid directories are
rejected.

- [ ] **Step 2: Verify RED**

Run:

```powershell
uv run pytest tests\test_skill_release.py -q
```

Expected: fail because `agentos.skills` does not exist.

- [ ] **Step 3: Implement minimal module**

Implement frozen dataclasses:

- `SkillReleaseFile`
- `SkillReleaseManifest`
- `SkillReleaseDriftReport`

Implement:

- `build_skill_release_manifest(skill_dir, version, source, skill_name="agent-os")`
- `compare_skill_release_manifests(expected, actual)`

- [ ] **Step 4: Verify GREEN**

Run:

```powershell
uv run pytest tests\test_skill_release.py -q
```

Expected: pass.

### Task 2: Public API And Readiness Evidence

**Files:**
- Modify: `src/agentos/__init__.py`
- Modify: `src/agentos/readiness.py`
- Modify: `tests/architecture/test_public_api.py`
- Modify: `tests/test_readiness.py`

- [ ] **Step 1: Write failing public API/readiness assertions**

Assert top-level `agentos` exports the manifest/drift classes and helper
functions, and that SDK developer guidance evidence includes the release
manifest and drift report boundary.

- [ ] **Step 2: Verify RED**

Run:

```powershell
uv run pytest tests\architecture\test_public_api.py tests\test_readiness.py -q
```

Expected: fail until exports and readiness evidence are updated.

- [ ] **Step 3: Export and wire readiness**

Add imports and `__all__` entries. If needed, add a dedicated readiness form or
extend objective docs evidence to include skill release governance.

- [ ] **Step 4: Verify GREEN**

Run the same tests. Expected: pass.

### Task 3: Docs And Skill Guidance

**Files:**
- Modify: `docs/production-readiness.md`
- Modify: `docs/agentos-objective-coverage-audit.md`
- Modify: `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`
- Modify: `.claude/skills/agent-os/SKILL.md`
- Modify: `.claude/skills/agent-os/modules/quick-start.md`
- Modify: `tests/docs/test_production_readiness_docs.py`
- Modify: `tests/docs/test_objective_coverage_audit_docs.py`

- [ ] **Step 1: Write failing docs assertions**

Assert docs and skill guidance mention `SkillReleaseManifest`,
`SkillReleaseDriftReport`, repository vs installed skill comparison, and that
installation/publishing remain deployment-owned.

- [ ] **Step 2: Verify RED**

Run:

```powershell
uv run pytest tests\docs\test_production_readiness_docs.py tests\docs\test_objective_coverage_audit_docs.py -q
```

Expected: fail until docs and skill guidance are updated.

- [ ] **Step 3: Update docs and skill guidance**

Document the release manifest/drift report as the SDK-owned boundary and keep
copy/install/publish/signing/approval deployment-owned.

- [ ] **Step 4: Verify GREEN**

Run the same docs tests. Expected: pass.

### Task 4: Final Verification

Run:

```powershell
uv run pytest tests\test_skill_release.py tests\architecture\test_public_api.py tests\test_readiness.py tests\docs\test_production_readiness_docs.py tests\docs\test_objective_coverage_audit_docs.py -q
rg "SkillRelease|build_skill_release_manifest|compare_skill_release_manifests" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
uv run pytest -q
uv run python -m compileall -q src tests
git diff --check
```

Expected: focused and full suites pass, compileall exits 0, runtime boundary
scan has no output and exit code 1, and diff check has no whitespace errors
apart from existing CRLF warnings if present.
