# Team Tools Design (Phase 9A)

> Date: 2026-06-12
> Branch: `review/agentos-sdk-architecture-20260611`
> Builds on: Phase 5A team runtime and distributed wakeup primitives

## Target Conclusion

```text
Team discussion agents need first-class LLM-callable tools over TeamRuntime;
team records/messages alone are not enough for an SDK-level agent pattern.
```

`TeamRuntime` already provides the correct state boundary: teams, members,
messages, wakeup notices, and inbox hints. The missing SDK layer is a tool
surface that lets a leader or worker agent create teams, add members, speak,
read visible messages, and close a team without learning internal dataclasses.

## Scope

Add `TeamTools` in `agentos.multi.team`:

- `team_create`
- `agent_create`
- `team_say`
- `team_read_messages`
- `team_delete`

The class mirrors `PlannerTools`:

- accepts `runtime: TeamRuntime`
- accepts `owner_agent_id`
- registers normal external `RegisteredTool` values into `ToolRegistry`
- returns JSON strings that are stable enough for LLM/tool callers
- scopes message reads and sends to the `owner_agent_id`

## Tool Semantics

### `team_create`

Creates a team with the owner as leader.

Input:

- `name`
- `description`
- optional `team_id`
- optional `leader_session_id`

Output:

- team record
- leader member record

### `agent_create`

Adds a member to an existing team. The name intentionally matches the roadmap
term, but the operation creates a team member record and inbox, not a full
worker session.

Input:

- `team_id`
- `agent_id`
- optional `role`
- optional `session_id`
- optional `capabilities`

Output:

- member record

### `team_say`

Writes a team message from `owner_agent_id`. The caller cannot spoof
`from_agent_id`.

Input:

- `team_id`
- `content`
- optional `to_agent_id`
- optional `kind`
- optional `correlation_id`
- optional `artifact_handles`
- optional `metadata`

Output:

- message record

### `team_read_messages`

Reads messages visible to `owner_agent_id`, with optional cursor support.

Input:

- `team_id`
- optional `after_message_id`

Output:

- list of visible messages

### `team_delete`

Marks a team deleted. The owner must be an active member of the team.

Input:

- `team_id`

Output:

- deleted boolean

## Security And Boundaries

- Tools must not expose messages invisible to `owner_agent_id`.
- Tools must not let callers send as another agent.
- Tools must not create or run worker sessions.
- Tools must not import runtime loops.
- Workspace narrowing remains represented by `TeamRuntime`/`WorkspaceHandle`,
  but tool JSON does not expose raw filesystem paths.

## Non-Goals

- No distributed `TeamStore`.
- No automatic worker wakeup runner.
- No worker session factory.
- No planner/team auto-integration.
- No UI stream protocol.

## Acceptance Criteria

- `TeamTools.register()` registers the five tool names in a stable order.
- `team_create`, `agent_create`, `team_say`, and `team_read_messages` work
  through `ToolRegistry`.
- `team_read_messages` returns only messages visible to the owner.
- `team_say` always uses `owner_agent_id` as sender.
- `team_delete` requires the owner to be an active member.
- Public exports include `TeamTools`.
- Readiness/docs remove "team tools" from required app glue but keep worker
  session lifecycle and distributed TeamStore gaps.
- Runtime loops remain free of team-tool imports.
