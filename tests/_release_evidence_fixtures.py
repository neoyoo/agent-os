from __future__ import annotations

from agentos.release import RELEASE_EVIDENCE_REQUIRED_GATES


def passing_gate(name: str) -> dict[str, object]:
    return {
        "status": "passed",
        "command": f"run {name}",
        "evidence_ref": f"ci://agentos/{name}",
        "required": True,
        "last_verified_at": "2026-06-17T01:00:00+08:00",
    }


def release_manifest(**overrides: object) -> dict[str, object]:
    manifest: dict[str, object] = {
        "schema": "agentos.release_evidence",
        "schema_version": 1,
        "certification_claim": "sdk-release-candidate-evidence",
        "sdk_owned": True,
        "release_candidate": {
            "branch": "review/agentos-sdk-architecture-20260611",
            "generated_at": "2026-06-17T01:00:00+08:00",
            "commit": "abc123",
            "version": "0.1.0rc1",
        },
        "deployment_owned": [
            "CI/CD execution",
            "artifact signing",
            "publishing",
            "deployment approval",
            "rollout and rollback",
        ],
        "independent_review": {
            "status": "passed",
            "ready_for_release_candidate": True,
            "evidence_ref": "review://fresh-subagent",
        },
        "live_backend_verification": {
            "status": "not_certified_by_sdk_rc",
            "sdk_rc_claim": "boundary_only",
            "required_before_production_deployment": True,
            "required_backends": (
                "postgres_state_store",
                "postgres_artifact_store",
                "redis_worker_queue",
                "redis_relay_queue",
                "redis_event_replay",
                "distributed_worker",
            ),
            "evidence_ref": (
                "docs/production-readiness.md"
                "#live-backend-verification-evidence-boundary"
            ),
        },
        "gates": {
            name: passing_gate(name)
            for name in RELEASE_EVIDENCE_REQUIRED_GATES
        },
        "notes": ["SDK-side release evidence only."],
    }
    manifest.update(overrides)
    return manifest
