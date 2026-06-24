# Quickstart

This quickstart shows the release-scope entry points for the first production
SDK release. The release supports terminal agent, single-node web agent,
distributed web agent, team/planner/A2A primitive composition, production state
plane, readiness evidence, and trusted internal service orchestration.

Sandbox / Docker / E2B / microVM / enterprise runner adapter work is a
non-blocking future adapter. Choose one sandbox posture for production specs:
`trusted tools only`, `deployment-owned isolation`, or `future adapter`.

## Terminal Agent

```python
from agentos import AgentBuilder
from agentos.providers import FakeProvider

agent = AgentBuilder().provider(FakeProvider(["hello"])).build()

print(agent.run("say hello").content)
```

## Single-Node Web Agent

```python
from agentos.channels import (
    AllowAllChannelAuthPolicy,
    AsgiAgentApp,
    InMemoryAgentSessionProvider,
)
from agentos.providers import FakeProvider
from agentos import AgentBuilder

def make_agent(session_id: str):
    return AgentBuilder().provider(FakeProvider([f"hello {session_id}"])).build()

app = AsgiAgentApp(
    sessions=InMemoryAgentSessionProvider(make_agent),
    auth_policy=AllowAllChannelAuthPolicy(),
)
```

`AllowAllChannelAuthPolicy` is for local/dev examples only. `AsgiAgentApp`
uses `RejectAllChannelAuthPolicy` for production-facing defaults, so
production web agents must inject an app-owned auth or tenant policy.

## Distributed Web Agent

For a distributed web agent, combine `DistributedWebRuntimeProfile`,
`DurableAgentSessionProvider`, `RedisSessionLeaseStore`,
`PostgresSessionSnapshotPersistence`, and readiness evidence. Deployment owns
Redis/Postgres credentials, migration execution, auth/tenant integration,
rollout, alerting, and live backend verification.

## Production State Plane

The reference state plane combines:

- `NacosAgentRegistryAdapter` for AgentCard discovery metadata
- `RedisAgentMessageQueue` for worker delivery and wakeup
- `PostgresTaskStore` and `PostgresPlanStore` for task/plan truth
- `WorkerProcessSupervisor` or `LocalSubprocessWorkerSupervisor` for worker
  lifecycle evidence
- `SessionSnapshotPersistence` or `PostgresSessionSnapshotPersistence` for
  runtime snapshots
- `AgentServiceReference` and `ProductionReadinessEvidenceBundle` for service
  composition and release evidence

## Release Gate Links

- `docs/release-scope.md`
- `docs/production-readiness.md`
- `docs/release-hardening.md`
- `docs/api-stability.md`
- `docs/migrations/README.md`
- `CHANGELOG.md`

Use `docs/release-hardening.md` for public API audit, stable API,
experimental API, migration index, README / quickstart / examples alignment,
full test suite evidence, diff/commit hygiene, and the SDK-owned release
evidence checklist.

## Production Reference Web Agent

Phase 101: Production Reference Example provides the production reference web
agent at `src/agentos/examples/production_reference_web_agent.py`. It composes
`AgentServiceReference`, `DistributedWebRuntimeProfile`, a
Nacos/Redis/Postgres state plane, a readiness endpoint, backend verification,
`ProductionReadinessEvidenceBundle`, `ReferenceStatePlaneStack`,
`ReferenceLiveBackendProbePack`, and a planner primitive.

```bash
python -m agentos.examples.production_reference_web_agent
```

The example is copyable SDK guidance. It does not create backend clients;
Nacos/Redis/Postgres deployment, credentials, migrations, CI/CD, process
supervision, and sandbox isolation are deployment-owned real infrastructure.
Production readiness requires deployment-owned runtime backends plus
`state_plane_backend_targets`, `reference_served_backend_binding`, and exact
state-plane target_ref bindings for every state-plane backend.
