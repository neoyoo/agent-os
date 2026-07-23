# AgentOS `0.3.0a1` Release Scope

This document is the canonical release boundary for the Phase 6 distributed
runtime cutover. Read it with `docs/production-readiness.md` and
`docs/migrations/phase6-distributed-runtime-breaking-map.md`.

## In Scope

- One native async `Agent -> QueryLoop -> RunDriver` execution path.
- Local agents through `AgentBuilder` and `LocalRuntimeProfile`.
- Durable single-process recovery through `DurableRuntimeProfile` and SQLite.
- Skills, planning, memory projection, context compression, and Artifact
  references without a second execution kernel.
- Distributed execution through `DistributedRuntimeProfile` and
  `DistributedWorker`.
- PostgreSQL truth for Run, accepted input, claim/fence, checkpoint, outbox,
  side effects, and Artifact metadata.
- Redis execution delivery, relay delivery, leases, and bounded event replay.
- Shared `BlobStore` bytes with PostgreSQL Artifact metadata.
- Stateless HTTP, SSE, WebSocket, and A2A composition through
  `ChannelServices` and `DistributedAsgiApp`.
- Tenant-scoped authorization, idempotent submission/commands, side-effect
  resolution, worker drain, recovery, and live backend evidence boundaries.

## Breaking Cutover

This alpha does not retain compatibility wrappers, aliases, dual stores, or
dynamic fallbacks for the removed Phase 5 web/distributed architecture. In
particular, it removes the legacy Session Snapshot distributed path,
synchronous PostgreSQL/Redis store wrappers, and the old production reference
web composition.

Canonical replacements live in:

- `agentos.distributed`
- `agentos.distributed.postgres`
- `agentos.distributed.redis.*`
- `agentos.distributed.worker.*`
- `agentos.channels`
- `agentos.transports.http`, `.sse`, `.websocket`, and `.a2a`

## Out Of Scope

- OCR and automatic attachment summarization.
- Embedding/vector retrieval for attachments.
- Provider transcript recovery.
- Global exactly-once execution.
- Cross-region multi-primary state.
- Kubernetes, systemd, autoscaling, tenant directory, secret distribution,
  CI/CD, signing, publishing, rollout, alerting, and runbooks.
- Physical sandbox infrastructure for untrusted code.

Applications must explicitly choose trusted tools or deployment-owned process,
container, microVM, or enterprise-runner isolation.

## Release Gate

The alpha is ready for review only when:

- architecture and public API inventory tests pass;
- the full unit suite and live integration suite pass;
- Ruff, compileall, and diff hygiene pass;
- Local and Durable imports do not load PostgreSQL or Redis clients;
- Transport modules do not own execution or persistence state;
- PostgreSQL remains the sole distributed truth source;
- the Phase 6 migration guide includes cancel safe-stop behavior;
- independent spec-compliance and code-quality/security reviews have no open
  P0, P1, or P2 findings.
