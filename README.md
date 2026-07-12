# agent-os

Production-grade agent runtime SDK and development guidance skill.

## What This Is

AgentOS is a company-level SDK for building agents. It provides stable
protocols, runtime profiles, context/message management, tool routing,
channels, multi-agent primitives, workspace boundaries, readiness evidence, and
reference compositions. You configure and extend it; you do not subclass a
framework.

AgentOS is not an AgentScope-style all-in-one platform. It does not create real
Nacos, Redis, Postgres, Kubernetes, systemd, tenant directory, CI/CD,
credential, autoscaling, or sandbox isolation infrastructure.

## First Production SDK Release

The first production SDK release supports:

- terminal agent and trusted internal tools
- single-node web agent with ASGI/SSE
- distributed web agent with durable session hydration primitives
- team/planner/A2A primitive composition
- production state plane boundaries
- readiness evidence and audit evidence
- internal service orchestration

Sandbox / Docker / E2B / microVM / enterprise runner adapter work is a
non-blocking future adapter. This release does not promise physical isolation
for untrusted code execution. Production specs must choose one sandbox posture:
`trusted tools only`, `deployment-owned isolation`, or `future adapter`.

## Quickstart

```python
from agentos import AgentBuilder
from agentos.providers import FakeProvider

agent = AgentBuilder().provider(FakeProvider(["hello"])).build()

result = agent.run("Say hello")
print(result.content)
```

See `docs/quickstart.md` for terminal, web, distributed web, state plane,
readiness, and release gate entry points.

Phase 101: Production Reference Example adds a production reference web agent
at `src/agentos/examples/production_reference_web_agent.py`. The example shows
`AgentServiceReference`, `DistributedWebRuntimeProfile`, a
Nacos/Redis/Postgres state plane, a readiness endpoint, backend verification,
`ProductionReadinessEvidenceBundle`, `ReferenceStatePlaneStack`,
`ReferenceLiveBackendProbePack`, and a planner primitive in one copyable
composition. It does not create backend clients; Nacos/Redis/Postgres,
credentials, migrations, CI/CD, process supervision, and sandbox isolation are
deployment-owned real infrastructure.

## Install

```bash
pip install -e .
pip install -e ".[redis]"
pip install -e ".[postgres]"
```

## Architecture

```text
AgentBuilder
  -> Provider
  -> ToolCallRouter
  -> ContextRuntime
  -> MessageRuntime
  -> CompressionRuntime
  -> HookManager
  -> EventBus

Channels -> AsgiAgentApp, HTTP/SSE, A2A primitives
Persistence -> SessionSnapshot, SQLite/Postgres, Redis leases
Multi-agent -> AgentCoordinator, TeamRuntime, PlannerRuntime
State plane -> registry, queue, task/plan stores, worker evidence, snapshots
Readiness -> ProductionReadinessEvidenceBundle and backend verification records
```

`QueryLoop` and `AsyncQueryLoop` remain turn execution loops. Planner, A2A,
team, worker, state-plane, readiness, sandbox, and release-hardening concerns
stay in their own modules and profiles.

## Release References

- `docs/release-scope.md`: first production SDK release boundary
- `docs/production-readiness.md`: readiness matrix and production ownership
- `docs/agentos-objective-coverage-audit.md`: objective coverage ledger
- `docs/release-hardening.md`: Phase 100 release hardening gate
- `docs/api-stability.md`: stable API and experimental API classification
- `docs/migrations/README.md`: migration index
- `CHANGELOG.md`: current release-line changes
- `.claude/skills/agent-os`: SDK development guidance skill

## Examples

Runnable examples live under `src/agentos/examples/`, including streaming,
multi-agent dispatch, MCP, persistent sessions, planner patterns, and the live
backend probe example. The production reference web agent lives at
`src/agentos/examples/production_reference_web_agent.py`.

## Tests

```bash
uv run pytest -q
```

For release hardening evidence, also run the commands listed in
`docs/release-hardening.md`.

## License

Private.
