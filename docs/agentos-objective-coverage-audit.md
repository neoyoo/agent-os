# AgentOS Objective Coverage Audit

Audit target: the `0.3.0a1` Phase 6 distributed runtime cutover.

Goal status: complete. The final Phase 6 test gates and two independent reviews
passed with no open P0, P1, or P2 findings. This document records SDK coverage;
it does not certify a deployment.

## Objective Matrix

| Objective | Current coverage | Evidence | Remaining ownership |
|---|---|---|---|
| One execution kernel | complete | `Agent -> QueryLoop -> RunDriver`; one `Agent.run(..., stream=...)` API | none in SDK scope |
| Local agent | complete | `AgentBuilder`, `LocalRuntimeProfile`, base install | application tools and Provider credentials |
| Durable agent | complete | `DurableRuntimeProfile`, `SQLiteDurableStore`, command/checkpoint recovery | backup and filesystem policy |
| Distributed Run lifecycle | complete | `DistributedRuntimeProfile`, `PostgresStateStore`, `DistributedWorker` | PostgreSQL/Redis deployment and tuning |
| Distributed artifacts | complete | `PostgresArtifactStore`, shared `BlobStore`, scoped references | object-store deployment and retention |
| Delivery and replay | complete | `RedisQueueAdapter`, `RedisEventReplayAdapter`, outbox relay | Redis topology and retention |
| HTTP/SSE/WebSocket | complete | `ChannelServices`, `DistributedAsgiApp`, `agentos.transports.*` | gateway and tenant policy |
| A2A wire/channel support | complete for Phase 6 contract | `agentos.transports.a2a`, A2A services and endpoints | external certification and peer policy |
| Team coordination | complete for Phase 6 contract | `TeamRuntime`, `PostgresTeamStore`, `TeamDeliveryRunner`, Redis team replay | product orchestration policy |
| Planner pattern layer | direct/primitives-ready | `PlannerRuntime`, typed plans and dispatch boundaries | model prompt, approval, and scheduling policy |
| Context protocol | complete for Phase 6 | per-attempt context rebuild, StoredMessage/Artifact separation, typed projections | application projection selection |
| Side effects | complete for Phase 6 | `SideEffectPolicy`, ledger, resolution, cancel safe-stop | business compensation handlers |
| Live failure evidence | complete in SDK test scope | restart, timeout, claim race, fence, checkpoint, outbox, artifact and side-effect integration tests | deployment drills and alerting |
| Release governance | complete | public API inventory, module-size baseline, migration guide, release evidence generator/validator, final green gates and review sign-off | publishing and deployment approval |

## Architecture Invariants

- Local and Durable profiles do not import PostgreSQL or Redis clients.
- PostgreSQL is the sole distributed state truth.
- Redis is delivery, lease, and bounded replay infrastructure only.
- Artifact bytes are shared through `BlobStore` and never embedded in stored
  messages, traces, or release evidence.
- Transports do not own execution, persistence, workers, or retries.
- Accepted input, command submission, and side-effect operations use stable
  idempotency identities.
- Claims and writes are protected by version plus claim/fencing guards.
- The worker commits authoritative state before ACK.
- Waiting exits the active loop and resumes from a reloaded checkpoint.
- No synchronous distributed store wrapper, legacy Session Snapshot path,
  compatibility facade, or dynamic fallback remains.

## Breaking Removals

The cutover removes the previous `AsgiAgentApp` / web-session profile stack,
the production reference web example, legacy distributed Session Snapshot
persistence, synchronous PostgreSQL/Redis wrappers, and superseded channel and
multi-agent modules. Canonical replacements are documented in
`docs/migrations/phase6-distributed-runtime-breaking-map.md`.

## Deferred Scope

- OCR
- automatic attachment summaries
- attachment embeddings and vector retrieval
- Provider transcript recovery
- global exactly-once execution
- cross-region multi-primary state
- SDK-owned Kubernetes, autoscaling, secret distribution, and physical sandbox
  infrastructure

These deferrals are explicit and do not change the Phase 6 contract.

## Completion Gate

The SDK objective is complete only after architecture tests, the full unit
suite, live integration tests, Ruff, compileall, diff hygiene, public API and
module-size regeneration, and independent spec-compliance plus
code-quality/security reviews all pass with no open P0, P1, or P2 findings.
