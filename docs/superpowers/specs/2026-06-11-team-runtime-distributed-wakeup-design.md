# Team Runtime And Distributed Wakeup Design (Phase 5A)

> Date: 2026-06-11  
> Branch: `review/agentos-sdk-architecture-20260611`  
> Status: design for next execution slice  
> Related roadmap: `docs/plans/2026-06-11-agentos-sdk-architecture-review-roadmap.md`

## Target Conclusion

```text
Team-style multi-agent coordination is not nested function calling.
Leader and worker agents are independent sessions coordinated by team records,
team messages, inbox notifications, wakeup notices, and explicit workspace/artifact
handles. QueryLoop remains unaware of team runtime.
```

Phase 5A should add a small team runtime boundary, not a planner and not a full
AgentScope clone. The important production boundary is that team conversation
state lives outside any one agent's active messages.

## External Baseline

Reviewed on 2026-06-11:

- AgentScope Agent Team documentation:
  https://docs.agentscope.io/v2/deploy/agent-team
- AgentScope issue about worker completion needing `TeamSay` wakeup:
  https://github.com/agentscope-ai/agentscope/issues/1797
- AgentScope issue about copying permission rules when creating team members:
  https://github.com/agentscope-ai/agentscope/issues/1793

Baseline interpretation:

- AgentScope-style team runtime treats the leader as the user-facing session and
  workers as independent sessions with their own state, workspace binding, and
  event stream.
- The first-class tools are team creation, worker creation, team messaging, and
  team deletion.
- Worker completion must notify the leader. Otherwise the leader can sleep while
  work has actually finished.
- Permission and workspace inheritance need explicit policy. Workers should not
  silently broaden the leader's access.

## Current agent-os Fit

Existing primitives that should be reused:

- `AgentCoordinator` already isolates spawned and dispatched workers from parent
  active messages.
- `AgentMessageQueue` provides an inbox/notification boundary with in-memory and
  Redis adapters.
- `AgentTaskNoticeStore` and continuation triggers show the right wakeup shape:
  write a notice, then let the parent run a continuation turn.
- `WorkspaceHandle`, `WorkspaceProvider`, and `WorkspacePolicy` can express team
  and worker workspace narrowing.
- `TaskStore` is already the truth source for tasks while the queue is delivery
  only. Team runtime should use the same pattern.

Current gaps:

- No `TeamRecord`, `TeamMemberRecord`, or `TeamMessage` model.
- No team store protocol or in-memory team store.
- No `team_message` envelope type.
- No team-specific notice store for continuation prompt injection.
- No SDK-level runtime that can append team messages, notify recipients, and
  allow recipients to read team messages without draining task envelopes.
- No public API guidance for team runtime readiness.

## Design Principle

Team messages must be stored before notifications are sent.

```text
TeamStore is the truth source.
AgentMessageQueue is a wakeup/delivery hint.
TeamNoticeStore is the prompt-injection bridge for continuation turns.
```

Do not implement `TeamRuntime.collect_messages()` by draining the shared
`AgentMessageQueue`. Draining the shared queue would risk swallowing task
request/result envelopes. Recipients should read team messages from `TeamStore`
and use queue envelopes only as wakeup hints.

## Proposed Public Model

Add `src/agentos/multi/team.py` with:

- `TeamStatus = Literal["active", "deleted"]`
- `TeamMemberRole = Literal["leader", "worker"]`
- `TeamMemberStatus = Literal["active", "offline", "removed"]`
- `TeamMessageKind = Literal["instruction", "observation", "result", "notice"]`
- `TeamRecord`
- `TeamMemberRecord`
- `TeamMessage`
- `TeamStore` protocol
- `InMemoryTeamStore`
- `TeamNoticeProvider`
- `TeamNoticeStore`
- `TeamWakeupTrigger` protocol
- `LocalTeamWakeupTrigger`
- `TeamRuntime`

Suggested dataclass shape:

```python
@dataclass(frozen=True, slots=True)
class TeamRecord:
    team_id: str
    name: str
    description: str
    leader_agent_id: str
    created_at: float
    status: TeamStatus = "active"
    workspace: WorkspaceHandle | None = None


@dataclass(frozen=True, slots=True)
class TeamMemberRecord:
    team_id: str
    agent_id: str
    role: TeamMemberRole
    session_id: str | None = None
    capabilities: tuple[str, ...] = ()
    workspace: WorkspaceHandle | None = None
    status: TeamMemberStatus = "active"
    created_at: float = 0


@dataclass(frozen=True, slots=True)
class TeamMessage:
    message_id: str
    team_id: str
    from_agent_id: str
    content: str
    created_at: float
    to_agent_id: str | None = None
    kind: TeamMessageKind = "observation"
    correlation_id: str | None = None
    artifact_handles: tuple[str, ...] = ()
    metadata: Mapping[str, str] = field(default_factory=dict)
```

