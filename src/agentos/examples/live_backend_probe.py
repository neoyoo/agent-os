from __future__ import annotations

import argparse
import json
import time
from collections.abc import Sequence


_BACKEND_KINDS: dict[str, str] = {
    "postgres_state_store": "postgres",
    "postgres_artifact_store": "postgres",
    "redis_worker_queue": "redis",
    "redis_relay_queue": "redis",
    "redis_event_replay": "redis",
    "distributed_worker": "distributed_worker",
}


def build_probe_report(
    backend_name: str,
    *,
    status: str = "unknown",
    checked_at: float | None = None,
    evidence_ref: str | None = None,
    target_ref: str | None = None,
    error: str | None = None,
) -> dict[str, object]:
    """Return a standard backend verification report payload.

    This reference example intentionally does not connect to live infrastructure.
    Deployment-owned probes can reuse the report shape after performing real
    PostgreSQL, Redis, or Distributed Worker checks.
    """

    backend_kind = _BACKEND_KINDS[backend_name]
    requested_status = status
    if status == "passed":
        status = "skipped"
        error = error or "reference probe cannot certify a live backend check"
    record: dict[str, object] = {
        "backend_name": backend_name,
        "backend_kind": backend_kind,
        "status": status,
        "checked_at": checked_at if checked_at is not None else time.time(),
        "evidence_ref": evidence_ref or f"reference://live-backend/{backend_name}",
        "target_ref": target_ref or f"deployment://{backend_name}",
        "metadata": {
            "probe": "agentos.examples.live_backend_probe",
            "does not create backend clients": True,
            "certification_claim": "non-certifying-example",
            "requested_status": requested_status,
        },
    }
    record["error"] = error
    if record["error"] is None and status != "passed":
        record["error"] = "reference probe did not execute a live backend check"
    return {"records": [record]}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Emit an AgentOS backend verification report JSON payload.",
    )
    parser.add_argument("backend_name", choices=tuple(_BACKEND_KINDS))
    parser.add_argument(
        "--status",
        choices=("passed", "failed", "skipped", "unknown"),
        default="unknown",
    )
    parser.add_argument("--checked-at", type=float, default=None)
    parser.add_argument("--evidence-ref", default=None)
    parser.add_argument("--target-ref", default=None)
    parser.add_argument("--error", default=None)
    args = parser.parse_args(argv)

    payload = build_probe_report(
        args.backend_name,
        status=args.status,
        checked_at=args.checked_at,
        evidence_ref=args.evidence_ref,
        target_ref=args.target_ref,
        error=args.error,
    )
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
