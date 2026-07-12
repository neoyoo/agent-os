# Production Readiness Guidance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the Phase 7A readiness matrix into public documentation and skill guidance that production SDK users can follow.

**Architecture:** Keep `agentos.readiness` as the structured source of truth. Add documentation tests that compare the guide and skill docs against the Python matrix so future form or dimension additions cannot silently skip production guidance.

**Tech Stack:** Python 3.11, pytest, Markdown docs.

---

## Scope Contract

Implement only the slice described by `docs/superpowers/specs/2026-06-12-production-readiness-guidance-design.md`.

Target conclusion:

```text
The readiness matrix is only useful for production SDK users when every agent
spec and public guide can turn it into an explicit delivery checklist.
```

Deferred:

- generated docs pipeline
- live readiness probes
- new persistence or lease adapters
- runtime loop integration

## File Structure

Create:

- `docs/production-readiness.md`
- `tests/docs/test_production_readiness_docs.py`

Modify:

- `.claude/skills/agent-os/flow/01-requirements.md`
- `.claude/skills/agent-os/flow/02-spec-generation.md`
- `.claude/skills/agent-os/modules/agent-forms.md`
- `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`

Do not modify:

- `src/agentos/runtime/query_loop.py`
- `src/agentos/runtime/async_query_loop.py`

## Task 1: Public Guide Coverage

**Files:**
- Create: `tests/docs/test_production_readiness_docs.py`
- Create: `docs/production-readiness.md`

- [x] **Step 1: Write the failing docs coverage test**

```python
from pathlib import Path

from agentos.readiness import (
    REQUIRED_READINESS_DIMENSIONS,
    list_agent_form_readiness,
)


ROOT = Path(__file__).resolve().parents[2]


def test_production_readiness_doc_covers_matrix_forms_and_dimensions() -> None:
    text = (ROOT / "docs" / "production-readiness.md").read_text(
        encoding="utf-8",
    )

    assert "agentos.readiness" in text
    assert "get_agent_form_readiness" in text
    assert "production_readiness" in text
    for form in list_agent_form_readiness():
        assert form.form_id in text
        assert form.name in text
    for dimension in REQUIRED_READINESS_DIMENSIONS:
        assert dimension in text
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/docs/test_production_readiness_docs.py::test_production_readiness_doc_covers_matrix_forms_and_dimensions -q`

Expected: FAIL because `docs/production-readiness.md` does not exist.

- [x] **Step 3: Write the public guide**

Create `docs/production-readiness.md` with:

- a source-of-truth section naming `agentos.readiness`
- a production checklist section listing all required dimensions
- one section per initial form id
- explicit primitives-ready app-glue gaps

- [x] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/docs/test_production_readiness_docs.py::test_production_readiness_doc_covers_matrix_forms_and_dimensions -q`

Expected: PASS.

## Task 2: Skill Guidance Coverage

**Files:**
- Modify: `tests/docs/test_production_readiness_docs.py`
- Modify: `.claude/skills/agent-os/flow/01-requirements.md`
- Modify: `.claude/skills/agent-os/flow/02-spec-generation.md`
- Modify: `.claude/skills/agent-os/modules/agent-forms.md`

- [x] **Step 1: Write the failing skill guidance test**

```python
def test_agent_os_skill_uses_readiness_lookup_for_production_specs() -> None:
    requirements = (
        ROOT / ".claude" / "skills" / "agent-os" / "flow" / "01-requirements.md"
    ).read_text(encoding="utf-8")
    spec_generation = (
        ROOT / ".claude" / "skills" / "agent-os" / "flow" / "02-spec-generation.md"
    ).read_text(encoding="utf-8")
    agent_forms = (
        ROOT / ".claude" / "skills" / "agent-os" / "modules" / "agent-forms.md"
    ).read_text(encoding="utf-8")

    for text in (requirements, spec_generation):
        assert "get_agent_form_readiness" in text
        assert "required_app_glue" in text
    assert "docs/production-readiness.md" in agent_forms
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/docs/test_production_readiness_docs.py::test_agent_os_skill_uses_readiness_lookup_for_production_specs -q`

Expected: FAIL because the docs do not yet name the lookup consistently.

- [x] **Step 3: Update skill docs**

Add explicit instructions to:

- requirements flow: call `get_agent_form_readiness(form_id)` after selecting a
  production form
- spec generation flow: copy `form_id`, `overall_level`, dimension levels, and
  `required_app_glue` into `production_readiness`
- agent forms module: link to `docs/production-readiness.md`

- [x] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/docs/test_production_readiness_docs.py::test_agent_os_skill_uses_readiness_lookup_for_production_specs -q`

Expected: PASS.

## Task 3: Roadmap And Boundary Verification

**Files:**
- Modify: `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`

- [x] **Step 1: Update roadmap**

Add Phase 7B evidence that production readiness is now represented in public
docs and skill guidance.

- [x] **Step 2: Run focused verification**

Run:

```powershell
uv run pytest tests/docs/test_production_readiness_docs.py tests/test_readiness.py -q
```

Expected: PASS.

- [x] **Step 3: Run runtime boundary search**

Run:

```powershell
rg "agentos.readiness|ReadinessLevel|AgentFormReadiness" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
```

Expected: no matches.

## Task 4: Final Verification

- [x] Run `uv run python -m compileall -q src tests`.
- [x] Run `uv run pytest -q`.
- [x] Run `git diff --check`.
- [x] Update this plan's checkboxes.

## Self-Review

- Spec coverage: public guide, form ids, dimensions, skill guidance, roadmap,
  and runtime boundary are covered.
- Placeholder scan: no TBD/TODO placeholders.
- Boundary check: no runtime loop imports are allowed.
