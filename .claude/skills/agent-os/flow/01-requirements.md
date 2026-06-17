---
name: agent-os-requirements
description: Phased requirements gathering for agent-os SDK projects; asks 6 dimensions one at a time and outputs structured decisions for spec generation.
---

# Requirements Gathering

## Rules

<HARD-GATE>
1. Present one dimension at a time. Wait for user response before proceeding.
2. For each dimension, explain what it means in agent-os terms, offer concrete options, and state the default.
3. After all 6 dimensions, summarize decisions in a table and ask for final confirmation.
4. Do not generate code or spec until the user confirms the summary.
5. Always respond in the user's language. The templates below are English reference; translate and adapt when presenting to the user.
</HARD-GATE>

## Pre-Check

Before asking dimensions, check whether the user's description maps to a known agent form in `modules/agent-forms.md`. If it does, mention the form name, readiness level, and which modules it uses.

For production-bound agents, select the closest readiness form and call
`get_agent_form_readiness(form_id)` from `agentos.readiness`. Carry its
`overall_level`, dimension levels, and `required_app_glue` into the final
summary so the user sees every production delivery gap before spec generation.
Also ask for the sandbox posture before spec generation: `trusted tools only`,
`deployment-owned isolation`, or `future adapter`.

Phase 99: SDK Skill / Spec Generator Finalization makes requirements gathering
the first spec generator finalization gate. For production-bound agents, gather
the inputs needed for a `production_design_constraints` block. The skill is a
production agent design constraint generator: before spec generation, the user
must explicitly choose agent form, runtime profile, state plane components,
persistence backend, registry backend, queue backend, worker supervisor, A2A
exposure, planner/team mode, production readiness checklist, and sandbox
posture: trusted tools only | deployment-owned isolation | future adapter.
Record that the SDK-owned constraint template does not create
deployment-owned infrastructure.

## Dimensions

### 1/6 Agent Purpose And Provider

Ask:

- What problem does this agent solve? One sentence is enough.
- Which LLM provider?
  - `AnthropicProvider`: Claude models, recommended for tool-use.
  - `OpenAIProvider`: OpenAI models.
  - `OpenAICompatibleProvider`: any OpenAI-compatible endpoint such as Ollama, vLLM, or a hosted gateway.
- Which model name?

Default: Anthropic provider with the project's recommended Claude model.

### 2/6 Deployment And State

Ask where the agent runs:

- **Local**: single process, in-memory state, dev/CLI use.
- **Server single-node**: HTTP API, live state in one process, optional snapshot persistence.
- **Server multi-node, primitives ready**: arbitrary nodes may receive the same session through `DistributedWebRuntimeProfile`; the app still owns backend configuration, migrations, TTL/recovery policy, auth, and workspace enforcement.

What this decides:

| Mode | SDK support | Session/state behavior |
|------|-------------|------------------------|
| Local | Direct | Held in the Agent object. |
| Single node | Direct primitives | `InMemoryAgentSessionProvider` for live process; optional `SessionSnapshot` plus SQLite/FileSystem for manual restore. |
| Multi-node | Primitives ready | `DistributedWebRuntimeProfile` with `DurableAgentSessionProvider`, `RedisSessionLeaseStore`, `PostgresSessionSnapshotPersistence`, `SnapshotAgentFactory`, and `SessionSnapshot`. |

For production deployments, check `agentos.readiness` for the chosen form and call out required app glue across session state, concurrency, auth, rate limiting, timeout, retry, observability, workspace, protocol, persistence, and schema migration.

Default: Local.

### 3/6 Tools And Capabilities

Ask:

- What actions can the agent perform?
- Does it need MCP servers?
- Are any tools sensitive enough to require hooks, approval, or sandboxing?

What this decides:

- `AgentBuilder.tools([...])` for `RegisteredTool` values.
- `ToolCallRouter`, wired automatically by builder.
- `WorkspaceToolSandboxPolicy` plus `ToolPathSandboxRule` if a tool has
  workspace-local path arguments or capability allow-list requirements.
- `MCPToolAdapter` if external MCP servers are needed.
- `HookManager` if approval or policy checks are required.

Context protocol tools are always available through AgentBuilder unless the app explicitly changes the default wiring.

Default: no external tools.

### 4/6 Context And Compression Strategy

Ask:

- How long are typical sessions?
  - **Short**: no compression needed.
  - **Medium**: rule-based compression recommended.
  - **Long**: LLM-based or fallback compression recommended.
- Does the agent need structured working state?
- Does the agent need recall over prior conversations or documents?

What this decides:

- `CompressionRuntime` strategy and budget.
- Whether to predeclare working-state schema.
- Whether to add `MemoryRuntime` / `RecallRuntime`.

Default: no compression for short sessions; context protocol remains available.

### 5/6 Multi-Agent

Ask whether this is a single agent or coordinates with others:

