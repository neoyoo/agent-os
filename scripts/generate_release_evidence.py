from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from agentos.release import RELEASE_EVIDENCE_REQUIRED_GATES


GateStatus = Literal["unknown", "pending", "passed", "failed"]

DEFAULT_GATE_COMMANDS: dict[str, str] = {
    "public_api_audit": "uv run pytest tests/architecture/test_public_api.py -q",
    "full_test_suite": "uv run pytest -q",
    "compileall": "uv run python -m compileall -q src tests",
    "diff_hygiene": "git diff --check",
    "runtime_boundary_scan": (
        'rg -n "ReferenceLiveBackendProbe|REFERENCE_LIVE_BACKEND|'
        "ReferenceStatePlane|state plane|readiness|planner|team|A2A|sandbox|"
        "worker supervisor|production_design_constraints|release hardening|"
        'production reference" src/agentos/runtime/query_loop.py '
        "src/agentos/runtime/async_query_loop.py"
    ),
    "docs_alignment": (
        "uv run pytest tests/docs/test_production_hardening_docs.py "
        "tests/docs/test_production_readiness_docs.py "
        "tests/docs/test_objective_coverage_audit_docs.py -q"
    ),
    "migration_index": "manual review: docs/migrations/README.md",
    "api_stability_inventory": "manual review: docs/api-stability.md",
    "production_reference_honesty": (
        "uv run pytest tests/examples/test_production_reference_web_agent.py -q"
    ),
    "planner_plan_store_concurrency": (
        "uv run pytest tests/multi/test_planner_runtime.py "
        "tests/multi/test_postgres_plan_store.py "
        "tests/multi/test_postgres_plan_claim_store.py "
        "tests/multi/test_planner_claimed_scheduler_daemon.py "
        "tests/multi/test_planner_scheduler_daemon.py -q"
    ),
    "workspace_security_policy": (
        "uv run pytest tests/test_workspace.py "
        "tests/capabilities/test_tool_sandbox_policy.py "
        "tests/capabilities/test_mcp.py "
        "tests/capabilities/test_execution_backend.py "
        "tests/capabilities/test_tool_argument_validation.py -q"
    ),
    "independent_review": (
        "fresh subagent review with objective rubric and no score-target disclosure"
    ),
}

DEFAULT_GATE_EVIDENCE_REFS: dict[str, str] = {
    "migration_index": "docs/migrations/README.md",
    "api_stability_inventory": "docs/api-stability.md",
}


def build_manifest(
    *,
    branch: str,
    commit: str,
    version: str,
    generated_at: str,
    independent_review_status: GateStatus,
    gate_results: dict[str, int],
) -> dict[str, object]:
    review_ready = independent_review_status == "passed"
    gates = {
        name: _build_gate(
            name,
            generated_at=generated_at,
            exit_code=gate_results.get(name),
        )
        for name in RELEASE_EVIDENCE_REQUIRED_GATES
    }
    if "independent_review" in gates:
        gates["independent_review"]["status"] = independent_review_status

    return {
        "schema": "agentos.release_evidence",
        "schema_version": 1,
        "certification_claim": "sdk-release-candidate-evidence",
        "sdk_owned": True,
        "release_candidate": {
            "branch": branch,
            "generated_at": generated_at,
            "commit": commit,
            "version": version,
        },
        "generator": {
            "source_path": "scripts/generate_release_evidence.py",
            "review_policy": "does_not_mark_independent_review_passed",
        },
        "deployment_owned": [
            "CI/CD execution",
            "artifact signing",
            "publishing",
            "deployment approval",
            "rollout and rollback",
        ],
        "independent_review": {
            "status": independent_review_status,
            "evidence_ref": "fresh independent review not yet attached",
            "ready_for_release_candidate": review_ready,
        },
        "gates": gates,
        "notes": [
            "This manifest records SDK-side release evidence only.",
            "It is not a production certification, deployment approval, or signing record.",
            "Live backend evidence must be imported from deployment-owned checks.",
        ],
        "live_backend_verification": {
            "status": "not_certified_by_sdk_rc",
            "sdk_rc_claim": "boundary_only",
            "required_before_production_deployment": True,
            "required_backends": [
                "agent_registry",
                "message_queue",
                "task_store",
                "plan_store",
                "worker_process_supervisor",
                "session_snapshot_persistence",
            ],
            "evidence_ref": (
                "docs/production-readiness.md#live-backend-verification-evidence-boundary"
            ),
        },
    }


def _build_gate(
    name: str,
    *,
    generated_at: str,
    exit_code: int | None,
) -> dict[str, object]:
    status = _gate_status_from_exit_code(name, exit_code)
    gate: dict[str, object] = {
        "status": status,
        "command": DEFAULT_GATE_COMMANDS[name],
        "evidence_ref": DEFAULT_GATE_EVIDENCE_REFS.get(
            name,
            f"release-evidence://{name}",
        ),
        "required": True,
        "last_verified_at": generated_at,
    }
    if exit_code is not None:
        gate["result"] = {"exit_code": exit_code}
        if name == "runtime_boundary_scan" and exit_code == 1:
            gate["result"]["matches"] = 0
    return gate


def _gate_status_from_exit_code(name: str, exit_code: int | None) -> GateStatus:
    if exit_code is None:
        return "unknown"
    if name == "runtime_boundary_scan":
        if exit_code == 1:
            return "passed"
        if exit_code == 0:
            return "failed"
    return "passed" if exit_code == 0 else "unknown"


def _parse_gate_result(value: str) -> tuple[str, int]:
    gate_name, separator, exit_code_text = value.partition("=")
    if separator != "=":
        raise argparse.ArgumentTypeError(
            "gate result must be formatted as <gate-name>=<exit-code>",
        )
    if gate_name not in RELEASE_EVIDENCE_REQUIRED_GATES:
        raise argparse.ArgumentTypeError(f"unknown release evidence gate: {gate_name}")
    try:
        exit_code = int(exit_code_text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"gate result exit code must be an integer: {value}",
        ) from exc
    return gate_name, exit_code


def _default_generated_at() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate AgentOS release candidate evidence manifest.",
    )
    parser.add_argument("--output", required=True, help="Output manifest path.")
    parser.add_argument("--branch", required=True, help="Release candidate branch.")
    parser.add_argument("--commit", required=True, help="Release candidate commit SHA.")
    parser.add_argument("--version", required=True, help="Release candidate version.")
    parser.add_argument(
        "--generated-at",
        default=None,
        help="ISO-8601 evidence generation timestamp. Defaults to current UTC time.",
    )
    parser.add_argument(
        "--independent-review-status",
        choices=("unknown", "pending", "failed"),
        default="pending",
        help="Independent review status. The generator never marks review passed.",
    )
    parser.add_argument(
        "--gate-result",
        action="append",
        default=[],
        type=_parse_gate_result,
        help="Attach a freshly observed gate exit code as <gate-name>=<exit-code>.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    gate_results = dict(args.gate_result)
    manifest = build_manifest(
        branch=args.branch,
        commit=args.commit,
        version=args.version,
        generated_at=args.generated_at or _default_generated_at(),
        independent_review_status=args.independent_review_status,
        gate_results=gate_results,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
