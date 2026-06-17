# A2A OIDC Discovery Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a narrow OIDC discovery metadata provider for A2A JWT auth rollout.

**Architecture:** Extend `agentos.channels.a2a` with metadata-only discovery primitives that reuse `A2ATransport.get_json(...)`. Keep JWT verification, runtime loops, planner, and team orchestration unchanged.

**Tech Stack:** Python dataclasses, stdlib `time`, stdlib URL validation, existing A2A transport protocol, pytest.

---

Spec: `docs/superpowers/specs/2026-06-15-a2a-oidc-discovery-boundary-design.md`

## Files

- Create: `tests/channels/test_a2a_oidc_discovery.py`
- Modify: `src/agentos/channels/a2a.py`
- Modify: `src/agentos/channels/__init__.py`
- Modify: `src/agentos/__init__.py`
- Modify: `tests/architecture/test_public_api.py`
- Modify: `src/agentos/readiness.py`
- Modify: `tests/test_readiness.py`
- Modify: `docs/production-readiness.md`
- Modify: `tests/docs/test_production_readiness_docs.py`
- Modify: `.claude/skills/agent-os/modules/agent-forms.md`
- Modify: `.claude/skills/agent-os/modules/multi-agent.md`
- Modify: `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`

## Tasks

### Task 1: RED Tests For OIDC Discovery Metadata

**Files:**
- Create: `tests/channels/test_a2a_oidc_discovery.py`

- [ ] **Step 1: Write failing provider tests**

Add tests that import `OidcDiscoveryMetadataProvider` and
`A2AOidcDiscoveryError`, use a fake transport, and assert:

- `metadata()` fetches
  `https://issuer.example/tenant/.well-known/openid-configuration`;
- repeated calls before TTL reuse cached metadata;
- `jwks_uri()` returns the validated HTTPS JWKS URI;
- metadata refreshes after TTL expiry;
- mismatched issuer fails closed;
- HTTP issuer and HTTP `jwks_uri` are rejected.

- [ ] **Step 2: Verify RED**

Run:

```powershell
uv run pytest tests\channels\test_a2a_oidc_discovery.py -q
```

Expected: FAIL because the discovery provider types do not exist.

### Task 2: Implement Discovery Provider

**Files:**
- Modify: `src/agentos/channels/a2a.py`

- [ ] **Step 1: Add discovery data types**

Add:

- `A2AOidcDiscoveryError(ValueError)`;
- `OidcDiscoveryMetadata(issuer: str, jwks_uri: str, raw: Mapping[str, object])`.

- [ ] **Step 2: Add `OidcDiscoveryMetadataProvider`**

Implement constructor validation:

- issuer must be a non-empty HTTPS URL;
- timeout must be `> 0`;
- cache TTL must be `>= 0`;
- default transport is `UrllibA2ATransport`;
- default clock is `time.time`.

Implement `metadata()` and `jwks_uri()`:

- fetch through `transport.get_json(discovery_url, timeout_seconds)`;
- require JSON object payload;
- require exact issuer match against the normalized configured issuer;
- require HTTPS `jwks_uri`;
- cache until TTL expires.

- [ ] **Step 3: Verify GREEN**

Run:

```powershell
uv run pytest tests\channels\test_a2a_oidc_discovery.py -q
```

Expected: PASS.

### Task 3: Public API Exports

**Files:**
- Modify: `src/agentos/channels/__init__.py`
- Modify: `src/agentos/__init__.py`
- Modify: `tests/architecture/test_public_api.py`

- [ ] **Step 1: Add public API expectations**

Add `A2AOidcDiscoveryError`, `OidcDiscoveryMetadata`, and
`OidcDiscoveryMetadataProvider` to channel and top-level public API tests.

- [ ] **Step 2: Export the symbols**

Export the same names from `agentos.channels` and top-level `agentos`.

- [ ] **Step 3: Verify public API**

Run:

```powershell
uv run pytest tests\architecture\test_public_api.py -q
```

Expected: PASS.

### Task 4: Readiness And Guidance

**Files:**
- Modify: `src/agentos/readiness.py`
- Modify: `tests/test_readiness.py`
- Modify: `docs/production-readiness.md`
- Modify: `tests/docs/test_production_readiness_docs.py`
- Modify: `.claude/skills/agent-os/modules/agent-forms.md`
- Modify: `.claude/skills/agent-os/modules/multi-agent.md`
- Modify: `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`

- [ ] **Step 1: Update readiness matrix**

Move OIDC discovery metadata from A2A auth gaps into evidence. Keep
RS256/JWKS JWT verification, CA trust, DNS/egress controls, credential
rotation, tenant RBAC, and external conformance as gaps.

- [ ] **Step 2: Update docs and SDK skill guidance**

State that OIDC discovery metadata fetch/validation/cache exists and that it
feeds future JWKS JWT verifier phases. Do not claim RS256/JWKS JWT verification.

- [ ] **Step 3: Verify docs**

Run:

```powershell
uv run pytest tests\test_readiness.py tests\docs\test_production_readiness_docs.py -q
```

Expected: PASS.

### Task 5: Final Verification

**Files:**
- No new files.

- [ ] **Step 1: Run targeted tests**

```powershell
uv run pytest tests\channels\test_a2a_oidc_discovery.py tests\channels\test_a2a_operations.py tests\architecture\test_public_api.py tests\test_readiness.py tests\docs\test_production_readiness_docs.py -q
```

- [ ] **Step 2: Run full tests**

```powershell
uv run pytest -q
```

- [ ] **Step 3: Compile and boundary scan**

```powershell
uv run python -m compileall -q src tests
rg "PlanRetry|PlannerRuntime|PlannerTools|A2A|PushNotification|Trust|Signature|InboundAuth" src\agentos\runtime\query_loop.py src\agentos\runtime\async_query_loop.py
git diff --check
```

Expected: pytest and compileall pass; `rg` exits 1 with no matches; diff check
exits 0 aside from existing CRLF warnings.