`to_agent_id=None` means team broadcast. Broadcast visibility should include all
active team members except the sender unless a caller explicitly requests all
messages for audit. Directed messages are visible to the recipient; audit callers
can read all messages through the store without passing `agent_id`.

## Runtime Behavior

`TeamRuntime.create_team(...)`:

1. Creates a `TeamRecord`.
2. Adds the leader as a `TeamMemberRecord`.
3. Creates a leader inbox through `AgentMessageQueue.create_inbox()`.
4. Does not mutate the leader agent's context or messages.

`TeamRuntime.add_member(...)`:

1. Verifies the team is active.
2. Adds a worker member record.
3. Creates a worker inbox.
4. Accepts an optional narrowed `WorkspaceHandle`.
5. Does not create an `Agent` instance in Phase 5A. Worker session lifecycle
   remains app/profile-owned until Phase 5B.

`TeamRuntime.say(...)`:

1. Verifies sender and recipient membership.
2. Appends `TeamMessage` to `TeamStore`.
3. Sends `AgentEnvelope(type="team_message", payload=message)` to each recipient
   inbox as a wakeup hint.
4. Calls `TeamWakeupTrigger.on_team_message(recipient_agent_id, team_id, message_id)`
   for continuation prompt injection.
5. If queue send fails, the message remains in `TeamStore`; consumers can recover
   by polling store-backed messages.

`TeamRuntime.messages_for(agent_id, team_id, after_message_id=None)`:

1. Reads from `TeamStore`.
2. Returns only messages visible to the member.
3. Does not drain `AgentMessageQueue`.

## Envelope Serialization

Extend `AgentEnvelopeType`:

```python
AgentEnvelopeType = Literal["task_request", "task_result", "team_message"]
```

Extend `AgentEnvelope.payload`:

```python
payload: TaskRequest | TaskResult | TeamMessage
```

`agentos.multi.serializers` should add `team_message_to_dict()` and
`team_message_from_dict()` and update `envelope_to_dict()` /
`envelope_from_dict()`.

## Workspace And Permission Boundary

Phase 5A should not implement sandboxing. It should preserve the following
invariants:

- A team can carry a `WorkspaceHandle(scope="team")`.
- Worker members can carry a narrowed `WorkspaceHandle` whose parent is the team
  or leader workspace.
- `WorkspacePolicy.ensure_child_workspace_allowed()` should be used by app/profile
  code before registering worker workspace handles.
- A2A Agent Cards must not publish local workspace roots.

## Non-Goals For Phase 5A

- No full AgentScope-style service.
- No worker session factory or automatic worker ReAct loop.
- No `team_create`, `agent_create`, `team_say`, `team_delete` LLM tools yet.
- Planner and intent-router primitives are outside Phase 5A; they land as a
  separate Phase 6A planner slice.
- No Postgres team store.
- No UI event stream.
- No direct sharing of active messages or working state between leader and worker.

## Acceptance Criteria

- A leader can create a team with an explicit leader member.
- A worker can be added as an independent member with optional narrowed workspace.
- A leader can send a directed team message to a worker.
- A worker can send a result message back to the leader.
- `TeamRuntime.messages_for()` reads stored team messages without draining task
  envelopes from the shared inbox.
- `TeamNoticeStore` can provide continuation notices for team messages.
- `AgentEnvelope` serialization round-trips `team_message`.
- Public exports are available from `agentos.multi` and top-level `agentos`.
- Skill docs describe team runtime as primitives-ready after Phase 5A, while team
  tools, worker lifecycle, and distributed Postgres store remain later work.
  Planner primitives land separately in Phase 6A and do not change the Phase 5A
  team runtime boundary.
- `QueryLoop` and `AsyncQueryLoop` have no imports or references to team runtime,
  queues, Redis, Postgres, A2A, or workspace.

## Phase 5B Preview

After Phase 5A, the next slice should add team tools and worker session lifecycle:

- `team_create`
- `agent_create`
- `team_say`
- `team_delete`
- worker session factory/profile binding
- worker completion middleware or runtime guard that ensures result messages wake
  the leader
