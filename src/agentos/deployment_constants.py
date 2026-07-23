from __future__ import annotations

from collections.abc import Mapping
from typing import Literal


BackendVerificationStatus = Literal["passed", "failed", "skipped", "unknown"]


WorkerProcessKind = Literal[
    "worker",
    "team_worker",
    "planner_worker",
    "a2a_push_worker",
]


WorkerProcessStatus = Literal[
    "configured",
    "running",
    "stopping",
    "stopped",
    "exited",
    "failed",
]


PRODUCTION_STATE_PLANE_REQUIRED_COMPONENTS: tuple[str, ...] = (
    "postgres_state_store",
    "postgres_artifact_store",
    "redis_worker_queue",
    "redis_relay_queue",
    "redis_event_replay",
    "distributed_worker",
    "state_plane_boundary_policy",
    "live_backend_verification",
)


LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS: tuple[str, ...] = (
    "postgres_state_store",
    "postgres_artifact_store",
    "redis_worker_queue",
    "redis_relay_queue",
    "redis_event_replay",
    "distributed_worker",
)


LIVE_BACKEND_VERIFICATION_EXPECTED_BACKEND_KINDS: Mapping[str, str] = {
    "postgres_state_store": "postgres",
    "postgres_artifact_store": "postgres",
    "redis_worker_queue": "redis",
    "redis_relay_queue": "redis",
    "redis_event_replay": "redis",
    "distributed_worker": "distributed_worker",
}


_BACKEND_VERIFICATION_STATUSES: tuple[str, ...] = (
    "passed",
    "failed",
    "skipped",
    "unknown",
)


_RESTRICTED_EVIDENCE_METADATA_KEYS: tuple[str, ...] = (
    "api_key",
    "apikey",
    "connection_string",
    "connection_uri",
    "credential",
    "dsn",
    "env",
    "password",
    "private_key",
    "raw_config",
    "secret",
    "token",
)


_PLACEHOLDER_EVIDENCE_REFS: tuple[str, ...] = (
    "<artifact-or-log-ref>",
    "<evidence-ref>",
    "<todo>",
    "artifact-or-log-ref",
    "example",
    "pending",
    "placeholder",
    "tbd",
    "todo",
)


__all__ = [
    "BackendVerificationStatus",
    "LIVE_BACKEND_VERIFICATION_EXPECTED_BACKEND_KINDS",
    "LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS",
    "PRODUCTION_STATE_PLANE_REQUIRED_COMPONENTS",
    "WorkerProcessKind",
    "WorkerProcessStatus",
]
