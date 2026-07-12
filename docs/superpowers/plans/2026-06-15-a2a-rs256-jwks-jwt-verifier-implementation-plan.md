# A2A RS256 JWKS JWT Verifier Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an optional-security RS256/JWKS JWT verifier for A2A inbound OIDC auth.

**Architecture:** Extend `agentos.channels.a2a` with a JWKS-backed `A2AJwtVerifier` that reuses existing JWT claim projection and `OidcClaimsA2AInboundAuthPolicy`. Keep `cryptography` optional via a `security` extra and keep runtime loops protocol-agnostic.

**Tech Stack:** Python dataclasses, stdlib `json`/`base64`/`time`, existing `A2ATransport`, optional `cryptography`, pytest.

---

Spec: `docs/superpowers/specs/2026-06-15-a2a-rs256-jwks-jwt-verifier-design.md`

## Files

- Create: `tests/channels/test_a2a_jwks_jwt_verifier.py`
- Modify: `src/agentos/channels/a2a.py`
- Modify: `src/agentos/channels/__init__.py`
- Modify: `src/agentos/__init__.py`
- Modify: `tests/architecture/test_public_api.py`
- Modify: `pyproject.toml`
- Modify: `src/agentos/readiness.py`
- Modify: `tests/test_readiness.py`
- Modify: `docs/production-readiness.md`
- Modify: `tests/docs/test_production_readiness_docs.py`
- Modify: `.claude/skills/agent-os/modules/agent-forms.md`
- Modify: `.claude/skills/agent-os/modules/multi-agent.md`
- Modify: `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`

## Tasks

### Task 1: RED Tests For RS256 JWT Verification

**Files:**
- Create: `tests/channels/test_a2a_jwks_jwt_verifier.py`

- [ ] **Step 1: Write failing happy-path test**

Generate an RSA key pair with `cryptography`, publish the public key as JWK
`{"kty": "RSA", "kid": "rsa-1", "use": "sig", "alg": "RS256", "n": ..., "e": ...}`,
sign a compact JWT with `RS256`, and assert `JwksA2AJwtVerifier.verify(token)`
returns claims with issuer, subject, audience, and raw claims preserved.

- [ ] **Step 2: Verify RED**

Run:

```powershell
uv run pytest tests\channels\test_a2a_jwks_jwt_verifier.py::test_jwks_a2a_jwt_verifier_accepts_rs256_token_from_jwks -q
```

Expected: FAIL because `JwksA2AJwtVerifier` does not exist.

### Task 2: RED Tests For Fail-Closed Cases

**Files:**
- Modify: `tests/channels/test_a2a_jwks_jwt_verifier.py`

- [ ] **Step 1: Add rejection tests**

Add tests for wrong signature, unknown `kid`, unsupported `alg`, ignored
non-RSA keys, disallowed key ids, JWKS cache reuse, and TTL refresh.

- [ ] **Step 2: Verify RED**

Run:

```powershell
uv run pytest tests\channels\test_a2a_jwks_jwt_verifier.py -q
```

Expected: FAIL because the verifier does not exist.

### Task 3: Implement JWKS RS256 Verifier

**Files:**
- Modify: `src/agentos/channels/a2a.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Add optional dependency extra**

Add:

```toml
security = [
    "cryptography>=42.0",
]
```

- [ ] **Step 2: Add `JwksA2AJwtVerifier`**

Implement constructor arguments:

- `jwks_urls: Sequence[str] = ()`
- `discovery_provider: OidcDiscoveryMetadataProvider | None = None`
- `allowed_key_ids: Sequence[str] = ()`
- `transport: object | None = None`
- `timeout_seconds: float = 5`
- `cache_ttl_seconds: float = 300`
- `clock: object | None = None`

Validation:

- require at least one JWKS URL or discovery provider;
- require direct JWKS URLs to be HTTPS;
- require positive timeout and non-negative TTL.

- [ ] **Step 3: Implement verification**

Parse the compact JWT, require header `alg == "RS256"` and non-empty string
`kid`, refresh cached keys if needed, find the public key, verify
`header.payload` with RSA PKCS1v15/SHA256, and return `_a2a_jwt_claims_from_payload(...)`.

- [ ] **Step 4: Verify GREEN**

Run:

```powershell
uv run pytest tests\channels\test_a2a_jwks_jwt_verifier.py -q
```

Expected: PASS.

### Task 4: Public API And Guidance

**Files:**
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

- [ ] **Step 1: Export public type**

Export `JwksA2AJwtVerifier` from `agentos.channels` and top-level `agentos`,
then update public API tests.

- [ ] **Step 2: Update readiness/docs**

Move RS256/JWKS JWT verification into A2A auth evidence. Keep CA trust rollout,
DNS/egress controls, credential rotation, tenant RBAC, and external
conformance as gaps.

### Task 5: Final Verification

**Files:**
- No new files.

- [ ] **Step 1: Run targeted tests**

```powershell
uv run pytest tests\channels\test_a2a_jwks_jwt_verifier.py tests\channels\test_a2a_oidc_discovery.py tests\channels\test_a2a_operations.py tests\architecture\test_public_api.py tests\test_readiness.py tests\docs\test_production_readiness_docs.py -q
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
