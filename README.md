# agent-os

AgentOS is an async-first SDK for building local, durable, and distributed
agents from one execution kernel. Applications compose providers, tools,
context projections, persistence, transports, and deployment policy without
subclassing a framework.

The current development line is `0.3.0a1`. Phase 6 is a breaking distributed
runtime cutover; it does not preserve the previous web-session, synchronous
store, or production-reference facades.

## Runtime Levels

- Local: `AgentBuilder`, one async `QueryLoop`, in-process state, optional
  skills, planning, memory projections, and artifacts.
- Durable: the same loop with `DurableRuntimeProfile` and SQLite-backed
  checkpoints. PostgreSQL and Redis are not imported or required.
- Distributed: `DistributedRuntimeProfile` with PostgreSQL as the only state
  truth, Redis for delivery and bounded replay, and a shared `BlobStore` for
  artifact bytes.

OCR, vectorized attachment summarization, global exactly-once execution,
cross-region multi-primary state, and physical sandbox infrastructure are not
part of this release.

## Quickstart

```python
import asyncio

from agentos import AgentBuilder
from agentos.providers import FakeProvider


async def main() -> None:
    agent = AgentBuilder().provider(FakeProvider(["hello"])).build()
    result = await agent.run("Say hello")
    print(result.content)


asyncio.run(main())
```

See `docs/quickstart.md` for streaming, durable, and distributed composition.

## Install

```bash
pip install -e .
pip install -e ".[durable]"
pip install -e ".[distributed]"
pip install -e ".[distributed,distributed-artifacts]"
```

The base and Durable installations do not load PostgreSQL or Redis clients.
The `distributed` extra installs both clients; artifact storage integrations
remain separately optional.

## Architecture

```text
AgentBuilder -> Agent -> QueryLoop -> RunDriver
                    |-> context / messages / compression
                    |-> tools / skills / planning / memory

DistributedRuntimeProfile
  -> PostgreSQL: Run, accepted input, claim/fence, checkpoint, outbox,
                 side-effect ledger, Artifact metadata
  -> Redis: execution delivery, relay delivery, live-event replay
  -> BlobStore: shared Artifact bytes
  -> DistributedWorker: claim -> hydrate -> execute -> commit -> ACK

ChannelServices -> DistributedAsgiApp -> HTTP / SSE / WebSocket / A2A
```

Every Provider attempt rebuilds its context projection. Stored messages keep
lightweight Artifact references; raw image or file content is mounted only for
the Provider request that needs it.

## Release References

- `docs/release-scope.md`: `0.3.0a1` release boundary
- `docs/production-readiness.md`: production ownership and readiness gates
- `docs/agentos-objective-coverage-audit.md`: objective coverage ledger
- `docs/api-stability.md`: stable and experimental API classification
- `docs/migrations/README.md`: API and schema migration index
- `docs/migrations/phase6-distributed-runtime-breaking-map.md`: Phase 6 cutover
- `docs/release-hardening.md`: release evidence commands
- `CHANGELOG.md`: current release-line changes

## Tests

```bash
uv run pytest tests/architecture -q
uv run pytest -q
uv run pytest -m integration -q
uv run ruff check src tests
uv run python -m compileall -q src tests
git diff --check
```

Integration tests require the deployment dependencies described by
`docker-compose.test.yml`.

## License

Private.
