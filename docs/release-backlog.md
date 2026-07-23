# AgentOS Phase 6 Residual Backlog

This file records work that is explicitly outside the `0.3.0a1` SDK contract.
It does not waive failures in the Phase 6 implementation or its release gates.

## Deployment-Owned Production Controls

- distributed/global quota storage and gateway enforcement;
- public A2A peer admission, external certification, credential issuance, and
  egress infrastructure;
- worker process supervision, autoscaling, rollout, and alert routing;
- tenant directory integration and physical sandbox isolation;
- backup, restore, migration rollout, and disaster-recovery drills.

These items are non-blocking for the SDK alpha only because the SDK exposes
fail-closed policy and evidence boundaries without claiming to operate the
deployment infrastructure.

## Planner Product Policy

`PlannerRuntime` remains a pattern layer over typed plan state and dispatch
ports. Model prompts, approval workflows, tenant scheduling, global fairness,
leader election, and business compensation policy remain application-owned.
They must not be moved into `QueryLoop` or treated as implicit defaults.

## Deferred Runtime Semantics

- global exactly-once execution;
- Provider transcript recovery;
- cross-region multi-primary state;
- automatic attachment summaries, embeddings, and vector retrieval.

The implemented contract uses idempotent submissions and commands,
claim/fencing guards, a PostgreSQL side-effect ledger, and at-least-once Redis
delivery. Documentation and release evidence must not broaden that claim.

## Future Adapter Work

Additional BlobStore, sandbox, secret-manager, metrics, and deployment adapters
may be added after the canonical ports are stable. They are not reasons to add
compatibility facades or fallback behavior to the Phase 6 core.
