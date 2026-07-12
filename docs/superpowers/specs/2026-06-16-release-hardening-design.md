# Release Hardening Design

## Target Conclusion

Phase 100: Release Hardening should stop adding runtime capability and instead
make the review branch reviewable, publishable, and maintainable. The phase
creates SDK-owned release evidence while preserving deployment ownership for
CI/CD, signing, publishing, deployment approval, real infrastructure, and
physical isolation.

## Scope

In scope:

- release hardening gate
- release candidate evidence
- public API audit
- stable API and experimental API classification
- migration index
- README / quickstart / examples alignment
- `CHANGELOG.md`
- full test suite evidence
- diff/commit hygiene
- runtime boundary scan for `QueryLoop` and `AsyncQueryLoop`

Out of scope:

- runtime feature expansion
- production CI/CD implementation
- artifact signing or publishing
- deployment approval workflows
- real backend migration execution
- sandbox image patching or physical isolation

## Design

The SDK provides documentation and tests that allow a release reviewer to verify
that Phase 96 through Phase 100 are coherent. Release evidence is stored in
normal repository docs and validated by docs tests. Public API shape is guarded
by `tests/architecture/test_public_api.py`.

The release hardening gate links:

- `docs/release-hardening.md`
- `docs/api-stability.md`
- `docs/migrations/README.md`
- `CHANGELOG.md`
- `README.md`
- `docs/quickstart.md`
- `docs/production-readiness.md`
- `docs/agentos-objective-coverage-audit.md`
- `.claude/skills/agent-os`

## Non-Goals

No Phase 100 edit should move planner, A2A, team, worker, state-plane,
readiness, sandbox, or release-hardening concepts into `QueryLoop` or
`AsyncQueryLoop`.

