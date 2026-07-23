from __future__ import annotations

from agentos._readiness_form_types import (
    AgentFormReadiness,
    WORKSPACE_BACKEND_EVIDENCE,
    _dimensions,
)


CHANNEL_AGENT_FORMS: dict[str, AgentFormReadiness] = {
"terminal-script": AgentFormReadiness(
        form_id="terminal-script",
        name="Terminal / Script Agent",
        overall_level="direct",
        summary=(
            "Single-process agent built with AgentBuilder and SyncAgent."
        ),
        recommended_profile="LocalRuntimeProfile",
        dimensions=_dimensions(
            {
                "auth": (
                    "not-applicable",
                    ("No network channel is exposed by default",),
                    "",
                ),
                "rate_limit": (
                    "not-applicable",
                    ("No network channel is exposed by default",),
                    "",
                ),
                "workspace": (
                    "primitives-ready",
                    (
                        "LocalWorkspaceProvider",
                        "WorkspacePolicy",
                        "WorkspaceExecutionIsolationProfile",
                        "WorkspaceToolSandboxPolicy",
                        *WORKSPACE_BACKEND_EVIDENCE,
                    ),
                    "OS/container sandboxing remains app/deployment-owned; Docker/E2B/enterprise runner adapters are deployment-owned.",
                ),
                "protocol": (
                    "not-applicable",
                    ("Programmatic Agent.run API",),
                    "",
                ),
                "schema_migration": (
                    "not-applicable",
                    ("No durable shared schema required by default",),
                    "",
                ),
            },
        ),
    ),
    "async-web-host": AgentFormReadiness(
        form_id="async-web-host",
        name="Async Web Host Agent",
        overall_level="primitives-ready",
        summary=(
            "Canonical HTTP/SSE transports and channel composition for an "
            "application-owned async host."
        ),
        recommended_profile="DistributedRuntimeProfile",
        dimensions=_dimensions(
            {
                "auth": (
                    "primitives-ready",
                    ("ChannelAuthenticator", "DistributedAsgiApp"),
                    "Production bearer/custom policy is application configured.",
                ),
                "rate_limit": (
                    "primitives-ready",
                    ("DistributedAsgiApp", "HTTP transport limits"),
                    "Gateway and tenant rate-limit policy remain deployment-owned.",
                ),
                "workspace": (
                    "primitives-ready",
                    (
                        "WorkspaceHandle",
                        "DistributedRuntimeProfile",
                        "WorkspaceExecutionIsolationProfile",
                        "WorkspaceToolSandboxPolicy",
                        *WORKSPACE_BACKEND_EVIDENCE,
                    ),
                    "OS/container sandboxing remains app/deployment-owned; Docker/E2B/enterprise runner adapters are deployment-owned.",
                ),
                "persistence": (
                    "primitives-ready",
                    ("PostgresStateStore", "PostgresArtifactStore"),
                    "Applications must configure backend credentials and migrations.",
                ),
                "schema_migration": (
                    "primitives-ready",
                    ("2026-07-20-postgres-distributed-runtime.sql",),
                    "Migration execution and rollout remain deployment-owned.",
                ),
            },
            default_evidence=("DistributedAsgiApp", "ChannelServices"),
        ),
    ),
    "web-distributed-session": AgentFormReadiness(
        form_id="web-distributed-session",
        name="Web Distributed Session Agent",
        overall_level="primitives-ready",
        summary=(
            "PostgreSQL truth, Redis delivery/replay, shared artifacts, and "
            "distributed Worker recovery use one async execution kernel."
        ),
        recommended_profile="DistributedRuntimeProfile",
        required_app_glue=(
            "Redis delivery/replay configuration and TTL policy",
            "PostgreSQL migration and credentials",
            "shared BlobStore configuration",
            "workspace policy",
            "failure recovery policy",
        ),
        dimensions=_dimensions(
            {
                "session_state": (
                    "primitives-ready",
                    (
                        "DistributedRuntimeProfile",
                        "ProductionStatePlaneDeploymentProfile",
                        "PostgresStateStore",
                        "PostgresArtifactStore",
                        "RunSubmissionService",
                        "RunCommandService",
                    ),
                    "Deployment must run the distributed schema migration and recovery drills.",
                ),
                "concurrency": (
                    "primitives-ready",
                    (
                        "ProductionStatePlaneDeploymentProfile",
                        "DistributedWorker",
                        "PostgresStateStore",
                        "RedisQueueAdapter",
                    ),
                    "Deployment must tune claim/lease TTL and stale-claim recovery policy.",
                ),
                "auth": (
                    "primitives-ready",
                    ("ChannelAuthenticator",),
                    "Tenant/auth integration is application configured.",
                ),
                "rate_limit": (
                    "primitives-ready",
                    ("DistributedAsgiApp", "HTTP transport limits"),
                    "Gateway and tenant rate-limit policy remain deployment-owned.",
                ),
                "workspace": (
                    "primitives-ready",
                    (
                        "WorkspacePolicy",
                        "DistributedRuntimeProfile",
                        "WorkspaceExecutionIsolationProfile",
                        "WorkspaceToolSandboxPolicy",
                        *WORKSPACE_BACKEND_EVIDENCE,
                    ),
                    "OS/container sandboxing remains app/deployment-owned; Docker/E2B/enterprise runner adapters are deployment-owned.",
                ),
                "persistence": (
                    "primitives-ready",
                    (
                        "PostgresStateStore",
                        "PostgresArtifactStore",
                        "RedisEventReplayAdapter",
                    ),
                    "Postgres schema migration, credentials, and live backend verification remain deployment-owned.",
                ),
                "schema_migration": (
                    "primitives-ready",
                    (
                        "2026-07-20-postgres-distributed-runtime.sql",
                    ),
                    "Application rollout/version policy remains deployment-owned.",
                ),
            },
            default_evidence=("DistributedAsgiApp", "DistributedWorker"),
        ),
    ),
    "a2a-discovery": AgentFormReadiness(
        form_id="a2a-discovery",
        name="A2A Discovery Agent",
        overall_level="primitives-ready",
        summary=(
            "A2A 1.0 wire types, stateless endpoint mapping, PostgreSQL task "
            "truth, and queued push delivery compose with the distributed runtime."
        ),
        recommended_profile="DistributedRuntimeProfile",
        required_app_glue=(
            "Agent Card publication and discovery policy",
            "ChannelAuthenticator tenant and peer policy",
            "A2APushClient HTTPS and egress policy",
            "A2APushWorker process supervision and retry policy",
            "credential issuance and secret distribution policy",
            "external A2A conformance and certification execution",
        ),
        dimensions=_dimensions(
            {
                "protocol": (
                    "primitives-ready",
                    (
                        "A2AAgentCard",
                        "A2AAgentInterface",
                        "A2AAgentSkill",
                        "A2AAgentExtension",
                        "A2AEndpoint",
                        "A2ATaskService",
                        "A2ATaskCatalogService",
                        "A2APushService",
                        "A2APushWorker",
                        "A2AProtocolVersionPolicy",
                        "A2AExtensionPolicy",
                        "A2AOperationRequest",
                        "A2AOperationResponse",
                        "A2AMessage",
                        "A2AArtifact",
                        "A2ATask",
                    ),
                    (
                        "External certification, public discovery, peer admission, "
                        "and long-running connection policy remain deployment-owned."
                    ),
                ),
                "auth": (
                    "primitives-ready",
                    (
                        "ChannelAuthenticator",
                        "A2AAgentCardProvider",
                        "A2AAgentCardSignature",
                        "A2ASecurityScheme",
                        "A2ASecurityRequirement",
                        "A2APushAuthenticationInput",
                        "A2APushUrlPolicy",
                    ),
                    (
                        "Credential issuance, trust roots, tenant directory, peer "
                        "roles, and egress enforcement remain deployment-owned."
                    ),
                ),
                "rate_limit": (
                    "primitives-ready",
                    ("DistributedAsgiApp request limits",),
                    (
                        "Per-peer and distributed quotas, gateway enforcement, "
                        "billing, and entitlement policy remain deployment-owned."
                    ),
                ),
                "workspace": (
                    "not-applicable",
                    ("Agent Card omits local workspace paths",),
                    "",
                ),
                "persistence": (
                    "primitives-ready",
                    (
                        "PostgresA2ATaskStore",
                        "PostgresA2ATaskCatalogStore",
                        "PostgresA2APushStore",
                        "PostgresA2APushDeliveryStore",
                    ),
                    "Agent Card discovery storage and deployment credentials remain application-owned.",
                ),
                "schema_migration": (
                    "primitives-ready",
                    (
                        "A2A transport serialization tests",
                        "2026-07-20-postgres-distributed-runtime.sql",
                    ),
                    "External A2A conformance, rollout policy, and certification remain deployment-owned.",
                ),
            },
            default_level="primitives-ready",
            default_evidence=(
                "A2AEndpoint",
                "A2ATaskService",
                "A2ATaskCatalogService",
            ),
            default_gap="Production A2A service policy remains app-owned.",
        ),
    ),
}


__all__ = ["CHANNEL_AGENT_FORMS"]

