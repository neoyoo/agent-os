# A2A Agent Card And Discovery Design (Phase 4)

> Status: draft  
> Date: 2026-06-11  
> Parent roadmap: `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`  
> Previous phase: `docs/superpowers/specs/2026-06-11-workspace-permission-boundary-design.md`

## Scope Contract

This design belongs to Phase 4: A2A protocol card and discovery.

Target conclusion:

```text
Internal agent-os AgentCard remains the SDK routing card.
A2A Agent Card is a protocol compatibility surface with its own serializer, well-known route, and resolver.
The existing /a2a/tasks bridge remains internal until task/message operation parity is implemented.
```

This phase completes:

- Define an A2A-compatible protocol card model separate from `agentos.multi.AgentCard`.
- Define serialization rules for identity, endpoint URL, capabilities, skills, and auth metadata.
- Add ASGI well-known card publication.
- Add a resolver that can load direct/static/well-known A2A cards.
- Update skill docs so A2A is described truthfully.

This phase does not complete:

- Full A2A task/message operation parity.
- Streaming task updates.
- Push notifications.
- Signed cards or trust store validation.
- OAuth/OIDC/JWKS enforcement.
- Team discussion runtime.

## External Baseline

A2A's latest documentation treats Agent Card discovery as a core interoperability layer. Agents can publish a card through a well-known URI, registries, or direct configuration. Cards describe protocol version, agent identity, service URL, provider, capabilities, skills, and security/auth metadata. The specification also includes task/message operations, streaming, push notifications, and richer auth schemes; those are later work for agent-os.

AgentScope 2.0 Agent Service and Agent Team reinforce the same boundary: service hosting and team execution are distributed service concerns, not local object references. A protocol card should advertise what a remote agent offers; it should not expose local workspace roots or Python runtime objects.

Reference sources used on 2026-06-11:

- https://a2a-protocol.org/latest/topics/agent-discovery/
- https://a2a-protocol.org/latest/specification/
- https://docs.agentscope.io/v2/deploy/agent-service
- https://docs.agentscope.io/v2/deploy/agent-team

## Current Evidence

Current agent-os has:

| Area | Evidence | Readiness |
|------|----------|-----------|
| Internal routing card | `agentos.multi.AgentCard` | Direct for internal dispatch |
| Registry/resolver | `PersistentAgentRegistry`, `StaticResolver`, `ServiceResolver` | Internal card discovery |
| Outbound task bridge | `A2AAdapter.send_task()` | Internal JSON `/a2a/tasks` bridge |
| Inbound task bridge | `A2AServerAdapter.handle_task()` and `AsgiAgentApp` routes | Internal JSON `/a2a/tasks` bridge |
| Well-known A2A card | none | Missing |
| Protocol card serializer | none | Missing |
| A2A card resolver | none | Missing |

The current `AgentCard` is intentionally small:

```text
agent_id, name, description, capabilities, version, endpoint, status, lifecycle, max_concurrent_tasks
```

That is good for internal routing, but insufficient as an A2A protocol card.

## Design Principles

1. Keep internal routing card and A2A protocol card separate.
2. Add adapters between internal cards and protocol cards where useful.
3. Do not claim A2A compliance until operation parity exists.
4. Well-known discovery should be optional and configured on `AsgiAgentApp`.
5. Protocol cards must not leak local workspace roots, credentials, or Python object names.
6. A2A resolver should use injected transport for deterministic tests.
7. `QueryLoop` remains unaware of A2A, registry, and channels.

## Core Contracts

### A2AAgentSkill

```python
@dataclass(frozen=True, slots=True)
class A2AAgentSkill:
    id: str
    name: str
    description: str
    tags: tuple[str, ...] = ()
    examples: tuple[str, ...] = ()
```

### A2AAgentCapabilities

```python
@dataclass(frozen=True, slots=True)
class A2AAgentCapabilities:
    streaming: bool = False
    push_notifications: bool = False
    state_transition_history: bool = False
```

### A2AAgentProvider

```python
@dataclass(frozen=True, slots=True)
class A2AAgentProvider:
    organization: str
    url: str | None = None
```

### A2AAgentCard

```python
@dataclass(frozen=True, slots=True)
class A2AAgentCard:
    name: str
    description: str
    url: str
    version: str
    protocol_version: str = "0.3.0"
    provider: A2AAgentProvider | None = None
    capabilities: A2AAgentCapabilities = field(default_factory=A2AAgentCapabilities)
    skills: tuple[A2AAgentSkill, ...] = ()
    default_input_modes: tuple[str, ...] = ("text/plain",)
    default_output_modes: tuple[str, ...] = ("text/plain",)
    security_schemes: Mapping[str, Mapping[str, object]] = field(default_factory=dict)
    security: tuple[Mapping[str, tuple[str, ...]], ...] = ()
```

