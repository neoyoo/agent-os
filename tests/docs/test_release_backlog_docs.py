from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_release_backlog_contains_only_residual_or_deployment_owned_work() -> None:
    text = (ROOT / "docs/release-backlog.md").read_text(encoding="utf-8")
    normalized = " ".join(text.split())

    for expected in (
        "Phase 6 Residual Backlog",
        "outside the `0.3.0a1` SDK contract",
        "does not waive failures",
        "distributed/global quota storage",
        "public A2A peer admission",
        "worker process supervision",
        "tenant directory integration",
        "physical sandbox isolation",
        "PlannerRuntime",
        "global fairness",
        "leader election",
        "global exactly-once execution",
        "Provider transcript recovery",
        "cross-region multi-primary state",
        "automatic attachment summaries",
        "PostgreSQL side-effect ledger",
        "at-least-once Redis delivery",
        "compatibility facades or fallback behavior",
    ):
        assert expected in normalized


def test_release_backlog_does_not_name_removed_phase5_implementations() -> None:
    text = (ROOT / "docs/release-backlog.md").read_text(encoding="utf-8")

    for removed in (
        "production_reference_web_agent.py",
        "DistributedWebRuntimeProfile",
        "PostgresSessionSnapshotPersistence",
        "RedisAgentMessageQueue",
        "PostgresTaskStore",
        "PostgresPlanStore",
    ):
        assert removed not in text
