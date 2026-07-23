# AgentOS Quickstart

AgentOS exposes one async Agent API across Local, Durable, and Distributed
runtime profiles. Choose the smallest profile that satisfies the deployment.

## Local Agent

```python
import asyncio

from agentos import AgentBuilder
from agentos.providers import FakeProvider


async def main() -> None:
    agent = AgentBuilder().provider(FakeProvider(["hello"])).build()

    result = await agent.run("Say hello")
    print(result.content)

    stream = await agent.run("Say hello again", stream=True)
    async for event in stream:
        print(event)


asyncio.run(main())
```

`Agent.run(..., stream=False)` and `Agent.run(..., stream=True)` use the same
`QueryLoop`; streaming is not a second execution implementation. Synchronous
applications may use the explicit `agentos.sync` boundary.

## Durable Agent

Install `agentos[durable]` and compose `DurableRuntimeProfile` with
`SQLiteDurableStore`. This level persists checkpoints and commands without
requiring PostgreSQL or Redis. Waiting turns exit the active loop; a later
command reloads the checkpoint and starts a new loop invocation.

```python
from agentos.durable import DurableRuntimeProfile, SQLiteDurableStore
```

Use Durable when one process owns execution but restart recovery matters. Do
not use it as a substitute for distributed claims, fencing, or shared queues.

## Distributed Runtime

Install `agentos[distributed]`. A distributed application supplies an
`AgentBuilder`, PostgreSQL DSN, Redis URL, shared `BlobStore`, worker identity,
relay identity, and a fail-closed side-effect resolution authorizer.

```python
from agentos.channels import ChannelServices, DistributedAsgiApp
from agentos.distributed import DistributedRuntimeProfile


async with DistributedRuntimeProfile(
    agent_builder=builder,
    postgres_dsn=postgres_dsn,
    redis_url=redis_url,
    blob_store=blob_store,
    worker_id=worker_id,
    relay_id=relay_id,
    side_effect_resolution_authorizer=authorizer,
) as profile:
    services = ChannelServices(
        run_submissions=profile.runs,
        run_commands=profile.commands,
        run_queries=profile.queries,
        run_events=profile.events,
        artifacts=profile.artifacts,
    )
    app = DistributedAsgiApp(services, authenticator=authenticator)
```

The host starts `profile.worker` and `profile.relay`, serves `app`, and drains
them during shutdown. The SDK does not create credentials, apply migrations,
select a tenant directory, or define autoscaling and rollout policy.

The distributed truth model is fixed:

- `PostgresStateStore`: Run, accepted input, claim/fence, checkpoint, outbox,
  and side-effect truth.
- `PostgresArtifactStore`: Artifact metadata and upload/deletion state.
- `RedisQueueAdapter`: execution and relay delivery only.
- `RedisEventReplayAdapter`: bounded live-event replay only.
- shared `BlobStore`: Artifact bytes visible to every worker.
- `DistributedWorker`: commit authoritative state before queue ACK.

## Context And Attachments

Stored messages contain Artifact handles, not raw file bytes. Each Provider
attempt rebuilds context from stored state. When a tool loads an attachment,
the Tool Result returns a lightweight reference and the runtime mounts the
corresponding image or file part into the next Provider request. The UI renders
persisted user-visible records, not internal context projection messages.

## Release Gates

- `docs/release-scope.md`
- `docs/production-readiness.md`
- `docs/api-stability.md`
- `docs/migrations/README.md`
- `docs/migrations/phase6-distributed-runtime-breaking-map.md`
- `docs/release-hardening.md`
- `CHANGELOG.md`

The Phase 6 alpha is a breaking cutover. Upgrade by replacing removed imports
with the canonical leaf modules; do not add compatibility wrappers.