- **Single**: one agent, one loop.
- **Local sub-agents**: spawn or dispatch inside the same process.
- **Distributed task dispatch**: use Postgres/Redis task primitives or endpoint-backed HTTP task bridge.
- **Team discussion**: team records/messages/wakeup/tools, worker session lifecycle, workspace/capability downgrade policy, workspace-aware tool path/capability sandbox policy, worker runner, daemon polling, worker process lifecycle readiness, persistent retry/backoff, persistent cancellation intent, UI event stream primitives, Postgres UI stream storage, JSON replay endpoint, and SSE follow endpoint exist; actual process supervision/scaling and OS/container sandboxing remain deployment-owned for untrusted code tools.
- **Planner / intent-router**: planner tools/state/template/decomposition-validation/dependency primitives, failure/retry metadata, `PlannerSchedulerDaemon`, `PostgresPlanStore`, decomposition policy readiness, and worker process lifecycle readiness exist; app owns LLM prompt/model/approval/evaluation policy, plan discovery, distributed scheduler locks, process supervision, worker dispatch loops, lifecycle execution, and complex compensation policy.

What this decides:

| Mode | SDK module | Coordination |
|------|------------|--------------|
| Single | Agent | Direct one-agent loop. |
| Local spawn | `AgentCoordinator`, `TaskTable`, `AgentInbox`, `SpawnExecutor` | In-process task delegation. |
| Distributed task dispatch | `PostgresTaskStore`, `RedisAgentMessageQueue`, `RemoteTaskExecutor`, `A2AAdapter` | Cross-process task/result primitives. |
| Team discussion | `TeamRuntime`, `TeamStore`, `TeamNoticeStore`, `TeamWorkerSessionProvider`, `TeamWorkerPermissionPolicy`, `WorkspaceToolSandboxPolicy`, `TeamWorkerRunner`, `TeamWorkerDaemon`, `WorkerProcessLifecycleDeploymentProfile`, `TeamWorkerRetryPolicy`, `PostgresTeamWorkerRetryStore`, `PostgresTeamWorkerCancellationStore`, `TeamUiStreamStore`, `PostgresTeamUiStreamStore` | Team state, message, worker-session, workspace/capability downgrade, tool path/capability pre-execution checks, continuation runner, daemon, worker lifecycle readiness, persistent retry/backoff, persistent cancellation intent, UI event stream boundary, distributed UI replay storage, JSON replay endpoint, and SSE follow endpoint; actual process supervision/scaling and OS/container sandboxing remain deployment-owned. |
| Planner / intent-router | `PlannerRuntime`, `PlannerTools`, `PlanStore`, `PlanDecomposition`, `PlanDecompositionValidationReport`, `PlannerDecompositionPolicyDeploymentProfile`, `PlannerSchedulerDaemon`, `SubAgentTemplate`, `PlanRetryPolicy`, `WorkerProcessLifecycleDeploymentProfile`, `EvidenceHandle`, `AgentCoordinator` | Plan state, structured decomposition validation/ingestion, dependency metadata, ready-step query, failure recording, retryable-step query, retry reset, tool-driven plan updates, step assignment, explicitly supplied plan id scheduler polling, decomposition policy readiness, worker lifecycle readiness, and evidence handles; LLM prompt/model/approval/evaluation policy/plan discovery/distributed scheduler locks/lifecycle execution/compensation policy remain app-owned. |

Default: Single agent.

### 6/6 Channel And Access

Ask how users or systems interact with the agent:

- **Programmatic**: imported as a library and called via `agent.run()` / `agent.stream()`.
- **HTTP API**: exposed via ASGI app.
- **HTTP + SSE streaming**: ASGI plus streaming endpoint.
- **Internal A2A task bridge**: accepts agent-os task payloads at `/a2a/tasks`.
- **A2A discovery surface**: publishes A2A Agent Card metadata for discovery.
- **A2A task status subscribe**: exposes `POST /a2a/tasks/{id}:subscribe`
  for SSE task updates.

What this decides:

- No channel: use Agent directly.
- HTTP/SSE: use `AsgiAgentApp` plus an `AgentSessionProvider`.
- Multi-node HTTP: use `DistributedWebRuntimeProfile` with `DurableAgentSessionProvider`, concrete lease and snapshot adapters, and explicit deployment policy.
- Internal task bridge: wire `A2AServerAdapter`.
- Discovery: publish A2A Agent Card and optionally expose
  `message/send`, task get/cancel, and task subscribe, while keeping push
  notifications, trust, auth, and full A2A parity as roadmap/deployment work.

Default: Programmatic.

## Summary Template

After all 6 dimensions, present a concise table:

| Dimension | Decision |
|-----------|----------|
| Agent | One-line description |
| Provider | Provider and model |
| Deployment | local / single-node / multi-node primitives |
| Tools | tool list or context-only |
| Context | compression, working state, recall |
| Multi-agent | single / local-spawn / distributed-task / team-primitives / planner-primitives |
| Channel | programmatic / HTTP / HTTP+SSE / internal-A2A / discovery |
| Production readiness | direct/primitives-ready/future plus required app glue from `agentos.readiness` |
| Sandbox posture | trusted tools only / deployment-owned isolation / future adapter |
| Production design constraints | `production_design_constraints`: agent form, runtime profile, state plane components, persistence backend, registry backend, queue backend, worker supervisor, A2A exposure, planner/team mode, production readiness checklist, sandbox posture: trusted tools only \| deployment-owned isolation \| future adapter, SDK does not create deployment-owned infrastructure |

Ask the user to confirm before generating the spec.
