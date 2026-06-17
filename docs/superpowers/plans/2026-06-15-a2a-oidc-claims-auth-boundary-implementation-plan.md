# A2A OIDC Claims Auth Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a narrow JWT/OIDC claims inbound auth policy for A2A operation calls.

**Architecture:** Extend `agentos.channels.a2a` with token claims/verifier primitives and a new `A2AInboundAuthPolicy` implementation. Reuse existing `A2AOperationServer` auth injection points. Keep runtime loops protocol-agnostic.

**Tech Stack:** Python dataclasses, stdlib `base64`/`hmac`/`hashlib`/`json`, existing A2A operation server tests, pytest.

---

Spec: `docs/superpowers/specs/2026-06-15-a2a-oidc-claims-auth-boundary-design.md`

## Tasks

### Task 1: RED Tests For Valid JWT Claims

**Files:**
- Modify: `tests/channels/test_a2a_operations.py`

- [ ] **Step 1: Write the failing test**

Add `test_a2a_operation_server_accepts_oidc_claims_inbound_auth`. The test
builds an HS256 JWT with `iss`, `aud`, `sub`, `azp`, `iat`, `nbf`, and `exp`,
configures `OidcClaimsA2AInboundAuthPolicy`, and asserts `message/send` reaches
the runner.

- [ ] **Step 2: Verify RED**

Run:

```powershell
uv run pytest tests\channels\test_a2a_operations.py::test_a2a_operation_server_accepts_oidc_claims_inbound_auth -q
```

Expected: FAIL because the policy/verifier types do not exist.

### Task 2: RED Tests For Rejections

**Files:**
- Modify: `tests/channels/test_a2a_operations.py`

- [ ] **Step 1: Write failing rejection tests**

Add tests for wrong issuer, wrong audience, expired token, not-yet-valid token,
and disallowed peer id. Each test asserts the A2A server returns `-32030` and
does not execute the runner.

- [ ] **Step 2: Verify RED**

Run:

```powershell
uv run pytest tests\channels\test_a2a_operations.py -k oidc_claims -q
```

Expected: FAIL because the policy/verifier types do not exist.

### Task 3: Implement JWT Claims And Verifier

**Files:**
- Modify: `src/agentos/channels/a2a.py`

- [ ] **Step 1: Add `A2AJwtClaims` and `A2AJwtVerifier`**

Expose issuer, subject, audience tuple, authorized party, issued-at,
not-before, expires-at, peer id, and raw claims.

- [ ] **Step 2: Add `HmacA2AJwtVerifier`**

Implement compact JWT parsing for HS256 only:

- require three token segments;
- base64url-decode header and payload;
- require `alg == "HS256"`;
- compare HMAC-SHA256 signature with `hmac.compare_digest`;
- parse `aud` from string or list;
- raise `A2AInboundAuthError` on malformed or invalid tokens.

- [ ] **Step 3: Keep errors generic**

Use generic authorization errors for callers. Do not include token text or
secret values in exception messages or reprs.

### Task 4: Implement OIDC Claims Inbound Policy

**Files:**
- Modify: `src/agentos/channels/a2a.py`

- [ ] **Step 1: Add `OidcClaimsA2AInboundAuthPolicy`**

Extract bearer token from headers, call the verifier, enforce:

- expected issuer exact match;
- expected audience inclusion;
- `exp` not expired with leeway;
- `nbf` not in the future with leeway;
- optional allowed peer id list using `azp` then `sub`.

- [ ] **Step 2: Verify GREEN**

Run:

```powershell
uv run pytest tests\channels\test_a2a_operations.py -k oidc_claims -q
```

Expected: PASS.

### Task 5: Public API And Guidance

**Files:**
- Modify: `src/agentos/channels/__init__.py`
- Modify: `src/agentos/__init__.py`
- Modify: `tests/architecture/test_public_api.py`
- Modify: `src/agentos/readiness.py`
- Modify: `tests/test_readiness.py`
- Modify: `docs/production-readiness.md`
- Modify: `.claude/skills/agent-os/modules/agent-forms.md`
- Modify: `.claude/skills/agent-os/modules/multi-agent.md`
- Modify: `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`

- [ ] **Step 1: Export new public types**

Export `A2AJwtClaims`, `A2AJwtVerifier`, `HmacA2AJwtVerifier`, and
`OidcClaimsA2AInboundAuthPolicy`.

- [ ] **Step 2: Update readiness and docs**

List JWT/OIDC claims validation as available; keep OIDC discovery, RS256/JWKS
public-key rollout, CA trust, DNS/egress, credential rotation, and tenant RBAC
as remaining gaps.

### Task 6: Final Verification

**Files:**
- No new files.

- [ ] **Step 1: Run targeted tests**

```powershell
uv run pytest tests\channels\test_a2a_operations.py tests\architecture\test_public_api.py tests\test_readiness.py tests\docs\test_production_readiness_docs.py -q
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
