# Nacos Registry Adapter Boundary Design

## Target Conclusion

AgentOS needs a production registry/discovery adapter boundary for AgentCard,
A2A endpoint, capabilities, version, worker service metadata, and health
metadata. Nacos may be used for registration and discovery metadata, but it
must not become task truth, plan truth, session snapshot storage, message
queue, or worker runtime state.

This keeps the production state plane split intact:

- Nacos owns discovery metadata only.
- Redis-style queues own worker messages, inbox, wakeup, and delivery.
- Postgres-style task and plan stores own task/plan truth.
- Worker supervisors own local process lifecycle evidence.
- Session snapshot persistence owns context/messages/compression/working-state
  runtime snapshots.

## Current State

The SDK already has:

- internal `AgentCard`
- `PersistentAgentRegistry`
- `PostgresAgentRegistryStore`
- `StaticResolver` and `ServiceResolver`
- A2A Agent Card serialization and `A2ACardResolver`
- `ProductionStatePlaneDeploymentProfile`, which names
  `NacosAgentRegistryAdapter` as the recommended discovery adapter boundary

The missing layer is a concrete SDK-owned Nacos adapter protocol that can map
AgentOS cards into Nacos service instance metadata and read them back through a
resolver without importing a concrete Nacos client package or doing network I/O
in unit tests.

## Proposed Boundary

Add the following primitives in `agentos.registry`:

- `NacosRegistryClient`: a protocol for the small subset of Nacos operations
  the SDK needs: register instance, deregister instance, and list instances.
- `NacosRegistryConfig`: namespace, group, cluster, service name prefix, and
  ephemeral registration settings.
- `NacosAgentRegistryAdapter`: maps `AgentCard` into a Nacos service instance
  with JSON-safe metadata and delegates the operation to an injected client.
- `NacosAgentCardResolver`: resolves and discovers `AgentCard` values from
  healthy Nacos instances.
- `NacosRegistryEvidence`: JSON-safe evidence describing registration,
  deregistration, and discovery results without credentials or raw client
  internals.
- `NacosRegistryError`: validation and metadata parsing error boundary.

## Metadata Shape

The adapter stores discovery-only metadata:

- `agentos.agent_id`
- `agentos.name`
- `agentos.description`
- `agentos.capabilities`
- `agentos.version`
- `agentos.endpoint`
- `agentos.status`
- `agentos.lifecycle`
- `agentos.max_concurrent_tasks`
- `agentos.a2a_card_url`
- `agentos.worker_service`
- `agentos.health`
- `agentos.metadata`

The adapter does not store task records, plan records, session snapshots,
message payloads, lease state, worker process state, secrets, credentials, or
environment values.

## SDK-Owned

- Stable client protocol
- AgentCard-to-Nacos metadata projection
- Nacos instance-to-AgentCard projection
- capability-based discovery filtering
- healthy-instance filtering
- JSON-safe evidence
- public API exports
- production readiness and skill guidance

## Deployment-Owned

- Nacos server deployment
- Nacos credentials and namespace policy
- concrete Nacos Python client construction
- TLS, auth, tenant directory integration, and secret distribution
- service naming conventions beyond SDK defaults
- live backend verification
- alerting, runbooks, and operational dashboards

## Non-Goals

- No concrete Nacos dependency in SDK core.
- No live network calls in unit tests.
- No task, plan, session, queue, or worker runtime state in Nacos metadata.
- No session affinity store in Nacos for this phase.
- No changes to `QueryLoop` or `AsyncQueryLoop`.

## Validation

- Registry tests prove registration metadata, deregistration, resolve,
  discover, unhealthy filtering, invalid metadata rejection, and JSON-safe
  evidence.
- Public API tests prove exports from `agentos.registry` and top-level
  `agentos`.
- Readiness/docs tests prove Nacos is documented as discovery-only and not a
  truth source.
- Runtime boundary scans prove Nacos concepts do not leak into query loops.
