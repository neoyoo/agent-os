from pathlib import Path
import json

from agentos.release import RELEASE_EVIDENCE_REQUIRED_GATES


ROOT = Path(__file__).resolve().parents[2]


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_required_release_documents_and_examples_exist() -> None:
    for name in (
        "README.md",
        "docs/quickstart.md",
        "docs/release-scope.md",
        "docs/production-readiness.md",
        "docs/agentos-objective-coverage-audit.md",
        "docs/release-hardening.md",
        "docs/api-stability.md",
        "docs/migrations/README.md",
        "docs/migrations/phase6-distributed-runtime-breaking-map.md",
        "CHANGELOG.md",
    ):
        assert (ROOT / name).is_file()

    for name in (
        "streaming_agent.py",
        "multi_agent_dispatch.py",
        "mcp_agent.py",
        "persistent_agent.py",
        "planner_patterns.py",
        "live_backend_probe.py",
    ):
        text = _read(f"src/agentos/examples/{name}")
        assert 'if __name__ == "__main__"' in text


def test_readme_and_quickstart_describe_current_runtime_levels() -> None:
    for text in (_read("README.md"), _read("docs/quickstart.md")):
        for expected in (
            "Local",
            "Durable",
            "Distributed",
            "QueryLoop",
            "DistributedRuntimeProfile",
            "PostgreSQL",
            "Redis",
            "BlobStore",
            "docs/release-hardening.md",
            "phase6-distributed-runtime-breaking-map.md",
        ):
            assert expected in text


def test_current_release_docs_do_not_reintroduce_removed_entry_points() -> None:
    documents = (
        "README.md",
        "docs/quickstart.md",
        "docs/release-scope.md",
        "docs/production-readiness.md",
        "CHANGELOG.md",
    )
    removed = (
        "production_reference_web_agent.py",
        "AgentServiceReference",
        "DistributedWebRuntimeProfile",
        "PostgresSessionSnapshotPersistence",
        "RedisAgentMessageQueue",
        "PostgresTaskStore",
        "PostgresPlanStore",
    )

    for name in documents:
        text = _read(name)
        for symbol in removed:
            assert symbol not in text, f"{name} still references {symbol}"


def test_release_evidence_example_matches_current_gate_and_backend_sets() -> None:
    manifest = json.loads(_read("docs/release-evidence.example.json"))

    assert manifest["schema"] == "agentos.release_evidence"
    assert manifest["certification_claim"] == "non-certifying-sdk-evidence"
    assert set(manifest["gates"]) == set(RELEASE_EVIDENCE_REQUIRED_GATES)
    assert manifest["live_backend_verification"]["required_backends"] == [
        "postgres_state_store",
        "postgres_artifact_store",
        "redis_worker_queue",
        "redis_relay_queue",
        "redis_event_replay",
        "distributed_worker",
    ]


def test_release_hardening_defines_revision_bound_non_certifying_evidence() -> None:
    text = _read("docs/release-hardening.md")

    for expected in (
        "docs/public-api-inventory.json",
        "docs/release-evidence.example.json",
        "scripts/generate_release_evidence.py",
        "validate_release_evidence_manifest",
        "validate_release_candidate_evidence_manifest",
        "RELEASE_EVIDENCE_REQUIRED_GATES",
        "non-certifying SDK evidence",
        "expected branch, commit, and version identity",
        "does not run CI/CD, signing, publishing, deployment approval",
    ):
        assert expected in text


def test_migration_index_and_changelog_record_phase6_cutover() -> None:
    migration_index = " ".join(_read("docs/migrations/README.md").split())
    changelog = " ".join(_read("CHANGELOG.md").split())

    for expected in (
        "phase6-distributed-runtime-breaking-map.md",
        "2026-07-20-postgres-distributed-runtime.sql",
        "removed Session Snapshot",
    ):
        assert expected in migration_index

    for expected in (
        "0.3.0a1",
        "DistributedRuntimeProfile",
        "DistributedWorker",
        "PostgreSQL",
        "Redis",
        "compatibility aliases or fallbacks",
    ):
        assert expected in changelog
