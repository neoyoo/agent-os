# Production Readiness Guidance Design (Phase 7B)

> Date: 2026-06-12
> Branch: `review/agentos-sdk-architecture-20260611`
> Builds on: Phase 7A production readiness matrix

## Target Conclusion

```text
The readiness matrix is only useful for production SDK users when every agent
spec and public guide can turn it into an explicit delivery checklist.
```

Capability labels are not enough for agent-os. A developer choosing a terminal,
web, distributed web, A2A discovery, team discussion, or planner agent must see
which dimensions are directly covered by the SDK and which dimensions require
application glue before production use.

## Scope

Add a public production-readiness guide and skill checks that are traceable to
`agentos.readiness`:

- `docs/production-readiness.md`
- docs tests that require all readiness form ids and dimensions to appear
- skill guidance that names `get_agent_form_readiness()` during requirements
  and spec generation

This phase does not add new runtime behavior. It makes the current readiness
truth harder to misread.

## Required Documentation Semantics

The guide must:

- name `agentos.readiness` as the structured source of truth
- tell users to call `get_agent_form_readiness(form_id)` for a chosen form
- list every required readiness dimension
- list every current form id
- explain that `primitives-ready` requires explicit app glue before production
- preserve known gaps for distributed sessions, A2A parity, team lifecycle,
  planner scheduling, persistent stores, and schema migration

## Skill Guidance Semantics

The agent-os skill must:

- select a readiness form during requirements gathering when possible
- copy `form_id`, `overall_level`, dimensions, and `required_app_glue` into the
  generated spec for production-bound agents
- avoid describing A2A discovery or internal task bridge support as full A2A
  compliance

## Non-Goals

- No generated docs pipeline.
- No live readiness probe.
- No new distributed persistence adapter.
- No runtime loop imports of readiness data.

## Acceptance Criteria

- `docs/production-readiness.md` exists and mentions all forms from
  `list_agent_form_readiness()`.
- The guide mentions all `REQUIRED_READINESS_DIMENSIONS`.
- The guide names `get_agent_form_readiness()` and the stable
  `production_readiness` spec section.
- Requirements and spec-generation skill docs instruct workers to use
  `get_agent_form_readiness()` for production-bound specs.
- Runtime loops remain free of readiness imports.