Serialization should use A2A JSON key style:

```json
{
  "protocolVersion": "0.3.0",
  "name": "Research Agent",
  "description": "Researches documents.",
  "url": "https://agents.example/a2a",
  "version": "1.0.0",
  "capabilities": {"streaming": true},
  "defaultInputModes": ["text/plain"],
  "defaultOutputModes": ["text/plain"],
  "skills": [
    {"id": "research", "name": "Research", "description": "..."}
  ]
}
```

### Adapter From Internal AgentCard

```python
def a2a_card_from_agent_card(
    card: AgentCard,
    *,
    url: str | None = None,
) -> A2AAgentCard:
    ...
```

Rules:

- Use `url or card.endpoint`.
- Convert each internal capability into an `A2AAgentSkill` unless richer skill metadata is supplied later.
- Preserve version, name, and description.
- Do not copy internal lifecycle/status/max concurrency into protocol card unless later mapped to A2A extensions.

### A2ACardResolver

```python
class A2ACardResolver:
    def __init__(
        self,
        *,
        cards: Sequence[A2AAgentCard] = (),
        well_known_urls: Mapping[str, str] | None = None,
        transport: A2ATransport | None = None,
    ) -> None: ...

    def resolve(self, name: str) -> A2AAgentCard | None: ...
    def discover(self, required_skills: Sequence[str]) -> list[A2AAgentCard]: ...
```

`well_known_urls` maps a logical name to the full well-known card URL or service base URL. The resolver uses `transport.get_json()` and `a2a_card_from_dict()`.

## ASGI Integration

`AsgiAgentApp` should accept:

```python
a2a_agent_card: A2AAgentCard | None = None
```

When configured:

- `GET /.well-known/agent-card.json` returns the card JSON.
- `GET /a2a/agent-card` may return the same card as a convenience route.
- Existing `/a2a/tasks` and `/a2a/health` behavior remains unchanged.

When not configured:

- Well-known route returns 404.

## Acceptance Criteria

### AC-4A-1: Protocol card serialization

```text
A2AAgentCard serializes to A2A JSON key style and round-trips from dict.
Capabilities, skills, provider, default modes, and security metadata are preserved.
```

### AC-4A-2: Internal card adapter

```text
An internal agent-os AgentCard can be converted to an A2AAgentCard without mutating the internal card model.
Capabilities become skills, and endpoint becomes protocol url.
```

### AC-4A-3: Well-known publication

```text
Given AsgiAgentApp(a2a_agent_card=card)
GET /.well-known/agent-card.json returns card JSON with content-type application/json.
Without a card, the route returns 404.
```

### AC-4A-4: Direct and well-known resolver

```text
A2ACardResolver resolves injected cards and cards fetched through an injected transport.
Discovery can filter by skill id.
```

### AC-4A-5: Public API

```text
A2AAgentCard, A2AAgentSkill, A2AAgentCapabilities, A2AAgentProvider,
A2ACardResolver, a2a_card_to_dict, a2a_card_from_dict, and a2a_card_from_agent_card
are exported from agentos.channels and top-level agentos if channel exports are mirrored.
```

### AC-4A-6: Docs remain truthful

```text
Skill docs say A2A card/discovery primitives exist,
but full A2A task/message/streaming/push/auth operation parity is not complete.
```

### AC-4A-7: QueryLoop remains deployment-agnostic

```text
No imports from query_loop.py or async_query_loop.py to channels, registry, A2A, workspace, Redis, Postgres, or runtime.profile.
```

## Implementation Slice

Phase 4A should implement:

- `A2AAgentCard` dataclasses in `agentos.channels.a2a`.
- Serializer/deserializer helpers.
- Adapter from internal `AgentCard`.
- `A2ACardResolver` with injected transport.
- `AsgiAgentApp` well-known route.
- Public exports and tests.
- Skill docs updates.

Phase 4A should not implement:

- A2A message/send/stream operation parity.
- Push notification registration.
- OAuth/JWKS enforcement.
- Signed card verification.
- Registry store schema changes.

## Later Work

Phase 4B:

- A2A task/message operation model.
- Streaming task update route.
- Auth scheme enforcement and signed-card trust policy.

Phase 5:

- Team runtime and distributed wakeup using card-discovered worker capabilities.

Phase 6:

- Planner/subagent templates that select workers by protocol skills/capabilities.

## Completion Checklist

| Requirement | Evidence |
|-------------|----------|
| Protocol card model exists | source + serialization tests |
| Internal adapter works | adapter tests |
| Well-known route works | ASGI tests |
| Resolver works | resolver tests |
| Public API exported | public API tests |
| Docs truthful | skill docs diff + drift search |
| QueryLoop agnostic | boundary search |
| Existing suite passes | targeted tests, compileall, full pytest, diff check |
