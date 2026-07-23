# AgentOS Production Readiness

This document defines the production-readiness boundary for the `0.3.0a1`
Phase 6 alpha. It is an SDK capability and evidence contract, not a production
certification.

## Runtime Selection

| Profile | State | Dependencies | Intended use |
|---|---|---|---|
| `LocalRuntimeProfile` | in-process | base install | scripts and request-scoped agents |
| `DurableRuntimeProfile` | SQLite checkpoint/command state | `agentos[durable]` | single-process restart recovery |
| `DistributedRuntimeProfile` | PostgreSQL truth, Redis delivery/replay, shared BlobStore | `agentos[distributed]` | multi-worker services |

Local and Durable imports must not load PostgreSQL or Redis clients. Distributed
is the only profile that requires both clients.

## Distributed Truth Model

PostgreSQL is the only authoritative distributed state source.

| Component | Owns | Must not own |
|---|---|---|
| `PostgresStateStore` | Run, accepted input, version, claim/fence, checkpoint, command, outbox, side-effect ledger | queue delivery, live replay, blob bytes |
| `PostgresArtifactStore` | Artifact metadata, upload/deletion state, references | raw bytes, Provider projection policy |
| `RedisQueueAdapter` | execution wakeup, relay delivery, pending reclaim, ACK | Run truth, terminal outcome, idempotency truth |
| `RedisEventReplayAdapter` | bounded typed live replay and tail | authoritative history, StoredMessage truth |
| `BlobStore` | shared Artifact bytes | Artifact metadata and access policy |
| `DistributedWorker` | claim, hydrate, execute, commit, ACK | migrations, ingress authorization, autoscaling |

The worker order is fixed:

```text
receive -> PostgreSQL claim/fence -> hydrate -> execute -> PostgreSQL commit
        -> publish/replay projection -> Redis ACK
```

If PostgreSQL cannot prove a WAITING or terminal commit, the worker must not
ACK the delivery or start another Provider/Tool action for that claim.

## Channel Boundary

`ChannelServices` exposes the application service layer to stateless channel
adapters. `DistributedAsgiApp` maps ASGI traffic to HTTP, SSE, WebSocket, and
A2A endpoints. Transports parse and serialize wire data only; they must not
import stores, workers, daemons, or the Agent execution kernel.

Ingress authentication is fail closed by default. Only the application may
construct a tenant-scoped `RequestScope`. Cross-tenant lookups return the same
not-found shape as absent resources.

## Context And Artifacts

Every Provider attempt rebuilds context from persisted messages and current
projections. A StoredMessage contains an Artifact handle, not raw image or file
bytes. When an attachment is needed, the runtime resolves the handle and mounts
the media part for that Provider request. Internal mount/projection messages are
not part of the UI conversation record.

The first release stores Artifact metadata for the owning Session/Run scope and
keeps bytes in the configured shared `BlobStore`. Automatic summaries,
embeddings, vector retrieval, and OCR are explicitly deferred.

## Side Effects And Cancellation

Every external tool declares a `SideEffectPolicy` and receives a stable
operation identity. The side-effect ledger distinguishes reserved, started,
ambiguous, compensating, and resolved work.

Cancel is a safe-stop protocol:

1. If a ledger row is `STARTED`, `AMBIGUOUS`, or `COMPENSATING`, cancel returns
   `409 side_effect_in_flight` without writing a command or changing Run state,
   input, cursor, claim, or fence.
2. An authorized caller resolves the row through `resolve_side_effect` using
   `accept_result`, `retry_proven_safe`, `compensate`, or `fail`.
3. If the Run remains non-terminal, retry cancel with the original
   `command_id`.
4. If resolution moved the Run to `FAILED`, do not submit another cancel.
5. A `RESERVED` invocation may atomically resolve as
   `cancelled_before_start`.

There is no legacy cancel fallback.

## Failure And Shutdown Guarantees

- Every PostgreSQL and Redis operation has a bounded I/O deadline.
- External cancellation propagates as `CancelledError`; it is not rewritten as
  backend unavailability.
- Heartbeat safety requires
  `heartbeat_interval + heartbeat_cycle_timeout < min(lease_ttl, claim_ttl)`.
- A lease or claim heartbeat failure closes the active stream and preserves
  no-ACK recovery semantics.
- Outbox relay claim, publish, mark, and tail release share one batch deadline.
- Worker drain rejects new work and waits only for its configured budget.
- Shutdown cleanup is bounded per resource and still attempts later resources
  after one close failure.

## Live Backend Verification Evidence Boundary

`DeploymentLiveBackendVerificationProfile` requires records for:

- `postgres_state_store`
- `postgres_artifact_store`
- `redis_worker_queue`
- `redis_relay_queue`
- `redis_event_replay`
- `distributed_worker`

Each passed record requires a backend kind, positive check timestamp,
non-placeholder evidence reference, and target reference. Duplicate, missing,
skipped, unknown, kind-mismatched, or secret-bearing evidence blocks readiness.

`ReferenceLiveBackendProbePack` produces argv-only invocation plans and
non-certifying report shapes. It does not create backend clients or claim a
real backend passed. Deployment-owned probes execute against real targets and
import their records into `ProductionReadinessEvidenceBundle`.

## Required Deployment Decisions

Before production, the application or platform team owns:

- PostgreSQL, Redis, and shared BlobStore credentials and topology;
- migration execution and rollback;
- tenant directory and authorization policy;
- worker count, autoscaling, rollout, and process supervision;
- claim, lease, heartbeat, replay, and retention tuning;
- gateway rate limiting, TLS, CORS, WAF, and egress policy;
- physical sandboxing for untrusted tools;
- monitoring, alerting, runbooks, backup, and restore drills.

## Release Evidence

The SDK release gate requires:

```powershell
uv run pytest tests/architecture -q
uv run pytest -q
uv run pytest -m integration -q
uv run ruff check src tests
uv run python -m compileall -q src tests
git diff --check
```

The machine-readable template is `docs/release-evidence.example.json`.
Generated candidate evidence is ignored by Git and must be bound to the exact
branch, commit, and version under review. CI/CD execution, signing, publishing,
and deployment approval remain deployment-owned.

## Explicit Exclusions

OCR, attachment embedding/vector retrieval, automatic attachment summaries,
Provider transcript recovery, global exactly-once execution, and cross-region
multi-primary state are not readiness claims for this release.
